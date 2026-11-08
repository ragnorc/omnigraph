//! SPIKE: the edition-3 front end (RFC 2026-11-07-gq-3, sections 3, 6, 7).
//!
//! [`parse_query3`] parses a source that opens with `gq 3;` into the
//! edition-3 tree ([`super::ast3`]). The recognizers of the grammar
//! (`arrow_traversal`, `where_stmt`, `mutation_decl`, …) become typed parse
//! errors whose `fix` prints the legal text.

use pest::Parser;
use pest::iterators::Pair;

use crate::error::CompilerError;

use super::ast::{AggFunc, CompOp, EdgeSelector, FileBody, Literal, MutationWriteMode, Traversal};
use super::ast3::*;
use super::codes::{Q002, Q004, Q005, Q006, Q007, Q008, Q009, Q010, Q011, Q012, Q013};
use super::diagnostic::{Applicability, Position, QueryDiagnostic, Suggestion, TextEdit};
use super::parser::{
    QueryParser, Rule, compiler_error_to_diagnostic, diagnostic_at, parse_branch_stmt,
    parse_commit_stmt, parse_edge_name, parse_load_stmt, parse_setting_stmt, parse_setting_target,
    parse_string_lit, parse_traversal_bounds, parse_unattached_base, pest_error_to_diagnostic,
    position_of, skip_trivia,
};

type R<T> = std::result::Result<T, QueryDiagnostic>;

/// The edition a source's first line selects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EditionLine {
    /// No `gq N;` line.
    Absent,
    /// `gq N;` spanning `start..end` in the source.
    Present {
        edition: u64,
        start: usize,
        end: usize,
    },
}

/// Read the edition line: `gq`, word-bounded, an integer and `;`, after
/// leading whitespace and comments. Anything else is `Absent` and the
/// edition-2 grammar reports it.
pub fn edition_line(input: &str) -> EditionLine {
    let rest = skip_trivia(input);
    let start = input.len() - rest.len();
    let Some(after) = rest.strip_prefix("gq") else {
        return EditionLine::Absent;
    };
    if after.starts_with(|c: char| c.is_ascii_alphanumeric() || c == '_') {
        return EditionLine::Absent;
    }
    let after = skip_trivia(after);
    let digits = after.len() - after.trim_start_matches(|c: char| c.is_ascii_digit()).len();
    if digits == 0 {
        return EditionLine::Absent;
    }
    let Ok(edition) = after[..digits].parse::<u64>() else {
        return EditionLine::Absent;
    };
    let after = skip_trivia(&after[digits..]);
    let Some(tail) = after.strip_prefix(';') else {
        return EditionLine::Absent;
    };
    EditionLine::Present {
        edition,
        start,
        end: input.len() - tail.len(),
    }
}

pub fn parse_query3(input: &str) -> R<File3> {
    let pairs = QueryParser::parse(Rule::gq3_file, input)
        .map_err(|error| pest_error_to_diagnostic(input, error))?;
    let file = pairs.into_iter().next().expect("grammar: one gq3_file");
    let mut settings = Vec::new();
    let mut decls = Vec::new();
    let mut statement: Option<Body3> = None;
    for inner in file.into_inner() {
        match inner.as_rule() {
            Rule::edition_line => {
                let n = inner
                    .clone()
                    .into_inner()
                    .find(|p| p.as_rule() == Rule::integer)
                    .expect("grammar: edition_line holds an integer");
                if n.as_str() != "3" {
                    return Err(diagnostic_at(
                        Q006,
                        format!("edition {} is not edition 3", n.as_str()),
                        &inner,
                    ));
                }
            }
            Rule::setting_stmt => settings.push(parse_setting_stmt(inner)?),
            Rule::branch_stmt => {
                statement = Some(Body3::Statement(Box::new(StatementBody(FileBody::Branch(
                    parse_branch_stmt(inner)?,
                )))))
            }
            Rule::commit_stmt => {
                statement = Some(Body3::Statement(Box::new(StatementBody(FileBody::Commit(
                    parse_commit_stmt(inner)?,
                )))))
            }
            Rule::load_stmt => {
                statement = Some(Body3::Statement(Box::new(StatementBody(FileBody::Load(
                    parse_load_stmt(inner)?,
                )))))
            }
            Rule::show_stmt => {
                statement = Some(Body3::Statement(Box::new(StatementBody(FileBody::Show(
                    parse_setting_target(inner)?,
                )))))
            }
            Rule::explain_stmt3 => {
                let wrapped = inner
                    .into_inner()
                    .find(|p| matches!(p.as_rule(), Rule::query_decl3 | Rule::load_stmt))
                    .expect("grammar: explain_stmt3 wraps one declaration or load");
                statement = Some(match wrapped.as_rule() {
                    Rule::load_stmt => Body3::Statement(Box::new(StatementBody(
                        FileBody::ExplainLoad(parse_load_stmt(wrapped)?),
                    ))),
                    _ => Body3::Explain(Box::new(decl(wrapped)?)),
                });
            }
            Rule::statement_trailer3 => {
                return Err(diagnostic_at(
                    Q004,
                    "a statement stands alone in its file",
                    &inner,
                ));
            }
            Rule::missing_param_list3 => {
                let name = inner
                    .clone()
                    .into_inner()
                    .find(|p| p.as_rule() == Rule::ident)
                    .expect("grammar: missing_param_list3 holds an ident");
                let byte = name.as_span().end();
                return Err(QueryDiagnostic::parse(
                    Q002,
                    "expected `(`: a query declares its parameters even when it has none",
                    Some(Position::at(input, byte)),
                )
                .with_fix(format!("query {}()", name.as_str())));
            }
            Rule::declaration3 => {
                let d = inner.into_inner().next().expect("grammar: one declaration");
                match d.as_rule() {
                    Rule::query_decl3 => decls.push(decl(d)?),
                    Rule::mutation_decl => return Err(mutation_keyword(input, d)),
                    other => unreachable!("grammar: declaration3 holds no {other:?}"),
                }
            }
            _ => {}
        }
    }
    Ok(File3 {
        settings,
        body: statement.unwrap_or(Body3::Decls(decls)),
    })
}

/// `mutation name(…) { … }`: a parse error with `query` as the fix, as a
/// machine-applicable edit of the keyword.
fn mutation_keyword(input: &str, d: Pair<Rule>) -> QueryDiagnostic {
    let keyword = d
        .clone()
        .into_inner()
        .next()
        .expect("grammar: mutation_decl opens with kw_mutation");
    let span = keyword.as_span();
    let mut fixed = String::new();
    fixed.push_str(&input[d.as_span().start()..span.start()]);
    fixed.push_str("query");
    fixed.push_str(&input[span.end()..d.as_span().end()]);
    QueryDiagnostic::parse(
        Q009,
        "`mutation` is not a declaration keyword: a write is a `query` whose body ends in statements",
        Some(position_of(&keyword)),
    )
    .with_fix(fixed)
    .with_suggestion(Suggestion {
        applicability: Applicability::MachineApplicable,
        edits: vec![TextEdit {
            start: span.start(),
            end: span.end(),
            replacement: "query".to_string(),
        }],
    })
}

fn decl(pair: Pair<Rule>) -> R<Decl3> {
    let mut name = None;
    let mut params = Vec::new();
    let mut annotations = Vec::new();
    let mut write_clause = None;
    let mut body = None;
    for item in pair.into_inner() {
        match item.as_rule() {
            Rule::kw_query => {}
            Rule::ident => name = Some(item.as_str().to_string()),
            Rule::param_list3 => {
                for p in item.into_inner() {
                    params.push(param(p)?);
                }
            }
            Rule::annotation => annotations.push(annotation(item)?),
            Rule::as_commit_clause | Rule::as_unattached_clause => write_clause = Some(item),
            Rule::read_body => body = Some(DeclBody3::Read(read_body(item)?)),
            Rule::mutation_body3 => body = Some(DeclBody3::Write(write_body(item)?)),
            other => unreachable!("grammar: query_decl3 holds no {other:?}"),
        }
    }
    let name = name.expect("grammar: query_decl3 names itself");
    let body = body.expect("grammar: query_decl3 has a body");
    let write_mode = match write_clause {
        None => MutationWriteMode::Publish,
        Some(clause) => {
            if matches!(body, DeclBody3::Read(_)) {
                let spelled = match clause.as_rule() {
                    Rule::as_commit_clause => "as commit",
                    _ => "as unattached branch",
                };
                // The RFC (section 5) makes this a type error naming the
                // missing sink; the kind is in the parse tree, so the parser
                // can refuse it.
                return Err(diagnostic_at(
                    Q011,
                    format!(
                        "`{spelled}` applies to a write; the body of `{name}` ends in `return`, so it is a read with no statement to commit"
                    ),
                    &clause,
                )
                .with_fix(format!(
                    "remove `{spelled}`, or end the body in `insert`, `update` or `delete`"
                )));
            }
            match clause.as_rule() {
                Rule::as_commit_clause => MutationWriteMode::AsCommit,
                _ => MutationWriteMode::Unattached {
                    base: parse_unattached_base(clause)?,
                },
            }
        }
    };
    Ok(Decl3 {
        name,
        params,
        annotations,
        write_mode,
        body,
    })
}

fn param(pair: Pair<Rule>) -> R<Param3> {
    let mut inner = pair.into_inner();
    let var = inner.next().unwrap().as_str();
    let name = var[1..].to_string();
    let type_ref = inner.next().unwrap();
    let nullable = type_ref.as_str().trim_end().ends_with('?');
    let core = type_ref.into_inner().next().unwrap();
    let type_name = match core.as_rule() {
        Rule::base_type => core.as_str().to_string(),
        Rule::list_type => format!("[{}]", core.into_inner().next().unwrap().as_str().trim()),
        Rule::vector_type => format!("Vector({})", core.into_inner().next().unwrap().as_str()),
        other => unreachable!("grammar: type_ref holds no {other:?}"),
    };
    let default = match inner.next() {
        None => None,
        Some(d) => {
            let mut negative = false;
            let mut value = None;
            for part in d.into_inner() {
                match part.as_rule() {
                    Rule::neg_sign => negative = true,
                    _ => value = Some(literal(part)?),
                }
            }
            Some(ParamDefault {
                negative,
                value: value.expect("grammar: param_default holds a literal"),
            })
        }
    };
    Ok(Param3 {
        name,
        type_name,
        nullable,
        default,
    })
}

fn annotation(pair: Pair<Rule>) -> R<Annotation3> {
    let mut inner = pair.clone().into_inner();
    let name = inner.next().unwrap().as_str()[1..].to_string();
    // The RFC refuses these two by name so a precondition or a validity
    // interval is never silently kept as metadata.
    if name == "if_commit" || name == "valid" {
        return Err(diagnostic_at(
            Q012,
            format!("`@{name}` is reserved for the writes RFC and has no meaning yet"),
            &pair,
        ));
    }
    let text = pair.as_str();
    let args = if text.contains('(') {
        let mut args = Vec::new();
        for arg in inner {
            let a = arg.into_inner().next().unwrap();
            args.push(match a.as_rule() {
                Rule::annotation_kwarg => {
                    let mut kv = a.into_inner();
                    let key = kv.next().unwrap().as_str().to_string();
                    AnnotationArg3::Named(key, annotation_value(kv.next().unwrap())?)
                }
                _ => AnnotationArg3::Positional(annotation_value(a)?),
            });
        }
        Some(args)
    } else {
        None
    };
    Ok(Annotation3 { name, args })
}

fn annotation_value(pair: Pair<Rule>) -> R<AnnotationValue3> {
    let v = pair.into_inner().next().unwrap();
    Ok(match v.as_rule() {
        Rule::literal3 => AnnotationValue3::Literal(literal(v)?),
        Rule::variable => AnnotationValue3::Variable(v.as_str()[1..].to_string()),
        Rule::meta_field => AnnotationValue3::MetaField(v.as_str()[1..].to_string()),
        _ => AnnotationValue3::Ident(v.as_str().to_string()),
    })
}

fn read_body(pair: Pair<Rule>) -> R<ReadBody3> {
    let mut body = ReadBody3 {
        clauses: Vec::new(),
        distinct: false,
        projections: Vec::new(),
        order: Vec::new(),
        limit: None,
    };
    for section in pair.into_inner() {
        match section.as_rule() {
            Rule::match_clause3 => {
                for c in section.into_inner() {
                    if c.as_rule() == Rule::clause3 {
                        body.clauses.push(clause(c)?);
                    }
                }
            }
            Rule::return_clause3 => {
                for p in section.into_inner() {
                    match p.as_rule() {
                        Rule::kw_distinct => body.distinct = true,
                        Rule::projection3 => body.projections.push(projection(p)?),
                        _ => {}
                    }
                }
            }
            Rule::order_clause3 => {
                for o in section.into_inner() {
                    if o.as_rule() == Rule::ordering3 {
                        let mut parts = o.into_inner();
                        let expr = expr(parts.next().unwrap())?;
                        let descending = parts.next().map(|d| d.as_str() == "desc");
                        body.order.push(Ordering3 { expr, descending });
                    }
                }
            }
            Rule::limit_clause3 => {
                let value = section
                    .into_inner()
                    .find(|p| p.as_rule() != Rule::kw_limit)
                    .unwrap();
                body.limit =
                    Some(match value.as_rule() {
                        Rule::variable => Limit3::Param(value.as_str()[1..].to_string()),
                        _ => Limit3::Literal(value.as_str().parse::<u64>().map_err(|e| {
                            diagnostic_at(Q005, format!("invalid limit: {e}"), &value)
                        })?),
                    });
            }
            _ => {}
        }
    }
    Ok(body)
}

fn projection(pair: Pair<Rule>) -> R<Projection3> {
    let mut inner = pair.into_inner();
    let e = expr(inner.next().unwrap())?;
    let mut alias = None;
    for part in inner {
        match part.as_rule() {
            Rule::reserved_alias => {
                return Err(diagnostic_at(
                    Q005,
                    format!(
                        "`{}` is a reserved word and cannot be a return alias",
                        part.as_str()
                    ),
                    &part,
                ));
            }
            Rule::expr_ident => alias = Some(part.as_str().to_string()),
            _ => {}
        }
    }
    Ok(Projection3 { expr: e, alias })
}

fn write_body(pair: Pair<Rule>) -> R<WriteBody3> {
    let mut groups = Vec::new();
    for part in pair.into_inner() {
        match part.as_rule() {
            Rule::write_group3 => {
                let mut clauses = Vec::new();
                let mut statements = Vec::new();
                for p in part.into_inner() {
                    match p.as_rule() {
                        Rule::clause3 => clauses.push(clause(p)?),
                        Rule::mutation_stmt3 => statements.push(statement(p)?),
                        _ => {}
                    }
                }
                groups.push(WriteGroup3 {
                    clauses,
                    statements,
                });
            }
            Rule::clause_after_stmt => {
                return Err(diagnostic_at(
                    Q013,
                    "a clause follows the last statement; every group of a write body ends with its statements",
                    &part,
                )
                .with_fix(
                    "move the binding, traversal or filter above the `insert`, `update` or `delete` it selects for",
                ));
            }
            _ => {}
        }
    }
    Ok(WriteBody3 { groups })
}

fn statement(pair: Pair<Rule>) -> R<Stmt3> {
    let s = pair.into_inner().next().unwrap();
    match s.as_rule() {
        Rule::insert_stmt3 => {
            let mut inner = s.into_inner().filter(|p| p.as_rule() != Rule::kw_insert);
            let type_name = inner.next().unwrap().as_str().to_string();
            let assignments = inner.map(assignment).collect::<R<Vec<_>>>()?;
            Ok(Stmt3::Insert {
                type_name,
                assignments,
            })
        }
        Rule::update_stmt3 => {
            let mut inner = s
                .into_inner()
                .filter(|p| !matches!(p.as_rule(), Rule::kw_update | Rule::kw_set));
            let variable = inner.next().unwrap().as_str()[1..].to_string();
            let assignments = inner.map(assignment).collect::<R<Vec<_>>>()?;
            Ok(Stmt3::Update {
                variable,
                assignments,
            })
        }
        Rule::delete_stmt3 => {
            let var = s
                .into_inner()
                .find(|p| p.as_rule() == Rule::variable)
                .unwrap();
            Ok(Stmt3::Delete {
                variable: var.as_str()[1..].to_string(),
            })
        }
        Rule::where_stmt => Err(where_statement(s)),
        other => unreachable!("grammar: mutation_stmt3 holds no {other:?}"),
    }
}

/// `delete T where φ` / `update T set { … } where φ`: the fix binds the type
/// and writes `φ` as a filter clause, each bare property and meta-field of
/// `φ` prefixed `$x.` (the rewriter's row, RFC section 6).
fn where_statement(s: Pair<Rule>) -> QueryDiagnostic {
    let at = s.clone();
    let mut kind = "";
    let mut type_name = "";
    let mut bound = None;
    let mut set = Vec::new();
    let mut predicate = None;
    for part in s.into_inner() {
        match part.as_rule() {
            Rule::kw_update => kind = "update",
            Rule::kw_delete => kind = "delete",
            Rule::type_name => type_name = part.as_str(),
            Rule::variable => bound = Some(part.as_str()),
            Rule::mutation_assignment3 => set.push(
                part.as_str()
                    .trim()
                    .trim_end_matches(',')
                    .trim()
                    .to_string(),
            ),
            Rule::expr3 => predicate = Some(part),
            _ => {}
        }
    }
    let predicate = predicate.expect("grammar: where_stmt holds its predicate");
    if let Some(var) = bound {
        let statement = match kind {
            "update" => format!("update {var} set {{ {} }}", set.join(" ")),
            _ => format!("delete {var}"),
        };
        return diagnostic_at(
            Q008,
            format!(
                "a statement takes no `where`; the clauses before it select the rows `{var}` names"
            ),
            &at,
        )
        .with_fix(format!("{}  {statement}", predicate.as_str().trim()));
    }
    let filter = qualify_bare_names(&predicate, "x");
    let filter = filter.trim();
    let statement = match kind {
        "update" => format!("update $x set {{ {} }}", set.join(" ")),
        _ => "delete $x".to_string(),
    };
    diagnostic_at(
        Q008,
        format!(
            "`{kind} {type_name} … where …` is edition 2's form; in edition 3 a statement names a bound variable and the clauses select its rows"
        ),
        &at,
    )
    .with_fix(format!("$x: {type_name}  {filter}  {statement}"))
}

/// `φ` with each bare identifier and meta-field in operand position written
/// as a property of `$var`.
fn qualify_bare_names(predicate: &Pair<Rule>, var: &str) -> String {
    let source = predicate.as_str();
    let base = predicate.as_span().start();
    let mut edits: Vec<(usize, usize)> = predicate
        .clone()
        .into_inner()
        .flatten()
        .filter(|p| p.as_rule() == Rule::operand3)
        .filter_map(|p| p.into_inner().next())
        .filter(|p| matches!(p.as_rule(), Rule::expr_ident | Rule::meta_field))
        .map(|p| (p.as_span().start() - base, p.as_span().end() - base))
        .collect();
    edits.sort();
    let mut out = String::new();
    let mut at = 0;
    for (start, _) in edits {
        out.push_str(&source[at..start]);
        out.push_str(&format!("${var}."));
        at = start;
    }
    out.push_str(&source[at..]);
    out
}

fn assignment(pair: Pair<Rule>) -> R<Assignment3> {
    let mut inner = pair.into_inner();
    let property = inner.next().unwrap().as_str().to_string();
    let value = inner.next().unwrap();
    let value = match value.as_rule() {
        Rule::null_value => Expr3::Literal(Literal::Null),
        _ => expr(value)?,
    };
    Ok(Assignment3 { property, value })
}

fn clauses_of(pair: Pair<Rule>) -> R<Vec<Clause3>> {
    pair.into_inner()
        .filter(|p| p.as_rule() == Rule::clause3)
        .map(clause)
        .collect()
}

fn clause(pair: Pair<Rule>) -> R<Clause3> {
    let c = pair.into_inner().next().unwrap();
    match c.as_rule() {
        Rule::let_clause => {
            let mut inner = c.into_inner().filter(|p| p.as_rule() != Rule::kw_let);
            let name = inner.next().unwrap().as_str()[1..].to_string();
            let value = inner.next().unwrap();
            let value = match value.as_rule() {
                Rule::inline_read => LetValue3::Relation(Box::new(inline_read(value)?)),
                _ => LetValue3::Expr(expr(value)?),
            };
            Ok(Clause3::Let { name, value })
        }
        Rule::from_clause => {
            let rel = c
                .into_inner()
                .find(|p| p.as_rule() == Rule::relation_expr)
                .unwrap();
            Ok(Clause3::From(relation(rel)?))
        }
        Rule::negation3 => Ok(Clause3::Not(clauses_of(c)?)),
        Rule::optional_block => Ok(Clause3::Optional(clauses_of(c)?)),
        Rule::binding3 => {
            let mut inner = c.into_inner();
            let variable = inner.next().unwrap().as_str()[1..].to_string();
            let type_name = inner.next().unwrap().as_str().to_string();
            let mut prop_matches = Vec::new();
            if let Some(list) = inner.next() {
                for pm in list.into_inner() {
                    let mut kv = pm.into_inner();
                    let key = kv.next().unwrap().as_str().to_string();
                    prop_matches.push((key, expr(kv.next().unwrap())?));
                }
            }
            Ok(Clause3::Binding {
                variable,
                type_name,
                prop_matches,
            })
        }
        Rule::binding_brace_filter => Err(brace_filter(c)),
        Rule::traversal3 => Ok(Clause3::Traversal(TraversalEq(traversal(c)?))),
        Rule::arrow_traversal => Err(arrow(c)),
        Rule::list_to_rows => {
            let first = c.clone().into_inner().next().unwrap();
            let written = match first.as_rule() {
                Rule::kw_for => "`for $x in …`",
                Rule::kw_unwind => "`unwind … as $x`",
                _ => "`let … in …`",
            };
            Err(diagnostic_at(
                Q012,
                format!(
                    "{written} is not GQ: `let` names one value and `from` draws rows from a relation; a list has no row form"
                ),
                &c,
            )
            .with_fix("`from <relation>` for rows, or `$x in $list` against a binding"))
        }
        Rule::filter3 => Ok(Clause3::Filter(expr(c.into_inner().next().unwrap())?)),
        other => unreachable!("grammar: clause3 holds no {other:?}"),
    }
}

fn brace_filter(c: Pair<Rule>) -> QueryDiagnostic {
    let mut inner = c.clone().into_inner();
    let var = inner.next().unwrap().as_str();
    let ty = inner.next().unwrap().as_str();
    let body = inner.next().unwrap().as_str().trim();
    let qualified = if body.starts_with(|ch: char| ch.is_ascii_lowercase() || ch == '_') {
        format!("{var}.{body}")
    } else {
        body.to_string()
    };
    diagnostic_at(
        Q010,
        format!("the braces of a binding hold `property: value` matches; `{body}` is a comparison"),
        &c,
    )
    .with_fix(format!("{var}: {ty}  {qualified}"))
}

/// `-e->`, `<-e-`, `-e-`: a parse error whose fix is the triple.
fn arrow(c: Pair<Rule>) -> QueryDiagnostic {
    let at = c.clone();
    let mut vars = Vec::new();
    let mut open = "";
    let mut close = "";
    let mut edge = String::new();
    let mut bounds = String::new();
    let mut binding = String::new();
    for part in c.into_inner() {
        match part.as_rule() {
            Rule::variable => vars.push(part.as_str()),
            Rule::arrow_open => open = part.as_str(),
            Rule::arrow_close => close = part.as_str(),
            Rule::edge_binding => binding = part.as_str().replace(' ', ""),
            Rule::edge_sel3 => edge = part.as_str().to_string(),
            Rule::traversal_bounds => bounds = part.as_str().to_string(),
            _ => {}
        }
    }
    let (a, b) = (vars[0], vars[1]);
    let fix = match (open, close) {
        ("-", "->") => format!("{a} {binding}{edge}{bounds} {b}"),
        ("<-", "-") => format!("{b} {binding}{edge}{bounds} {a}"),
        _ => format!("{a} {binding}<{edge}>{bounds} {b}"),
    };
    diagnostic_at(
        Q007,
        "a traversal is a triple: the source, the edge and the target, with no arrow",
        &at,
    )
    .with_fix(fix)
}

fn traversal(pair: Pair<Rule>) -> R<Traversal> {
    let mut inner = pair.into_inner();
    let src = inner.next().unwrap().as_str()[1..].to_string();
    let mut next = inner.next().unwrap();
    let mut edge_binding = None;
    if next.as_rule() == Rule::edge_binding {
        edge_binding = Some(next.into_inner().next().unwrap().as_str()[1..].to_string());
        next = inner.next().unwrap();
    }
    let (selector, undirected) = match next.as_rule() {
        Rule::undirected_edge3 => (edge_selector(next.into_inner().next().unwrap())?, true),
        _ => (edge_selector(next)?, false),
    };
    let mut min_hops = 1;
    let mut max_hops = Some(1);
    let mut next = inner.next().unwrap();
    if next.as_rule() == Rule::traversal_bounds {
        let (min, max) = parse_traversal_bounds(next).map_err(into_diagnostic)?;
        min_hops = min;
        max_hops = max;
        next = inner.next().unwrap();
    }
    Ok(Traversal {
        src,
        selector,
        dst: next.as_str()[1..].to_string(),
        min_hops,
        max_hops,
        undirected,
        edge_binding,
    })
}

fn edge_selector(pair: Pair<Rule>) -> R<EdgeSelector> {
    match pair.as_rule() {
        Rule::edge_sel3 | Rule::edge_alternation3 => {
            edge_selector(pair.into_inner().next().unwrap())
        }
        Rule::edge_wildcard => Ok(EdgeSelector::Wildcard),
        Rule::edge_alternatives3 => Ok(EdgeSelector::Alternation(
            pair.into_inner().map(edge_name).collect::<R<Vec<_>>>()?,
        )),
        _ => Ok(EdgeSelector::Named(edge_name(pair)?)),
    }
}

fn edge_name(pair: Pair<Rule>) -> R<String> {
    match pair.as_rule() {
        Rule::catalogue_edge | Rule::edge_ident3 => Ok(pair.as_str().to_string()),
        _ => parse_edge_name(pair).map_err(into_diagnostic),
    }
}

fn into_diagnostic(error: CompilerError) -> QueryDiagnostic {
    compiler_error_to_diagnostic(error, Position::at("", 0))
}

fn inline_read(pair: Pair<Rule>) -> R<ReadBody3> {
    read_body(pair.into_inner().next().unwrap())
}

fn relation(pair: Pair<Rule>) -> R<RelExpr3> {
    let r = pair.into_inner().next().unwrap();
    match r.as_rule() {
        Rule::inline_read => Ok(RelExpr3::Inline(Box::new(inline_read(r)?))),
        Rule::call => Ok(RelExpr3::Call(call(r)?)),
        Rule::variable => Ok(RelExpr3::Variable(r.as_str()[1..].to_string())),
        other => unreachable!("grammar: relation_expr holds no {other:?}"),
    }
}

fn call(pair: Pair<Rule>) -> R<Call3> {
    let mut name = String::new();
    let mut args = Vec::new();
    let mut options = Vec::new();
    for part in pair.into_inner() {
        match part.as_rule() {
            Rule::call_head => {
                name = part.into_inner().next().unwrap().as_str().to_string();
            }
            Rule::call_args => {
                for a in part.into_inner() {
                    match a.as_rule() {
                        Rule::positional => {
                            let v = a.into_inner().next().unwrap();
                            args.push(match v.as_rule() {
                                Rule::inline_read => Arg3::Inline(Box::new(inline_read(v)?)),
                                _ => Arg3::Expr(expr(v)?),
                            });
                        }
                        Rule::call_option => {
                            let mut kv = a.into_inner();
                            let key = kv.next().unwrap().as_str().to_string();
                            options.push((key, expr(kv.next().unwrap())?));
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }
    Ok(Call3 {
        name,
        args,
        options,
    })
}

fn agg_func(name: &str) -> AggFunc {
    match name {
        "count" => AggFunc::Count,
        "sum" => AggFunc::Sum,
        "avg" => AggFunc::Avg,
        "min" => AggFunc::Min,
        _ => AggFunc::Max,
    }
}

fn comp_op(op: &str) -> CompOp {
    match op {
        "=" => CompOp::Eq,
        "!=" => CompOp::Ne,
        ">" => CompOp::Gt,
        "<" => CompOp::Lt,
        ">=" => CompOp::Ge,
        _ => CompOp::Le,
    }
}

fn expr(pair: Pair<Rule>) -> R<Expr3> {
    match pair.as_rule() {
        Rule::expr3 => expr(pair.into_inner().next().unwrap()),
        Rule::or_expr3 | Rule::and_expr3 => {
            let is_or = pair.as_rule() == Rule::or_expr3;
            let mut operands = pair
                .into_inner()
                .filter(|p| !matches!(p.as_rule(), Rule::kw_and | Rule::kw_or));
            let first = expr(operands.next().unwrap())?;
            operands.try_fold(first, |left, right| {
                let right = Box::new(expr(right)?);
                Ok(if is_or {
                    Expr3::Or(Box::new(left), right)
                } else {
                    Expr3::And(Box::new(left), right)
                })
            })
        }
        Rule::not_expr3 => {
            let mut parts = pair.into_inner();
            let first = parts.next().unwrap();
            if first.as_rule() == Rule::kw_not {
                Ok(Expr3::Not(Box::new(expr(parts.next().unwrap())?)))
            } else {
                expr(first)
            }
        }
        Rule::comparison3 => {
            let mut parts = pair.into_inner();
            let left_pair = parts.next().unwrap();
            let left_source = left_pair.as_str().trim().to_string();
            let left = expr(left_pair)?;
            match parts.next() {
                None => Ok(left),
                Some(op) => match op.as_rule() {
                    Rule::comp_op => Ok(Expr3::Compare {
                        op: comp_op(op.as_str()),
                        left: Box::new(left),
                        right: Box::new(expr(parts.next().unwrap())?),
                    }),
                    Rule::kw_in => Ok(Expr3::In {
                        needle: Box::new(left),
                        list: Box::new(expr(parts.next().unwrap())?),
                    }),
                    Rule::infix_string_op => {
                        let right = parts.next().unwrap();
                        let fix =
                            format!("{}({left_source}, {})", op.as_str(), right.as_str().trim());
                        Err(diagnostic_at(
                            Q012,
                            format!(
                                "`{}` is a function in edition 3, called with its operands",
                                op.as_str()
                            ),
                            &op,
                        )
                        .with_fix(fix))
                    }
                    _ => Ok(Expr3::IsNull {
                        expr: Box::new(left),
                        negated: op.into_inner().any(|k| k.as_rule() == Rule::kw_not),
                    }),
                },
            }
        }
        Rule::sum | Rule::product => {
            let mut parts = pair.into_inner();
            let mut left = expr(parts.next().unwrap())?;
            while let Some(op) = parts.next() {
                let right = expr(parts.next().unwrap())?;
                let symbol = op.as_str();
                if symbol == "||" {
                    return Err(diagnostic_at(
                        Q012,
                        "`||` is not an operator: concatenation is the registry call `concat`",
                        &op,
                    )
                    .with_fix("concat(a, b)"));
                }
                let op = match symbol {
                    "+" => ArithOp::Add,
                    "-" => ArithOp::Sub,
                    "*" => ArithOp::Mul,
                    "/" => ArithOp::Div,
                    _ => ArithOp::Rem,
                };
                left = Expr3::Arith {
                    op,
                    left: Box::new(left),
                    right: Box::new(right),
                };
            }
            Ok(left)
        }
        Rule::unary => {
            let mut parts = pair.into_inner();
            let first = parts.next().unwrap();
            if first.as_rule() == Rule::unary_minus {
                Ok(Expr3::Neg(Box::new(expr(parts.next().unwrap())?)))
            } else {
                expr(first)
            }
        }
        Rule::operand3 => operand(pair),
        other => unreachable!("grammar: no expression rule {other:?}"),
    }
}

fn operand(pair: Pair<Rule>) -> R<Expr3> {
    let inner = pair.into_inner().next().unwrap();
    match inner.as_rule() {
        Rule::expr3 => expr(inner),
        Rule::block_value => {
            let mut func = None;
            let mut arg = None;
            let mut clauses = Vec::new();
            for part in inner.into_inner() {
                match part.as_rule() {
                    Rule::agg_name => func = Some(BlockFunc3::Agg(agg_func(part.as_str()))),
                    Rule::kw_exists => func = Some(BlockFunc3::Exists),
                    Rule::expr3 => arg = Some(Box::new(expr(part)?)),
                    Rule::subquery_block3 => clauses = clauses_of(part)?,
                    _ => {}
                }
            }
            Ok(Expr3::Block(Box::new(Block3 {
                func: func.unwrap(),
                arg,
                clauses,
            })))
        }
        Rule::agg_distinct => {
            let mut parts = inner.into_inner();
            let func = agg_func(parts.next().unwrap().as_str());
            let arg = parts.find(|p| p.as_rule() == Rule::expr3).unwrap();
            Ok(Expr3::AggDistinct {
                func,
                arg: Box::new(expr(arg)?),
            })
        }
        Rule::literal3 => Ok(Expr3::Literal(literal(inner)?)),
        Rule::case_keyword => Err(diagnostic_at(
            Q012,
            "`CASE` is not GQ: a conditional is the registry call `if`",
            &inner,
        )
        .with_fix("if(condition, then, else)")),
        Rule::call => Ok(Expr3::Call(call(inner)?)),
        Rule::prop_access3 => {
            let mut parts = inner.into_inner();
            let variable = parts.next().unwrap().as_str()[1..].to_string();
            let property = parts.next().unwrap().as_str().to_string();
            Ok(Expr3::Prop { variable, property })
        }
        Rule::meta_field => Ok(Expr3::Meta(inner.as_str()[1..].to_string())),
        Rule::variable => Ok(Expr3::Variable(inner.as_str()[1..].to_string())),
        Rule::reserved_property => Err(diagnostic_at(
            Q005,
            format!(
                "`{}` is a reserved word; a property of that name is written `$p.{}`",
                inner.as_str(),
                inner.as_str()
            ),
            &inner,
        )),
        Rule::expr_ident => Ok(Expr3::Ident(inner.as_str().to_string())),
        other => unreachable!("grammar: operand3 holds no {other:?}"),
    }
}

fn literal(pair: Pair<Rule>) -> R<Literal> {
    let inner = pair.into_inner().next().unwrap();
    let parse_err =
        |e: CompilerError, at: &Pair<Rule>| compiler_error_to_diagnostic(e, position_of(at));
    Ok(match inner.as_rule() {
        Rule::string_lit => {
            Literal::String(parse_string_lit(&inner).map_err(|e| parse_err(e, &inner))?)
        }
        Rule::integer3 => {
            Literal::Integer(i128::from(inner.as_str().parse::<i64>().map_err(|e| {
                diagnostic_at(Q005, format!("invalid integer: {e}"), &inner)
            })?))
        }
        Rule::float_lit3 => Literal::Float(
            inner
                .as_str()
                .parse::<f64>()
                .map_err(|e| diagnostic_at(Q005, format!("invalid float: {e}"), &inner))?,
        ),
        Rule::bool_lit => Literal::Bool(inner.as_str() == "true"),
        Rule::date_lit | Rule::datetime_lit => {
            let is_date = inner.as_rule() == Rule::date_lit;
            let s = inner.clone().into_inner().next().unwrap();
            let text = parse_string_lit(&s).map_err(|e| parse_err(e, &s))?;
            if is_date {
                Literal::Date(text)
            } else {
                Literal::DateTime(text)
            }
        }
        Rule::list_lit3 => Literal::List(inner.into_inner().map(literal).collect::<R<Vec<_>>>()?),
        other => unreachable!("grammar: literal3 holds no {other:?}"),
    })
}
