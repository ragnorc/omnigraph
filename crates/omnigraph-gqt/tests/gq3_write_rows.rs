#![recursion_limit = "512"]
//! SPIKE (`spike/gq3-rewriter`): the two write-statement rows of the GQ 3
//! rewriter select the same rows as the edition-2 `where`.
//!
//! For every `.gqt` case under `cases/` with a schema and seed, the case's
//! main-branch `--- mutate` steps are replayed in order on its fixture. Before
//! each single-statement `delete T where φ` or `update T set { … } where φ`,
//! the edition-2 read of the rewrite's clauses, `match { $x: T  φ′ } return
//! { $x.@id }` (an edge target bound through its traversal), runs on main;
//! then the statement runs on a fresh branch from main, and its effect is
//! compared with that selection: the ids a delete removes, the entities an
//! update reports as affected and the rows it changes. Results go to
//! `target/spike-gq3-rewriter/write_rows.tsv`; any disagreement fails.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph_compiler::catalog::build_catalog;
use omnigraph_compiler::migrate::{Row, write_selections};
use omnigraph_compiler::query::ast::FileBody;
use omnigraph_compiler::query::parser::parse_query_diagnostic;
use omnigraph_compiler::query_input::{JsonParamMode, json_params_to_param_map};
use omnigraph_compiler::schema::parser::parse_schema;
use omnigraph_compiler::settings::Engine;
use omnigraph_gqt_core::{case_session, parse_case, seed_case};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("cases")
}

fn collect(dir: &Path, out: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(dir).unwrap().flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect(&path, out);
        } else if path.extension().is_some_and(|e| e == "gqt") {
            out.push(path);
        }
    }
}

struct Step {
    kind: String,
    header: String,
    body: String,
    params: Option<String>,
}

fn steps(text: &str) -> Vec<Step> {
    let mut sections: Vec<(String, String)> = Vec::new();
    for line in text.split_inclusive('\n') {
        if let Some(h) = line.strip_prefix("--- ") {
            sections.push((h.trim_end().to_string(), String::new()));
        } else if let Some((_, b)) = sections.last_mut() {
            b.push_str(line);
        }
    }
    let mut out: Vec<Step> = Vec::new();
    for (header, body) in sections {
        let kind = header.split_whitespace().next().unwrap_or("").to_string();
        if kind == "params" {
            if let Some(last) = out.last_mut()
                && last.params.is_none()
            {
                last.params = Some(body);
            }
            continue;
        }
        out.push(Step {
            kind,
            header,
            body,
            params: None,
        });
    }
    out
}

fn ids(result: &omnigraph_compiler::QueryResult) -> Result<BTreeSet<String>, String> {
    let rows = result.to_rust_json().map_err(|e| e.to_string())?;
    Ok(rows
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|r| r.get("id").map(|v| v.to_string()))
        .collect())
}

fn rows_by_id(result: &omnigraph_compiler::QueryResult) -> Result<Vec<(String, String)>, String> {
    let rows = result.to_rust_json().map_err(|e| e.to_string())?;
    Ok(rows
        .as_array()
        .into_iter()
        .flatten()
        .map(|r| {
            (
                r.get("id").map(|v| v.to_string()).unwrap_or_default(),
                r.to_string(),
            )
        })
        .collect())
}

#[derive(Default)]
struct Tally {
    statements: usize,
    agree_nonempty: usize,
    agree_empty: usize,
    disagree: Vec<String>,
    write_refused: usize,
    read_refused: Vec<String>,
    skipped: usize,
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn write_rows_select_what_the_edition_2_where_selects() {
    let mut files = Vec::new();
    collect(&root(), &mut files);
    files.sort();
    let mut tally = Tally::default();
    let mut tsv = vec!["case\tstatement\toutcome\tdetail".to_string()];
    let mut branch_seq = 0usize;
    for file in files {
        let text = std::fs::read_to_string(&file).unwrap();
        let stem = file.file_stem().unwrap().to_string_lossy().to_string();
        let name = file.strip_prefix(root()).unwrap().display().to_string();
        let all = steps(&text);
        let has_write = all.iter().any(|s| {
            s.kind == "mutate" && (s.body.contains("delete ") || s.body.contains("update "))
        });
        if !has_write {
            continue;
        }
        let Ok(case) = parse_case(&stem, &text) else {
            continue;
        };
        let Some(fixture) = case.fixture.as_ref() else {
            continue;
        };
        let Some(catalog) = parse_schema(&fixture.schema)
            .ok()
            .and_then(|s| build_catalog(&s).ok())
        else {
            continue;
        };
        let dir = tempfile::tempdir().unwrap();
        let uri = dir.path().to_str().unwrap().to_string();
        let Ok(db) = Omnigraph::init(&uri, &fixture.schema).await else {
            continue;
        };
        let Ok(session) = case_session(db, &case, Engine::V2) else {
            continue;
        };
        if seed_case(&session, &fixture.seed, case.needs_indices)
            .await
            .is_err()
        {
            continue;
        }
        for (i, step) in all.iter().enumerate() {
            let on_main = step.header == "mutate" || step.header.trim() == "mutate branch: main";
            match step.kind.as_str() {
                "mutate" if on_main => {}
                "query" | "expect" | "bind" | "restart" | "schema" | "seed" | "runner" | "csv"
                | "seam" => continue,
                "mutate" => continue, // another branch; main is unchanged
                _ => break,           // load, loop, concurrent: stop replaying this case
            }
            let source = step.body.as_str();
            if source.contains("${") || step.params.as_deref().is_some_and(|p| p.contains("${")) {
                break;
            }
            let Ok(file_ast) = parse_query_diagnostic(source) else {
                continue;
            };
            let FileBody::Queries(decls) = &file_ast.body else {
                break; // a control write may move main
            };
            let Some(decl) = decls.first() else { continue };
            let json = match step
                .params
                .as_deref()
                .map(str::trim)
                .filter(|p| !p.is_empty())
            {
                Some(p) => match serde_json::from_str::<serde_json::Value>(p) {
                    Ok(v) => Some(v),
                    Err(_) => break,
                },
                None => None,
            };
            let Ok(params) =
                json_params_to_param_map(json.as_ref(), &decl.params, JsonParamMode::Standard)
            else {
                // An expect-error parameter case: the write refuses before selection.
                continue;
            };
            for sel in write_selections(source, &catalog) {
                tally.statements += 1;
                let label = format!("{name}#{i}:{}", sel.decl);
                let selected = session
                    .query(
                        ReadTarget::branch("main"),
                        &sel.selection_read,
                        "__gq3_selection",
                        &params,
                    )
                    .await;
                branch_seq += 1;
                let branch = format!("gq3chk{branch_seq}");
                if session
                    .db()
                    .branch_create_from(ReadTarget::branch("main"), &branch)
                    .await
                    .is_err()
                {
                    tally.skipped += 1;
                    continue;
                }
                let before_rows = session
                    .query(
                        ReadTarget::branch(branch.as_str()),
                        &sel.all_read,
                        "__gq3_all",
                        &params,
                    )
                    .await;
                let written = session.mutate(&branch, source, &decl.name, &params).await;
                let after_rows = session
                    .query(
                        ReadTarget::branch(branch.as_str()),
                        &sel.all_read,
                        "__gq3_all",
                        &params,
                    )
                    .await;
                let selected = match selected {
                    Ok(r) => ids(&r),
                    Err(e) => Err(e.to_string()),
                };
                match (&selected, &written) {
                    (Err(read_err), Err(_)) => {
                        tally.write_refused += 1;
                        tsv.push(format!("{label}\t{:?}\tboth-refused\t{read_err}", sel.row));
                    }
                    (Err(read_err), Ok(_)) => {
                        tally
                            .read_refused
                            .push(format!("{label}: {read_err} | {}", sel.selection_read));
                        tsv.push(format!("{label}\t{:?}\tread-refused\t{read_err}", sel.row));
                    }
                    (Ok(_), Err(e)) => {
                        tally.write_refused += 1;
                        tsv.push(format!(
                            "{label}\t{:?}\twrite-refused\t{}",
                            sel.row,
                            e.to_string().replace(['\n', '\t'], " ")
                        ));
                    }
                    (Ok(selected), Ok(result)) => {
                        let verdict = match sel.row {
                            Row::Delete => {
                                let (Ok(before), Ok(after)) = (&before_rows, &after_rows) else {
                                    tally.skipped += 1;
                                    continue;
                                };
                                let (before, after) = (ids(before).unwrap(), ids(after).unwrap());
                                let removed: BTreeSet<String> =
                                    before.difference(&after).cloned().collect();
                                (removed == *selected).then_some(()).ok_or_else(|| {
                                    format!("removed {removed:?}, selected {selected:?}")
                                })
                            }
                            _ => {
                                let affected = result.affected_nodes + result.affected_edges;
                                let changed: BTreeSet<String> = match (&before_rows, &after_rows) {
                                    (Ok(b), Ok(a)) => {
                                        let b: BTreeSet<_> =
                                            rows_by_id(b).unwrap().into_iter().collect();
                                        let a: BTreeSet<_> =
                                            rows_by_id(a).unwrap().into_iter().collect();
                                        b.symmetric_difference(&a)
                                            .map(|(id, _)| id.clone())
                                            .collect()
                                    }
                                    _ => BTreeSet::new(),
                                };
                                if affected == selected.len() && changed.is_subset(selected) {
                                    Ok(())
                                } else {
                                    Err(format!(
                                        "affected {affected}, changed {changed:?}, selected {selected:?}"
                                    ))
                                }
                            }
                        };
                        match verdict {
                            Ok(()) if selected.is_empty() => {
                                tally.agree_empty += 1;
                                tsv.push(format!("{label}\t{:?}\tagree-empty\t", sel.row));
                            }
                            Ok(()) => {
                                tally.agree_nonempty += 1;
                                tsv.push(format!(
                                    "{label}\t{:?}\tagree\t{} rows",
                                    sel.row,
                                    selected.len()
                                ));
                            }
                            Err(detail) => {
                                tally
                                    .disagree
                                    .push(format!("{label}: {detail} | {}", sel.selection_read));
                                tsv.push(format!("{label}\t{:?}\tdisagree\t{detail}", sel.row));
                            }
                        }
                    }
                }
            }
            // Advance main as the case does.
            let _ = session.mutate("main", source, &decl.name, &params).await;
        }
    }
    let out = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3-rewriter");
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("write_rows.tsv"), tsv.join("\n") + "\n").unwrap();
    println!(
        "write rows: {} statements; agree with rows {}; agree, both empty {}; write refused {}; read refused {}; disagree {}; skipped {}",
        tally.statements,
        tally.agree_nonempty,
        tally.agree_empty,
        tally.write_refused,
        tally.read_refused.len(),
        tally.disagree.len(),
        tally.skipped
    );
    for r in &tally.read_refused {
        println!("read refused: {r}");
    }
    for d in &tally.disagree {
        println!("disagree: {d}");
    }
    assert!(
        tally.disagree.is_empty(),
        "{} disagreements",
        tally.disagree.len()
    );
}
