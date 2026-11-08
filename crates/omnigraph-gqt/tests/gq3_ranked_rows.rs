#![recursion_limit = "512"]
//! SPIKE (`spike/gq3-exec`): rows-equality of the GQ 3 rewriter's ranking
//! rows on the engine, through the GQT runner.
//!
//! For every `.gqt` case under `cases/` whose `--- query` steps hold a
//! `nearest`, `bm25` or `rrf` that `omnigraph queries migrate` moves into
//! `from`, the harness builds the case's **twin**: the same file with each
//! such query body replaced by its rewritten edition-3 text, parsed by
//! `parse_query3`, lowered by the normalising bridge
//! (`omnigraph_compiler::query::normalise3`) and printed back as edition-2
//! text, the only text today's engine runs. Original and twin run on fresh
//! copies of the case's fixture through `omnigraph_gqt_core::execute_steps`
//! (the GQT runner's executor) with a host that records every query step's
//! rows; the ranked steps' rows are compared, ordered where the case orders.
//!
//! The bridge folds `from rrf(…)` back into edition 2's `rrf` node, so the
//! twin of an `rrf` step proves the normalisation, not the RFC's `Fuse`. Each
//! `rrf` step is therefore also run on the **fuse route**: the case up to and
//! including that step runs through the same executor, and on the session it
//! leaves the harness evaluates section 4's `Fuse` itself from the arms: each
//! arm's rows (an edition-2 read of the population ranked by the arm, run on
//! the engine), its distinct targets ranked by the arm's own score in its
//! direction then by target id, a `nearest` arm cut to `k` targets,
//! `Σ 1 / (k + rank)` in written order in `F64`, one row per target ordered
//! by the fused score descending then target id, cut to `limit`, and the
//! read's `return` drawn for each target. Those rows are compared with the
//! original step's.
//!
//! Writes `target/spike-gq3-exec/rows.tsv` and `rows_summary.md`. Does not
//! fail on a difference: the differences are the findings.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use omnigraph::Session;
use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph_compiler::catalog::build_catalog;
use omnigraph_compiler::migrate::{Row, migrate};
use omnigraph_compiler::query::ast::{Clause, Expr, FileBody, Ordering, Projection, QueryDecl};
use omnigraph_compiler::query::ast3::Body3;
use omnigraph_compiler::query::normalise3::{Normalised, Shape, normalise_decl, print_read2};
use omnigraph_compiler::query::parser::parse_query_diagnostic;
use omnigraph_compiler::query::parser3::parse_query3;
use omnigraph_compiler::query_input::{JsonParamMode, json_params_to_param_map};
use omnigraph_compiler::schema::parser::parse_schema;
use omnigraph_compiler::settings::Engine;
use omnigraph_compiler::{ParamMap, QueryResult};
use omnigraph_gqt_core::{
    Case, Execution, ExecutionHost, Item, SeamDirective, Step, canonical_json, case_session,
    execute_steps, parse_case, seed_case,
};
use serde_json::Value;

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

/// One `--- header` section: the header line's start and the body's range.
struct Section {
    kind: String,
    header_start: usize,
    body_start: usize,
    body_end: usize,
}

fn sections(text: &str) -> Vec<Section> {
    let mut out: Vec<Section> = Vec::new();
    let mut at = 0;
    for line in text.split_inclusive('\n') {
        if let Some(h) = line.strip_prefix("--- ") {
            if let Some(last) = out.last_mut() {
                last.body_end = at;
            }
            out.push(Section {
                kind: h.split_whitespace().next().unwrap_or("").to_string(),
                header_start: at,
                body_start: at + line.len(),
                body_end: text.len(),
            });
        }
        at += line.len();
    }
    out
}

const STEP_KINDS: &[&str] = &[
    "query",
    "mutate",
    "restart",
    "load",
    "loop",
    "endloop",
    "foreach",
    "concurrent",
];

/// What the twin holds for one ranked `--- query` section.
enum Twin {
    Bridged {
        shape: Shape,
        printed: String,
        normalised: Vec<Normalised>,
    },
    Refused {
        shape: Shape,
        reason: String,
    },
}

/// The ranked shape of a rewrite, from the rows it used.
fn ranked_shape(rows: &BTreeMap<Row, usize>) -> Option<Shape> {
    rows.keys().find_map(|r| match r {
        Row::Nearest => Some(Shape::Nearest),
        Row::Bm25 => Some(Shape::Bm25),
        Row::Rrf => Some(Shape::Rrf),
        _ => None,
    })
}

fn twin_of(body: &str, catalog: &omnigraph_compiler::catalog::Catalog) -> Option<Twin> {
    let m = migrate(body, Some(catalog)).ok()?;
    let shape = m.decls.iter().find_map(|d| ranked_shape(&d.rows));
    let shape = match shape {
        Some(s) => s,
        None => {
            // A ranking the rewriter refused is reported, not twinned.
            let r = m
                .refusals
                .iter()
                .find(|r| matches!(r.row, Some(Row::Nearest | Row::Bm25 | Row::Rrf)))?;
            let shape = match r.row {
                Some(Row::Nearest) => Shape::Nearest,
                Some(Row::Bm25) => Shape::Bm25,
                _ => Shape::Rrf,
            };
            return Some(Twin::Refused {
                shape,
                reason: format!("rewriter refused: {}", r.reason),
            });
        }
    };
    if let Some(r) = m.refusals.first() {
        return Some(Twin::Refused {
            shape,
            reason: format!("rewriter refused: {}", r.reason),
        });
    }
    let file3 = match parse_query3(&m.text) {
        Ok(f) => f,
        Err(e) => {
            return Some(Twin::Refused {
                shape,
                reason: format!("edition-3 parse: {e}"),
            });
        }
    };
    let Body3::Decls(decls) = file3.body else {
        return None;
    };
    let mut normalised = Vec::new();
    let mut printed = String::new();
    for d in decls {
        match normalise_decl(d) {
            Ok(n) => {
                match print_read2(&n.decl) {
                    Ok(t) => printed.push_str(&t),
                    Err(e) => {
                        return Some(Twin::Refused {
                            shape,
                            reason: format!("print: {e}"),
                        });
                    }
                }
                normalised.push(n);
            }
            Err(e) => {
                return Some(Twin::Refused {
                    shape,
                    reason: format!("bridge: {e}"),
                });
            }
        }
    }
    Some(Twin::Bridged {
        shape,
        printed,
        normalised,
    })
}

/// The case text with every `--- expect same as v1` section removed: the
/// recording host carries no reference engine, and that comparison is not
/// what this harness measures.
fn without_same_as_v1(text: &str) -> String {
    let mut out = String::new();
    let mut skipping = false;
    for line in text.split_inclusive('\n') {
        if line.starts_with("--- ") {
            skipping = line.trim_end() == "--- expect same as v1";
        }
        if !skipping {
            out.push_str(line);
        }
    }
    out
}

#[derive(Debug, Clone)]
struct Observed {
    ordinal: usize,
    rows: Result<Vec<Value>, String>,
    ordered: bool,
}

#[derive(Default)]
struct RecordingHost {
    current: Mutex<usize>,
    observed: Mutex<Vec<Observed>>,
}

impl ExecutionHost for RecordingHost {
    type StepGuard = ();

    fn arm_seams(&self, seams: &[SeamDirective], _step: &Step) -> Result<(), String> {
        if seams.is_empty() {
            Ok(())
        } else {
            Err("unsupported_environment: seams".into())
        }
    }

    fn finish_seams(&self, _guard: ()) -> Result<(), String> {
        Ok(())
    }

    fn begin_operation(&self, value: impl FnOnce() -> Value) {
        if let Some(n) = value().get("ordinal").and_then(Value::as_u64) {
            *self.current.lock().unwrap() = n as usize;
        }
    }

    fn observe_query<E: std::fmt::Display>(&self, result: &Result<QueryResult, E>, ordered: bool) {
        let rows = match result {
            Ok(r) => match r.to_rust_json() {
                Ok(Value::Array(rows)) => Ok(rows),
                Ok(other) => Err(format!("non-array rows {other}")),
                Err(e) => Err(format!("render: {e}")),
            },
            Err(e) => Err(e.to_string()),
        };
        self.observed.lock().unwrap().push(Observed {
            ordinal: *self.current.lock().unwrap(),
            rows,
            ordered,
        });
    }
}

struct Run {
    verdict: Result<(), String>,
    session: Option<Session>,
    observed: Vec<Observed>,
    _dirs: Vec<tempfile::TempDir>,
}

async fn run_text(stem: &str, path: &Path, text: &str) -> Result<Run, String> {
    let mut case = parse_case(stem, text)?;
    let csv_dir = tempfile::tempdir().map_err(|e| e.to_string())?;
    case.materialize_csv_fixtures(csv_dir.path())?;
    let fixture = case.fixture.as_ref().ok_or("no fixture")?;
    let dir = tempfile::tempdir().map_err(|e| e.to_string())?;
    let uri = dir.path().to_str().unwrap().to_string();
    let db = Omnigraph::init(&uri, &fixture.schema)
        .await
        .map_err(|e| format!("init: {e}"))?;
    let db = match &case.external_blob_policy {
        Some(policy) => db
            .with_external_blob_policy(policy.clone())
            .map_err(|e| e.to_string())?,
        None => db,
    };
    let session = case_session(db, &case, Engine::V2)?;
    seed_case(&session, &fixture.seed, case.needs_indices).await?;
    let host = RecordingHost::default();
    let result = execute_steps(&case, path, false, session, &uri, None, &host).await;
    let observed = host.observed.into_inner().unwrap();
    let (verdict, session) = match result {
        Ok(s) => (Ok(()), Some(s)),
        Err(e) => (Err(e), None),
    };
    Ok(Run {
        verdict,
        session,
        observed,
        _dirs: vec![dir, csv_dir],
    })
}

fn query_steps(case: &Case) -> Vec<(usize, bool, String, Option<String>)> {
    let mut out = Vec::new();
    for item in &case.items {
        let (steps, in_loop): (&[Step], bool) = match item {
            Item::Step(s) => (std::slice::from_ref(s), false),
            Item::Loop { steps, .. } => (steps, true),
        };
        for s in steps {
            if let Step::Query(q) = s {
                out.push((s.ordinal(), in_loop, q.branch.clone(), q.snapshot.clone()));
            }
        }
    }
    out
}

/// Rows equal as the case compares them; on a difference, the first row
/// that differs.
fn compare(a: &[Value], b: &[Value], ordered: bool) -> Result<(), String> {
    if ordered {
        for (i, (x, y)) in a.iter().zip(b).enumerate() {
            if x != y {
                return Err(format!("row {i}: edition 2 {x} / edition 3 {y}"));
            }
        }
    } else {
        let key = |v: &Value| canonical_json(v);
        let mut x: Vec<&Value> = a.iter().collect();
        let mut y: Vec<&Value> = b.iter().collect();
        x.sort_by_key(|v| key(v));
        y.sort_by_key(|v| key(v));
        for (p, q) in x.iter().zip(&y) {
            if key(p) != key(q) {
                let missing = x.iter().find(|r| !y.iter().any(|s| key(s) == key(r)));
                let extra = y.iter().find(|r| !x.iter().any(|s| key(s) == key(r)));
                return Err(format!(
                    "unordered: edition 2 only {} / edition 3 only {}",
                    missing.map_or("-".into(), |v| v.to_string()),
                    extra.map_or("-".into(), |v| v.to_string())
                ));
            }
            if p != q {
                return Err(format!(
                    "equal to 12 digits, unequal bits: edition 2 {p} / edition 3 {q}"
                ));
            }
        }
    }
    if a.len() != b.len() {
        return Err(format!(
            "{} rows in edition 2, {} in edition 3",
            a.len(),
            b.len()
        ));
    }
    Ok(())
}

/// The `--- params` JSON that follows section `at`, before the next step.
fn params_after(text: &str, secs: &[Section], at: usize) -> Option<Value> {
    for s in &secs[at + 1..] {
        if STEP_KINDS.contains(&s.kind.as_str()) {
            return None;
        }
        if s.kind == "params" {
            let body = text[s.body_start..s.body_end].trim();
            return if body.is_empty() {
                None
            } else {
                serde_json::from_str(body).ok()
            };
        }
    }
    None
}

/// The end of a step's sections: the next step's header, or the file's end.
fn step_end(text: &str, secs: &[Section], at: usize) -> usize {
    secs[at + 1..]
        .iter()
        .find(|s| STEP_KINDS.contains(&s.kind.as_str()))
        .map_or(text.len(), |s| s.header_start)
}

fn rank_expr(arm: &omnigraph_compiler::query::normalise3::Arm) -> Expr {
    if arm.kind == "nearest" {
        Expr::Nearest {
            variable: arm.variable.clone(),
            property: arm.property.clone(),
            query: Box::new(arm.query.clone()),
        }
    } else {
        Expr::Bm25 {
            field: Box::new(Expr::PropAccess {
                variable: arm.variable.clone(),
                property: arm.property.clone(),
            }),
            query: Box::new(arm.query.clone()),
        }
    }
}

const ARM_SCAN: u64 = 10_000;

fn is_rank(e: &Expr) -> bool {
    matches!(
        e,
        Expr::Nearest { .. } | Expr::Bm25 { .. } | Expr::Rrf { .. }
    )
}

fn mentions_rank(e: &Expr) -> bool {
    if is_rank(e) {
        return true;
    }
    match e {
        Expr::Binary { left, right, .. } => mentions_rank(left) || mentions_rank(right),
        Expr::Not(x) | Expr::Aggregate { arg: x, .. } => mentions_rank(x),
        Expr::IsNull { expr, .. } => mentions_rank(expr),
        Expr::In { needle, list } => mentions_rank(needle) || mentions_rank(list),
        _ => false,
    }
}

fn replace_rank(e: &Expr, score: f64) -> Expr {
    if is_rank(e) {
        return Expr::Literal(omnigraph_compiler::query::ast::Literal::Float(score));
    }
    match e {
        Expr::Binary { left, op, right } => Expr::Binary {
            left: Box::new(replace_rank(left, score)),
            op: *op,
            right: Box::new(replace_rank(right, score)),
        },
        Expr::Not(x) => Expr::Not(Box::new(replace_rank(x, score))),
        Expr::IsNull { expr, negated } => Expr::IsNull {
            expr: Box::new(replace_rank(expr, score)),
            negated: *negated,
        },
        other => other.clone(),
    }
}

fn contains_aggregate(e: &Expr) -> bool {
    match e {
        Expr::Aggregate { .. } => true,
        Expr::Binary { left, right, .. } => contains_aggregate(left) || contains_aggregate(right),
        Expr::Not(x) => contains_aggregate(x),
        _ => false,
    }
}

/// The variables a population binds at top level, in written order.
fn population_vars(clauses: &[Clause]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut push = |v: &String| {
        if !out.contains(v) {
            out.push(v.clone());
        }
    };
    for c in clauses {
        match c {
            Clause::Binding(b) => push(&b.variable),
            Clause::Traversal(t) => {
                push(&t.src);
                if let Some(e) = &t.edge_binding {
                    push(e);
                }
                push(&t.dst);
            }
            _ => {}
        }
    }
    out
}

/// A variable's declared type: its binding, else a traversal endpoint.
fn type_of(
    v: &str,
    clauses: &[Clause],
    catalog: &omnigraph_compiler::catalog::Catalog,
) -> Option<String> {
    for c in clauses {
        if let Clause::Binding(b) = c
            && b.variable == v
        {
            return Some(b.type_name.clone());
        }
    }
    for c in clauses {
        if let Clause::Traversal(t) = c
            && let omnigraph_compiler::query::ast::EdgeSelector::Named(n) = &t.selector
        {
            let key = catalog
                .edge_name_index
                .get(&n.to_lowercase())
                .cloned()
                .unwrap_or_else(|| n.clone());
            let e = catalog.edge_types.get(&key)?;
            if t.dst == v && !t.undirected {
                return Some(e.to_type.clone());
            }
            if t.src == v && !t.undirected {
                return Some(e.from_type.clone());
            }
            if t.edge_binding.as_deref() == Some(v) {
                return Some(key);
            }
        }
    }
    None
}

/// The fixed value order: null first, then ascending within a kind.
fn value_cmp(a: &Value, b: &Value) -> std::cmp::Ordering {
    use std::cmp::Ordering::*;
    match (a, b) {
        (Value::Null, Value::Null) => Equal,
        (Value::Null, _) => Less,
        (_, Value::Null) => Greater,
        (Value::Number(x), Value::Number(y)) => x
            .as_f64()
            .unwrap_or(0.0)
            .total_cmp(&y.as_f64().unwrap_or(0.0)),
        (Value::String(x), Value::String(y)) => x.cmp(y),
        (Value::Bool(x), Value::Bool(y)) => x.cmp(y),
        _ => canonical_json(a).cmp(&canonical_json(b)),
    }
}

fn read(
    base: &QueryDecl,
    name: &str,
    clauses: Vec<Clause>,
    items: Vec<Projection>,
    order: Vec<Ordering>,
    limit: Option<u64>,
) -> QueryDecl {
    QueryDecl {
        name: name.into(),
        description: None,
        instruction: None,
        params: base.params.clone(),
        match_clause: clauses,
        return_clause: items,
        distinct: false,
        order_clause: order,
        limit,
        mutations: Vec::new(),
        write_mode: base.write_mode.clone(),
        spans: Default::default(),
    }
}

async fn rows_of(
    session: &Session,
    target: &ReadTarget,
    params: &ParamMap,
    decl: &QueryDecl,
) -> Result<Vec<Value>, String> {
    let text = print_read2(decl)?;
    let result = session
        .query(target.clone(), &text, &decl.name, params)
        .await
        .map_err(|e| format!("`{}`: {e}", text.replace('\n', " ")))?;
    match result.to_rust_json().map_err(|e| e.to_string())? {
        Value::Array(r) => Ok(r),
        _ => Err("rows".into()),
    }
}

fn prop(v: &str, p: &str) -> Expr {
    Expr::PropAccess {
        variable: v.into(),
        property: p.into(),
    }
}

fn id_filter(v: &str, ids: &[String]) -> Clause {
    let list = ids
        .iter()
        .map(|id| {
            omnigraph_compiler::query::ast::Literal::String(
                serde_json::from_str::<String>(id).unwrap_or_else(|_| id.clone()),
            )
        })
        .collect();
    Clause::Filter(Expr::In {
        needle: Box::new(prop(v, "@id")),
        list: Box::new(Expr::Literal(
            omnigraph_compiler::query::ast::Literal::List(list),
        )),
    })
}

/// The read evaluated under the RFC's semantics (sections 2 and 4) from
/// edition-2 pieces on the step's session: `ApproxTopK`, `Score` or `Fuse`
/// over the population, then the read's `return`, `order` (its keys, then the
/// tie rule over the relation's reference columns) and `limit`.
async fn rfc_rows(
    session: &Session,
    target: ReadTarget,
    params: &ParamMap,
    n: &Normalised,
    catalog: &omnigraph_compiler::catalog::Catalog,
    type_arms: bool,
) -> Result<Vec<Value>, String> {
    let fusion = n.fusion.as_ref().ok_or("no ranking")?;
    let base = &n.decl;
    // `type_arms`: each `rrf` arm ranks every node of the target's type and
    // the population filters the fused targets afterwards (the rewrite
    // `from rrf(nearest({ match { $v: T } return { $v } }, …), …)  P`).
    let arm_population = if type_arms {
        let t =
            type_of(&fusion.target, &fusion.population, catalog).ok_or("the target has no type")?;
        vec![Clause::Binding(omnigraph_compiler::query::ast::Binding {
            variable: fusion.target.clone(),
            type_name: t,
            prop_matches: Vec::new(),
        })]
    } else {
        fusion.population.clone()
    };
    let v = &fusion.target;
    // Each arm: its distinct targets ranked by its own score, then target id.
    let mut arm_scores: Vec<Vec<(String, f64)>> = Vec::new();
    for (i, arm) in fusion.arms.iter().enumerate() {
        let rank = rank_expr(arm);
        let decl = read(
            base,
            &format!("__gq3_arm{i}"),
            arm_population.clone(),
            vec![
                Projection {
                    expr: prop(v, "@id"),
                    alias: Some("__id".into()),
                },
                Projection {
                    expr: rank.clone(),
                    alias: Some("__s".into()),
                },
            ],
            vec![Ordering {
                expr: rank,
                descending: arm.kind != "nearest",
                explicit: arm.kind != "nearest",
            }],
            Some(ARM_SCAN),
        );
        let rows = rows_of(session, &target, params, &decl).await?;
        if rows.len() as u64 >= ARM_SCAN {
            return Err("arm scan saturated".into());
        }
        let mut best: BTreeMap<String, f64> = BTreeMap::new();
        for r in &rows {
            let id = r.get("__id").map(|x| x.to_string()).ok_or("no __id")?;
            let s = r.get("__s").and_then(Value::as_f64).ok_or("no __s")?;
            best.entry(id).or_insert(s);
        }
        let mut ordered: Vec<(String, f64)> = best.into_iter().collect();
        ordered.sort_by(|a, b| {
            let by = if arm.kind == "nearest" {
                a.1.total_cmp(&b.1)
            } else {
                b.1.total_cmp(&a.1)
            };
            by.then_with(|| a.0.cmp(&b.0))
        });
        if let Some(k) = arm.k {
            ordered.truncate(k as usize);
        }
        arm_scores.push(ordered);
    }
    // The score per selected target.
    let scores: BTreeMap<String, f64> = if n.shape == Shape::Rrf {
        let k = fusion.k as f64;
        let mut fused: BTreeMap<String, f64> = BTreeMap::new();
        for id in arm_scores.iter().flatten().map(|(id, _)| id) {
            if fused.contains_key(id) {
                continue;
            }
            let mut s = 0.0f64;
            for (i, arm) in arm_scores.iter().enumerate() {
                if let Some(rank) = arm.iter().position(|(x, _)| x == id) {
                    let w = fusion
                        .weights
                        .as_ref()
                        .and_then(|w| w.get(i).copied())
                        .unwrap_or(1.0);
                    s += w / (k + (rank + 1) as f64);
                }
            }
            fused.insert(id.clone(), s);
        }
        fused
    } else {
        arm_scores[0].iter().cloned().collect()
    };
    let mut scores = scores;
    if type_arms {
        let decl = read(
            base,
            "__gq3_eligible",
            fusion.population.clone(),
            vec![Projection {
                expr: prop(v, "@id"),
                alias: Some("__id".into()),
            }],
            Vec::new(),
            None,
        );
        let eligible: std::collections::BTreeSet<String> = rows_of(session, &target, params, &decl)
            .await?
            .iter()
            .filter_map(|r| r.get("__id").map(|x| x.to_string()))
            .collect();
        scores.retain(|id, _| eligible.contains(id));
    }
    let ids: Vec<String> = scores.keys().cloned().collect();
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    // After `rrf` the relation is the target and its score; otherwise every
    // row of the population whose target was selected.
    let (clauses, vars) = if n.shape == Shape::Rrf {
        let t = type_of(v, &fusion.population, catalog).ok_or("the target has no type")?;
        (
            vec![Clause::Binding(omnigraph_compiler::query::ast::Binding {
                variable: v.clone(),
                type_name: t,
                prop_matches: Vec::new(),
            })],
            vec![v.clone()],
        )
    } else {
        (
            fusion.population.clone(),
            population_vars(&fusion.population),
        )
    };
    let mut clauses = clauses;
    clauses.push(id_filter(v, &ids));
    let aggregate = base
        .return_clause
        .iter()
        .any(|p| contains_aggregate(&p.expr));
    if aggregate {
        // The window or score selected the rows the aggregate reads; the
        // rank key orders nothing and `limit` cuts the groups.
        if base.return_clause.iter().any(|p| mentions_rank(&p.expr))
            || base
                .order_clause
                .iter()
                .skip(1)
                .any(|o| mentions_rank(&o.expr))
        {
            return Err("a score read under an aggregate".into());
        }
        let order: Vec<Ordering> = base
            .order_clause
            .iter()
            .filter(|o| !is_rank(&o.expr))
            .cloned()
            .collect();
        let decl = read(
            base,
            "__gq3_rfc",
            clauses,
            base.return_clause.clone(),
            order,
            base.limit,
        );
        return rows_of(session, &target, params, &decl).await;
    }
    if base.distinct && base.order_clause.iter().any(|o| mentions_rank(&o.expr)) {
        return Err("a rank key under `return distinct`: `distinct` is a `Group`, and the score is no group column (an order key edition 3 cannot type)".into());
    }
    // Project ids, the plain return items and the plain order keys.
    let mut items = vec![Projection {
        expr: prop(v, "@id"),
        alias: Some("__gq3_t".into()),
    }];
    for x in &vars {
        items.push(Projection {
            expr: prop(x, "@id"),
            alias: Some(format!("__gq3_r_{x}")),
        });
    }
    let names: Vec<String> = base
        .return_clause
        .iter()
        .map(|p| match (&p.alias, &p.expr) {
            (Some(a), _) => Ok(a.clone()),
            (None, Expr::Variable(x)) => Ok(x.clone()),
            (None, Expr::PropAccess { variable, property }) => Ok(format!("{variable}.{property}")),
            (None, e) => Err(format!("an unaliased return item `{e}`")),
        })
        .collect::<Result<_, _>>()?;
    let mut compound = false;
    for (i, p) in base.return_clause.iter().enumerate() {
        if is_rank(&p.expr) {
            continue;
        }
        if mentions_rank(&p.expr) {
            compound = true;
            continue;
        }
        items.push(Projection {
            expr: p.expr.clone(),
            alias: Some(format!("__gq3_i{i}")),
        });
    }
    let alias_item = |o: &Ordering| -> Option<usize> {
        match &o.expr {
            Expr::AliasRef(a) => base
                .return_clause
                .iter()
                .position(|p| p.alias.as_deref() == Some(a.as_str())),
            _ => None,
        }
    };
    for (i, o) in base.order_clause.iter().enumerate() {
        if alias_item(o).is_none() && !mentions_rank(&o.expr) {
            items.push(Projection {
                expr: o.expr.clone(),
                alias: Some(format!("__gq3_o{i}")),
            });
        }
    }
    let decl = read(base, "__gq3_rfc", clauses.clone(), items, Vec::new(), None);
    let rows = rows_of(session, &target, params, &decl).await?;
    let score_of = |r: &Value| -> f64 {
        r.get("__gq3_t")
            .and_then(|t| scores.get(&t.to_string()))
            .copied()
            .unwrap_or(f64::NAN)
    };
    // Every row's output values first: an order key may name an output.
    let mut computed: Vec<(Value, f64, Vec<Value>)> = Vec::new();
    for r in rows {
        let score = score_of(&r);
        let mut values = Vec::new();
        for (i, p) in base.return_clause.iter().enumerate() {
            let value = if is_rank(&p.expr) {
                serde_json::Number::from_f64(score)
                    .map(Value::Number)
                    .unwrap_or(Value::Null)
            } else if compound && mentions_rank(&p.expr) {
                // The item with the score as a literal, for this target.
                let t = r.get("__gq3_t").map(|x| x.to_string()).unwrap_or_default();
                let mut cs = clauses.clone();
                cs.pop();
                cs.push(id_filter(v, &[t]));
                let d = read(
                    base,
                    "__gq3_item",
                    cs,
                    vec![Projection {
                        expr: replace_rank(&p.expr, score),
                        alias: Some("__v".into()),
                    }],
                    Vec::new(),
                    Some(1),
                );
                rows_of(session, &target, params, &d)
                    .await?
                    .first()
                    .and_then(|x| x.get("__v").cloned())
                    .unwrap_or(Value::Null)
            } else {
                r.get(format!("__gq3_i{i}")).cloned().unwrap_or(Value::Null)
            };
            values.push(value);
        }
        computed.push((r, score, values));
    }
    // Order keys, then the tie rule: the identity (type, id) of every
    // reference column in column order, then every remaining column.
    let types: Vec<String> = vars
        .iter()
        .map(|x| type_of(x, &fusion.population, catalog).unwrap_or_default())
        .collect();
    if base
        .order_clause
        .iter()
        .any(|o| alias_item(o).is_none() && !is_rank(&o.expr) && mentions_rank(&o.expr))
    {
        return Err("an order key computed from the score".into());
    }
    computed.sort_by(|(a, sa, va), (b, sb, vb)| {
        for (i, o) in base.order_clause.iter().enumerate() {
            let c = if is_rank(&o.expr) {
                sa.total_cmp(sb)
            } else if let Some(j) = alias_item(o) {
                value_cmp(&va[j], &vb[j])
            } else {
                let k = format!("__gq3_o{i}");
                value_cmp(
                    a.get(&k).unwrap_or(&Value::Null),
                    b.get(&k).unwrap_or(&Value::Null),
                )
            };
            let c = if o.descending { c.reverse() } else { c };
            if c != std::cmp::Ordering::Equal {
                return c;
            }
        }
        for (x, t) in vars.iter().zip(&types) {
            let k = format!("__gq3_r_{x}");
            let c =
                (t, a.get(&k).map(|v| v.to_string())).cmp(&(t, b.get(&k).map(|v| v.to_string())));
            if c != std::cmp::Ordering::Equal {
                return c;
            }
        }
        canonical_json(&Value::Array(va.clone())).cmp(&canonical_json(&Value::Array(vb.clone())))
    });
    if let Some(l) = base.limit {
        computed.truncate(l as usize);
    }
    let mut out = Vec::new();
    for (_, _, values) in computed {
        let mut o = serde_json::Map::new();
        for (name, value) in names.iter().zip(values) {
            o.insert(name.clone(), value);
        }
        out.push(Value::Object(o));
    }
    Ok(out)
}

#[derive(Default)]
struct Tally {
    steps: BTreeMap<(Shape, &'static str, String), usize>,
}

fn settings_prefix(body: &str) -> &str {
    let mut at = 0;
    for line in body.split_inclusive('\n') {
        if line.trim_start().starts_with("query ") {
            return &body[..at];
        }
        at += line.len();
    }
    ""
}

fn compare_runs(a: &[&Observed], b: &[&Observed]) -> (String, String) {
    if a.is_empty() || b.is_empty() {
        return (
            "not-reached".into(),
            format!("{} / {} runs", a.len(), b.len()),
        );
    }
    let mut v = ("equal".to_string(), String::new());
    for (x, y) in a.iter().zip(b) {
        match (&x.rows, &y.rows) {
            (Ok(p), Ok(q)) => {
                if let Err(d) = compare(p, q, x.ordered) {
                    return ("different".into(), d);
                }
            }
            (Err(p), Err(q)) if p == q => v = ("both-error-alike".into(), p.clone()),
            (p, q) => {
                return (
                    "error-differs".into(),
                    format!(
                        "edition 2 {:?} / edition 3 {:?}",
                        p.as_ref().err(),
                        q.as_ref().err()
                    ),
                );
            }
        }
    }
    v
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ranked_rewrites_return_the_original_rows() {
    let mut files = Vec::new();
    collect(&root(), &mut files);
    files.sort();
    let filter = std::env::var("GQ3_CASE").ok();
    let mut tsv = vec!["case\tordinal\tshape\troute\toutcome\tdetail".to_string()];
    let mut tally = Tally::default();
    let mut cases_run = 0usize;
    let mut twins_run = 0usize;
    let mut twins_green = 0usize;
    let mut cases_original_red = Vec::new();
    for file in files {
        let name = file.strip_prefix(root()).unwrap().display().to_string();
        if filter.as_ref().is_some_and(|f| !name.contains(f.as_str())) {
            continue;
        }
        let text = std::fs::read_to_string(&file).unwrap();
        if !["nearest(", "bm25(", "rrf("]
            .iter()
            .any(|w| text.contains(w))
        {
            continue;
        }
        let stem = file.file_stem().unwrap().to_string_lossy().to_string();
        let Ok(case) = parse_case(&stem, &text) else {
            continue;
        };
        if case.runner.environments.len() != 1
            || !matches!(
                case.runner.environments[0].execution,
                Execution::Engine { .. }
            )
        {
            continue;
        }
        let Some(fixture) = case.fixture.as_ref() else {
            continue;
        };
        let Some(catalog) = parse_schema(&fixture.schema)
            .ok()
            .and_then(|s| build_catalog(&s).ok())
        else {
            continue;
        };
        let base = without_same_as_v1(&text);
        let secs = sections(&base);
        let qsteps = query_steps(&case);
        let mut twins: Vec<(usize, usize, Twin)> = Vec::new();
        let mut qi = 0usize;
        for (si, s) in secs.iter().enumerate() {
            if s.kind != "query" {
                continue;
            }
            let body = &base[s.body_start..s.body_end];
            if !body.contains("${")
                && let Some(t) = twin_of(body, &catalog)
            {
                twins.push((si, qi, t));
            }
            qi += 1;
        }
        if twins.is_empty() {
            continue;
        }
        cases_run += 1;
        let original = match run_text(&stem, &file, &base).await {
            Ok(r) => r,
            Err(e) => {
                tsv.push(format!("{name}\t-\t-\tcase\tnot-run\t{e}"));
                continue;
            }
        };
        if let Err(e) = &original.verdict {
            cases_original_red.push(format!("{name}: {e}"));
        }
        for (si, qi, t) in &twins {
            let Some((ordinal, in_loop, branch, snapshot)) = qsteps.get(*qi).cloned() else {
                continue;
            };
            let s = &secs[*si];
            let body = &base[s.body_start..s.body_end];
            let (shape, normalised) = match t {
                Twin::Refused { shape, reason } => {
                    *tally
                        .steps
                        .entry((*shape, "bridge", "refused".into()))
                        .or_default() += 1;
                    tsv.push(format!(
                        "{name}\t{ordinal}\t{}\tbridge\trefused\t{}",
                        shape.name(),
                        reason.replace(['\n', '\t'], " ")
                    ));
                    continue;
                }
                Twin::Bridged {
                    shape,
                    printed,
                    normalised,
                } => {
                    // The twin: only this step's body replaced.
                    let mut twin_text = base.clone();
                    twin_text.replace_range(
                        s.body_start..s.body_end,
                        &format!("{}{printed}\n", settings_prefix(body)),
                    );
                    twins_run += 1;
                    let (outcome, mut detail) = match run_text(&stem, &file, &twin_text).await {
                        Ok(twin) => {
                            if twin.verdict.is_ok() {
                                twins_green += 1;
                            }
                            let a: Vec<&Observed> = original
                                .observed
                                .iter()
                                .filter(|o| o.ordinal == ordinal)
                                .collect();
                            let b: Vec<&Observed> = twin
                                .observed
                                .iter()
                                .filter(|o| o.ordinal == ordinal)
                                .collect();
                            let (o, d) = compare_runs(&a, &b);
                            let gqt = match (&original.verdict, &twin.verdict) {
                                (Ok(()), Err(e)) => format!(" [twin red under GQT: {e}]"),
                                _ => String::new(),
                            };
                            (o, format!("{d}{gqt}"))
                        }
                        Err(e) => ("twin-not-run".into(), e),
                    };
                    detail = detail.chars().take(900).collect();
                    *tally
                        .steps
                        .entry((*shape, "bridge", outcome.clone()))
                        .or_default() += 1;
                    tsv.push(format!(
                        "{name}\t{ordinal}\t{}\tbridge\t{outcome}\t{}",
                        shape.name(),
                        detail.replace(['\n', '\t'], " ")
                    ));
                    (*shape, normalised)
                }
            };

            // The RFC route: the step's read under sections 2 and 4.
            let mut alt_line: Option<(String, String)> = None;
            let rfc = async {
                if in_loop {
                    return Err(("skipped".to_string(), "inside a loop".to_string()));
                }
                if snapshot.is_some() {
                    return Err(("skipped".into(), "a snapshot read".into()));
                }
                let n = normalised
                    .iter()
                    .find(|n| n.fusion.is_some())
                    .ok_or(("skipped".to_string(), "no ranking".to_string()))?;
                let end = step_end(&base, &secs, *si);
                let run = run_text(&stem, &file, &base[..end])
                    .await
                    .map_err(|e| ("not-run".to_string(), e))?;
                let session = run.session.as_ref().ok_or_else(|| {
                    (
                        "not-reached".to_string(),
                        run.verdict.clone().err().unwrap_or_default(),
                    )
                })?;
                let observed = run
                    .observed
                    .iter()
                    .rev()
                    .find(|o| o.ordinal == ordinal)
                    .ok_or(("not-reached".to_string(), String::new()))?;
                let Ok(original_rows) = &observed.rows else {
                    return Err((
                        "original-error".into(),
                        observed.rows.clone().err().unwrap(),
                    ));
                };
                let decl = match parse_query_diagnostic(body).map(|f| f.body) {
                    Ok(FileBody::Queries(d)) => d.into_iter().next(),
                    _ => None,
                }
                .ok_or(("skipped".to_string(), "unparsed".to_string()))?;
                let json = params_after(&base, &secs, *si);
                let params =
                    json_params_to_param_map(json.as_ref(), &decl.params, JsonParamMode::Standard)
                        .map_err(|e| ("skipped".to_string(), format!("params: {e}")))?;
                if shape == Shape::Rrf {
                    let alt = match rfc_rows(
                        session,
                        ReadTarget::branch(branch.as_str()),
                        &params,
                        n,
                        &catalog,
                        true,
                    )
                    .await
                    {
                        Ok(rows) => match compare(original_rows, &rows, observed.ordered) {
                            Ok(()) => ("equal".to_string(), format!("{} rows", rows.len())),
                            Err(d) => ("different".to_string(), d),
                        },
                        Err(e) => ("rfc-not-evaluated".to_string(), e),
                    };
                    alt_line.replace(alt);
                }
                let rows = rfc_rows(
                    session,
                    ReadTarget::branch(branch.as_str()),
                    &params,
                    n,
                    &catalog,
                    false,
                )
                .await
                .map_err(|e| ("rfc-not-evaluated".to_string(), e))?;
                let same_order = compare(original_rows, &rows, true).is_ok();
                match compare(original_rows, &rows, observed.ordered) {
                    Ok(()) => Ok((
                        "equal".to_string(),
                        format!(
                            "{} rows{}",
                            rows.len(),
                            if same_order {
                                ", same order"
                            } else {
                                ", order differs (the case compares unordered)"
                            }
                        ),
                    )),
                    Err(d) => Ok((
                        "different".to_string(),
                        format!(
                            "{d}; edition 2 {} rows {}; edition 3 {} rows {}",
                            original_rows.len(),
                            Value::Array(original_rows.clone()),
                            rows.len(),
                            Value::Array(rows)
                        ),
                    )),
                }
            };
            let (outcome, detail) = match rfc.await {
                Ok(x) | Err(x) => x,
            };
            let detail: String = detail.chars().take(1500).collect();
            *tally
                .steps
                .entry((shape, "rfc", outcome.clone()))
                .or_default() += 1;
            tsv.push(format!(
                "{name}\t{ordinal}\t{}\trfc\t{outcome}\t{}",
                shape.name(),
                detail.replace(['\n', '\t'], " ")
            ));
            if let Some((outcome, detail)) = alt_line.take() {
                *tally
                    .steps
                    .entry((shape, "rfc-type-arms", outcome.clone()))
                    .or_default() += 1;
                tsv.push(format!(
                    "{name}\t{ordinal}\trrf\trfc-type-arms\t{outcome}\t{}",
                    detail.replace(['\n', '\t'], " ")
                ));
            }
        }
    }
    let out = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3-exec");
    std::fs::create_dir_all(&out).unwrap();
    std::fs::write(out.join("rows.tsv"), tsv.join("\n") + "\n").unwrap();
    let mut summary = format!(
        "cases run: {cases_run}; twins run: {twins_run}, green under the GQT runner: {twins_green}; originals red: {}\n\n| shape | route | outcome | steps |\n|---|---|---|---:|\n",
        cases_original_red.len()
    );
    for ((shape, route, outcome), n) in &tally.steps {
        summary.push_str(&format!(
            "| {} | {route} | {outcome} | {n} |\n",
            shape.name()
        ));
    }
    std::fs::write(out.join("rows_summary.md"), &summary).unwrap();
    println!("{summary}");
    for r in &cases_original_red {
        println!("original red: {r}");
    }
}
