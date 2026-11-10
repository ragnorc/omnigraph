//! Edition 2 read by the one checker (docs/rfcs/2026-11-07-gq-3.md, section
//! 6, "The rewriter" and "The edition window at boot").
//!
//! An edition-2 declaration is rewritten in memory into the edition-3 form
//! the rewriter writes, and checked as that form. The rows that need no type
//! are rewritten here, on the tree: `update`/`delete … where` become a
//! binding, a filter and a statement over the bound variable, one group per
//! statement. The rows a type decides (an infix `contains` over a list or a
//! String, a positional option) and the acceptance edition 2 keeps (its
//! refusals of forms edition 3 admits) are switches at the checker's own
//! sites, each reading [`Edition`].

use std::collections::{HashMap, HashSet};

use crate::catalog::Catalog;
use crate::query::ast::{DISTANCE_COLUMN, Literal, RRF_COLUMN, SCORE_COLUMN};
use crate::query::codes::{T17, T21, T32, T33, T54, T59};
use crate::query::diagnostic::QueryDiagnostic;
use crate::query::syntax::{
    Arg, Assignment, Call, Clause, Decl, DeclBody, Edition, Expr, LetValue, Limit, Ordering,
    Projection, ReadBody, RelExpr, Stmt, WriteBody, WriteGroup,
};

/// The declaration the checker reads: an edition-2 declaration's rewrite, or
/// an edition-3 declaration unchanged.
pub(super) fn rewrite(
    catalog: &Catalog,
    decl: &Decl,
    edition: Edition,
) -> Result<Decl, QueryDiagnostic> {
    if edition == Edition::Three {
        return Ok(decl.clone());
    }
    let mut out = decl.clone();
    match &decl.body {
        DeclBody::Write(body) => out.body = DeclBody::Write(write_body(catalog, body)),
        DeclBody::Read(body) => {
            if let Some(body) = ranking(catalog, body)? {
                out.body = DeclBody::Read(body);
            }
        }
    }
    Ok(out)
}

/// Each `update`/`delete … where` its own group, opening with the binding
/// of its target type and the filter of its predicate; a run of inserts one
/// group with no clauses, so every statement runs once, as in edition 2.
fn write_body(catalog: &Catalog, body: &WriteBody) -> WriteBody {
    let mut groups: Vec<WriteGroup> = Vec::new();
    for group in &body.groups {
        let mut pending: Vec<Stmt> = Vec::new();
        let flush = |pending: &mut Vec<Stmt>, groups: &mut Vec<WriteGroup>| {
            if !pending.is_empty() {
                groups.push(WriteGroup {
                    clauses: group.clauses.clone(),
                    statements: std::mem::take(pending),
                });
            }
        };
        for statement in &group.statements {
            match statement {
                Stmt::UpdateWhere {
                    type_name,
                    assignments,
                    predicate,
                } => {
                    flush(&mut pending, &mut groups);
                    groups.push(WriteGroup {
                        clauses: target_clauses(catalog, type_name, predicate),
                        statements: vec![Stmt::Update {
                            variable: type_name.clone(),
                            assignments: assignments
                                .iter()
                                .map(|a| Assignment {
                                    property: a.property.clone(),
                                    value: a.value.clone(),
                                })
                                .collect(),
                        }],
                    });
                }
                Stmt::DeleteWhere {
                    type_name,
                    predicate,
                } => {
                    flush(&mut pending, &mut groups);
                    groups.push(WriteGroup {
                        clauses: target_clauses(catalog, type_name, predicate),
                        statements: vec![Stmt::Delete {
                            variable: type_name.clone(),
                        }],
                    });
                }
                other => pending.push(other.clone()),
            }
        }
        flush(&mut pending, &mut groups);
    }
    WriteBody { groups }
}

/// `$T: T  φ′`: the target type bound under its own name (the name edition
/// 2's predicate already reads its properties through), and the predicate
/// with an edge's `from` and `to` read as `@src` and `@dst`.
fn target_clauses(catalog: &Catalog, type_name: &str, predicate: &Expr) -> Vec<Clause> {
    let edge = catalog
        .lookup_edge_by_name(type_name)
        .is_some_and(|e| e.name == type_name);
    let mut predicate = predicate.clone();
    if edge {
        endpoints(&mut predicate, type_name);
    }
    vec![
        Clause::Binding {
            variable: type_name.to_string(),
            type_name: type_name.to_string(),
            prop_matches: Vec::new(),
        },
        Clause::Filter(predicate),
    ]
}

/// An edge predicate's `from` and `to` as the meta fields they read.
fn endpoints(e: &mut Expr, variable: &str) {
    match e {
        Expr::Prop {
            variable: v,
            property,
        } if v == variable && (property == "from" || property == "to") => {
            *property = if property == "from" { "@src" } else { "@dst" }.to_string();
        }
        Expr::Compare { left, right, .. }
        | Expr::And(left, right)
        | Expr::Or(left, right)
        | Expr::Arith { left, right, .. }
        | Expr::InfixString { left, right, .. } => {
            endpoints(left, variable);
            endpoints(right, variable);
        }
        Expr::In { needle, list, .. } => {
            endpoints(needle, variable);
            endpoints(list, variable);
        }
        Expr::Not(inner) | Expr::Neg(inner) | Expr::IsNull { expr: inner, .. } => {
            endpoints(inner, variable)
        }
        Expr::Literal(_)
        | Expr::Variable(_)
        | Expr::Prop { .. }
        | Expr::Meta(_)
        | Expr::Ident(_)
        | Expr::Call(_)
        | Expr::AggDistinct { .. }
        | Expr::Block(_) => {}
    }
}

// ---------------------------------------------------------------------------
// Rankings.

const RANKINGS: &[&str] = &["nearest", "bm25", "rrf"];

fn ranking_call(e: &Expr) -> Option<&Call> {
    match e {
        Expr::Call(c) if RANKINGS.contains(&c.name.as_str()) => Some(c),
        _ => None,
    }
}

fn arg(c: &Call, i: usize) -> Option<&Expr> {
    match c.args.get(i) {
        Some(Arg::Expr(e)) => Some(e),
        _ => None,
    }
}

/// The node column a `nearest` or `bm25` call ranks, and the column of the
/// score it projects (edition 2's executed name, `d._distance`).
fn ranked_column(c: &Call) -> Option<String> {
    match c.name.as_str() {
        "nearest" | "bm25" => match arg(c, 0) {
            Some(Expr::Prop { variable, .. }) => Some(variable.clone()),
            _ => None,
        },
        "rrf" => arg(c, 0).and_then(|a| match a {
            Expr::Call(inner) => ranked_column(inner),
            _ => None,
        }),
        _ => None,
    }
}

fn score_column(c: &Call) -> Option<String> {
    let suffix = match c.name.as_str() {
        "nearest" => DISTANCE_COLUMN,
        "bm25" => SCORE_COLUMN,
        _ => RRF_COLUMN,
    };
    ranked_column(c).map(|v| format!("{v}.{suffix}"))
}

fn typecheck(code: crate::query::diagnostic::QueryCode, message: String) -> QueryDiagnostic {
    QueryDiagnostic::typecheck(code, message)
}

/// Whether `e` holds a ranking call anywhere.
fn holds_ranking(e: &Expr) -> bool {
    if ranking_call(e).is_some() {
        return true;
    }
    let mut found = false;
    children(e, &mut |child| found |= holds_ranking(child));
    found
}

fn children(e: &Expr, f: &mut dyn FnMut(&Expr)) {
    match e {
        Expr::Compare { left, right, .. }
        | Expr::And(left, right)
        | Expr::Or(left, right)
        | Expr::Arith { left, right, .. }
        | Expr::InfixString { left, right, .. } => {
            f(left);
            f(right);
        }
        Expr::In { needle, list, .. } => {
            f(needle);
            f(list);
        }
        Expr::Not(inner) | Expr::Neg(inner) | Expr::IsNull { expr: inner, .. } => f(inner),
        Expr::AggDistinct { arg, .. } => f(arg),
        Expr::Call(c) => {
            for a in &c.args {
                if let Arg::Expr(e) = a {
                    f(e);
                }
            }
        }
        Expr::Literal(_)
        | Expr::Variable(_)
        | Expr::Prop { .. }
        | Expr::Meta(_)
        | Expr::Ident(_)
        | Expr::Block(_) => {}
    }
}

/// `e` with every occurrence of `rank` read as the score column `out`.
fn replace(e: &Expr, rank: &Expr, out: &str) -> Expr {
    if e == rank {
        return Expr::Variable(out.to_string());
    }
    let r = |x: &Expr| Box::new(replace(x, rank, out));
    match e {
        Expr::Compare { op, left, right } => Expr::Compare {
            op: *op,
            left: r(left),
            right: r(right),
        },
        Expr::And(a, b) => Expr::And(r(a), r(b)),
        Expr::Or(a, b) => Expr::Or(r(a), r(b)),
        Expr::Arith { op, left, right } => Expr::Arith {
            op: *op,
            left: r(left),
            right: r(right),
        },
        Expr::Not(x) => Expr::Not(r(x)),
        Expr::Neg(x) => Expr::Neg(r(x)),
        Expr::IsNull { expr, negated } => Expr::IsNull {
            expr: r(expr),
            negated: *negated,
        },
        Expr::In { needle, list, at } => Expr::In {
            needle: r(needle),
            list: r(list),
            at: at.clone(),
        },
        other => other.clone(),
    }
}

fn is_aggregate(e: &Expr) -> bool {
    match e {
        Expr::AggDistinct { .. } => true,
        Expr::Call(c) if ["count", "sum", "avg", "min", "max"].contains(&c.name.as_str()) => true,
        _ => {
            let mut found = false;
            children(e, &mut |child| found |= is_aggregate(child));
            found
        }
    }
}

/// The variables the clauses bind, in first-binding order.
fn bound_variables(clauses: &[Clause]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut add = |v: &str| {
        if v != "_" && !out.iter().any(|o| o == v) {
            out.push(v.to_string());
        }
    };
    for c in clauses {
        match c {
            Clause::Binding { variable, .. } => add(variable),
            Clause::Traversal(t) => {
                add(&t.0.src);
                if let Some(e) = &t.0.edge_binding {
                    add(e);
                }
                add(&t.0.dst);
            }
            _ => {}
        }
    }
    out
}

/// The node type the clauses give `var`: its binding, or a traversal end.
fn variable_type(catalog: &Catalog, clauses: &[Clause], var: &str) -> Option<String> {
    for c in clauses {
        if let Clause::Binding {
            variable,
            type_name,
            ..
        } = c
            && variable == var
        {
            return Some(type_name.clone());
        }
    }
    for c in clauses {
        if let Clause::Traversal(t) = c
            && let crate::query::ast::EdgeSelector::Named(edge) = &t.0.selector
            && let Some(e) = catalog.lookup_edge_by_name(edge)
        {
            if t.0.src == var {
                return Some(e.from_type.clone());
            }
            if t.0.dst == var {
                return Some(e.to_type.clone());
            }
        }
    }
    None
}

/// T59: the two bindings an `rrf` ranks, connected by a traversal.
fn refuse_rrf_over_one_component(rank: &Call, clauses: &[Clause]) -> Result<(), QueryDiagnostic> {
    let ranked: Vec<String> = rank
        .args
        .iter()
        .filter_map(|a| match a {
            Arg::Expr(Expr::Call(c)) => ranked_column(c),
            _ => None,
        })
        .collect();
    let [first, second] = ranked.as_slice() else {
        return Ok(());
    };
    if first == second {
        return Ok(());
    }
    let mut adjacent: HashMap<&str, Vec<&str>> = HashMap::new();
    for c in clauses {
        if let Clause::Traversal(t) = c {
            let (src, dst) = (t.0.src.as_str(), t.0.dst.as_str());
            if src != "_" && dst != "_" {
                adjacent.entry(src).or_default().push(dst);
                adjacent.entry(dst).or_default().push(src);
            }
        }
    }
    let mut reached = HashSet::from([first.as_str()]);
    let mut frontier = vec![first.as_str()];
    while let Some(var) = frontier.pop() {
        for &next in adjacent.get(var).into_iter().flatten() {
            if reached.insert(next) {
                frontier.push(next);
            }
        }
    }
    if !reached.contains(second.as_str()) {
        return Ok(());
    }
    Err(typecheck(
        T59,
        format!(
            "`rrf()` ranks `${first}` and `${second}`, which one traversal connects; a ranking starts the traversal at the binding it ranks, and one traversal cannot start at both"
        ),
    ))
}

/// Edition 2's rankings rewritten as the rewriter writes them: the leading
/// order key's retrieval drawn with `from` over the population the `match`
/// states, its score read as a column wherever the key repeats; `None` when
/// the read ranks nothing.
fn ranking(catalog: &Catalog, body: &ReadBody) -> Result<Option<ReadBody>, QueryDiagnostic> {
    let in_return = body.projections.iter().any(|p| holds_ranking(&p.expr));
    let in_order = body.order.iter().any(|o| holds_ranking(&o.expr));
    if !in_return && !in_order {
        return Ok(None);
    }
    let lead = body
        .order
        .first()
        .and_then(|o| ranking_call(&o.expr).map(|c| (o, c)));
    // A projected rank repeats the leading order key's retrieval (T33); under
    // an aggregate it is refused (T32).
    for p in &body.projections {
        if let Expr::Call(c) = &p.expr
            && ["count", "sum", "avg", "min", "max"].contains(&c.name.as_str())
            && let Some(inner) = arg(c, 0).and_then(ranking_call)
        {
            return Err(typecheck(
                T32,
                format!(
                    "`{}` under `{}` in `return`: a retrieval selects the rows an aggregate counts; state the filter instead",
                    inner.name, c.name
                ),
            ));
        }
        if let Some(c) = ranking_call(&p.expr) {
            let executed = lead.is_some_and(|(o, _)| o.expr == p.expr);
            if !executed {
                return Err(typecheck(
                    T33,
                    format!(
                        "`{}` in `return` must repeat the retrieval stated as the leading `order` key; the projection reads the score that ordering computed",
                        c.name
                    ),
                ));
            }
            if score_column(c).is_none() {
                return Err(typecheck(
                    T33,
                    format!(
                        "`{}` projects its score only over a property field; name the property the retrieval ranks",
                        c.name
                    ),
                ));
            }
        }
    }
    let Some((lead_order, rank)) = lead else {
        // A ranking nested in a non-leading key or an expression is the
        // checker's own refusal.
        return Ok(None);
    };
    if matches!(rank.name.as_str(), "bm25" | "rrf") && lead_order.descending == Some(false) {
        return Err(typecheck(
            T54,
            format!(
                "`{}` ranks most relevant first; its order key does not accept the asc modifier",
                rank.name
            ),
        ));
    }
    if rank.name == "rrf" {
        refuse_rrf_over_one_component(rank, &body.clauses)?;
    }
    let limit = match body.limit {
        Some(Limit::Literal(n)) => Some(n),
        _ => None,
    };
    if rank.name == "rrf" && limit.is_none() {
        return Err(typecheck(
            T21,
            "rrf ordering requires a limit clause".to_string(),
        ));
    }
    if rank.name == "nearest" && limit.is_none() {
        return Err(typecheck(
            T17,
            "nearest ordering requires a limit clause".to_string(),
        ));
    }
    let Some(ranked) = ranked_column(rank) else {
        return Ok(None);
    };
    let aggregate = body.projections.iter().any(|p| is_aggregate(&p.expr));
    let population = |clauses: Vec<Clause>| ReadBody {
        projections: bound_variables(&clauses)
            .into_iter()
            .map(|v| Projection {
                expr: Expr::Variable(v),
                alias: None,
            })
            .collect(),
        clauses,
        distinct: false,
        order: Vec::new(),
        limit: None,
    };
    let k = |n: Option<u64>| {
        (
            "k".to_string(),
            Expr::Literal(Literal::Integer(n.unwrap_or(100) as i128)),
        )
    };
    let (clauses, out) = match rank.name.as_str() {
        "nearest" | "bm25" => {
            let mut args = vec![Arg::Inline(Box::new(population(body.clauses.clone())))];
            args.extend(rank.args.iter().cloned());
            let options = if rank.name == "nearest" {
                vec![k(limit)]
            } else {
                Vec::new()
            };
            let out = if rank.name == "nearest" {
                "distance"
            } else {
                "score"
            };
            (
                vec![Clause::From(RelExpr::Call(Call {
                    name: rank.name.clone(),
                    args,
                    options,
                }))],
                out,
            )
        }
        _ => {
            // The arms rank the whole type of the ranked binding, as edition
            // 2's `rrf` does; the rest of the pattern follows, correlated on it.
            let Some(type_name) = variable_type(catalog, &body.clauses, &ranked) else {
                return Ok(None);
            };
            let all = "_all".to_string();
            let mut arms = Vec::new();
            let mut options = Vec::new();
            for (i, a) in rank.args.iter().enumerate() {
                match a {
                    Arg::Expr(Expr::Call(c)) if i < 2 => {
                        let mut args = vec![Arg::Expr(Expr::Variable(all.clone()))];
                        args.extend(c.args.iter().cloned());
                        arms.push(Arg::Expr(Expr::Call(Call {
                            name: c.name.clone(),
                            args,
                            options: if c.name == "nearest" {
                                vec![k(limit)]
                            } else {
                                Vec::new()
                            },
                        })));
                    }
                    Arg::Expr(e) if i == 2 => options.push(("k".to_string(), e.clone())),
                    _ => return Ok(None),
                }
            }
            let mut clauses = vec![
                Clause::Let {
                    name: all.clone(),
                    value: LetValue::Relation(Box::new(ReadBody {
                        clauses: vec![Clause::Binding {
                            variable: ranked.clone(),
                            type_name,
                            prop_matches: Vec::new(),
                        }],
                        distinct: false,
                        projections: vec![Projection {
                            expr: Expr::Variable(ranked.clone()),
                            alias: None,
                        }],
                        order: Vec::new(),
                        limit: None,
                    })),
                },
                Clause::From(RelExpr::Call(Call {
                    name: "rrf".into(),
                    args: arms,
                    options,
                })),
            ];
            clauses.extend(body.clauses.iter().cloned());
            (clauses, "score")
        }
    };
    let rank_expr = Expr::Call(rank.clone());
    let projections = body
        .projections
        .iter()
        .map(|p| Projection {
            alias: match (&p.alias, ranking_call(&p.expr)) {
                (None, Some(c)) => score_column(c),
                (alias, _) => alias.clone(),
            },
            expr: replace(&p.expr, &rank_expr, out),
        })
        .collect();
    let mut order: Vec<Ordering> = body
        .order
        .iter()
        .skip(1)
        .map(|o| Ordering {
            expr: replace(&o.expr, &rank_expr, out),
            descending: o.descending,
        })
        .collect();
    // Under an aggregate or `distinct` the rank key leaves `order`; the
    // window still takes `limit`'s value.
    if !aggregate && !body.distinct {
        order.insert(
            0,
            Ordering {
                expr: Expr::Variable(out.to_string()),
                descending: Some(rank.name != "nearest"),
            },
        );
    }
    Ok(Some(ReadBody {
        clauses,
        distinct: body.distinct,
        projections,
        order,
        limit: body.limit.clone(),
    }))
}
