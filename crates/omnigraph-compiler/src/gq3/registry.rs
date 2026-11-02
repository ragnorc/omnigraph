//! SPIKE: the function registry of RFC PR #1202. Every function is a row:
//! a name, its kind, its required positional parameters and its named
//! options. A call resolves here and nowhere else, so a new function is a
//! row, never a grammar rule. Type rules stay with today's type checker,
//! which receives the same `Expr` variants the edition-2 grammar built.

use crate::query::ast::{AggFunc, CompOp, Expr, Literal};
use crate::query::codes::{T61, T66};
use crate::query::diagnostic::QueryDiagnostic;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Scalar,
    Aggregate,
    Predicate,
    Ranking,
}

pub struct Row {
    pub name: &'static str,
    pub kind: Kind,
    /// Required positional parameters, by name (for messages).
    pub positional: &'static [&'static str],
    /// Named options; a caller cannot pass one positionally.
    pub options: &'static [&'static str],
    pub can_fail: bool,
    pub volatile: bool,
    build: fn(Vec<Expr>, Vec<(String, Expr)>) -> Result<Expr, String>,
}

fn b(e: Expr) -> Box<Expr> {
    Box::new(e)
}

fn opt(options: &mut Vec<(String, Expr)>, name: &str) -> Option<Expr> {
    let at = options.iter().position(|(k, _)| k == name)?;
    Some(options.remove(at).1)
}

const fn aggregate(func: AggFunc) -> fn(Vec<Expr>, Vec<(String, Expr)>) -> Result<Expr, String> {
    match func {
        AggFunc::Count => |a, _| {
            Ok(Expr::Aggregate {
                func: AggFunc::Count,
                distinct: false,
                arg: b(a.into_iter().next().unwrap()),
            })
        },
        AggFunc::Sum => |a, _| {
            Ok(Expr::Aggregate {
                func: AggFunc::Sum,
                distinct: false,
                arg: b(a.into_iter().next().unwrap()),
            })
        },
        AggFunc::Avg => |a, _| {
            Ok(Expr::Aggregate {
                func: AggFunc::Avg,
                distinct: false,
                arg: b(a.into_iter().next().unwrap()),
            })
        },
        AggFunc::Min => |a, _| {
            Ok(Expr::Aggregate {
                func: AggFunc::Min,
                distinct: false,
                arg: b(a.into_iter().next().unwrap()),
            })
        },
        AggFunc::Max => |a, _| {
            Ok(Expr::Aggregate {
                func: AggFunc::Max,
                distinct: false,
                arg: b(a.into_iter().next().unwrap()),
            })
        },
    }
}

fn date_like(make: fn(String) -> Literal) -> impl Fn(Vec<Expr>) -> Result<Expr, String> {
    move |a: Vec<Expr>| match a.into_iter().next() {
        Some(Expr::Literal(Literal::String(s))) => Ok(Expr::Literal(make(s))),
        _ => Err("takes one string literal and is folded to a literal".to_string()),
    }
}

pub static ROWS: &[Row] = &[
    Row {
        name: "count",
        kind: Kind::Aggregate,
        positional: &["value"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: aggregate(AggFunc::Count),
    },
    Row {
        name: "sum",
        kind: Kind::Aggregate,
        positional: &["value"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: aggregate(AggFunc::Sum),
    },
    Row {
        name: "avg",
        kind: Kind::Aggregate,
        positional: &["value"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: aggregate(AggFunc::Avg),
    },
    Row {
        name: "min",
        kind: Kind::Aggregate,
        positional: &["value"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: aggregate(AggFunc::Min),
    },
    Row {
        name: "max",
        kind: Kind::Aggregate,
        positional: &["value"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: aggregate(AggFunc::Max),
    },
    Row {
        name: "now",
        kind: Kind::Scalar,
        positional: &[],
        options: &[],
        can_fail: false,
        volatile: true,
        build: |_, _| Ok(Expr::Now),
    },
    Row {
        name: "date",
        kind: Kind::Scalar,
        positional: &["iso"],
        options: &[],
        can_fail: true,
        volatile: false,
        build: |a, _| date_like(Literal::Date)(a),
    },
    Row {
        name: "datetime",
        kind: Kind::Scalar,
        positional: &["iso"],
        options: &[],
        can_fail: true,
        volatile: false,
        build: |a, _| date_like(Literal::DateTime)(a),
    },
    Row {
        name: "nearest",
        kind: Kind::Ranking,
        positional: &["property", "query"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: |a, _| {
            let mut a = a.into_iter();
            match (a.next(), a.next()) {
                (Some(Expr::PropAccess { variable, property }), Some(query)) => Ok(Expr::Nearest {
                    variable,
                    property,
                    query: b(query),
                }),
                _ => Err("the first argument is a vector property, `$d.embedding`".to_string()),
            }
        },
    },
    Row {
        name: "bm25",
        kind: Kind::Ranking,
        positional: &["field", "text"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: |a, _| {
            let mut a = a.into_iter();
            Ok(Expr::Bm25 {
                field: b(a.next().unwrap()),
                query: b(a.next().unwrap()),
            })
        },
    },
    Row {
        name: "rrf",
        kind: Kind::Ranking,
        positional: &["first", "second"],
        options: &["k"],
        can_fail: false,
        volatile: false,
        build: |a, mut o| {
            let mut a = a.into_iter();
            Ok(Expr::Rrf {
                primary: b(a.next().unwrap()),
                secondary: b(a.next().unwrap()),
                k: opt(&mut o, "k").map(b),
            })
        },
    },
    Row {
        name: "search",
        kind: Kind::Predicate,
        positional: &["field", "text"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: |a, _| {
            let mut a = a.into_iter();
            Ok(Expr::Search {
                field: b(a.next().unwrap()),
                query: b(a.next().unwrap()),
            })
        },
    },
    Row {
        name: "match_text",
        kind: Kind::Predicate,
        positional: &["field", "text"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: |a, _| {
            let mut a = a.into_iter();
            Ok(Expr::MatchText {
                field: b(a.next().unwrap()),
                query: b(a.next().unwrap()),
            })
        },
    },
    // One argument, one form: the edit budget is optional, so it is an option.
    Row {
        name: "fuzzy",
        kind: Kind::Predicate,
        positional: &["field", "text"],
        options: &["max_edits"],
        can_fail: false,
        volatile: false,
        build: |a, mut o| {
            let mut a = a.into_iter();
            Ok(Expr::Fuzzy {
                field: b(a.next().unwrap()),
                query: b(a.next().unwrap()),
                max_edits: opt(&mut o, "max_edits").map(b),
            })
        },
    },
    Row {
        name: "contains",
        kind: Kind::Predicate,
        positional: &["text", "part"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: |a, _| {
            let mut a = a.into_iter();
            Ok(Expr::comparison(
                a.next().unwrap(),
                CompOp::Contains,
                a.next().unwrap(),
            ))
        },
    },
    Row {
        name: "starts_with",
        kind: Kind::Predicate,
        positional: &["text", "prefix"],
        options: &[],
        can_fail: false,
        volatile: false,
        build: |a, _| {
            let mut a = a.into_iter();
            Ok(Expr::comparison(
                a.next().unwrap(),
                CompOp::StartsWith,
                a.next().unwrap(),
            ))
        },
    },
];

pub fn row(name: &str) -> Option<&'static Row> {
    ROWS.iter().find(|row| row.name == name)
}

/// Resolve one call. A positional argument past the required ones that
/// names an option is the edition-2 spelling (`rrf(a, b, 60)`), refused with
/// the named form as its fix.
pub fn resolve(
    name: &str,
    positional: Vec<Expr>,
    options: Vec<(String, Expr)>,
) -> Result<Expr, QueryDiagnostic> {
    resolve_in(ROWS, name, positional, options)
}

pub fn resolve_in(
    rows: &[Row],
    name: &str,
    positional: Vec<Expr>,
    options: Vec<(String, Expr)>,
) -> Result<Expr, QueryDiagnostic> {
    let Some(row) = rows.iter().find(|row| row.name == name) else {
        return Err(QueryDiagnostic::typecheck(
            T66,
            format!("`{name}` is not a function in the registry"),
        )
        .with_fix(format!(
            "use one of {}",
            rows.iter().map(|r| r.name).collect::<Vec<_>>().join(", ")
        )));
    };
    if positional.len() > row.positional.len() {
        let extra = positional.len() - row.positional.len();
        let fix = if extra <= row.options.len() {
            let named: Vec<String> = row.options[..extra]
                .iter()
                .zip(&positional[row.positional.len()..])
                .map(|(o, v)| format!("{o}: {}", render(v)))
                .collect();
            format!("pass {} by name", named.join(", "))
        } else {
            format!(
                "`{name}` takes {} positional arguments",
                row.positional.len()
            )
        };
        return Err(QueryDiagnostic::typecheck(
            T61,
            format!(
                "`{name}` takes {} positional arguments; an optional argument is passed by name",
                row.positional.len()
            ),
        )
        .with_fix(fix));
    }
    if positional.len() < row.positional.len() {
        return Err(QueryDiagnostic::typecheck(
            T61,
            format!(
                "`{name}` needs {} ({})",
                row.positional.len(),
                row.positional.join(", ")
            ),
        ));
    }
    for (key, _) in &options {
        if !row.options.contains(&key.as_str()) {
            return Err(
                QueryDiagnostic::typecheck(T61, format!("`{name}` has no option `{key}`"))
                    .with_fix(if row.options.is_empty() {
                        format!("`{name}` takes no options")
                    } else {
                        format!("its options are {}", row.options.join(", "))
                    }),
            );
        }
    }
    (row.build)(positional, options)
        .map_err(|message| QueryDiagnostic::typecheck(T61, format!("`{name}` {message}")))
}

fn render(e: &Expr) -> String {
    match e {
        Expr::Literal(Literal::Integer(i)) => i.to_string(),
        Expr::Literal(l) => l.to_string(),
        Expr::Variable(v) => format!("${v}"),
        other => format!("{other:?}"),
    }
}

#[cfg(test)]
impl Row {
    /// A row a later release could add: proves a function is data.
    pub fn __spike_lower() -> Row {
        Row {
            name: "lower",
            kind: Kind::Scalar,
            positional: &["text"],
            options: &[],
            can_fail: false,
            volatile: false,
            build: |a, _| Ok(a.into_iter().next().unwrap()),
        }
    }
}
