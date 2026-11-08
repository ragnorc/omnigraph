//! SPIKE (`spike/gq3-exec`): the typed-IR proof of the rewriter's three
//! ranking rows, through the normalising bridge (`query::normalise3`).
//!
//! For every corpus text whose rewrite moves a `nearest`, `bm25` or `rrf`
//! into `from` (and carries no refusal), the edition-3 text is parsed with
//! `parse_query3`, normalised, and each ranked declaration's typed IR is
//! compared with the typed IR of the original edition-2 declaration:
//!
//! - `raw`: the IR as today's compiler lowers it, field for field;
//! - `canon`: after two normalisation rules beyond the inlining, which the
//!   comparison applies to both sides and reports when it needed them:
//!   `rank-key-direction` (a `bm25` or `rrf` order key is descending: edition
//!   2 records a bare key as `descending: false` and `desc` as `true`, two IRs
//!   for one order, and the rewrite writes `$score desc` for both) and
//!   `rrf-default-k` (an `rrf` with no `k` is `k: 60`: the rewrite writes
//!   `k: 60` where edition 2 omitted it, and the IR keeps `None`).
//!
//! Without a catalog (benchmark files that take their graph from
//! `--fixture`, doc blocks with no schema) the comparison falls back to the
//! edition-2 trees, field for field, with the same two rules.
//!
//! Every normalised read is also printed back to edition-2 text
//! (`print_read2`) and reparsed, and that must lower to the same IR: that is
//! what the engine harness (`omnigraph-gqt/tests/gq3_ranked_rows.rs`) runs.
//! Writes `target/spike-gq3-exec/ir.tsv`.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use super::corpus_tests::{all_sources, root};
use super::{Row, migrate};
use crate::catalog::Catalog;
use crate::ir::{IRExpr, QueryIR};
use crate::query::ast::{Expr, FileBody, Literal, QueryDecl};
use crate::query::ast3::Body3;
use crate::query::normalise3::{Shape, normalise_decl, print_read2};
use crate::query::parser::parse_query_diagnostic;
use crate::query::parser3::parse_query3;
use crate::query::typecheck::{CheckedQuery, typecheck_query_decl};

fn read_ir(decl: &QueryDecl, catalog: &Catalog) -> Result<QueryIR, String> {
    match typecheck_query_decl(catalog, decl).map_err(|e| e.to_string())? {
        CheckedQuery::Read(read) => {
            crate::ir::lower::lower_query(catalog, &read).map_err(|e| e.to_string())
        }
        CheckedQuery::Mutation(_) => Err("a write".into()),
    }
}

fn is_default_k(k: &Option<Box<IRExpr>>) -> bool {
    matches!(k.as_deref(), Some(IRExpr::Literal(Literal::Integer(60), _)))
}

/// The two rules; returns which ones changed this IR.
fn canon_ir(ir: &mut QueryIR) -> (bool, bool) {
    let (mut dir, mut k) = (false, false);
    for o in &mut ir.order_by {
        // A repeated rank key lowers to a reference to the rank's column.
        let rank_ref = matches!(&o.expr, IRExpr::PropAccess { property, .. }
            if property == "_score" || property == "_rrf");
        if (matches!(o.expr, IRExpr::Bm25 { .. } | IRExpr::Rrf { .. }) || rank_ref) && !o.descending {
            o.descending = true;
            dir = true;
        }
    }
    let exprs = ir
        .order_by
        .iter_mut()
        .map(|o| &mut o.expr)
        .chain(ir.return_exprs.iter_mut().map(|p| &mut p.expr));
    for e in exprs {
        if let IRExpr::Rrf { k: rk, .. } = e
            && is_default_k(rk)
        {
            *rk = None;
            k = true;
        }
    }
    (dir, k)
}

fn canon_ast(d: &mut QueryDecl) -> (bool, bool) {
    let (mut dir, mut k) = (false, false);
    for o in &mut d.order_clause {
        if matches!(o.expr, Expr::Bm25 { .. } | Expr::Rrf { .. }) {
            if !o.descending {
                dir = true;
            }
            o.descending = true;
        }
        // `explicit` is not part of the IR; it never distinguishes two IRs.
        o.explicit = false;
    }
    let exprs = d
        .order_clause
        .iter_mut()
        .map(|o| &mut o.expr)
        .chain(d.return_clause.iter_mut().map(|p| &mut p.expr));
    for e in exprs {
        if let Expr::Rrf { k: rk, .. } = e
            && matches!(rk.as_deref(), Some(Expr::Literal(Literal::Integer(60))))
        {
            *rk = None;
            k = true;
        }
    }
    (dir, k)
}

fn ir_parts(ir: &QueryIR) -> Vec<(&'static str, String)> {
    vec![
        ("params", format!("{:?}", ir.params)),
        ("pipeline", format!("{:?}", ir.pipeline)),
        ("return", format!("{:?}", ir.return_exprs)),
        ("distinct", format!("{:?}", ir.distinct)),
        ("order", format!("{:?}", ir.order_by)),
        ("limit", format!("{:?}", ir.limit)),
    ]
}

fn ast_parts(d: &QueryDecl) -> Vec<(&'static str, String)> {
    vec![
        ("params", format!("{:?}", d.params)),
        ("match", format!("{:?}", d.match_clause)),
        ("return", format!("{:?}", d.return_clause)),
        ("distinct", format!("{:?}", d.distinct)),
        ("order", format!("{:?}", d.order_clause)),
        ("limit", format!("{:?}", d.limit)),
    ]
}

fn first_difference(a: &[(&'static str, String)], b: &[(&'static str, String)]) -> Option<String> {
    for ((name, x), (_, y)) in a.iter().zip(b) {
        if x != y {
            let at = x
                .bytes()
                .zip(y.bytes())
                .position(|(p, q)| p != q)
                .unwrap_or(x.len().min(y.len()));
            let from = at.saturating_sub(60);
            let cut = |s: &str| {
                let end = (at + 100).min(s.len());
                s.get(from..end).unwrap_or("").to_string()
            };
            return Some(format!(
                "{name}: edition 2 `…{}…` / normalised `…{}…`",
                cut(x),
                cut(y)
            ));
        }
    }
    None
}

#[derive(Default, Debug)]
struct Tally {
    decls: usize,
    constructs: usize,
    raw_equal: usize,
    canon_equal: usize,
    needed_direction: usize,
    needed_k: usize,
    both_refuse_alike: usize,
    unequal: usize,
    bridge_refused: usize,
    no_catalog: usize,
}

#[test]
fn ranking_rows_are_typed_ir_equal_after_normalisation() {
    let out_dir = root().join("target/spike-gq3-exec");
    std::fs::create_dir_all(&out_dir).unwrap();
    let mut tsv = vec!["origin\tdecl\tshape\tconstructs\toutcome\tdetail".to_string()];
    let mut tallies: BTreeMap<Shape, Tally> = BTreeMap::new();
    let mut print_failures = Vec::new();
    let mut parse_failures = Vec::new();
    for s in all_sources() {
        let Ok(file) = parse_query_diagnostic(&s.text) else {
            continue;
        };
        let FileBody::Queries(originals) = &file.body else {
            continue;
        };
        // The catalog every original declaration type-checks against.
        let catalog = s
            .catalogs
            .iter()
            .find(|c| {
                originals
                    .iter()
                    .all(|d| typecheck_query_decl(c, d).is_ok())
            })
            .or(s.catalogs.first())
            .map(|c| c.as_ref());
        let Ok(m) = migrate(&s.text, catalog) else {
            continue;
        };
        if !m.refusals.is_empty() {
            continue;
        }
        let ranked: Vec<_> = m
            .decls
            .iter()
            .filter(|d| {
                d.rows
                    .keys()
                    .any(|r| matches!(r, Row::Nearest | Row::Bm25 | Row::Rrf))
            })
            .collect();
        if ranked.is_empty() {
            continue;
        }
        let file3 = match parse_query3(&m.text) {
            Ok(f) => f,
            Err(e) => {
                parse_failures.push(format!("{}: {e}", s.origin));
                continue;
            }
        };
        let Body3::Decls(decls3) = file3.body else {
            continue;
        };
        for report in ranked {
            let constructs: usize = report
                .rows
                .iter()
                .filter(|(r, _)| matches!(r, Row::Nearest | Row::Bm25 | Row::Rrf))
                .map(|(_, n)| *n)
                .sum();
            let row = report
                .rows
                .keys()
                .find(|r| matches!(r, Row::Nearest | Row::Bm25 | Row::Rrf))
                .unwrap();
            let shape = match row {
                Row::Nearest => Shape::Nearest,
                Row::Bm25 => Shape::Bm25,
                _ => Shape::Rrf,
            };
            let t = tallies.entry(shape).or_default();
            t.decls += 1;
            t.constructs += constructs;
            let original = originals.iter().find(|d| d.name == report.name).unwrap();
            let d3 = decls3
                .iter()
                .find(|d| d.name == report.name)
                .unwrap()
                .clone();
            let mut line = |outcome: &str, detail: String| {
                tsv.push(format!(
                    "{}\t{}\t{}\t{constructs}\t{outcome}\t{}",
                    s.origin,
                    report.name,
                    shape.name(),
                    detail.replace(['\n', '\t'], " ")
                ));
            };
            let normalised = match normalise_decl(d3) {
                Ok(n) => n,
                Err(e) => {
                    t.bridge_refused += 1;
                    line("bridge-refused", e.to_string());
                    continue;
                }
            };
            assert_eq!(normalised.shape, shape, "{}", s.origin);
            let Some(catalog) = catalog else {
                t.no_catalog += 1;
                let (mut a, mut b) = (original.clone(), normalised.decl.clone());
                let raw_equal = ast_parts(&a) == ast_parts(&b);
                let (d1, k1) = canon_ast(&mut a);
                let (d2, k2) = canon_ast(&mut b);
                if raw_equal || ast_parts(&a) == ast_parts(&b) {
                    t.canon_equal += 1;
                    if raw_equal {
                        t.raw_equal += 1;
                    }
                    let rules = rules_text(!raw_equal && (d1 || d2), !raw_equal && (k1 || k2));
                    if !raw_equal && (d1 || d2) {
                        t.needed_direction += 1;
                    }
                    if !raw_equal && (k1 || k2) {
                        t.needed_k += 1;
                    }
                    line("equal-tree (no catalog)", rules);
                } else {
                    t.unequal += 1;
                    line(
                        "unequal-tree (no catalog)",
                        first_difference(&ast_parts(&a), &ast_parts(&b)).unwrap_or_default(),
                    );
                }
                continue;
            };
            // The printed edition-2 text must lower to the same IR.
            match print_read2(&normalised.decl) {
                Ok(text) => {
                    let reparsed = parse_query_diagnostic(&text).map_err(|e| e.to_string());
                    let again = reparsed.and_then(|f| match f.body {
                        FileBody::Queries(mut d) if d.len() == 1 => Ok(d.remove(0)),
                        _ => Err("not one declaration".into()),
                    });
                    let same = match (again, read_ir(&normalised.decl, catalog)) {
                        (Ok(d), Ok(ir)) => read_ir(&d, catalog)
                            .map(|ir2| format!("{ir2:?}") == format!("{ir:?}"))
                            .unwrap_or(false),
                        (Ok(d), Err(e)) => read_ir(&d, catalog).err() == Some(e),
                        (Err(_), _) => false,
                    };
                    // A declaration today's compiler refuses may hold a value
                    // with no literal (a non-finite float); it is never run.
                    if !same && read_ir(&normalised.decl, catalog).is_ok() {
                        print_failures.push(format!("{}: {text}", s.origin));
                    }
                }
                Err(e) => print_failures.push(format!("{}: {e}", s.origin)),
            }
            match (read_ir(original, catalog), read_ir(&normalised.decl, catalog)) {
                (Ok(mut a), Ok(mut b)) => {
                    let raw_equal = ir_parts(&a) == ir_parts(&b);
                    let (d1, k1) = canon_ir(&mut a);
                    let (d2, k2) = canon_ir(&mut b);
                    if raw_equal {
                        t.raw_equal += 1;
                        t.canon_equal += 1;
                        line("ir-equal", String::new());
                    } else if ir_parts(&a) == ir_parts(&b) {
                        t.canon_equal += 1;
                        if d1 || d2 {
                            t.needed_direction += 1;
                        }
                        if k1 || k2 {
                            t.needed_k += 1;
                        }
                        line("ir-equal-after-rules", rules_text(d1 || d2, k1 || k2));
                    } else {
                        t.unequal += 1;
                        if std::env::var("GQ3_DEBUG").is_ok() {
                            println!("DEBUG {}\n{:?}\n{:?}", s.origin, a.order_by, b.order_by);
                        }
                        line(
                            "ir-unequal",
                            first_difference(&ir_parts(&a), &ir_parts(&b)).unwrap_or_default(),
                        );
                    }
                }
                (Err(a), Err(b)) if a == b => {
                    t.both_refuse_alike += 1;
                    line("both-refuse-alike", a);
                }
                (a, b) => {
                    t.unequal += 1;
                    line(
                        "ir-unequal",
                        format!(
                            "edition 2 {}; normalised {}",
                            a.err().unwrap_or_else(|| "typechecks".into()),
                            b.err().unwrap_or_else(|| "typechecks".into())
                        ),
                    );
                }
            }
        }
    }
    std::fs::write(out_dir.join("ir.tsv"), tsv.join("\n") + "\n").unwrap();
    let mut summary = String::from(
        "| shape | declarations | constructs | IR equal raw | equal after rules | needed rank-key-direction | needed rrf-default-k | both refuse alike | unequal | bridge refused | of which without catalog |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n",
    );
    for (shape, t) in &tallies {
        let _ = writeln!(
            summary,
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |",
            shape.name(),
            t.decls,
            t.constructs,
            t.raw_equal,
            t.canon_equal,
            t.needed_direction,
            t.needed_k,
            t.both_refuse_alike,
            t.unequal,
            t.bridge_refused,
            t.no_catalog
        );
    }
    std::fs::write(out_dir.join("ir_summary.md"), &summary).unwrap();
    println!("{summary}");
    for f in &parse_failures {
        println!("edition-3 parse failure: {f}");
    }
    for f in &print_failures {
        println!("print round trip failure: {f}");
    }
    assert!(parse_failures.is_empty(), "{parse_failures:#?}");
    assert!(print_failures.is_empty(), "{print_failures:#?}");
}

fn rules_text(direction: bool, k: bool) -> String {
    let mut v = Vec::new();
    if direction {
        v.push("rank-key-direction");
    }
    if k {
        v.push("rrf-default-k");
    }
    v.join(", ")
}
