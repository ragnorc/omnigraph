//! SPIKE (`spike/gq3-instrument`): the checker behind the GQ 3 competence
//! instrument (RFC 2026-11-07-gq-3, `blocked_on` item 2; section 10, "The
//! competence instrument").
//!
//! Reads JSON lines on stdin, one job per line:
//!
//! ```json
//! {"id": "t01/e3/r1/try1", "edition": 3, "text": "gq 3;\nquery …", "truth": "gq 3;\nquery …"}
//! ```
//!
//! and writes one JSON result per line on stdout. Edition 2 text goes through
//! today's front door (`parse_query_diagnostic`, no edition line), then the
//! edition-2 type checker and lowering against `--schema`. Edition 3 text goes
//! through `parse_query3` (the parser-only build the RFC's gate names); where
//! the tree also lowers into edition 2 (`bridge3`), it is type-checked too, as
//! extra evidence.
//!
//! Semantic comparison with the ground truth: edition 2 compares the lowered
//! typed IR (`lower_query` / `lower_mutation_query`, `Debug`), edition 3 the
//! edition-3 tree after the spike printer prints and reparses both. Both
//! comparisons are made twice: `strict` (only declaration names normalised)
//! and `renamed` (every non-parameter variable, alias and declaration name
//! renamed by order of first appearance, so `$p`/`$person` and `as n`/`as
//! headcount` do not count as differences).
//!
//! Run: `cargo run -p omnigraph-compiler --example gq3_instrument -- --schema <edition-2 .pg>`.

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, Write};

use omnigraph_compiler::catalog::Catalog;
use omnigraph_compiler::error::CompilerError;
use omnigraph_compiler::query::ast::{FileBody, QueryDecl};
use omnigraph_compiler::query::ast3::*;
use omnigraph_compiler::query::diagnostic::QueryDiagnostic;
use omnigraph_compiler::query::parser::parse_query_diagnostic;
use omnigraph_compiler::query::parser3::parse_query3;
use omnigraph_compiler::query::typecheck::{CheckedQuery, typecheck_query_decl};
use omnigraph_compiler::query::{bridge3, print3};
use omnigraph_compiler::schema::parser::parse_schema;
use omnigraph_compiler::{build_catalog, lower_mutation_query, lower_query};
use serde_json::{Value, json};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let schema_path = args
        .iter()
        .position(|a| a == "--schema")
        .and_then(|i| args.get(i + 1))
        .expect("--schema <edition-2 .pg>");
    let schema_text = std::fs::read_to_string(schema_path).expect("read schema");
    let schema = parse_schema(&schema_text).expect("edition-2 schema parses");
    let catalog = build_catalog(&schema).expect("edition-2 schema builds a catalog");

    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    for line in stdin.lock().lines() {
        let line = line.expect("stdin");
        if line.trim().is_empty() {
            continue;
        }
        let job: Value = serde_json::from_str(&line).expect("job is JSON");
        let result = check(&catalog, &job);
        writeln!(out, "{}", result).expect("stdout");
        out.flush().ok();
    }
}

fn diag_json(d: &QueryDiagnostic) -> Value {
    json!({
        "kind": format!("{:?}", d.kind).to_lowercase(),
        "code": d.code.as_str(),
        "message": d.message,
        "line": d.position.map(|p| p.line),
        "column": d.position.map(|p| p.column),
        "fix": d.fix,
        "suggestion": d.suggestion.as_ref().map(|s| serde_json::to_value(s).unwrap()),
        "rendered": render(d),
    })
}

/// The diagnostic's full text, as a repair turn shows it to the agent.
fn render(d: &QueryDiagnostic) -> String {
    let mut s = format!(
        "{} error {}: {}",
        format!("{:?}", d.kind).to_lowercase(),
        d.code.as_str(),
        d.message
    );
    if let Some(p) = d.position {
        s.push_str(&format!("\n  at line {}, column {}", p.line, p.column));
    }
    if let Some(st) = &d.stage {
        s.push_str(&format!("\n  stage: {}", st.name));
        if let Some(e) = &st.expression {
            s.push_str(&format!(" ({e})"));
        }
    }
    if let Some(f) = &d.fix {
        s.push_str(&format!("\n  fix: {f}"));
    }
    if let Some(sg) = &d.suggestion {
        for e in &sg.edits {
            s.push_str(&format!(
                "\n  suggested edit: replace bytes {}..{} with {:?}",
                e.start, e.end, e.replacement
            ));
        }
    }
    s
}

fn compiler_error_json(e: &CompilerError) -> Value {
    match e {
        CompilerError::Query(d) => diag_json(d),
        other => {
            json!({"kind": "other", "code": Value::Null, "message": other.to_string(), "rendered": other.to_string()})
        }
    }
}

fn check(catalog: &Catalog, job: &Value) -> Value {
    let id = job["id"].clone();
    let edition = job["edition"].as_u64().expect("edition");
    let text = job["text"].as_str().expect("text");
    let truth = job["truth"].as_str();
    match edition {
        2 => check2(catalog, id, text, truth),
        3 => check3(catalog, id, text, truth),
        _ => panic!("edition"),
    }
}

// ---------------------------------------------------------------- edition 2

fn decls2(text: &str) -> Result<Vec<QueryDecl>, QueryDiagnostic> {
    let file = parse_query_diagnostic(text)?;
    Ok(match file.body {
        FileBody::Queries(q) => q,
        FileBody::Explain(q) => vec![q],
        _ => vec![],
    })
}

/// Type-check and lower every declaration; the IR `Debug` per declaration.
fn ir2(catalog: &Catalog, decls: &[QueryDecl]) -> Result<Vec<String>, Value> {
    let mut out = Vec::new();
    for d in decls {
        let checked = typecheck_query_decl(catalog, d).map_err(|e| compiler_error_json(&e))?;
        let ir = match checked {
            CheckedQuery::Read(r) => lower_query(catalog, &r).map(|ir| format!("{ir:?}")),
            CheckedQuery::Mutation(m) => {
                lower_mutation_query(catalog, &m).map(|ir| format!("{ir:?}"))
            }
        }
        .map_err(|e| compiler_error_json(&e))?;
        out.push(ir);
    }
    Ok(out)
}

fn params2(decls: &[QueryDecl]) -> (HashSet<String>, Vec<String>) {
    let params = decls
        .iter()
        .flat_map(|d| d.params.iter().map(|p| p.name.clone()))
        .collect();
    let names = decls.iter().map(|d| d.name.clone()).collect();
    (params, names)
}

fn check2(catalog: &Catalog, id: Value, text: &str, truth: Option<&str>) -> Value {
    let decls = match decls2(text) {
        Ok(d) => d,
        Err(d) => return json!({"id": id, "parse_ok": false, "diagnostic": diag_json(&d)}),
    };
    let n = decls.len();
    let typed = ir2(catalog, &decls);
    let mut result = json!({
        "id": id, "parse_ok": true, "decls": n,
        "type_ok": typed.is_ok(),
        "type_diagnostic": typed.as_ref().err(),
    });
    if let (Some(truth), Ok(_)) = (truth, &typed) {
        result["semantic"] = semantic2(catalog, text, truth);
    }
    result
}

fn semantic2(catalog: &Catalog, text: &str, truth: &str) -> Value {
    let level = |rename_vars: bool| -> Result<bool, String> {
        let norm = |t: &str| -> Result<Vec<String>, String> {
            let d = decls2(t).map_err(|d| format!("truth/answer reparse: {}", render(&d)))?;
            let (params, names) = params2(&d);
            let renamed = alpha(t, &params, &names, rename_vars);
            let d = decls2(&renamed).map_err(|d| format!("renamed reparse: {}", render(&d)))?;
            ir2(catalog, &d).map_err(|e| format!("renamed typecheck: {e}"))
        };
        Ok(norm(text)? == norm(truth)?)
    };
    // Rename inside the typed IR, in the IR's own order: the lowering orders
    // clauses itself, so two answers that bind the same pattern in another
    // written order, under other names, compare equal here.
    let ir_level = || -> Result<bool, String> {
        let norm = |t: &str| -> Result<String, String> {
            let d = decls2(t).map_err(|d| render(&d))?;
            let (params, names) = params2(&d);
            let renamed = alpha(t, &params, &names, false);
            let d = decls2(&renamed).map_err(|d| render(&d))?;
            let ir = ir2(catalog, &d).map_err(|e| e.to_string())?.join("\n");
            Ok(rename_quoted(&ir, &source_names(t, &params)))
        };
        Ok(norm(text)? == norm(truth)?)
    };
    json!({
        "ir_renamed": ir_level().unwrap_or(false),
        "strict": level(false).map_err(|e| e).unwrap_or(false),
        "renamed": match level(true) { Ok(b) => json!(b), Err(e) => json!(e) },
    })
}

// ---------------------------------------------------------------- edition 3

fn decls3(file: &File3) -> Vec<&Decl3> {
    match &file.body {
        Body3::Decls(d) => d.iter().collect(),
        Body3::Explain(d) => vec![d.as_ref()],
        Body3::Statement(_) => vec![],
    }
}

fn check3(catalog: &Catalog, id: Value, text: &str, truth: Option<&str>) -> Value {
    let file = match parse_query3(text) {
        Ok(f) => f,
        Err(d) => return json!({"id": id, "parse_ok": false, "diagnostic": diag_json(&d)}),
    };
    let decls = decls3(&file);
    let mut ranking_in_order_or_return = Vec::new();
    let mut new_forms = HashSet::new();
    for d in &decls {
        scan_decl(d, &mut ranking_in_order_or_return, &mut new_forms);
    }
    // Extra evidence: the part of edition 3 that has an edition-2 node.
    let lowered = bridge3::lower_file(file.clone());
    let (lower_ok, lower_diag, type_ok, type_diag) = match lowered {
        Err(d) => (false, Some(diag_json(&d)), Value::Null, Value::Null),
        Ok(qf) => {
            let ds = match qf.body {
                FileBody::Queries(q) => q,
                FileBody::Explain(q) => vec![q],
                _ => vec![],
            };
            match ir2(catalog, &ds) {
                Ok(_) => (true, None, json!(true), Value::Null),
                Err(e) => (true, None, json!(false), e),
            }
        }
    };
    let mut new_forms: Vec<_> = new_forms.into_iter().collect();
    new_forms.sort();
    let mut result = json!({
        "id": id, "parse_ok": true, "decls": decls.len(),
        "t65_ranking_call_in_order_or_return": ranking_in_order_or_return,
        "edition3_forms": new_forms,
        "lowers_to_edition2": lower_ok, "lower_diagnostic": lower_diag,
        "lowered_type_ok": type_ok, "lowered_type_diagnostic": type_diag,
        "printed": print3::print_file(&file, print3::Style::default()),
    });
    if let Some(truth) = truth {
        result["semantic"] = semantic3(text, truth);
    }
    result
}

/// The `queries fmt` rules that matter for comparing answers (RFC section 6,
/// "`queries fmt`"): an alias equal to its column's name is dropped, `asc`
/// is dropped, and (this instrument's addition) a bare `order` key that
/// names a `return` column is replaced by that column's expression.
fn fmt3(f: &mut File3) {
    if let Body3::Decls(ds) = &mut f.body {
        for d in ds {
            match &mut d.body {
                DeclBody3::Read(r) => fmt_read(r),
                DeclBody3::Write(w) => fmt_clauses(&mut w.clauses),
            }
        }
    }
}

fn implied_name(e: &Expr3) -> Option<&str> {
    match e {
        Expr3::Variable(v) => Some(v),
        Expr3::Prop { property, .. } => Some(property),
        _ => None,
    }
}

fn fmt_read(r: &mut ReadBody3) {
    fmt_clauses(&mut r.clauses);
    for p in &mut r.projections {
        if p.alias.is_some() && p.alias.as_deref() == implied_name(&p.expr) {
            p.alias = None;
        }
        fmt_expr(&mut p.expr);
    }
    let projections = r.projections.clone();
    for o in &mut r.order {
        if o.descending == Some(false) {
            o.descending = None;
        }
        if let Expr3::Ident(name) = &o.expr {
            if let Some(p) = projections.iter().find(|p| {
                p.alias.as_deref() == Some(name)
                    || (p.alias.is_none() && implied_name(&p.expr) == Some(name))
            }) {
                o.expr = p.expr.clone();
            }
        }
        fmt_expr(&mut o.expr);
    }
}

fn fmt_clauses(cs: &mut [Clause3]) {
    for c in cs {
        match c {
            Clause3::Let {
                value: LetValue3::Relation(r),
                ..
            } => fmt_read(r),
            Clause3::Let {
                value: LetValue3::Expr(e),
                ..
            }
            | Clause3::Filter(e) => fmt_expr(e),
            Clause3::From(rel) => fmt_rel(rel),
            Clause3::Not(cs) | Clause3::Optional(cs) => fmt_clauses(cs),
            _ => {}
        }
    }
}

fn fmt_rel(rel: &mut RelExpr3) {
    match rel {
        RelExpr3::Inline(r) => fmt_read(r),
        RelExpr3::Call(c) => fmt_call(c),
        RelExpr3::Variable(_) => {}
    }
}

fn fmt_call(c: &mut Call3) {
    for a in &mut c.args {
        match a {
            Arg3::Inline(r) => fmt_read(r),
            Arg3::Expr(e) => fmt_expr(e),
        }
    }
}

fn fmt_expr(e: &mut Expr3) {
    match e {
        Expr3::Call(c) => fmt_call(c),
        Expr3::Block(b) => fmt_clauses(&mut b.clauses),
        _ => {}
    }
}

fn tree3(t: &str, rename_vars: bool) -> Result<File3, String> {
    let mut f = parse_query3(t).map_err(|d| format!("parse: {}", render(&d)))?;
    fmt3(&mut f);
    let params: HashSet<String> = decls3(&f)
        .iter()
        .flat_map(|d| d.params.iter().map(|p| p.name.clone()))
        .collect();
    let names: Vec<String> = decls3(&f).iter().map(|d| d.name.clone()).collect();
    let printed = print3::print_file(&f, print3::Style::default());
    let renamed = alpha(&printed, &params, &names, rename_vars);
    let f = parse_query3(&renamed).map_err(|d| format!("renamed reparse: {}", render(&d)))?;
    let printed = print3::print_file(&f, print3::Style::default());
    let mut f = parse_query3(&printed).map_err(|d| format!("printed reparse: {}", render(&d)))?;
    fmt3(&mut f);
    Ok(f)
}

fn semantic3(text: &str, truth: &str) -> Value {
    let level = |rename: bool| -> Value {
        match (tree3(text, rename), tree3(truth, rename)) {
            (Ok(a), Ok(b)) => json!(a == b),
            (Err(e), _) => json!(format!("answer: {e}")),
            (_, Err(e)) => json!(format!("truth: {e}")),
        }
    };
    json!({"strict": level(false), "renamed": level(true)})
}

/// Record a ranking call (`nearest`, `bm25`, `rrf`, `distance`) written in
/// `order` or `return`, which the RFC refuses with `T65` at type check, a
/// stage the parser-only build does not have; and the edition-3 constructs a
/// declaration uses.
fn scan_decl(d: &Decl3, t65: &mut Vec<String>, forms: &mut HashSet<&'static str>) {
    if !d.params.iter().all(|p| p.default.is_none()) {
        forms.insert("param_default");
    }
    if !d.annotations.is_empty() {
        forms.insert("annotation");
    }
    match &d.body {
        DeclBody3::Read(r) => scan_read(r, t65, forms),
        DeclBody3::Write(w) => {
            forms.insert("write_body");
            if !w.clauses.is_empty() {
                forms.insert("write_clauses");
            }
            scan_clauses(&w.clauses, t65, forms);
            for s in &w.statements {
                match s {
                    Stmt3::Insert { assignments, .. } | Stmt3::Update { assignments, .. } => {
                        for a in assignments {
                            scan_expr(&a.value, false, t65, forms)
                        }
                    }
                    Stmt3::Delete { .. } => {}
                }
            }
        }
    }
}

const RANKINGS: &[&str] = &["nearest", "bm25", "rrf", "distance"];

fn scan_read(r: &ReadBody3, t65: &mut Vec<String>, forms: &mut HashSet<&'static str>) {
    scan_clauses(&r.clauses, t65, forms);
    if matches!(r.limit, Some(Limit3::Param(_))) {
        forms.insert("limit_param");
    }
    for p in &r.projections {
        scan_expr(&p.expr, true, t65, forms);
    }
    for o in &r.order {
        scan_expr(&o.expr, true, t65, forms);
    }
}

fn scan_clauses(cs: &[Clause3], t65: &mut Vec<String>, forms: &mut HashSet<&'static str>) {
    for c in cs {
        match c {
            Clause3::Let { value, .. } => {
                forms.insert("let");
                match value {
                    LetValue3::Relation(r) => {
                        forms.insert("let_relation");
                        scan_read(r, t65, forms)
                    }
                    LetValue3::Expr(e) => scan_expr(e, false, t65, forms),
                }
            }
            Clause3::From(rel) => {
                forms.insert("from");
                scan_rel(rel, t65, forms)
            }
            Clause3::Not(cs) => scan_clauses(cs, t65, forms),
            Clause3::Optional(cs) => {
                forms.insert("optional");
                scan_clauses(cs, t65, forms)
            }
            Clause3::Binding { prop_matches, .. } => {
                for (_, e) in prop_matches {
                    scan_expr(e, false, t65, forms)
                }
            }
            Clause3::Traversal(_) => {}
            Clause3::Filter(e) => scan_expr(e, false, t65, forms),
        }
    }
}

fn scan_rel(rel: &RelExpr3, t65: &mut Vec<String>, forms: &mut HashSet<&'static str>) {
    match rel {
        RelExpr3::Inline(r) => {
            forms.insert("inline_read");
            scan_read(r, t65, forms)
        }
        RelExpr3::Call(c) => {
            if RANKINGS.contains(&c.name.as_str()) || c.name == "union" {
                forms.insert(match c.name.as_str() {
                    "nearest" => "from_nearest",
                    "bm25" => "from_bm25",
                    "rrf" => "from_rrf",
                    "distance" => "from_distance",
                    _ => "from_union",
                });
            } else {
                forms.insert("from_declared_read");
            }
            for a in &c.args {
                match a {
                    Arg3::Inline(r) => {
                        forms.insert("inline_read");
                        scan_read(r, t65, forms)
                    }
                    Arg3::Expr(Expr3::Call(inner)) => {
                        scan_rel(&RelExpr3::Call(inner.clone()), t65, forms)
                    }
                    Arg3::Expr(e) => scan_expr(e, false, t65, forms),
                }
            }
        }
        RelExpr3::Variable(_) => {}
    }
}

fn scan_expr(
    e: &Expr3,
    scored_position: bool,
    t65: &mut Vec<String>,
    forms: &mut HashSet<&'static str>,
) {
    match e {
        Expr3::Call(c) => {
            if scored_position && RANKINGS.contains(&c.name.as_str()) {
                t65.push(c.name.clone());
            }
            for a in &c.args {
                match a {
                    Arg3::Expr(x) => scan_expr(x, scored_position, t65, forms),
                    Arg3::Inline(r) => scan_read(r, t65, forms),
                }
            }
        }
        Expr3::Block(b) => {
            forms.insert("block_value");
            scan_clauses(&b.clauses, t65, forms)
        }
        Expr3::Arith { left, right, .. } => {
            forms.insert("arithmetic");
            scan_expr(left, scored_position, t65, forms);
            scan_expr(right, scored_position, t65, forms)
        }
        Expr3::Neg(x) | Expr3::Not(x) => scan_expr(x, scored_position, t65, forms),
        Expr3::Compare { left, right, .. } | Expr3::And(left, right) | Expr3::Or(left, right) => {
            scan_expr(left, scored_position, t65, forms);
            scan_expr(right, scored_position, t65, forms)
        }
        Expr3::In { needle, list } => {
            scan_expr(needle, scored_position, t65, forms);
            scan_expr(list, scored_position, t65, forms)
        }
        Expr3::IsNull { expr, .. } => scan_expr(expr, scored_position, t65, forms),
        Expr3::AggDistinct { arg, .. } => scan_expr(arg, scored_position, t65, forms),
        _ => {}
    }
}

// ---------------------------------------------------------------- alpha renaming

/// Every `$var` (not a parameter) and every `as alias` name in a source.
fn source_names(text: &str, params: &HashSet<String>) -> HashSet<String> {
    let mut out = HashSet::new();
    let b = text.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'$' {
            let s = i + 1;
            let mut e = s;
            while e < b.len() && (b[e].is_ascii_alphanumeric() || b[e] == b'_') {
                e += 1;
            }
            if e > s && !params.contains(&text[s..e]) {
                out.insert(text[s..e].to_string());
            }
            i = e;
        } else {
            i += 1;
        }
    }
    let words: Vec<&str> = text
        .split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
        .filter(|w| !w.is_empty())
        .collect();
    for w in words.windows(2) {
        if w[0] == "as" {
            out.insert(w[1].to_string());
        }
    }
    out
}

/// Rename every quoted string in `ir` that is one of `names`, in order of
/// first appearance.
fn rename_quoted(ir: &str, names: &HashSet<String>) -> String {
    let mut map: HashMap<String, String> = HashMap::new();
    let mut out = String::with_capacity(ir.len());
    let mut rest = ir;
    while let Some(start) = rest.find('"') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        let Some(end) = after.find('"') else {
            out.push_str(&rest[start..]);
            return out;
        };
        let word = &after[..end];
        if names.contains(word) {
            let n = map.len();
            let new = map
                .entry(word.to_string())
                .or_insert_with(|| format!("v{n}"));
            out.push('"');
            out.push_str(new);
            out.push('"');
        } else {
            out.push('"');
            out.push_str(word);
            out.push('"');
        }
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    out
}

const KEYWORDS: &[&str] = &[
    "query",
    "match",
    "return",
    "order",
    "limit",
    "distinct",
    "as",
    "desc",
    "asc",
    "and",
    "or",
    "not",
    "is",
    "null",
    "in",
    "true",
    "false",
    "let",
    "from",
    "optional",
    "exists",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "insert",
    "update",
    "delete",
    "set",
    "where",
    "gq",
    "commit",
    "unattached",
    "branch",
    "contains",
    "starts_with",
];

/// Rename, in order of first appearance: every `$var` that is not a
/// parameter (when `rename_vars`), every `as alias` and its bare uses, and
/// every declaration name and its call sites. Strings and comments are kept.
fn alpha(text: &str, params: &HashSet<String>, decls: &[String], rename_vars: bool) -> String {
    let decl_map: HashMap<&str, String> = decls
        .iter()
        .enumerate()
        .map(|(i, d)| (d.as_str(), format!("decl{i}")))
        .collect();
    let mut names: HashMap<String, String> = HashMap::new();
    let mut aliases: HashSet<String> = HashSet::new();
    let b = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    let mut prev_word = String::new();
    let mut prev_char = ' ';
    let ident_start = |c: u8| c.is_ascii_alphabetic() || c == b'_';
    let ident_cont = |c: u8| c.is_ascii_alphanumeric() || c == b'_';
    while i < b.len() {
        let c = b[i];
        if c == b'"' {
            let s = i;
            i += 1;
            while i < b.len() && b[i] != b'"' {
                if b[i] == b'\\' {
                    i += 1;
                }
                i += 1;
            }
            i = (i + 1).min(b.len());
            out.push_str(&text[s..i]);
            prev_char = '"';
            continue;
        }
        if c == b'/' && b.get(i + 1) == Some(&b'/') {
            while i < b.len() && b[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        if c == b'/' && b.get(i + 1) == Some(&b'*') {
            i += 2;
            while i + 1 < b.len() && !(b[i] == b'*' && b[i + 1] == b'/') {
                i += 1;
            }
            i += 2;
            continue;
        }
        if c == b'$' && i + 1 < b.len() && ident_start(b[i + 1]) {
            let s = i + 1;
            let mut e = s;
            while e < b.len() && ident_cont(b[e]) {
                e += 1;
            }
            let name = &text[s..e];
            if rename_vars && !params.contains(name) && name != "_" {
                let n = names.len();
                let new = names
                    .entry(name.to_string())
                    .or_insert_with(|| format!("v{n}"));
                out.push('$');
                out.push_str(new);
            } else {
                out.push_str(&text[i..e]);
            }
            i = e;
            prev_char = 'v';
            prev_word.clear();
            continue;
        }
        if ident_start(c) && !(i > 0 && ident_cont(b[i - 1])) {
            let s = i;
            let mut e = s;
            while e < b.len() && ident_cont(b[e]) {
                e += 1;
            }
            let word = &text[s..e];
            let next = text[e..].chars().find(|ch| !ch.is_whitespace());
            let touches_paren = b.get(e) == Some(&b'(');
            let replaced: Option<String> = if prev_word == "query" {
                decl_map.get(word).cloned()
            } else if touches_paren && decl_map.contains_key(word) && prev_char != '.' {
                decl_map.get(word).cloned()
            } else if rename_vars && prev_word == "as" && !KEYWORDS.contains(&word) {
                aliases.insert(word.to_string());
                let n = names.len();
                Some(
                    names
                        .entry(word.to_string())
                        .or_insert_with(|| format!("v{n}"))
                        .clone(),
                )
            } else if rename_vars
                && aliases.contains(word)
                && prev_char != '.'
                && prev_char != '@'
                && prev_char != ':'
                && next != Some(':')
                && next != Some('(')
            {
                names.get(word).cloned()
            } else {
                None
            };
            out.push_str(replaced.as_deref().unwrap_or(word));
            prev_word = word.to_string();
            prev_char = 'w';
            i = e;
            continue;
        }
        let ch = text[i..].chars().next().unwrap();
        out.push(ch);
        if !ch.is_whitespace() {
            prev_char = ch;
            prev_word.clear();
        }
        i += ch.len_utf8();
    }
    out
}
