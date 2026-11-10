//! The edition-window parse tree as the one surface tree
//! ([`crate::query::syntax`]): every edition-2 node becomes the node edition
//! 3 reads, and the spellings only edition 2 has keep a node of their own
//! (an infix `contains` or `starts_with`, a positional `fuzzy` budget or
//! `rrf` constant, `update`/`delete … where`, a bare return alias). The
//! checker reads the tree under [`Edition::Two`], which accepts those
//! spellings as edition 2 does.

use crate::query::ast::{self, BinaryOp, BlockKeyword, CompOp, FileBody, Literal, QueryFile};
use crate::query::syntax::{
    Annotation, AnnotationArg, AnnotationValue, Arg, Assignment, Block, BlockFunc, Body, Call,
    Clause, Decl, DeclBody, Edition, Expr, InfixStringOp, Limit, Ordering, Param, Projection,
    ReadBody, SourceFile, SourceSpan, StatementBody, Stmt, TraversalEq, WriteBody, WriteGroup,
};

/// The surface tree of an edition-window parse.
pub fn surface(file: &QueryFile) -> SourceFile {
    let body = match &file.body {
        FileBody::Queries(decls) => Body::Decls(decls.iter().map(decl).collect()),
        FileBody::Explain(query) => Body::Explain(Box::new(decl(query))),
        other => Body::Statement(Box::new(StatementBody(other.clone()))),
    };
    SourceFile {
        edition: Edition::Two,
        settings: file.settings.clone(),
        body,
    }
}

/// One declaration's surface tree.
pub fn decl(query: &ast::QueryDecl) -> Decl {
    let metadata = |name: &str, value: &Option<String>| {
        value.as_ref().map(|text| Annotation {
            name: name.to_string(),
            args: Some(vec![AnnotationArg::Positional(AnnotationValue::Literal(
                Literal::String(text.clone()),
            ))]),
            at: SourceSpan::default(),
        })
    };
    let body = if query.mutations.is_empty() {
        DeclBody::Read(ReadBody {
            clauses: clauses(&query.match_clause),
            distinct: query.distinct,
            projections: query
                .return_clause
                .iter()
                .map(|p| Projection {
                    expr: expr(&p.expr),
                    alias: p.alias.clone(),
                })
                .collect(),
            order: query
                .order_clause
                .iter()
                .map(|o| Ordering {
                    expr: expr(&o.expr),
                    descending: o.explicit.then_some(o.descending),
                })
                .collect(),
            limit: query.limit.map(Limit::Literal),
        })
    } else {
        DeclBody::Write(WriteBody {
            groups: vec![WriteGroup {
                clauses: clauses(&query.match_clause),
                statements: query.mutations.iter().map(statement).collect(),
            }],
        })
    };
    Decl {
        name: query.name.clone(),
        params: query
            .params
            .iter()
            .map(|p| Param {
                name: p.name.clone(),
                type_name: p.type_name.clone(),
                nullable: p.nullable,
                default: None,
            })
            .collect(),
        annotations: [
            metadata("description", &query.description),
            metadata("instruction", &query.instruction),
        ]
        .into_iter()
        .flatten()
        .collect(),
        write_mode: query.write_mode.clone(),
        body,
    }
}

fn statement(mutation: &ast::Mutation) -> Stmt {
    let assignments = |a: &[ast::MutationAssignment]| {
        a.iter()
            .map(|a| Assignment {
                property: a.property.clone(),
                value: expr(&a.value),
            })
            .collect()
    };
    match mutation {
        ast::Mutation::Insert(m) => Stmt::Insert {
            type_name: m.type_name.clone(),
            assignments: assignments(&m.assignments),
        },
        ast::Mutation::Update(m) => Stmt::UpdateWhere {
            type_name: m.type_name.clone(),
            assignments: assignments(&m.assignments),
            predicate: expr(&m.predicate),
        },
        ast::Mutation::Delete(m) => Stmt::DeleteWhere {
            type_name: m.type_name.clone(),
            predicate: expr(&m.predicate),
        },
    }
}

fn clauses(clauses: &[ast::Clause]) -> Vec<Clause> {
    clauses.iter().map(clause).collect()
}

fn clause(clause: &ast::Clause) -> Clause {
    match clause {
        ast::Clause::Binding(b) => Clause::Binding {
            variable: b.variable.clone(),
            type_name: b.type_name.clone(),
            prop_matches: b
                .prop_matches
                .iter()
                .map(|m| (m.prop_name.clone(), expr(&m.value)))
                .collect(),
        },
        ast::Clause::Traversal(t) => Clause::Traversal(TraversalEq(t.clone())),
        ast::Clause::Filter(e) => Clause::Filter(expr(e)),
        ast::Clause::Subquery(s) => match s.keyword {
            BlockKeyword::Not => Clause::Not(clauses(&s.clauses)),
            BlockKeyword::Exists => Clause::Filter(Expr::Block(Box::new(Block {
                func: BlockFunc::Exists,
                arg: None,
                clauses: clauses(&s.clauses),
            }))),
            BlockKeyword::Aggregate => Clause::Filter(Expr::Compare {
                op: s.op,
                left: Box::new(Expr::Block(Box::new(Block {
                    func: BlockFunc::Agg(s.func),
                    arg: s.arg.as_ref().map(|a| Box::new(expr(a))),
                    clauses: clauses(&s.clauses),
                }))),
                right: Box::new(expr(&s.right)),
            }),
        },
    }
}

fn call(name: &str, args: Vec<Expr>) -> Expr {
    Expr::Call(Call {
        name: name.to_string(),
        args: args.into_iter().map(Arg::Expr).collect(),
        options: Vec::new(),
    })
}

fn expr(e: &ast::Expr) -> Expr {
    let boxed = |e: &ast::Expr| Box::new(expr(e));
    match e {
        ast::Expr::Now => call("now", Vec::new()),
        ast::Expr::PropAccess { variable, property } => Expr::Prop {
            variable: variable.clone(),
            property: property.clone(),
        },
        ast::Expr::Nearest {
            variable,
            property,
            query,
        } => call(
            "nearest",
            vec![
                Expr::Prop {
                    variable: variable.clone(),
                    property: property.clone(),
                },
                expr(query),
            ],
        ),
        ast::Expr::Search { field, query } => call("search", vec![expr(field), expr(query)]),
        ast::Expr::MatchText { field, query } => call("match_text", vec![expr(field), expr(query)]),
        ast::Expr::Bm25 { field, query } => call("bm25", vec![expr(field), expr(query)]),
        ast::Expr::Fuzzy {
            field,
            query,
            max_edits,
        } => {
            let mut args = vec![expr(field), expr(query)];
            args.extend(max_edits.as_deref().map(expr));
            call("fuzzy", args)
        }
        ast::Expr::Rrf {
            primary,
            secondary,
            k,
        } => {
            let mut args = vec![expr(primary), expr(secondary)];
            args.extend(k.as_deref().map(expr));
            call("rrf", args)
        }
        ast::Expr::Variable(name) => Expr::Variable(name.clone()),
        ast::Expr::Literal(literal) => Expr::Literal(literal.clone()),
        ast::Expr::Aggregate {
            func,
            distinct: true,
            arg,
        } => Expr::AggDistinct {
            func: *func,
            arg: boxed(arg),
        },
        ast::Expr::Aggregate {
            func,
            distinct: false,
            arg,
        } => call(&func.to_string(), vec![expr(arg)]),
        ast::Expr::AliasRef(name) => Expr::Ident(name.clone()),
        // A search predicate is a call compared with `true` in edition 2's
        // tree, written bare or with `= true`; the surface keeps the call.
        ast::Expr::Binary {
            left,
            op: BinaryOp::Compare(CompOp::Eq),
            right,
        } if matches!(
            left.as_ref(),
            ast::Expr::Search { .. } | ast::Expr::Fuzzy { .. } | ast::Expr::MatchText { .. }
        ) && matches!(right.as_ref(), ast::Expr::Literal(Literal::Bool(true))) =>
        {
            expr(left)
        }
        ast::Expr::Binary { left, op, right } => match op {
            BinaryOp::Compare(op @ (CompOp::Contains | CompOp::StartsWith)) => Expr::InfixString {
                op: if *op == CompOp::Contains {
                    InfixStringOp::Contains
                } else {
                    InfixStringOp::StartsWith
                },
                left: boxed(left),
                right: boxed(right),
                at: SourceSpan::default(),
            },
            BinaryOp::Compare(op) => Expr::Compare {
                op: *op,
                left: boxed(left),
                right: boxed(right),
            },
            BinaryOp::And => Expr::And(boxed(left), boxed(right)),
            BinaryOp::Or => Expr::Or(boxed(left), boxed(right)),
            BinaryOp::Arith(op) => Expr::Arith {
                op: *op,
                left: boxed(left),
                right: boxed(right),
            },
        },
        ast::Expr::Not(inner) => Expr::Not(boxed(inner)),
        ast::Expr::IsNull {
            expr: inner,
            negated,
        } => Expr::IsNull {
            expr: boxed(inner),
            negated: *negated,
        },
        ast::Expr::In { needle, list } => Expr::In {
            needle: boxed(needle),
            list: boxed(list),
            at: SourceSpan::default(),
        },
    }
}
