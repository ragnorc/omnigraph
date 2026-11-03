//! Read-path cost instrument for the vectorized-execution autoresearch run.
//!
//! Opens a GQT dataset file (schema and generated seed, no steps), then runs
//! each query of a plain query file through `Session::query_inspected`,
//! `--warmup` times unmeasured and `--repeat` times measured. The primary
//! scores are deterministic process-wide counters from the counting global
//! allocator (allocations and bytes requested, `realloc` counted as one
//! allocation of its new size); CPU time (`getrusage`, user plus system) and
//! wall time are indicative only. Per-operator rows come from the run's own
//! report (`Executed::profile`). Output is one JSON object per query on
//! stdout; progress goes to stderr.
//!
//! Not a benchmark record: no environment qualification, no reset protocol.

#![recursion_limit = "512"]

use std::alloc::{GlobalAlloc, Layout, System};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph_compiler::ParamMap;
use omnigraph_compiler::settings::Engine;
use omnigraph_gqt_core::{case_session, parse_case, seed_case};

struct Counting;

static ALLOCS: AtomicU64 = AtomicU64::new(0);
static BYTES: AtomicU64 = AtomicU64::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        BYTES.fetch_add(layout.size() as u64, Ordering::Relaxed);
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        BYTES.fetch_add(layout.size() as u64, Ordering::Relaxed);
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        ALLOCS.fetch_add(1, Ordering::Relaxed);
        BYTES.fetch_add(new_size as u64, Ordering::Relaxed);
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

fn cpu_micros() -> u64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: `ts` is a valid, writable timespec.
    let rc = unsafe { libc::clock_gettime(libc::CLOCK_PROCESS_CPUTIME_ID, &mut ts) };
    assert_eq!(rc, 0, "clock_gettime failed");
    ts.tv_sec as u64 * 1_000_000 + ts.tv_nsec as u64 / 1_000
}

#[derive(Clone, Copy, Debug)]
struct Sample {
    allocs: u64,
    bytes: u64,
    cpu_us: u64,
    wall_us: u64,
}

fn median(values: &mut [u64]) -> u64 {
    values.sort_unstable();
    values[values.len() / 2]
}

struct Args {
    fixture: PathBuf,
    queries: PathBuf,
    only: Vec<String>,
    repeat: usize,
    warmup: usize,
    indices: bool,
    label: String,
}

fn parse_args() -> Result<Args, String> {
    let mut args = std::env::args().skip(1);
    let mut out = Args {
        fixture: PathBuf::new(),
        queries: PathBuf::new(),
        only: Vec::new(),
        repeat: 10,
        warmup: 2,
        indices: false,
        label: String::new(),
    };
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--fixture" => out.fixture = args.next().ok_or("--fixture needs a path")?.into(),
            "--queries" => out.queries = args.next().ok_or("--queries needs a path")?.into(),
            "--only" => out.only.push(args.next().ok_or("--only needs a label")?),
            "--repeat" => {
                out.repeat = args
                    .next()
                    .ok_or("--repeat needs a count")?
                    .parse()
                    .map_err(|e| format!("--repeat: {e}"))?
            }
            "--warmup" => {
                out.warmup = args
                    .next()
                    .ok_or("--warmup needs a count")?
                    .parse()
                    .map_err(|e| format!("--warmup: {e}"))?
            }
            "--indices" => out.indices = true,
            "--label" => out.label = args.next().ok_or("--label needs text")?,
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if out.fixture.as_os_str().is_empty() || out.queries.as_os_str().is_empty() {
        return Err("usage: vectorized_instrument --fixture <dataset.gqt> --queries <queries.txt> [--only <label>]... [--repeat N] [--warmup W] [--indices] [--label <text>]".into());
    }
    Ok(out)
}

/// `=== <label>` lines separate queries; the query name is read off `query <name>(`.
fn parse_queries(text: &str) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    let mut label: Option<String> = None;
    let mut body = String::new();
    let flush = |label: &Option<String>, body: &str, out: &mut Vec<(String, String, String)>| {
        if let Some(label) = label {
            let name = body
                .split("query ")
                .nth(1)
                .and_then(|rest| rest.split('(').next())
                .map(str::trim)
                .unwrap_or_default()
                .to_string();
            out.push((label.clone(), name, body.trim().to_string()));
        }
    };
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("=== ") {
            flush(&label, &body, &mut out);
            label = Some(rest.trim().to_string());
            body.clear();
        } else {
            body.push_str(line);
            body.push('\n');
        }
    }
    flush(&label, &body, &mut out);
    out
}

async fn run() -> Result<(), String> {
    let args = parse_args()?;
    let text = std::fs::read_to_string(&args.fixture).map_err(|e| format!("fixture: {e}"))?;
    let stem = args
        .fixture
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("fixture")
        .to_string();
    let case = parse_case(&stem, &text).map_err(|e| format!("fixture refused: {e}"))?;
    let fixture = case
        .fixture
        .as_ref()
        .ok_or("the fixture file needs a schema and a seed")?;
    let queries = std::fs::read_to_string(&args.queries).map_err(|e| format!("queries: {e}"))?;
    let queries = parse_queries(&queries);

    let dir = tempfile::tempdir().map_err(|e| format!("tempdir: {e}"))?;
    let uri = dir.path().to_str().ok_or("temp path is not utf-8")?.to_string();
    eprintln!("[instrument] loading {stem} into {uri}");
    let load_started = Instant::now();
    let db = Omnigraph::init(&uri, &fixture.schema)
        .await
        .map_err(|e| format!("init failed: {e}"))?;
    let session = case_session(db, &case, Engine::V2)?;
    seed_case(&session, &fixture.seed, case.needs_indices || args.indices).await?;
    eprintln!(
        "[instrument] loaded in {:.1}s",
        load_started.elapsed().as_secs_f64()
    );

    let params = ParamMap::default();
    for (label, name, source) in queries {
        if !args.only.is_empty() && !args.only.iter().any(|only| only == &label) {
            continue;
        }
        eprintln!("[instrument] {stem} {label}");
        let mut first: Option<serde_json::Value> = None;
        let mut samples: Vec<Sample> = Vec::new();
        let mut failed: Option<String> = None;
        for iteration in 0..(args.warmup + args.repeat) {
            let allocs0 = ALLOCS.load(Ordering::Relaxed);
            let bytes0 = BYTES.load(Ordering::Relaxed);
            let cpu0 = cpu_micros();
            let wall0 = Instant::now();
            let executed = session
                .query_inspected(ReadTarget::branch("main"), &source, &name, &params)
                .await;
            let wall_us = wall0.elapsed().as_micros() as u64;
            let cpu_us = cpu_micros().saturating_sub(cpu0);
            let allocs = ALLOCS.load(Ordering::Relaxed).saturating_sub(allocs0);
            let bytes = BYTES.load(Ordering::Relaxed).saturating_sub(bytes0);
            let executed = match executed {
                Ok(executed) => executed,
                Err(error) => {
                    failed = Some(error.to_string());
                    break;
                }
            };
            if iteration < args.warmup {
                continue;
            }
            samples.push(Sample {
                allocs,
                bytes,
                cpu_us,
                wall_us,
            });
            if first.is_none() {
                let result = &executed.result;
                let rows = result.num_rows();
                let batches = result.batches().len();
                let profile = executed
                    .profile()
                    .map_err(|e| format!("profile: {e}"))?
                    .to_rust_json()
                    .map_err(|e| format!("profile json: {e}"))?;
                let operators: Vec<serde_json::Value> = profile
                    .as_array()
                    .map(|rows| {
                        rows.iter()
                            .filter(|row| row["tree"] == "profile")
                            .map(|row| {
                                let detail: serde_json::Value = row["detail"]
                                    .as_str()
                                    .and_then(|d| serde_json::from_str(d).ok())
                                    .unwrap_or(serde_json::Value::Null);
                                serde_json::json!({
                                    "node": row["node"],
                                    "operator": detail["operator"],
                                    "status": detail["status"],
                                    "ran": detail["attempts"][0]["ran"],
                                    "rows": detail["attempts"][0]["actual_rows"],
                                    "batches": detail["attempts"][0]["output_batches"],
                                })
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                first = Some(serde_json::json!({
                    "rows": rows,
                    "batches": batches,
                    "operators": operators,
                    "physical": executed.explain.physical_plan.clone(),
                }));
            }
        }
        let mut record = serde_json::json!({
            "label": args.label,
            "fixture": stem,
            "query": label,
        });
        if let Some(error) = failed {
            record["error"] = serde_json::Value::String(error);
        } else {
            let mut allocs: Vec<u64> = samples.iter().map(|s| s.allocs).collect();
            let mut bytes: Vec<u64> = samples.iter().map(|s| s.bytes).collect();
            let mut cpu: Vec<u64> = samples.iter().map(|s| s.cpu_us).collect();
            let mut wall: Vec<u64> = samples.iter().map(|s| s.wall_us).collect();
            record["samples"] = serde_json::json!(samples.len());
            record["allocs"] = serde_json::json!({"median": median(&mut allocs), "min": allocs.first(), "max": allocs.last()});
            record["bytes"] = serde_json::json!({"median": median(&mut bytes), "min": bytes.first(), "max": bytes.last()});
            // The process CPU clock ticks at 10 ms on this host, so the mean
            // over the repeats is the usable number; the median is kept for
            // the spread.
            let cpu_total: u64 = cpu.iter().sum();
            record["cpu_us"] = serde_json::json!({"mean": cpu_total / samples.len().max(1) as u64, "median": median(&mut cpu), "min": cpu.first(), "max": cpu.last()});
            record["wall_us"] = serde_json::json!({"median": median(&mut wall), "min": wall.first(), "max": wall.last()});
            if let Some(first) = first {
                record["first"] = first;
            }
        }
        println!("{record}");
    }
    Ok(())
}

#[tokio::main]
async fn main() -> ExitCode {
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("vectorized_instrument: {error}");
            ExitCode::FAILURE
        }
    }
}
