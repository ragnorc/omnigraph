//! SPIKE: lower an edition-3 tree into the edition-2 tree the type checker
//! reads.
//!
//! The part of edition 3 that edition 2 already has a node for (bindings,
//! triples, filters, the blocks as predicates, `return`, `order`, a literal
//! `limit`, `insert`, the registry calls with an edition-2 node) lowers to
//! that node, so the existing type checker checks it unchanged. Every other
//! node (`let`, `from`, `optional`, arithmetic, a block as a value, a generic
//! call, `limit $k`, a parameter default, a non-legacy annotation, clauses in
//! a write, `update $x`, `delete $x`) is refused with the one placeholder code
//! `T60`: type checking the new nodes is outside the prototype.

use super::ast::*;
use super::ast3::*;
use super::codes::T60;
use super::diagnostic::QueryDiagnostic;

type R<T> = std::result::Result<T, QueryDiagnostic>;

fn placeholder(what: impl Into<String>) -> QueryDiagnostic {
    QueryDiagnostic::typecheck(
        T60,
        format!(
            "{} is edition-3 syntax this prototype parses but does not type-check",
            what.into()
        ),
    )
}

pub fn lower_file(file: File3) -> R<QueryFile> {
    let body = match file.body {
        Body3::Decls(decls) => {
            FileBody::Queries(decls.into_iter().map(lower_decl).collect::<R<Vec<_>>>()?)
        }
        Body3::Statement(statement) => statement.0,
        Body3::Explain(decl) => FileBody::Explain(lower_decl(*decl)?),
    };
    Ok(QueryFile {
        settings: file.settings,
        body,
    })
}

pub fn lower_decl(decl: Decl3) -> R<QueryDecl> {
    let mut description = None;
    let mut instruction = None;
    for a in decl.annotations {
        let value = match (&a.args, a.name.as_str()) {
            (Some(args), "description" | "instruction") => match args.as_slice() {
                [AnnotationArg3::Positional(AnnotationValue3::Literal(Literal::String(s)))] => {
                    s.clone()
                }
                _ => return Err(placeholder(format!("`@{}` with these arguments", a.name))),
            },
            _ => return Err(placeholder(format!("the annotation `@{}`", a.name))),
        };
        let slot = if a.name == "description" {
            &mut description
        } else {
            &mut instruction
        };
        if slot.replace(value).is_some() {
            return Err(QueryDiagnostic::parse(
                super::codes::Q005,
                format!(
                    "query `{}` cannot include duplicate @{} annotations",
                    decl.name, a.name
                ),
                None,
            ));
        }
    }
    let mut params = Vec::new();
    for p in decl.params {
        if p.default.is_some() {
            return Err(placeholder(format!("the default of `${}`", p.name)));
        }
        params.push(Param {
            name: p.name,
            type_name: p.type_name,
            nullable: p.nullable,
        });
    }
    let mut out = QueryDecl {
        name: decl.name,
        description,
        instruction,
        params,
        match_clause: Vec::new(),
        return_clause: Vec::new(),
        distinct: false,
        order_clause: Vec::new(),
        limit: None,
        mutations: Vec::new(),
        write_mode: decl.write_mode,
        spans: Default::default(),
    };
    match decl.body {
        DeclBody3::Read(body) => {
            out.match_clause = lower_clauses(body.clauses)?;
            out.distinct = body.distinct;
            out.return_clause = body
                .projections
                .into_iter()
                .map(|p| {
                    Ok(Projection {
                        expr: lower_expr(p.expr, Scope::Alias)?,
                        alias: p.alias,
                    })
                })
                .collect::<R<Vec<_>>>()?;
            out.order_clause = body
                .order
                .into_iter()
                .map(|o| {
                    Ok(Ordering {
                        expr: lower_expr(o.expr, Scope::Alias)?,
                        descending: o.descending.unwrap_or(false),
                        explicit: o.descending.is_some(),
                    })
                })
                .collect::<R<Vec<_>>>()?;
            out.limit = match body.limit {
                None => None,
                Some(Limit3::Literal(n)) => Some(n),
                Some(Limit3::Param(p)) => return Err(placeholder(format!("`limit ${p}`"))),
            };
        }
        DeclBody3::Write(body) => {
            if !body.clauses.is_empty() {
                return Err(placeholder("a write body's clauses"));
            }
            for s in body.statements {
                out.mutations.push(match s {
                    Stmt3::Insert {
                        type_name,
                        assignments,
                    } => {
                        let assignments = assignments
                            .into_iter()
                            .map(|a| {
                                Ok(MutationAssignment {
                                    property: a.property,
                                    value: lower_expr(a.value, Scope::Property(&type_name))?,
                                })
                            })
                            .collect::<R<Vec<_>>>()?;
                        Mutation::Insert(InsertMutation {
                            type_name,
                            assignments,
                        })
                    }
                    Stmt3::Update { variable, .. } => {
                        return Err(placeholder(format!("`update ${variable}`")));
                    }
                    Stmt3::Delete { variable } => {
                        return Err(placeholder(format!("`delete ${variable}`")));
                    }
                });
            }
        }
    }
    Ok(out)
}

#[derive(Clone, Copy)]
pub(crate) enum Scope<'a> {
    Alias,
    Property(&'a str),
}

fn lower_clauses(clauses: Vec<Clause3>) -> R<Vec<Clause>> {
    clauses.into_iter().map(lower_clause).collect()
}

fn lower_clause(clause: Clause3) -> R<Clause> {
    Ok(match clause {
        Clause3::Let { name, .. } => return Err(placeholder(format!("`let ${name}`"))),
        Clause3::From(_) => return Err(placeholder("`from`")),
        Clause3::Optional(_) => return Err(placeholder("`optional { }`")),
        Clause3::Not(c) => Clause::Subquery(Subquery::not_block(lower_clauses(c)?)),
        Clause3::Binding {
            variable,
            type_name,
            prop_matches,
        } => {
            let prop_matches = prop_matches
                .into_iter()
                .map(|(prop_name, value)| {
                    Ok(PropMatch {
                        prop_name,
                        value: lower_expr(value, Scope::Property(&type_name))?,
                    })
                })
                .collect::<R<Vec<_>>>()?;
            Clause::Binding(Binding {
                variable,
                type_name,
                prop_matches,
            })
        }
        Clause3::Traversal(TraversalEq(t)) => {
            let catalogue = match &t.selector {
                EdgeSelector::Named(n) => n.starts_with('@'),
                EdgeSelector::Alternation(ns) => ns.iter().any(|n| n.starts_with('@')),
                EdgeSelector::Wildcard => false,
            };
            if catalogue {
                return Err(placeholder("a catalogue edge"));
            }
            Clause::Traversal(t)
        }
        // A block as a whole filter, or compared with its block on the left,
        // is edition 2's block predicate.
        Clause3::Filter(Expr3::Block(block)) if block.func == BlockFunc3::Exists => {
            Clause::Subquery(Subquery::exists_block(lower_clauses(block.clauses)?))
        }
        Clause3::Filter(Expr3::Compare { op, left, right }) if matches!(&*left, Expr3::Block(b) if b.func != BlockFunc3::Exists) =>
        {
            let Expr3::Block(block) = *left else {
                unreachable!()
            };
            let BlockFunc3::Agg(func) = block.func else {
                unreachable!()
            };
            Clause::Subquery(Subquery {
                keyword: BlockKeyword::Aggregate,
                clauses: lower_clauses(block.clauses)?,
                func,
                arg: block
                    .arg
                    .map(|a| lower_expr(*a, Scope::Alias))
                    .transpose()?,
                op,
                right: lower_expr(*right, Scope::Alias)?,
            })
        }
        Clause3::Filter(e) => {
            Clause::Filter(lower_expr(e, Scope::Alias)?.with_search_predicates_spelled())
        }
    })
}

fn bare(scope: Scope<'_>, name: &str) -> Expr {
    match scope {
        Scope::Alias => Expr::AliasRef(name.to_string()),
        Scope::Property(t) => Expr::mutation_property(t, name),
    }
}

fn boxed(e: Expr3, scope: Scope<'_>) -> R<Box<Expr>> {
    lower_expr(e, scope).map(Box::new)
}

pub(crate) fn lower_expr(e: Expr3, scope: Scope<'_>) -> R<Expr> {
    Ok(match e {
        Expr3::Literal(l) => Expr::Literal(l),
        Expr3::Variable(v) => Expr::Variable(v),
        Expr3::Prop { variable, property } => Expr::PropAccess { variable, property },
        Expr3::Meta(m) => bare(scope, &format!("@{m}")),
        Expr3::Ident(i) => bare(scope, &i),
        Expr3::Call(c) => lower_call(c, scope)?,
        Expr3::AggDistinct { func, arg } => Expr::Aggregate {
            func,
            distinct: true,
            arg: boxed(*arg, scope)?,
        },
        Expr3::Block(_) => return Err(placeholder("a block as a value")),
        Expr3::Neg(_) | Expr3::Arith { .. } => return Err(placeholder("arithmetic")),
        Expr3::Compare { op, left, right } => {
            Expr::comparison(lower_expr(*left, scope)?, op, lower_expr(*right, scope)?)
        }
        Expr3::In { needle, list } => Expr::In {
            needle: boxed(*needle, scope)?,
            list: boxed(*list, scope)?,
        },
        Expr3::IsNull { expr, negated } => Expr::IsNull {
            expr: boxed(*expr, scope)?,
            negated,
        },
        Expr3::Not(e) => Expr::Not(boxed(*e, scope)?),
        Expr3::And(l, r) => Expr::Binary {
            left: boxed(*l, scope)?,
            op: BinaryOp::And,
            right: boxed(*r, scope)?,
        },
        Expr3::Or(l, r) => Expr::Binary {
            left: boxed(*l, scope)?,
            op: BinaryOp::Or,
            right: boxed(*r, scope)?,
        },
    })
}

/// The registry calls that have an edition-2 node. `nearest`, `bm25` and
/// `rrf` as scalar calls are the RFC's `T65`; this prototype refuses them
/// with the placeholder.
fn lower_call(c: Call3, scope: Scope<'_>) -> R<Expr> {
    let name = c.name.clone();
    let mut args = Vec::new();
    for a in c.args {
        match a {
            Arg3::Expr(e) => args.push(lower_expr(e, scope)?),
            Arg3::Inline(_) => {
                return Err(placeholder(format!(
                    "an inline read as an argument of `{name}`"
                )));
            }
        }
    }
    let mut options = Vec::new();
    for (k, v) in c.options {
        options.push((k, lower_expr(v, scope)?));
    }
    let agg = |func| -> R<Expr> {
        match (args.as_slice(), options.is_empty()) {
            ([arg], true) => Ok(Expr::Aggregate {
                func,
                distinct: false,
                arg: Box::new(arg.clone()),
            }),
            _ => Err(placeholder(format!("`{name}` with these arguments"))),
        }
    };
    let two = |make: fn(Box<Expr>, Box<Expr>) -> Expr| -> R<Expr> {
        match (args.as_slice(), options.is_empty()) {
            ([a, b], true) => Ok(make(Box::new(a.clone()), Box::new(b.clone()))),
            _ => Err(placeholder(format!("`{name}` with these arguments"))),
        }
    };
    match name.as_str() {
        "now" if args.is_empty() && options.is_empty() => Ok(Expr::Now),
        "count" => agg(AggFunc::Count),
        "sum" => agg(AggFunc::Sum),
        "avg" => agg(AggFunc::Avg),
        "min" => agg(AggFunc::Min),
        "max" => agg(AggFunc::Max),
        "search" => two(|field, query| Expr::Search { field, query }),
        "match_text" => two(|field, query| Expr::MatchText { field, query }),
        "contains" | "starts_with" => match (args.as_slice(), options.is_empty()) {
            ([a, b], true) => Ok(Expr::comparison(
                a.clone(),
                if name == "contains" {
                    CompOp::Contains
                } else {
                    CompOp::StartsWith
                },
                b.clone(),
            )),
            _ => Err(placeholder(format!("`{name}` with these arguments"))),
        },
        "fuzzy" => match (args.as_slice(), options.as_slice()) {
            ([field, query], []) => Ok(Expr::Fuzzy {
                field: Box::new(field.clone()),
                query: Box::new(query.clone()),
                max_edits: None,
            }),
            ([field, query], [(k, edits)]) if k == "max_edits" => Ok(Expr::Fuzzy {
                field: Box::new(field.clone()),
                query: Box::new(query.clone()),
                max_edits: Some(Box::new(edits.clone())),
            }),
            ([_, _, _], []) => Err(QueryDiagnostic::typecheck(
                T60,
                "`fuzzy`'s edit budget is an option in edition 3".to_string(),
            )
            .with_fix("fuzzy(field, query, max_edits: n)")),
            _ => Err(placeholder("`fuzzy` with these arguments")),
        },
        "nearest" | "bm25" | "rrf" | "l2" => Err(placeholder(format!(
            "`{name}` (a relation function; RFC T65 when written as a scalar)"
        ))),
        _ => Err(placeholder(format!("the call `{name}(…)`"))),
    }
}
