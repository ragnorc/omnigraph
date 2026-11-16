#![recursion_limit = "512"]
#![allow(dead_code)]
//! Research driver (`research/key-lookup-stress`, cells 3 and 4): keyed
//! inserts against the length of the unindexed tail, with requests and bytes
//! per insert. Self-contained so the same file builds on `main`, on the
//! group-commit prototype and on the key-lookup branch.
//!
//! Environment:
//!
//! | variable | default | meaning |
//! |---|---|---|
//! | `KLC_URI` | required | graph root (a local directory or `s3://..`) |
//! | `KLC_MODE` | `measure` | `build` (init, seed, index, then grow the tail with checkpoints) or `measure` (open and measure) |
//! | `KLC_KIND` | `item` | `item` (`@key`), `unique` (`@key` + `@unique email`), `edge` (`@card(0..5)` edges between seeded persons) |
//! | `KLC_SEED_ROWS` | `10000` | rows (persons for `edge`) loaded and indexed before the tail |
//! | `KLC_ROWS_PER_WRITE` | `10` | rows per insert (one fragment each) |
//! | `KLC_CHECKPOINTS` | `1000,10000,50000,200000` | tail lengths (rows) at which `build` copies the root and measures |
//! | `KLC_COPY_TO` | unset | `build`: copy the root to `<dir>/tail-<N>` at each checkpoint (local roots only) |
//! | `KLC_MEASURE_WRITES` | `100` | writes per measurement |
//! | `KLC_MEASURE_SECS` | `30` | cap per measurement |
//! | `KLC_WRITERS` | `1` | concurrent writers per measurement |
//! | `KLC_LABEL` | `unlabeled` | variant name |
//! | `KLC_OUT` | unset | append one JSON line per measurement |
//! | `KLC_PREFIX` | `m` | key prefix of measured writes (keeps runs on one root disjoint) |

use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use async_trait::async_trait;
use futures::StreamExt;
use futures::stream::BoxStream;
use lance::io::WrappingObjectStore;
use object_store::path::Path;
use object_store::{
    CopyOptions, GetOptions, GetResult, ListResult, MultipartUpload, ObjectMeta, ObjectStore,
    PutMultipartOptions, PutOptions, PutPayload, PutResult, Result as OSResult,
};

use omnigraph::Session;
use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph::instrumentation::{ProbedStores, QueryIoProbes, with_query_io_probes};
use omnigraph::loader::LoadMode;
use omnigraph::settings::SessionSettings;

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}
fn env_num(name: &str, default: u64) -> u64 {
    env_or(name, &default.to_string()).parse().unwrap()
}

#[derive(Default, Debug)]
struct Counts {
    get: AtomicU64,
    get_range: AtomicU64,
    head: AtomicU64,
    put: AtomicU64,
    list: AtomicU64,
    delete: AtomicU64,
}

#[derive(Clone, Default, Debug)]
struct RequestCounter(Arc<Counts>);

impl WrappingObjectStore for RequestCounter {
    fn wrap_paginated(
        &self,
        _: &str,
        _original: Arc<dyn object_store::list::PaginatedListStore>,
    ) -> Option<Arc<dyn object_store::list::PaginatedListStore>> {
        None
    }
    fn wrap(&self, _: &str, target: Arc<dyn ObjectStore>) -> Arc<dyn ObjectStore> {
        Arc::new(CountingStore {
            target,
            counts: Arc::clone(&self.0),
        })
    }
}

#[derive(Debug)]
struct CountingStore {
    target: Arc<dyn ObjectStore>,
    counts: Arc<Counts>,
}

impl fmt::Display for CountingStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "CountingStore({})", self.target)
    }
}

#[async_trait]
impl ObjectStore for CountingStore {
    async fn put_opts(&self, l: &Path, p: PutPayload, o: PutOptions) -> OSResult<PutResult> {
        self.counts.put.fetch_add(1, Ordering::Relaxed);
        self.target.put_opts(l, p, o).await
    }
    async fn put_multipart_opts(
        &self,
        l: &Path,
        o: PutMultipartOptions,
    ) -> OSResult<Box<dyn MultipartUpload>> {
        self.counts.put.fetch_add(1, Ordering::Relaxed);
        self.target.put_multipart_opts(l, o).await
    }
    async fn get_opts(&self, l: &Path, o: GetOptions) -> OSResult<GetResult> {
        if o.head {
            self.counts.head.fetch_add(1, Ordering::Relaxed);
        } else if o.range.is_some() {
            self.counts.get_range.fetch_add(1, Ordering::Relaxed);
        } else {
            self.counts.get.fetch_add(1, Ordering::Relaxed);
        }
        self.target.get_opts(l, o).await
    }
    fn delete_stream(
        &self,
        locations: BoxStream<'static, OSResult<Path>>,
    ) -> BoxStream<'static, OSResult<Path>> {
        let counts = Arc::clone(&self.counts);
        self.target.delete_stream(
            locations
                .inspect(move |_| {
                    counts.delete.fetch_add(1, Ordering::Relaxed);
                })
                .boxed(),
        )
    }
    fn list(&self, p: Option<&Path>) -> BoxStream<'static, OSResult<ObjectMeta>> {
        self.counts.list.fetch_add(1, Ordering::Relaxed);
        self.target.list(p)
    }
    fn list_with_offset(
        &self,
        p: Option<&Path>,
        o: &Path,
    ) -> BoxStream<'static, OSResult<ObjectMeta>> {
        self.counts.list.fetch_add(1, Ordering::Relaxed);
        self.target.list_with_offset(p, o)
    }
    async fn list_with_delimiter(&self, p: Option<&Path>) -> OSResult<ListResult> {
        self.counts.list.fetch_add(1, Ordering::Relaxed);
        self.target.list_with_delimiter(p).await
    }
    async fn copy_opts(&self, f: &Path, t: &Path, o: CopyOptions) -> OSResult<()> {
        self.target.copy_opts(f, t, o).await
    }
}

#[derive(Clone, Copy, Default, Debug)]
struct Snap {
    get: u64,
    range: u64,
    head: u64,
    put: u64,
    list: u64,
    read_iops: u64,
    read_bytes: u64,
    write_iops: u64,
    write_bytes: u64,
}

struct Meter {
    table: RequestCounter,
    manifest: RequestCounter,
    probes: QueryIoProbes,
    io: Mutex<(u64, u64, u64, u64)>,
}

impl Meter {
    fn new() -> Self {
        let table = RequestCounter::default();
        let manifest = RequestCounter::default();
        let probes = QueryIoProbes {
            manifest_wrapper: Some(Arc::new(manifest.clone())),
            history_wrapper: Some(Arc::new(manifest.clone())),
            table_wrapper: Some(Arc::new(table.clone())),
            ..Default::default()
        };
        Self {
            table,
            manifest,
            probes,
            io: Mutex::new((0, 0, 0, 0)),
        }
    }
    fn snap(&self) -> Snap {
        let mut io = self.io.lock().unwrap();
        let mut seen = Vec::<Arc<lance::io::ObjectStore>>::new();
        for plane in [
            &self.probes.manifest_stores,
            &self.probes.history_stores,
            &self.probes.table_stores,
        ] {
            for store in ProbedStores::stores(plane) {
                if seen.iter().any(|known| Arc::ptr_eq(known, &store)) {
                    continue;
                }
                let stats = store.io_stats_incremental();
                io.0 += stats.read_iops;
                io.1 += stats.read_bytes;
                io.2 += stats.write_iops;
                io.3 += stats.written_bytes;
                seen.push(store);
            }
        }
        let l = |a: &AtomicU64| a.load(Ordering::Relaxed);
        let (t, m) = (&self.table.0, &self.manifest.0);
        Snap {
            get: l(&t.get) + l(&m.get),
            range: l(&t.get_range) + l(&m.get_range),
            head: l(&t.head) + l(&m.head),
            put: l(&t.put) + l(&m.put),
            list: l(&t.list) + l(&m.list),
            read_iops: io.0,
            read_bytes: io.1,
            write_iops: io.2,
            write_bytes: io.3,
        }
    }
}

fn schema(kind: &str) -> &'static str {
    match kind {
        "item" => "node Item {\n    slug: String @key\n    n: I64\n}\n",
        "unique" => "node Item {\n    slug: String @key\n    email: String @unique\n    n: I64\n}\n",
        "edge" => "node P {\n    name: String @key\n}\nedge F: P -> P @card(0..5)\n",
        other => panic!("KLC_KIND {other}"),
    }
}

/// One insert statement for row `i` of a write with key prefix `prefix`.
fn statement(kind: &str, prefix: &str, i: u64, seed_rows: u64) -> String {
    match kind {
        "item" => format!("    insert Item {{ slug: \"{prefix}{i}\", n: {i} }}\n"),
        "unique" => format!(
            "    insert Item {{ slug: \"{prefix}{i}\", email: \"{prefix}e{i}\", n: {i} }}\n"
        ),
        // Edge i goes from person (i mod seed) to person ((i * 7 + 1) mod
        // seed): every person gets at most ceil(tail / seed) edges, within
        // @card(0..5) for tail <= 4 * seed.
        "edge" => format!(
            "    insert F {{ from: \"p{}\", to: \"p{}\" }}\n",
            i % seed_rows,
            (i * 7 + 1) % seed_rows
        ),
        _ => unreachable!(),
    }
}

fn seed_jsonl(kind: &str, rows: u64) -> String {
    let mut out = String::new();
    for i in 0..rows {
        match kind {
            "item" => out.push_str(&format!(
                "{{\"type\":\"Item\",\"data\":{{\"slug\":\"s{i}\",\"n\":{i}}}}}\n"
            )),
            "unique" => out.push_str(&format!(
                "{{\"type\":\"Item\",\"data\":{{\"slug\":\"s{i}\",\"email\":\"se{i}\",\"n\":{i}}}}}\n"
            )),
            "edge" => out.push_str(&format!("{{\"type\":\"P\",\"data\":{{\"name\":\"p{i}\"}}}}\n")),
            _ => unreachable!(),
        }
    }
    out
}

async fn write_once(db: &Session, kind: &str, prefix: &str, first: u64, rows: u64, seed: u64) {
    let mut body = String::new();
    for i in first..first + rows {
        body.push_str(&statement(kind, prefix, i, seed));
    }
    let source = format!("query q() {{\n{body}}}");
    db.mutate("main", &source, "q", &Default::default())
        .await
        .unwrap_or_else(|e| panic!("write {prefix}{first}: {e}"));
}

struct Measured {
    writes: u64,
    secs: f64,
    lat_us: Vec<u64>,
    delta: Snap,
}

/// `writers` closed-loop writers, `max_writes` writes in all or `secs`.
async fn measure(
    db: &Arc<Session>,
    meter: &Meter,
    kind: &str,
    prefix: &str,
    start_key: u64,
    rows: u64,
    seed: u64,
    writers: u64,
    max_writes: u64,
    secs: u64,
) -> Measured {
    let next = Arc::new(AtomicU64::new(0));
    let lat = Arc::new(Mutex::new(Vec::new()));
    let before = meter.snap();
    let started = Instant::now();
    let deadline = started + Duration::from_secs(secs);
    let mut tasks = Vec::new();
    for _ in 0..writers {
        let (db, next, lat) = (Arc::clone(db), Arc::clone(&next), Arc::clone(&lat));
        let (kind, prefix) = (kind.to_string(), prefix.to_string());
        tasks.push(tokio::spawn(async move {
            loop {
                if Instant::now() >= deadline {
                    break;
                }
                let n = next.fetch_add(1, Ordering::Relaxed);
                if n >= max_writes {
                    break;
                }
                let t = Instant::now();
                write_once(&db, &kind, &prefix, start_key + n * rows, rows, seed).await;
                lat.lock().unwrap().push(t.elapsed().as_micros() as u64);
            }
        }));
    }
    for task in tasks {
        task.await.unwrap();
    }
    let secs = started.elapsed().as_secs_f64();
    let after = meter.snap();
    let lat_us = std::mem::take(&mut *lat.lock().unwrap());
    let delta = Snap {
        get: after.get - before.get,
        range: after.range - before.range,
        head: after.head - before.head,
        put: after.put - before.put,
        list: after.list - before.list,
        read_iops: after.read_iops - before.read_iops,
        read_bytes: after.read_bytes - before.read_bytes,
        write_iops: after.write_iops - before.write_iops,
        write_bytes: after.write_bytes - before.write_bytes,
    };
    Measured {
        writes: lat_us.len() as u64,
        secs,
        lat_us,
        delta,
    }
}

fn pct(sorted: &[u64], q: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    sorted[((q * sorted.len() as f64).ceil() as usize).clamp(1, sorted.len()) - 1]
}

async fn table_shape(db: &Session, table: &str) -> (usize, u64) {
    let snapshot = db.snapshot_of(ReadTarget::branch("main")).await.unwrap();
    let opened = snapshot.open_dataset(table).await.unwrap();
    let rows = opened.count_rows(None).await.unwrap() as u64;
    let version = opened.published_dataset_version();
    let entry = snapshot.dataset(table).unwrap();
    let uri = format!("{}/{}", db.uri().trim_end_matches('/'), entry.dataset_path);
    let fragments = match lance::Dataset::open(&uri).await {
        Ok(ds) => match ds.checkout_version(version).await {
            Ok(ds) => ds.fragments().len(),
            Err(_) => 0,
        },
        Err(_) => 0,
    };
    (fragments, rows)
}

fn report(label: &str, phase: &str, tail_rows: u64, shape: (usize, u64), m: &Measured) {
    let mut lat = m.lat_us.clone();
    lat.sort_unstable();
    let per = m.writes.max(1) as f64;
    let d = m.delta;
    let line = serde_json::json!({
        "label": label,
        "kind": env_or("KLC_KIND", "item"),
        "phase": phase,
        "tail_rows": tail_rows,
        "fragments": shape.0,
        "table_rows": shape.1,
        "rows_per_write": env_num("KLC_ROWS_PER_WRITE", 10),
        "writers": env_num("KLC_WRITERS", 1),
        "writes": m.writes,
        "secs": m.secs,
        "writes_per_s": m.writes as f64 / m.secs.max(1e-9),
        "p50_ms": pct(&lat, 0.5) as f64 / 1000.0,
        "p99_ms": pct(&lat, 0.99) as f64 / 1000.0,
        "requests_per_write": (d.get + d.range + d.head + d.put + d.list) as f64 / per,
        "gets_per_write": (d.get + d.range) as f64 / per,
        "heads_per_write": d.head as f64 / per,
        "puts_per_write": d.put as f64 / per,
        "lists_per_write": d.list as f64 / per,
        "store_read_iops_per_write": d.read_iops as f64 / per,
        "store_read_bytes_per_write": d.read_bytes as f64 / per,
        "store_write_bytes_per_write": d.write_bytes as f64 / per,
    });
    println!("[klc] {line}");
    if let Ok(out) = std::env::var("KLC_OUT") {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(out)
            .unwrap();
        writeln!(f, "{line}").unwrap();
    }
}

fn copy_dir(from: &std::path::Path, to: &std::path::Path) {
    let status = std::process::Command::new("cp")
        .arg("-a")
        .arg(from)
        .arg(to)
        .status()
        .unwrap();
    assert!(status.success());
}

#[test]
#[ignore = "instrument: research driver, configured by KLC_* (module doc)"]
fn key_lookup_cliff() {
    let workers = env_num("KLC_TOKIO_WORKERS", 8) as usize;
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers)
        .thread_stack_size(16 << 20)
        .enable_all()
        .build()
        .unwrap()
        .block_on(Box::pin(run()));
}

async fn run() {
    let uri = std::env::var("KLC_URI").expect("KLC_URI");
    let mode = env_or("KLC_MODE", "measure");
    let kind = env_or("KLC_KIND", "item");
    let label = env_or("KLC_LABEL", "unlabeled");
    let seed = env_num("KLC_SEED_ROWS", 10_000);
    let rows = env_num("KLC_ROWS_PER_WRITE", 10);
    let writers = env_num("KLC_WRITERS", 1);
    let max_writes = env_num("KLC_MEASURE_WRITES", 100);
    let secs = env_num("KLC_MEASURE_SECS", 30);
    let table = if kind == "edge" { "edge:F" } else { "node:Item" };
    let meter = Arc::new(Meter::new());
    let probes = meter.probes.clone();
    let meter2 = Arc::clone(&meter);
    with_query_io_probes(probes, Box::pin(async move {
        let meter = meter2;
        match mode.as_str() {
            "build" => {
                let db = Omnigraph::init(&uri, schema(&kind)).await.unwrap();
                let db = Arc::new(Session::from_defaults(Arc::new(db), SessionSettings::default()));
                db.load_jsonl(&seed_jsonl(&kind, seed), LoadMode::Overwrite)
                    .await
                    .unwrap();
                if kind == "edge" {
                    // One indexed edge per person pair so the edge table exists.
                    let mut body = String::new();
                    for i in 0..8u64 {
                        body.push_str(&format!(
                            "    insert F {{ from: \"p{}\", to: \"p{}\" }}\n",
                            seed - 1 - i,
                            i
                        ));
                    }
                    db.mutate("main", &format!("query q() {{\n{body}}}"), "q", &Default::default())
                        .await
                        .unwrap();
                }
                db.ensure_indices_as(None).await.unwrap();
                let checkpoints: Vec<u64> = env_or("KLC_CHECKPOINTS", "1000,10000,50000,200000")
                    .split(',')
                    .map(|c| c.trim().parse().unwrap())
                    .collect();
                let mut written = 0u64;
                let build_started = Instant::now();
                for checkpoint in checkpoints {
                    let t = Instant::now();
                    let before = written;
                    while written < checkpoint {
                        // The tail grows by single writes, one fragment each.
                        write_once(&db, &kind, "t", written, rows, seed).await;
                        written += rows;
                    }
                    let built = (written - before) / rows;
                    eprintln!(
                        "[klc] {label} built tail {written} rows ({built} writes in {:.1}s, {:.1} writes/s; total {:.0}s)",
                        t.elapsed().as_secs_f64(),
                        built as f64 / t.elapsed().as_secs_f64().max(1e-9),
                        build_started.elapsed().as_secs_f64()
                    );
                    let shape = table_shape(&db, table).await;
                    if let Ok(dir) = std::env::var("KLC_COPY_TO") {
                        let to = std::path::Path::new(&dir).join(format!("tail-{checkpoint}"));
                        let _ = std::fs::remove_dir_all(&to);
                        copy_dir(std::path::Path::new(&uri), &to);
                    }
                    let m = measure(
                        &db,
                        &meter,
                        &kind,
                        &format!("w{checkpoint}-"),
                        written,
                        rows,
                        seed,
                        writers,
                        max_writes,
                        secs,
                    )
                    .await;
                    report(&label, "warm", checkpoint, shape, &m);
                    // The measured writes are part of the tail now.
                    written += m.writes * rows;
                }
            }
            "measure" => {
                let db = Omnigraph::open(&uri).await.unwrap();
                let db = Arc::new(Session::from_defaults(Arc::new(db), SessionSettings::default()));
                let shape = table_shape(&db, table).await;
                let tail = env_num("KLC_TAIL_ROWS", 0);
                let prefix = env_or("KLC_PREFIX", "m");
                let start = env_num("KLC_START", tail);
                let m = measure(
                    &db, &meter, &kind, &prefix, start, rows, seed, writers, max_writes, secs,
                )
                .await;
                report(&label, &env_or("KLC_PHASE", "cold"), tail, shape, &m);
            }
            other => panic!("KLC_MODE {other}"),
        }
    }))
    .await;
}
