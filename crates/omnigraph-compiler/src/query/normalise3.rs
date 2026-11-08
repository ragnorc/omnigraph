//! SPIKE (`spike/gq3-exec`): the normalising bridge of RFC 2026-11-07-gq-3,
//! section 6 ("The rewriter"): "for the `nearest` and `bm25` rows it holds
//! after a stated normalisation that inlines a `from` whose relation is a
//! ranking over the whole `match`".
//!
//! [`normalise_decl`] recognises, on the edition-3 parse tree, the exact read
//! shapes `omnigraph queries migrate` emits for the three ranking rows and
//! lowers each into the edition-2 tree whose typed IR today's compiler
//! produces for the original text: the population's clauses become the one
//! `match`, and the ranking returns to `order` (and `return`) as the
//! edition-2 expression, in place of the `$distance` or `$score` column.
//!
//! ```text
//! match { from nearest({ match { P } return { <vars of P> } }, $v.f, q, k: n) }
//! return { …$distance… }  order { $distance, … }  limit n
//!   ==>  match { P }  return { …nearest($v.f, q)… }  order { nearest($v.f, q), … }  limit n
//!
//! match { let $p = { match { P } return { … } }
//!         from rrf(nearest($p, $v.e, q1, k: n), bm25($p, $v.t, q2), k: K) }
//! return { …$score… }  order { $score desc }  limit n
//!   ==>  match { P }  return { …rrf(…)… }  order { rrf(nearest($v.e, q1), bm25($v.t, q2), K) desc }  limit n
//! ```
//!
//! Each recognition states the side conditions under which the inlining is
//! sound (the window equals the edition-2 window, the population is the whole
//! `match`, nothing follows the ranking's `from`), and refuses every other
//! edition-3 shape with `T60` naming the condition. Reads with no ranking
//! `from` go through [`super::bridge3::lower_decl`] unchanged.

use std::collections::BTreeSet;
use std::fmt::Write as _;

use super::ast::*;
use super::ast3::*;
use super::bridge3::{self, Scope};
use super::codes::T60;
use super::diagnostic::QueryDiagnostic;

type R<T> = std::result::Result<T, QueryDiagnostic>;

/// The edition-2 nearest arm window when the read has no `limit`
/// (`omnigraph_planner::optimizer::RRF_NEAREST_ARM_K`).
pub const RRF_NEAREST_ARM_K: u64 = 100;
/// `rrf`'s fused `k` when edition 2 omits it.
pub const RRF_DEFAULT_K: i128 = 60;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Shape {
    /// No ranking `from`: lowered by the parser spike's bridge as it is.
    Plain,
    Nearest,
    Bm25,
    Rrf,
}

impl Shape {
    pub fn name(self) -> &'static str {
        match self {
            Shape::Plain => "plain",
            Shape::Nearest => "nearest",
            Shape::Bm25 => "bm25",
            Shape::Rrf => "rrf",
        }
    }
}

/// One fusion arm as the edition-3 text spelled it.
#[derive(Debug, Clone)]
pub struct Arm {
    /// `nearest` or `bm25`.
    pub kind: &'static str,
    pub variable: String,
    pub property: String,
    pub query: Expr,
    /// The arm's window, `k: n` on a `nearest` arm.
    pub k: Option<u64>,
}

/// What the bridge recognised besides the lowered declaration: the parts of
/// a fusion that the edition-2 tree flattens, so an executor of the RFC's
/// `Fuse` can run the arms itself.
#[derive(Debug, Clone)]
pub struct Fusion {
    pub target: String,
    /// The population's clauses, lowered.
    pub population: Vec<Clause>,
    pub arms: Vec<Arm>,
    pub k: i128,
    pub weights: Option<Vec<f64>>,
}

#[derive(Debug, Clone)]
pub struct Normalised {
    pub decl: QueryDecl,
    pub shape: Shape,
    /// The ranking as the edition-3 text states it: one arm for `nearest`
    /// and `bm25`, the arms of an `rrf`; `None` for [`Shape::Plain`].
    pub fusion: Option<Fusion>,
}

fn refuse(what: impl Into<String>) -> QueryDiagnostic {
    QueryDiagnostic::typecheck(
        T60,
        format!(
            "{}: the normalising bridge has no edition-2 tree for this shape",
            what.into()
        ),
    )
}

/// Every declaration of an edition-3 file, normalised.
pub fn normalise_file(file: File3) -> R<(QueryFile, Vec<Normalised>)> {
    match file.body {
        Body3::Decls(decls) => {
            let normalised = decls
                .into_iter()
                .map(normalise_decl)
                .collect::<R<Vec<_>>>()?;
            Ok((
                QueryFile {
                    settings: file.settings,
                    body: FileBody::Queries(normalised.iter().map(|n| n.decl.clone()).collect()),
                },
                normalised,
            ))
        }
        other => Ok((
            bridge3::lower_file(File3 {
                settings: file.settings,
                body: other,
            })?,
            Vec::new(),
        )),
    }
}

/// The ranking a read's first clause draws from, recognised.
enum Ranked {
    Single {
        kind: &'static str,
        population: ReadBody3,
        variable: String,
        property: String,
        query: Expr3,
        k: Option<u64>,
    },
    Fused {
        let_name: String,
        population: ReadBody3,
        arms: Vec<(&'static str, String, String, Expr3, Option<u64>)>,
        k: i128,
        weights: Option<Vec<f64>>,
    },
}

pub fn normalise_decl(decl: Decl3) -> R<Normalised> {
    let DeclBody3::Read(body) = &decl.body else {
        return plain(decl);
    };
    let Some(ranked) = recognise(&body.clauses)? else {
        return plain(decl);
    };
    let mut decl = decl;
    let DeclBody3::Read(body) = std::mem::replace(
        &mut decl.body,
        DeclBody3::Write(WriteBody3 {
            clauses: Vec::new(),
            statements: Vec::new(),
        }),
    ) else {
        unreachable!()
    };
    let param_names: BTreeSet<String> = decl.params.iter().map(|p| p.name.clone()).collect();
    let limit = match &body.limit {
        None => None,
        Some(Limit3::Literal(n)) => Some(*n),
        Some(Limit3::Param(p)) => return Err(refuse(format!("`limit ${p}`"))),
    };
    let (population, score_name, shape) = match &ranked {
        Ranked::Single {
            kind, population, ..
        } => (
            population.clone(),
            if *kind == "nearest" {
                "distance"
            } else {
                "score"
            },
            if *kind == "nearest" {
                Shape::Nearest
            } else {
                Shape::Bm25
            },
        ),
        Ranked::Fused { population, .. } => (population.clone(), "score", Shape::Rrf),
    };
    let pop_vars = whole_match_population(&population)?;
    if pop_vars.contains(score_name) {
        return Err(refuse(format!(
            "the population binds `${score_name}`, the ranking's own column (T68)"
        )));
    }

    // Lower the read with the population's clauses as its `match` and the
    // score column left as a variable; then put the ranking in its place.
    let outer = ReadBody3 {
        clauses: population.clauses.clone(),
        distinct: body.distinct,
        projections: body.projections.clone(),
        order: body.order.clone(),
        limit: body.limit.clone(),
    };
    decl.body = DeclBody3::Read(outer);
    let mut out = bridge3::lower_decl(decl)?;

    let (rank, fusion) = match ranked {
        Ranked::Single {
            kind,
            variable,
            property,
            query,
            k,
            ..
        } => {
            if !pop_vars.contains(&variable) {
                return Err(refuse(format!(
                    "the ranked column `${variable}` is not a column of the population"
                )));
            }
            let query = bridge3::lower_expr(query, Scope::Alias)?;
            let rank = if kind == "nearest" {
                // The window is edition 2's only when it equals `limit`.
                match (k, limit) {
                    (Some(k), Some(l)) if k == l => {}
                    (Some(k), l) => {
                        return Err(refuse(format!(
                            "`nearest(…, k: {k})` with {}: edition 2 sizes the window by `limit`",
                            l.map_or("no `limit`".to_string(), |l| format!("`limit {l}`"))
                        )));
                    }
                    (None, _) => return Err(refuse("`nearest` without `k` (T17)")),
                }
                Expr::Nearest {
                    variable: variable.clone(),
                    property: property.clone(),
                    query: Box::new(query.clone()),
                }
            } else {
                if k.is_some() {
                    return Err(refuse("`bm25` takes no `k`"));
                }
                Expr::Bm25 {
                    field: Box::new(Expr::PropAccess {
                        variable: variable.clone(),
                        property: property.clone(),
                    }),
                    query: Box::new(query.clone()),
                }
            };
            let arm_query = match &rank {
                Expr::Nearest { query, .. } => (**query).clone(),
                Expr::Bm25 { query, .. } => (**query).clone(),
                _ => unreachable!(),
            };
            let ranking = Fusion {
                target: variable.clone(),
                population: out.match_clause.clone(),
                arms: vec![Arm {
                    kind,
                    variable,
                    property,
                    query: arm_query,
                    k,
                }],
                k: RRF_DEFAULT_K,
                weights: None,
            };
            (rank, Some(ranking))
        }
        Ranked::Fused {
            let_name,
            arms,
            k,
            weights,
            ..
        } => {
            if arms.len() != 2 {
                return Err(refuse(format!(
                    "an `rrf` of {} arms: edition 2 fuses exactly two",
                    arms.len()
                )));
            }
            if let Some(w) = &weights
                && w.iter().any(|w| *w != 1.0)
            {
                return Err(refuse("`rrf(…, weights: …)` other than 1.0"));
            }
            let target = arms[0].1.clone();
            if arms.iter().any(|a| a.1 != target) {
                return Err(refuse(
                    "`rrf` arms over different node columns (an edition-3 type error)",
                ));
            }
            if !pop_vars.contains(&target) {
                return Err(refuse(format!(
                    "the fused column `${target}` is not a column of the population"
                )));
            }
            if arms[0].0 == arms[1].0
                && arms[0].2 == arms[1].2
                && print3::expr(&arms[0].3) == print3::expr(&arms[1].3)
            {
                return Err(refuse(
                    "duplicate `rrf` arms (an edition-3 type error, section 4 Fusion)",
                ));
            }
            // After `rrf` only the target and `score` are in scope.
            let mut allowed: BTreeSet<String> = param_names.clone();
            allowed.insert(target.clone());
            allowed.insert("score".to_string());
            for p in &body.projections {
                out_of_scope(&p.expr, &allowed, &let_name)?;
            }
            for o in &body.order {
                out_of_scope(&o.expr, &allowed, &let_name)?;
            }
            let window = limit.unwrap_or(RRF_NEAREST_ARM_K);
            let mut lowered = Vec::new();
            for (kind, variable, property, query, arm_k) in arms {
                let query = bridge3::lower_expr(query, Scope::Alias)?;
                match (kind, arm_k) {
                    ("nearest", Some(k)) if k == window => {}
                    ("nearest", Some(k)) => {
                        return Err(refuse(format!(
                            "a `nearest` arm with `k: {k}` where edition 2's arm window is {window} ({})",
                            if limit.is_some() {
                                "the read's `limit`"
                            } else {
                                "RRF_NEAREST_ARM_K"
                            }
                        )));
                    }
                    ("nearest", None) => return Err(refuse("a `nearest` arm without `k` (T17)")),
                    (_, Some(_)) => return Err(refuse("a `bm25` arm takes no `k`")),
                    _ => {}
                }
                lowered.push(Arm {
                    kind,
                    variable,
                    property,
                    query,
                    k: arm_k,
                });
            }
            let arm_expr = |a: &Arm| -> Expr {
                if a.kind == "nearest" {
                    Expr::Nearest {
                        variable: a.variable.clone(),
                        property: a.property.clone(),
                        query: Box::new(a.query.clone()),
                    }
                } else {
                    Expr::Bm25 {
                        field: Box::new(Expr::PropAccess {
                            variable: a.variable.clone(),
                            property: a.property.clone(),
                        }),
                        query: Box::new(a.query.clone()),
                    }
                }
            };
            let rank = Expr::Rrf {
                primary: Box::new(arm_expr(&lowered[0])),
                secondary: Box::new(arm_expr(&lowered[1])),
                k: Some(Box::new(Expr::Literal(Literal::Integer(k)))),
            };
            let fusion = Fusion {
                target,
                population: out.match_clause.clone(),
                arms: lowered,
                k,
                weights,
            };
            (rank, Some(fusion))
        }
    };

    // The score column becomes the ranking, wherever the read names it.
    for p in &mut out.return_clause {
        substitute(&mut p.expr, score_name, &rank);
    }
    let key = out
        .order_clause
        .iter()
        .position(|o| o.expr == Expr::Variable(score_name.to_string()));
    let aggregate = out
        .return_clause
        .iter()
        .any(|p| contains_aggregate(&p.expr));
    match key {
        Some(0) => {
            let o = &mut out.order_clause[0];
            if shape == Shape::Nearest {
                if o.descending {
                    return Err(refuse(
                        "`order { $distance desc }`: edition 2 has no farthest-first window",
                    ));
                }
                *o = Ordering {
                    expr: rank.clone(),
                    descending: false,
                    explicit: false,
                };
            } else {
                // `$score desc` is edition 2's bare key or `desc`; `$score`
                // ascending is edition 2's `asc`, which T54 refuses.
                o.expr = rank.clone();
                o.explicit = true;
            }
        }
        Some(_) => {
            return Err(refuse(format!(
                "`${score_name}` is not the leading order key: edition 2 ranks by its leading key"
            )));
        }
        None if aggregate => {
            // Section 6: under an aggregate the key left `order`; edition 2
            // carried it as the leading key, selecting and ordering nothing.
            out.order_clause.insert(
                0,
                Ordering {
                    expr: rank.clone(),
                    descending: shape != Shape::Nearest,
                    explicit: shape != Shape::Nearest,
                },
            );
        }
        None if shape == Shape::Bm25 => {}
        None => {
            return Err(refuse(format!(
                "a `{}` window that no `order {{ ${score_name} }}` orders",
                shape.name()
            )));
        }
    }
    for o in out.order_clause.iter_mut().skip(1) {
        substitute(&mut o.expr, score_name, &rank);
    }
    Ok(Normalised {
        decl: out,
        shape,
        fusion,
    })
}

fn plain(decl: Decl3) -> R<Normalised> {
    // A ranking `from` anywhere else is a shape the bridge does not know.
    if let DeclBody3::Read(body) = &decl.body
        && body.clauses.iter().any(|c| match c {
            Clause3::From(RelExpr3::Call(c)) => {
                matches!(c.name.as_str(), "nearest" | "bm25" | "rrf" | "distance")
            }
            _ => false,
        })
    {
        return Err(refuse(
            "a ranking `from` that is not the read's first and only clause",
        ));
    }
    Ok(Normalised {
        decl: bridge3::lower_decl(decl)?,
        shape: Shape::Plain,
        fusion: None,
    })
}

fn recognise(clauses: &[Clause3]) -> R<Option<Ranked>> {
    match clauses {
        [Clause3::From(RelExpr3::Call(call))]
            if matches!(call.name.as_str(), "nearest" | "bm25") =>
        {
            let kind = if call.name == "nearest" {
                "nearest"
            } else {
                "bm25"
            };
            let [Arg3::Inline(population), Arg3::Expr(field), Arg3::Expr(query)] =
                call.args.as_slice()
            else {
                return Err(refuse(format!(
                    "`{kind}` whose arguments are not (inline read, $x.f, query)"
                )));
            };
            let (variable, property) = ranked_column(field, kind)?;
            let k = window(&call.options, kind)?;
            Ok(Some(Ranked::Single {
                kind,
                population: (**population).clone(),
                variable,
                property,
                query: query.clone(),
                k,
            }))
        }
        [
            Clause3::Let {
                name,
                value: LetValue3::Relation(population),
            },
            Clause3::From(RelExpr3::Call(call)),
        ] if call.name == "rrf" => {
            let mut arms = Vec::new();
            for a in &call.args {
                let Arg3::Expr(Expr3::Call(arm)) = a else {
                    return Err(refuse("an `rrf` arm that is not a ranking call"));
                };
                let kind = match arm.name.as_str() {
                    "nearest" => "nearest",
                    "bm25" => "bm25",
                    other => return Err(refuse(format!("an `rrf` arm `{other}(…)`"))),
                };
                let [Arg3::Expr(Expr3::Variable(p)), Arg3::Expr(field), Arg3::Expr(query)] =
                    arm.args.as_slice()
                else {
                    return Err(refuse(format!(
                        "an `rrf` arm `{kind}` whose arguments are not (${name}, $x.f, query)"
                    )));
                };
                if p != name {
                    return Err(refuse(format!(
                        "an `rrf` arm over `${p}`, not the named population `${name}`"
                    )));
                }
                let (variable, property) = ranked_column(field, kind)?;
                let k = window(&arm.options, kind)?;
                arms.push((kind, variable, property, query.clone(), k));
            }
            let mut k = RRF_DEFAULT_K;
            let mut weights = None;
            for (option, value) in &call.options {
                match (option.as_str(), value) {
                    ("k", Expr3::Literal(Literal::Integer(n))) => k = *n,
                    ("weights", Expr3::Literal(Literal::List(items))) => {
                        weights = Some(
                            items
                                .iter()
                                .map(|l| match l {
                                    Literal::Float(f) => Ok(*f),
                                    Literal::Integer(i) => Ok(*i as f64),
                                    _ => Err(refuse("a non-numeric `rrf` weight")),
                                })
                                .collect::<R<Vec<_>>>()?,
                        )
                    }
                    (other, _) => {
                        return Err(refuse(format!("`rrf` option `{other}` with this value")));
                    }
                }
            }
            Ok(Some(Ranked::Fused {
                let_name: name.clone(),
                population: (**population).clone(),
                arms,
                k,
                weights,
            }))
        }
        [Clause3::From(RelExpr3::Call(call)), ..]
            if matches!(call.name.as_str(), "nearest" | "bm25" | "rrf" | "distance") =>
        {
            Err(refuse(format!(
                "clauses after `from {}(…)`: the population is not the whole `match`",
                call.name
            )))
        }
        _ => Ok(None),
    }
}

fn ranked_column(field: &Expr3, kind: &str) -> R<(String, String)> {
    match field {
        Expr3::Prop { variable, property } => Ok((variable.clone(), property.clone())),
        Expr3::Ident(name) => Err(refuse(format!(
            "`{kind}(…, {name}, …)`: a bare field name does not say which column it ranks (the RFC writes `$x.{name}`)"
        ))),
        _ => Err(refuse(format!(
            "`{kind}` whose second argument is not `$x.f`"
        ))),
    }
}

fn window(options: &[(String, Expr3)], kind: &str) -> R<Option<u64>> {
    let mut k = None;
    for (option, value) in options {
        match (option.as_str(), value) {
            ("k", Expr3::Literal(Literal::Integer(n))) if *n > 0 => {
                k = Some(u64::try_from(*n).map_err(|_| refuse("`k` out of range"))?)
            }
            ("recall", _) => {
                return Err(refuse(format!(
                    "`{kind}(…, recall: …)`: edition 2 declares no recall"
                )));
            }
            (other, _) => {
                return Err(refuse(format!("`{kind}` option `{other}` with this value")));
            }
        }
    }
    Ok(k)
}

/// The population must be the whole `match`: its `return` lists bare
/// variables, every one the pattern binds at top level and nothing else, and
/// it has no `distinct`, `order` or `limit`.
fn whole_match_population(population: &ReadBody3) -> R<BTreeSet<String>> {
    if population.distinct || !population.order.is_empty() || population.limit.is_some() {
        return Err(refuse(
            "a population with `distinct`, `order` or `limit` is not the whole `match`",
        ));
    }
    let mut returned = BTreeSet::new();
    for p in &population.projections {
        match (&p.expr, &p.alias) {
            (Expr3::Variable(v), None) => {
                if !returned.insert(v.clone()) {
                    return Err(refuse(format!("the population returns `${v}` twice")));
                }
            }
            _ => {
                return Err(refuse(
                    "a population whose `return` is not bare variables is not the whole `match`",
                ));
            }
        }
    }
    let mut bound = BTreeSet::new();
    for c in &population.clauses {
        match c {
            Clause3::Binding { variable, .. } => {
                bound.insert(variable.clone());
            }
            Clause3::Traversal(TraversalEq(t)) => {
                bound.insert(t.src.clone());
                bound.insert(t.dst.clone());
                if let Some(e) = &t.edge_binding {
                    bound.insert(e.clone());
                }
            }
            Clause3::Let { .. } | Clause3::From(_) | Clause3::Optional(_) => {
                return Err(refuse(
                    "a population with `let`, `from` or `optional` has no edition-2 match",
                ));
            }
            _ => {}
        }
    }
    if bound != returned {
        let missing: Vec<String> = bound.difference(&returned).map(|v| format!("${v}")).collect();
        let extra: Vec<String> = returned.difference(&bound).map(|v| format!("${v}")).collect();
        return Err(refuse(format!(
            "the population's `return` is not every variable its pattern binds (missing {missing:?}, extra {extra:?})"
        )));
    }
    Ok(bound)
}

fn out_of_scope(e: &Expr3, allowed: &BTreeSet<String>, let_name: &str) -> R<()> {
    let mut bad = None;
    visit3(e, &mut |e| {
        let v = match e {
            Expr3::Variable(v) => Some(v),
            Expr3::Prop { variable, .. } => Some(variable),
            _ => None,
        };
        if let Some(v) = v
            && !allowed.contains(v)
            && bad.is_none()
        {
            bad = Some(v.clone());
        }
    });
    match bad {
        Some(v) => Err(refuse(format!(
            "`${v}` after `from rrf(${let_name}, …)`: a fusion yields its target and `score` only (T68 in edition 3; the RFC's rewriter draws the population again with a correlated `from`)"
        ))),
        None => Ok(()),
    }
}

fn visit3(e: &Expr3, f: &mut impl FnMut(&Expr3)) {
    f(e);
    match e {
        Expr3::Call(c) => {
            for a in &c.args {
                if let Arg3::Expr(e) = a {
                    visit3(e, f);
                }
            }
            for (_, v) in &c.options {
                visit3(v, f);
            }
        }
        Expr3::AggDistinct { arg, .. } | Expr3::Neg(arg) | Expr3::Not(arg) => visit3(arg, f),
        Expr3::IsNull { expr, .. } => visit3(expr, f),
        Expr3::Arith { left, right, .. } | Expr3::Compare { left, right, .. } => {
            visit3(left, f);
            visit3(right, f);
        }
        Expr3::In { needle, list } => {
            visit3(needle, f);
            visit3(list, f);
        }
        Expr3::And(l, r) | Expr3::Or(l, r) => {
            visit3(l, f);
            visit3(r, f);
        }
        Expr3::Block(b) => {
            if let Some(a) = &b.arg {
                visit3(a, f);
            }
        }
        _ => {}
    }
}

fn contains_aggregate(e: &Expr) -> bool {
    let mut found = false;
    visit2(e, &mut |e| found |= matches!(e, Expr::Aggregate { .. }));
    found
}

fn visit2(e: &Expr, f: &mut impl FnMut(&Expr)) {
    f(e);
    match e {
        Expr::Nearest { query, .. } => visit2(query, f),
        Expr::Search { field, query }
        | Expr::MatchText { field, query }
        | Expr::Bm25 { field, query } => {
            visit2(field, f);
            visit2(query, f);
        }
        Expr::Fuzzy {
            field,
            query,
            max_edits,
        } => {
            visit2(field, f);
            visit2(query, f);
            if let Some(m) = max_edits {
                visit2(m, f);
            }
        }
        Expr::Rrf {
            primary,
            secondary,
            k,
        } => {
            visit2(primary, f);
            visit2(secondary, f);
            if let Some(k) = k {
                visit2(k, f);
            }
        }
        Expr::Aggregate { arg, .. } | Expr::Not(arg) => visit2(arg, f),
        Expr::IsNull { expr, .. } => visit2(expr, f),
        Expr::Binary { left, right, .. } => {
            visit2(left, f);
            visit2(right, f);
        }
        Expr::In { needle, list } => {
            visit2(needle, f);
            visit2(list, f);
        }
        Expr::Now
        | Expr::PropAccess { .. }
        | Expr::Variable(_)
        | Expr::Literal(_)
        | Expr::AliasRef(_) => {}
    }
}

fn substitute(e: &mut Expr, name: &str, with: &Expr) {
    if matches!(e, Expr::Variable(v) if v == name) {
        *e = with.clone();
        return;
    }
    match e {
        Expr::Nearest { query, .. } => substitute(query, name, with),
        Expr::Search { field, query }
        | Expr::MatchText { field, query }
        | Expr::Bm25 { field, query } => {
            substitute(field, name, with);
            substitute(query, name, with);
        }
        Expr::Fuzzy {
            field,
            query,
            max_edits,
        } => {
            substitute(field, name, with);
            substitute(query, name, with);
            if let Some(m) = max_edits {
                substitute(m, name, with);
            }
        }
        Expr::Rrf {
            primary,
            secondary,
            k,
        } => {
            substitute(primary, name, with);
            substitute(secondary, name, with);
            if let Some(k) = k {
                substitute(k, name, with);
            }
        }
        Expr::Aggregate { arg, .. } | Expr::Not(arg) => substitute(arg, name, with),
        Expr::IsNull { expr, .. } => substitute(expr, name, with),
        Expr::Binary { left, right, .. } => {
            substitute(left, name, with);
            substitute(right, name, with);
        }
        Expr::In { needle, list } => {
            substitute(needle, name, with);
            substitute(list, name, with);
        }
        _ => {}
    }
}

use super::print3;

/// An edition-2 read declaration as edition-2 text, so the engine (which
/// takes source text) can run the bridge's output. Writes are not printed.
/// `parse_query` of the result must give back a declaration with the same
/// typed IR; the spike's tests check that on every normalised read.
pub fn print_read2(d: &QueryDecl) -> Result<String, String> {
    if !d.mutations.is_empty() {
        return Err("print_read2 prints reads only".into());
    }
    let mut out = String::new();
    let params: Vec<String> = d
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
        .collect();
    let _ = write!(out, "query {}({})", d.name, params.join(", "));
    if let Some(desc) = &d.description {
        let _ = write!(out, " @description({})", print3::literal(&Literal::String(desc.clone())));
    }
    if let Some(ins) = &d.instruction {
        let _ = write!(out, " @instruction({})", print3::literal(&Literal::String(ins.clone())));
    }
    match d.write_mode {
        MutationWriteMode::Publish => {}
        _ => return Err("a read with a write mode".into()),
    }
    out.push_str(" {\n    match {\n");
    for c in &d.match_clause {
        let _ = writeln!(out, "        {}", clause2(c)?);
    }
    out.push_str("    }\n    return ");
    if d.distinct {
        out.push_str("distinct ");
    }
    let items: Vec<String> = d
        .return_clause
        .iter()
        .map(|p| match &p.alias {
            Some(a) => format!("{} as {a}", p.expr),
            None => p.expr.to_string(),
        })
        .collect();
    let _ = writeln!(out, "{{ {} }}", items.join(", "));
    if !d.order_clause.is_empty() {
        let keys: Vec<String> = d
            .order_clause
            .iter()
            .map(|o| {
                // `nearest_ordering` is tried first and does not backtrack:
                // a key that only starts with `nearest(` is parenthesised.
                let key = match &o.expr {
                    Expr::Nearest { .. } => o.expr.to_string(),
                    e if e.to_string().starts_with("nearest(") => format!("({e})"),
                    e => e.to_string(),
                };
                match (o.explicit, o.descending) {
                    (false, _) => key,
                    (true, true) => format!("{key} desc"),
                    (true, false) => format!("{key} asc"),
                }
            })
            .collect();
        let _ = writeln!(out, "    order {{ {} }}", keys.join(", "));
    }
    if let Some(l) = d.limit {
        let _ = writeln!(out, "    limit {l}");
    }
    out.push_str("}\n");
    Ok(out)
}

fn clauses2(cs: &[Clause]) -> Result<String, String> {
    Ok(cs.iter().map(clause2).collect::<Result<Vec<_>, _>>()?.join("  "))
}

fn clause2(c: &Clause) -> Result<String, String> {
    Ok(match c {
        Clause::Binding(b) => {
            if b.prop_matches.is_empty() {
                format!("${}: {}", b.variable, b.type_name)
            } else {
                let items: Vec<String> = b
                    .prop_matches
                    .iter()
                    .map(|m| format!("{}: {}", m.prop_name, m.value))
                    .collect();
                format!("${}: {} {{ {} }}", b.variable, b.type_name, items.join(", "))
            }
        }
        Clause::Traversal(t) => print3::clause(
            &Clause3::Traversal(TraversalEq(t.clone())),
            print3::Style::default(),
        ),
        Clause::Filter(e) => e.to_string(),
        Clause::Subquery(s) => {
            let body = clauses2(&s.clauses)?;
            match (s.keyword, &s.arg) {
                (BlockKeyword::Not, None) => format!("not {{ {body} }}"),
                (BlockKeyword::Exists, None) => format!("exists {{ {body} }}"),
                (BlockKeyword::Aggregate, None) => {
                    format!("{} {{ {body} }} {} {}", s.func, s.op, s.right)
                }
                (BlockKeyword::Aggregate, Some(arg)) => {
                    format!("{}({arg}) {{ {body} }} {} {}", s.func, s.op, s.right)
                }
                (k, a) => return Err(format!("a block {k:?} with argument {a:?}")),
            }
        }
    })
}
