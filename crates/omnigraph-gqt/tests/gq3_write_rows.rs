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
                    tsv.push(format!(
                        "{label}\t{:?}\tskipped\tbranch create failed",
                        sel.row
                    ));
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
                                    let why = before_rows
                                        .as_ref()
                                        .err()
                                        .or(after_rows.as_ref().err())
                                        .map(|e| e.to_string())
                                        .unwrap_or_default();
                                    tsv.push(format!(
                                        "{label}\t{:?}\tskipped\t{}",
                                        sel.row,
                                        why.replace(['\n', '\t'], " ")
                                    ));
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

// ---------------------------------------------------------------------------
// SPIKE (`spike/gq3-exec`): the group rule for a body of several statements.
//
// RFC 2026-11-07-gq-3, section 2 ("A write is a sequence of groups … a later
// group sees the effects of the groups before it") and section 6 ("a body of
// several statements: one group per statement, in order"). Edition 3 has no
// executor, and finding 3 above proved each single-statement group selects
// what its edition-2 statement selects; so a group is run as that edition-2
// statement on its own. For every `--- mutate` step on main whose body holds
// two or more statements, the case's earlier main-branch writes are replayed
// on its fixture; then the body runs whole on one branch, and its statements
// run one by one, as sequential single-statement declarations, on another
// branch from the same head; the two final states (every node and edge of
// every type) are compared. Each group's effect is also recorded, and the
// RFC's within-one-write conflict rule is evaluated over them: an entity and
// property written by two groups with two different values, or one entity
// written and deleted. Writes `target/spike-gq3-exec/write_groups.tsv`.

fn state_reads(catalog: &omnigraph_compiler::catalog::Catalog) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut nodes: Vec<&String> = catalog.node_types.keys().collect();
    nodes.sort();
    for t in nodes {
        out.push((
            t.clone(),
            format!("query __gq3_state() {{ match {{ $x: {t} }} return {{ $x.@id as id, $x }} }}"),
        ));
    }
    let mut edges: Vec<(&String, &omnigraph_compiler::catalog::EdgeType)> =
        catalog.edge_types.iter().collect();
    edges.sort_by_key(|(k, _)| k.as_str());
    for (key, e) in edges {
        let ident = catalog
            .edge_name_index
            .iter()
            .find(|(_, k)| *k == key)
            .map(|(i, _)| i.clone())
            .unwrap_or_else(|| key.to_lowercase());
        let mut props: Vec<&String> = e.properties.keys().collect();
        props.sort();
        let extra: String = props.iter().map(|p| format!(", $x.{p}")).collect();
        out.push((
            key.clone(),
            format!(
                "query __gq3_state() {{ match {{ $s: {}  $s $x:\"{ident}\" $d }} return {{ $x.@id as id, $x.@src as src, $x.@dst as dst{extra} }} }}",
                e.from_type
            ),
        ));
    }
    out
}

type State = BTreeMap<String, BTreeMap<String, serde_json::Value>>;

async fn snapshot(
    session: &omnigraph::Session,
    branch: &str,
    reads: &[(String, String)],
) -> Result<State, String> {
    let mut state = State::new();
    for (t, read) in reads {
        let r = session
            .query(
                ReadTarget::branch(branch),
                read,
                "__gq3_state",
                &Default::default(),
            )
            .await
            .map_err(|e| format!("state of {t}: {e}"))?;
        let rows = r.to_rust_json().map_err(|e| e.to_string())?;
        let mut by_id = BTreeMap::new();
        let is_edge = read.contains(" $x:\"");
        for row in rows.as_array().into_iter().flatten() {
            // An edge's `@id` is generated per insert, so two branches that
            // insert the same edge disagree on it: key an edge by its
            // endpoints and properties, with a counter for duplicates.
            let (key, row) = if is_edge {
                let mut r = row.clone();
                if let Some(o) = r.as_object_mut() {
                    o.remove("id");
                }
                (r.to_string(), r)
            } else {
                (
                    row.get("id").map(|v| v.to_string()).unwrap_or_default(),
                    row.clone(),
                )
            };
            let mut n = 0;
            while by_id.contains_key(&format!("{key}#{n}")) {
                n += 1;
            }
            by_id.insert(format!("{key}#{n}"), row);
        }
        state.insert(t.clone(), by_id);
    }
    Ok(state)
}

/// The (type, id) → row changes between two states: `None` for a removed row.
fn changes(a: &State, b: &State) -> BTreeMap<(String, String), Option<serde_json::Value>> {
    let mut out = BTreeMap::new();
    for (t, rows) in b {
        let before = a.get(t);
        for (id, row) in rows {
            if before.and_then(|r| r.get(id)) != Some(row) {
                out.insert((t.clone(), id.clone()), Some(row.clone()));
            }
        }
        if let Some(before) = before {
            for id in before.keys() {
                if !rows.contains_key(id) {
                    out.insert((t.clone(), id.clone()), None);
                }
            }
        }
    }
    out
}

fn first_state_difference(a: &State, b: &State) -> Option<String> {
    changes(a, b).into_iter().next().map(|((t, id), row)| {
        format!(
            "{t} {id}: whole body {} / groups {}",
            a.get(&t)
                .and_then(|r| r.get(&id))
                .map_or("absent".into(), |v| v.to_string()),
            row.map_or("absent".into(), |v| v.to_string())
        )
    })
}

use omnigraph_compiler::query::ast::Mutation;
use std::collections::BTreeMap;

/// Refusals that judge the whole write at publication (cardinality, byte
/// limits), which the harness's one publication per group cannot reproduce.
fn whole_write_check(error: &str) -> bool {
    error.contains("@card violation") || error.contains("resource limit exceeded")
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn several_statements_as_sequential_groups_match_the_whole_body() {
    let mut files = Vec::new();
    collect(&root(), &mut files);
    files.sort();
    let mut tsv = vec![
        "case\tstep\tdecl\tstatements\treached by\toutcome\tconflict rule\tdetail".to_string(),
    ];
    let mut summary: BTreeMap<String, usize> = BTreeMap::new();
    let mut seq = 0usize;
    let mut reached: BTreeSet<(String, String, String)> = BTreeSet::new();
    for file in files.clone() {
        let text = std::fs::read_to_string(&file).unwrap();
        let stem = file.file_stem().unwrap().to_string_lossy().to_string();
        let name = file.strip_prefix(root()).unwrap().display().to_string();
        let all = steps(&text);
        let multi = all.iter().any(|s| {
            s.kind == "mutate"
                && parse_query_diagnostic(&s.body)
                    .ok()
                    .is_some_and(|f| matches!(&f.body, FileBody::Queries(d) if d.iter().any(|d| d.mutations.len() > 1)))
        });
        if !multi {
            continue;
        }
        let Ok(case) = parse_case(&stem, &text) else {
            tsv.push(format!(
                "{name}\t-\t-\t-\tnot-run\t-\tcase refused by the GQT parser"
            ));
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
        let reads = state_reads(&catalog);
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
                "mutate" => continue,
                _ => break,
            }
            let source = step.body.as_str();
            if source.contains("${") || step.params.as_deref().is_some_and(|p| p.contains("${")) {
                break;
            }
            let Ok(file_ast) = parse_query_diagnostic(source) else {
                continue;
            };
            let FileBody::Queries(decls) = &file_ast.body else {
                break;
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
            let params =
                json_params_to_param_map(json.as_ref(), &decl.params, JsonParamMode::Standard)
                    .unwrap_or_default();
            if decl.mutations.len() > 1 {
                seq += 1;
                let label = format!("{name}\t{i}\t{}\t{}", decl.name, decl.mutations.len());
                reached.insert((name.clone(), decl.name.clone(), source.to_string()));
                let (outcome, rule, detail) =
                    compare_body(&session, "main", seq, source, decl, &params, &reads).await;
                *summary.entry(outcome.clone()).or_default() += 1;
                if rule.starts_with("RFC refuses") {
                    *summary.entry("conflict rule refuses".into()).or_default() += 1;
                }
                tsv.push(format!(
                    "{label}\treplay\t{outcome}\t{rule}\t{}",
                    detail.replace(['\n', '\t'], " ")
                ));
            }
            // Advance main as the case does.
            let _ = session.mutate("main", source, &decl.name, &params).await;
        }
    }
    // Second pass: the bodies the main-branch replay did not reach (on
    // another branch, after a control step or a load), reached through the
    // GQT runner's executor running the case up to that step.
    for file in files {
        let text = std::fs::read_to_string(&file).unwrap();
        let stem = file.file_stem().unwrap().to_string_lossy().to_string();
        let name = file.strip_prefix(root()).unwrap().display().to_string();
        let mut sections: Vec<(String, usize, usize, usize)> = Vec::new(); // header, header start, body start, body end
        let mut at = 0;
        for line in text.split_inclusive('\n') {
            if let Some(h) = line.strip_prefix("--- ") {
                if let Some(last) = sections.last_mut() {
                    last.3 = at;
                }
                sections.push((h.trim_end().to_string(), at, at + line.len(), text.len()));
            }
            at += line.len();
        }
        for (k, (header, header_start, b0, b1)) in sections.iter().enumerate() {
            if !header.starts_with("mutate") {
                continue;
            }
            let source = &text[*b0..*b1];
            let Ok(f) = parse_query_diagnostic(source) else {
                continue;
            };
            let FileBody::Queries(decls) = &f.body else {
                continue;
            };
            let Some(decl) = decls.iter().find(|d| d.mutations.len() > 1) else {
                continue;
            };
            if reached.contains(&(name.clone(), decl.name.clone(), source.to_string())) {
                continue;
            }
            let label = format!("{name}\t{k}\t{}\t{}", decl.name, decl.mutations.len());
            let branch = header
                .strip_prefix("mutate")
                .and_then(|h| h.trim().strip_prefix("branch:"))
                .map(|b| b.trim().to_string())
                .unwrap_or_else(|| "main".to_string());
            let json = sections[k + 1..]
                .iter()
                .take_while(|(h, ..)| h.starts_with("params") || h.starts_with("expect"))
                .find(|(h, ..)| h.starts_with("params"))
                .and_then(|(_, _, p0, p1)| {
                    serde_json::from_str::<serde_json::Value>(text[*p0..*p1].trim()).ok()
                });
            let params =
                json_params_to_param_map(json.as_ref(), &decl.params, JsonParamMode::Standard)
                    .unwrap_or_default();
            let prefix = &text[..*header_start];
            let outcome = async {
                let mut case =
                    parse_case(&stem, prefix).map_err(|e| format!("prefix refused: {e}"))?;
                let csv = tempfile::tempdir().unwrap();
                case.materialize_csv_fixtures(csv.path())?;
                let fixture = case.fixture.as_ref().ok_or("no fixture")?;
                let catalog = parse_schema(&fixture.schema)
                    .ok()
                    .and_then(|s| build_catalog(&s).ok())
                    .ok_or("schema")?;
                let dir = tempfile::tempdir().unwrap();
                let uri = dir.path().to_str().unwrap().to_string();
                let db = Omnigraph::init(&uri, &fixture.schema)
                    .await
                    .map_err(|e| e.to_string())?;
                let session = case_session(db, &case, Engine::V2)?;
                seed_case(&session, &fixture.seed, case.needs_indices).await?;
                let session = omnigraph_gqt_core::execute_steps(
                    &case,
                    &file,
                    false,
                    session,
                    &uri,
                    None,
                    &omnigraph_gqt_core::PlainHost,
                )
                .await
                .map_err(|e| format!("prefix did not run green: {e}"))?;
                seq += 1;
                Ok::<_, String>(
                    compare_body(
                        &session,
                        &branch,
                        seq,
                        source,
                        decl,
                        &params,
                        &state_reads(&catalog),
                    )
                    .await,
                )
            }
            .await;
            match outcome {
                Ok((outcome, rule, detail)) => {
                    *summary.entry(outcome.clone()).or_default() += 1;
                    if rule.starts_with("RFC refuses") {
                        *summary.entry("conflict rule refuses".into()).or_default() += 1;
                    }
                    tsv.push(format!(
                        "{label}\tgqt-prefix\t{outcome}\t{rule}\t{}",
                        detail.replace(['\n', '\t'], " ")
                    ));
                }
                Err(e) => {
                    *summary.entry("not-reached".into()).or_default() += 1;
                    let e: String = e.chars().take(300).collect();
                    tsv.push(format!(
                        "{label}\tgqt-prefix\tnot-reached\t-\t{}",
                        e.replace(['\n', '\t'], " ")
                    ));
                }
            }
        }
    }
    let out = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3-exec");
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("write_groups.tsv"), tsv.join("\n") + "\n").unwrap();
    println!("write groups: {summary:?}");
    for line in &tsv[1..] {
        println!("{line}");
    }
}

#[allow(clippy::too_many_arguments)]
async fn compare_body(
    session: &omnigraph::Session,
    from_branch: &str,
    seq: usize,
    source: &str,
    decl: &omnigraph_compiler::query::ast::QueryDecl,
    params: &omnigraph_compiler::ParamMap,
    reads: &[(String, String)],
) -> (String, String, String) {
    let (whole, groups) = (format!("gq3w{seq}"), format!("gq3g{seq}"));
    for b in [&whole, &groups] {
        session
            .db()
            .branch_create_from(ReadTarget::branch(from_branch), b)
            .await
            .unwrap();
    }
    let whole_result = session.mutate(&whole, source, &decl.name, params).await;
    // Each statement as its own declaration, in order, with the
    // body's parameter list.
    let param_list = decl
        .params
        .iter()
        .map(|p| {
            format!(
                "${}: {}{}",
                p.name,
                p.type_name,
                if p.nullable { "?" } else { "" }
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    let mut group_error = None;
    let mut affected = 0usize;
    // (type, id, property) -> (group, value)
    let mut written: BTreeMap<(String, String, String), (usize, serde_json::Value)> =
        BTreeMap::new();
    let mut conflicts = Vec::new();
    let mut identical = 0usize;
    for (g, (m, spans)) in decl.mutations.iter().zip(&decl.spans.mutations).enumerate() {
        let stmt = spans.stmt.text(source);
        let single = format!("query {}({param_list}) {{\n    {stmt}\n}}\n", decl.name);
        let before = snapshot(session, &groups, reads).await;
        match session.mutate(&groups, &single, &decl.name, params).await {
            Ok(r) => affected += r.affected_nodes + r.affected_edges,
            Err(e) => {
                group_error = Some(format!("group {}: {e}", g + 1));
                break;
            }
        }
        let after = snapshot(session, &groups, reads).await;
        let (Ok(before), Ok(after)) = (before, after) else {
            continue;
        };
        let (type_name, props): (String, Vec<String>) = match m {
            Mutation::Insert(x) => (
                x.type_name.clone(),
                x.assignments.iter().map(|a| a.property.clone()).collect(),
            ),
            Mutation::Update(x) => (
                x.type_name.clone(),
                x.assignments.iter().map(|a| a.property.clone()).collect(),
            ),
            Mutation::Delete(x) => (x.type_name.clone(), vec!["*".to_string()]),
        };
        for ((t, id), row) in changes(&before, &after) {
            if t != type_name {
                continue;
            }
            for p in &props {
                let value = match &row {
                    None => serde_json::Value::String("<deleted>".into()),
                    Some(r) => r
                        .get("x")
                        .and_then(|x| x.get(p))
                        .or_else(|| r.get(format!("x.{p}")))
                        .cloned()
                        .unwrap_or(serde_json::Value::Null),
                };
                let key = (t.clone(), id.clone(), p.clone());
                if let Some((h, prev)) = written.get(&key) {
                    if *prev == value {
                        identical += 1;
                    } else {
                        conflicts.push(format!(
                            "{t} {id}.{p}: group {} wrote {prev}, group {} wrote {value}",
                            h + 1,
                            g + 1
                        ));
                    }
                }
                // A delete also conflicts with any write of the entity.
                if p == "*" {
                    for ((t2, id2, p2), (h, _)) in &written {
                        if t2 == &t && id2 == &id && p2 != "*" {
                            conflicts.push(format!(
                                "{t} {id}: group {} writes {p2}, group {} deletes it",
                                h + 1,
                                g + 1
                            ));
                        }
                    }
                }
                written.insert(key, (g, value));
            }
        }
    }
    let rule = if conflicts.is_empty() {
        if identical > 0 {
            format!("no conflict ({identical} identical re-writes apply once)")
        } else {
            "no conflict".to_string()
        }
    } else {
        format!("RFC refuses: {}", conflicts.join("; "))
    };
    let (outcome, detail) = match (&whole_result, &group_error) {
        (Err(a), Some(b)) => ("both-refuse".to_string(), format!("whole body: {a} | {b}")),
        (Err(a), None) if whole_write_check(&a.to_string()) => (
            "whole-write-check-refuses-whole-body".to_string(),
            format!("{a} (a check over the whole write; the harness publishes each group alone)"),
        ),
        (Err(a), None) => ("whole-refuses-groups-run".to_string(), a.to_string()),
        (Ok(_), Some(b)) if whole_write_check(b) => (
            "whole-write-check-refuses-a-group".to_string(),
            format!("{b} (a check over the whole write; the harness publishes each group alone)"),
        ),
        (Ok(_), Some(b)) => ("groups-refuse-whole-runs".to_string(), b.clone()),
        (Ok(r), None) => {
            let a = snapshot(session, &whole, reads).await;
            let b = snapshot(session, &groups, reads).await;
            match (a, b) {
                (Ok(a), Ok(b)) if a == b => {
                    let whole_affected = r.affected_nodes + r.affected_edges;
                    (
                        "same-final-state".to_string(),
                        if whole_affected == affected {
                            format!("affected {whole_affected} both")
                        } else {
                            format!(
                                "affected: whole body {whole_affected}, groups summed {affected}"
                            )
                        },
                    )
                }
                (Ok(a), Ok(b)) => (
                    "different-final-state".to_string(),
                    first_state_difference(&a, &b).unwrap_or_default(),
                ),
                (a, b) => (
                    "state-read-failed".to_string(),
                    format!("{:?} {:?}", a.err(), b.err()),
                ),
            }
        }
    };
    (outcome, rule, detail)
}
