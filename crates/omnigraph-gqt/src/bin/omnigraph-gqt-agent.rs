//! The agent instrument's language door (`crates/omnigraph-gqt/agent/README.md`).
//! The Python runner calls the agent; everything about the language, from
//! acceptance to rows on the seed, happens here.

#![recursion_limit = "512"]

use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use omnigraph_gqt::agent::{self, Arm, Expect, Instrument, Judge, card};
use serde_json::{Value, json};

const USAGE: &str = "usage: omnigraph-gqt-agent [--root <agent dir>] <command>
commands:
  tasks                    every task, one JSON object per line
  seeds --arm <gq3|where>  the seeded answers of an arm
  judge --arm <gq3|where>  judge {\"id\", \"task\", \"text\"} lines from stdin
  cards --check|--write    the card digests in manifest.json
  record --check|--write   rows from the first edition-3 truth, for tasks with no edition-2 rows
  truths                   every edition-3 truth run against the expected rows, one JSON line each
  variant                  every edition-3 truth converted to the variant, with its IR comparison";

struct Args {
    root: PathBuf,
    command: String,
    arm: Option<Arm>,
    write: Option<bool>,
}

fn parse() -> Result<Args, String> {
    let mut root = Instrument::default_root();
    let mut command = None;
    let mut arm = None;
    let mut write = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = PathBuf::from(args.next().ok_or("--root needs a directory")?),
            "--arm" => arm = Some(Arm::parse(&args.next().ok_or("--arm needs gq3 or where")?)?),
            "--check" => write = Some(false),
            "--write" => write = Some(true),
            "-h" | "--help" => return Err(USAGE.into()),
            other if command.is_none() && !other.starts_with('-') => {
                command = Some(other.to_string())
            }
            other => return Err(format!("unexpected argument `{other}`\n{USAGE}")),
        }
    }
    Ok(Args {
        root,
        command: command.ok_or(USAGE)?,
        arm,
        write,
    })
}

fn print(value: &Value) {
    let mut out = std::io::stdout().lock();
    writeln!(out, "{value}").expect("stdout");
    out.flush().expect("stdout");
}

/// Whether the first edition-3 truth's entry read orders its rows.
fn truth_orders(truth: &str) -> Result<bool, String> {
    use omnigraph_compiler::query::syntax::DeclBody;
    let decl = agent::entry_declaration(truth)?;
    Ok(matches!(&decl.syntax.decl().body, DeclBody::Read(r) if !r.order.is_empty()))
}

async fn run(args: Args) -> Result<(), String> {
    match args.command.as_str() {
        "tasks" => {
            let instrument = Instrument::load(&args.root)?;
            for task in &instrument.tasks {
                print(&json!({
                    "id": task.id,
                    "set": task.set,
                    "dataset": task.dataset,
                    "kind": task.kind,
                    "answer": task.answer.as_str(),
                    "composition": task.composition,
                    "intent": task.intent,
                    "signature": task.signature()?,
                    "write": task.is_write(),
                    "schema": instrument.datasets[&task.dataset].schema,
                    "edition2_queries": task.edition2.len(),
                    "edition3_truths": task.edition3.len(),
                    "expect_from_edition3": task.expect.as_ref().is_some_and(|e| e.from_edition3),
                }));
            }
            Ok(())
        }
        "seeds" => {
            let arm = args.arm.ok_or("seeds needs --arm")?;
            let instrument = Instrument::load(&args.root)?;
            for seed in instrument.seeds.iter().filter(|s| s.arm == arm.name()) {
                print(&json!({
                    "id": seed.id,
                    "task": seed.task,
                    "class": seed.class,
                    "code": seed.code,
                    "text": seed.text,
                }));
            }
            Ok(())
        }
        "judge" => {
            let arm = args.arm.ok_or("judge needs --arm")?;
            let instrument = Instrument::load(&args.root)?;
            let mut judge = Judge::new(&instrument);
            for line in std::io::stdin().lock().lines() {
                let line = line.map_err(|e| e.to_string())?;
                if line.trim().is_empty() {
                    continue;
                }
                let job: Value = serde_json::from_str(&line).map_err(|e| format!("job: {e}"))?;
                let (Some(task), Some(text)) = (job["task"].as_str(), job["text"].as_str()) else {
                    return Err(format!("a job needs `task` and `text`: {line}"));
                };
                let mut verdict = judge.judge(task, arm, text).await;
                verdict["id"] = job["id"].clone();
                print(&verdict);
            }
            Ok(())
        }
        "cards" => {
            let write = args.write.ok_or("cards needs --check or --write")?;
            let mut manifest = card::Manifest::read(&args.root)?;
            if write {
                for (arm, digest) in &mut manifest.cards {
                    let bytes = std::fs::read(card::card_path(&args.root, *arm))
                        .map_err(|e| e.to_string())?;
                    *digest = card::sha256(&bytes);
                }
                std::fs::write(args.root.join(card::MANIFEST), manifest.render())
                    .map_err(|e| e.to_string())?;
                return Ok(());
            }
            let problems = manifest.verify(&args.root);
            if problems.is_empty() {
                Ok(())
            } else {
                Err(problems.join("\n"))
            }
        }
        "record" => {
            let write = args.write.ok_or("record needs --check or --write")?;
            let instrument = Instrument::load(&args.root)?;
            let mut stale = Vec::new();
            for task in instrument.tasks.iter().filter(|t| t.edition2.is_empty()) {
                if task.expect.as_ref().is_some_and(|e| e.pinned.is_none()) {
                    continue;
                }
                let ordered = match &task.expect {
                    Some(e) => e.ordered,
                    None => truth_orders(&task.edition3[0])?,
                };
                let rows = match agent::run_truth(&instrument, task, &task.edition3[0]).await {
                    Ok(rows) => rows,
                    Err(e) => {
                        // Recorded nowhere: the task has no expected rows on
                        // this engine, and the judge says so.
                        print(&json!({"task": task.id, "unrecorded": e.to_string()}));
                        continue;
                    }
                };
                if let Some(Expect {
                    pinned: Some(pinned),
                    ..
                }) = &task.expect
                    && rows.matches(pinned, ordered)
                {
                    continue;
                }
                stale.push(task.id.clone());
                if write {
                    let header = format!(
                        "--- expect {} edition3\n",
                        if ordered { "ordered" } else { "unordered" }
                    );
                    write_expect(&task.path, &header, &rows.recorded(ordered).render())?;
                }
            }
            if stale.is_empty() || write {
                for id in &stale {
                    eprintln!("recorded {id}");
                }
                Ok(())
            } else {
                Err(format!("recorded rows differ for: {}", stale.join(", ")))
            }
        }
        "truths" => {
            let instrument = Instrument::load(&args.root)?;
            for task in &instrument.tasks {
                let expected = agent::expected_rows(&instrument, task).await;
                for (index, truth) in task.edition3.iter().enumerate() {
                    let actual = agent::run_truth(&instrument, task, truth).await;
                    let (status, detail) = match (&expected, &actual) {
                        (Err(e), _) => ("no_expected", json!(e)),
                        (_, Err(e)) => (
                            if e.is_planner_gap() {
                                "planner_gap"
                            } else {
                                "error"
                            },
                            json!(e.to_string()),
                        ),
                        (Ok(want), Ok(got)) => {
                            if got.same_values(want, task.ordered()) {
                                ("equal", Value::Null)
                            } else {
                                (
                                    "differ",
                                    json!({"expected": want.to_json(), "actual": got.to_json()}),
                                )
                            }
                        }
                    };
                    print(&json!({
                        "task": task.id,
                        "truth": index + 1,
                        "expect_from_edition3": task.expect.as_ref().is_some_and(|e| e.from_edition3),
                        "status": status,
                        "detail": detail,
                    }));
                }
            }
            Ok(())
        }
        "variant" => {
            let instrument = Instrument::load(&args.root)?;
            for task in &instrument.tasks {
                let catalog = agent::catalog(&instrument.datasets[&task.dataset])?;
                for (index, truth) in task.edition3.iter().enumerate() {
                    let report = agent::variant::lowering_report(&catalog, truth);
                    print(&json!({"task": task.id, "truth": index + 1, "report": report}));
                }
            }
            Ok(())
        }
        other => Err(format!("unknown command `{other}`\n{USAGE}")),
    }
}

/// Replace the `--- expect` section (header and body), or append one.
fn write_expect(path: &std::path::Path, header: &str, body: &str) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let mut out = String::new();
    let mut skipping = false;
    let mut replaced = false;
    for line in text.split_inclusive('\n') {
        if line.starts_with("--- ") {
            if skipping {
                out.push('\n');
            }
            skipping = line.starts_with("--- expect");
            if skipping {
                out.push_str(header);
                out.push_str(body);
                replaced = true;
                continue;
            }
            out.push_str(line);
            continue;
        }
        if !skipping {
            out.push_str(line);
        }
    }
    if !replaced {
        if !out.ends_with('\n') {
            out.push('\n');
        }
        out.push('\n');
        out.push_str(header);
        out.push_str(body);
    }
    std::fs::write(path, out).map_err(|e| e.to_string())
}

fn main() -> ExitCode {
    let args = match parse() {
        Ok(args) => args,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        // Unoptimized engine futures nest deeper than a default 2 MiB stack.
        .thread_stack_size(16 << 20)
        .build()
        .expect("tokio runtime");
    match runtime.block_on(run(args)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    }
}
