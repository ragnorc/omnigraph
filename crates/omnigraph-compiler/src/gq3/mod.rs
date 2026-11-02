//! SPIKE (throwaway, branch `spike/gq3-1202`): an edition-3 front end for GQ,
//! built from RFC PR #1202 to test whether its freeze list is a correct
//! foundation. A source whose first token is `gq 3;` is parsed by
//! `gq3.pest`, its slots are recorded as type-check refusals, and the working
//! constructs are desugared onto today's `QueryDecl`, so the existing type
//! checker, lowering, planner and Lance executor run them unchanged. The
//! findings, not this code, are the deliverable: `docs/spikes/gq3-1202.md`.

#[cfg(test)]
mod corpus_tests;
#[cfg(test)]
mod later_tests;
pub mod migrate;
pub mod registry;
#[cfg(test)]
pub mod tests;

use std::collections::HashMap;

use pest::Parser;
use pest::error::InputLocation;
use pest::iterators::Pair;
use pest_derive::Parser;

use crate::error::decode_string_literal;
use crate::query::ast::*;
use crate::query::codes::{Q001, Q002, Q006, Q007, Q008, Q009, T60, T61, T62, T63, T64, T65};
use crate::query::diagnostic::{Applicability, Position, QueryDiagnostic, Suggestion, TextEdit};

#[derive(Parser)]
#[grammar = "gq3/gq3.pest"]
pub struct Gq3Parser;

/// What edition 3 adds to a declaration beyond today's `QueryDecl`: the
/// slot refusals the type checker raises first, `having`, `limit $k`, the
/// retrieve arms, parameter defaults and retained annotations.
#[derive(Debug, Clone, Default)]
pub struct Gq3Extras {
    /// Type-check refusals, in source order; the first one is raised.
    pub refusals: Vec<QueryDiagnostic>,
    pub having: Option<Expr>,
    pub limit_param: Option<String>,
    pub arms: Vec<Arm>,
    pub defaults: Vec<(String, Literal)>,
    /// Unknown annotations, retained as metadata.
    pub annotations: Vec<(String, String)>,
}

/// One `retrieve` arm: `$<variable>.<name>: <call>`.
#[derive(Debug, Clone)]
pub struct Arm {
    pub variable: String,
    pub name: String,
    pub call: Expr,
}

/// The `limit` a declaration with `limit $k` carries until the parameter is
/// bound; it satisfies the "ranking requires a limit" rules and is replaced
/// before planning.
pub const LIMIT_PARAM_PLACEHOLDER: u64 = u64::MAX;

/// The owners the slot refusals name.
pub mod owner {
    pub const DERIVED: &str = "the derived relations and optional patterns RFC (new)";
    pub const BATCH: &str = "the agent-first batch RFC";
    pub const WRITES: &str = "the writes RFC (new)";
    pub const POLY: &str = "the polymorphic types RFC (PR #884)";
    pub const NAMESPACES: &str = "the schema namespaces RFC (PR #1015)";
    pub const CATALOGUE: &str = "the engine catalogue RFC's schema extension";
    pub const BRANCH: &str = "the branch-statements RFC";
    pub const SEARCH: &str = "RFC 0048 (PR #793)";
    pub const LEXICAL: &str = "the analyzed lexical search RFC (PR #792)";
    pub const SCHEMA_IN_QUERY: &str =
        "no RFC: the GQ 3 RFC does not say what a query source holding a schema declaration means";
}

pub const RESERVED_CATALOGUE_TYPES: &[&str] = &[
    "@Type",
    "@Property",
    "@Edge",
    "@Interface",
    "@Index",
    "@Function",
    "@View",
];
pub const RESERVED_ANNOTATIONS: &[&str] = &["budget", "approximate", "satisfice", "brief", "phase"];
/// `@example` and `@if_commit` are "typed and working" in the RFC; the
/// compiler has no mechanism for either (review finding 5), so the spike
/// refuses them as slots.
pub const UNSERVED_TYPED_ANNOTATIONS: &[&str] = &["example", "if_commit"];

pub fn slot(construct: &str, owner: &str) -> QueryDiagnostic {
    QueryDiagnostic::typecheck(
        T60,
        format!("{construct} is a reserved GQ 3 construct, not yet served in this release"),
    )
    .with_fix(format!(
        "not yet served in this release; its semantics belong to {owner}"
    ))
}

// ---------------------------------------------------------------------------
// The edition line

/// `gq N;` as the first token of a source.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EditionLine {
    pub edition: u32,
    pub start: usize,
    pub end: usize,
}

pub fn edition_line(input: &str) -> Option<EditionLine> {
    let start = input.len() - skip_trivia(input).len();
    let rest = &input[start..];
    let after = rest.strip_prefix("gq")?;
    if after.starts_with(|c: char| c.is_ascii_alphanumeric() || c == '_') {
        return None;
    }
    let digits_at = after.len() - after.trim_start().len();
    let after = after.trim_start();
    let digits: String = after.chars().take_while(char::is_ascii_digit).collect();
    if digits.is_empty() {
        return None;
    }
    let tail = after[digits.len()..].trim_start();
    let tail = tail.strip_prefix(';')?;
    let end = input.len() - tail.len();
    let _ = digits_at;
    Some(EditionLine {
        edition: digits.parse().ok()?,
        start,
        end,
    })
}

fn skip_trivia(mut input: &str) -> &str {
    loop {
        let trimmed = input.trim_start_matches([' ', '\t', '\r', '\n']);
        if let Some(rest) = trimmed.strip_prefix("//") {
            input = rest.split_once('\n').map_or("", |(_, tail)| tail);
        } else if let Some(rest) = trimmed.strip_prefix("/*") {
            input = rest.split_once("*/").map_or("", |(_, tail)| tail);
        } else {
            return trimmed;
        }
    }
}

/// `input` with `start..end` replaced by spaces, so byte offsets survive.
pub fn blank(input: &str, start: usize, end: usize) -> String {
    let mut out = String::with_capacity(input.len());
    out.push_str(&input[..start]);
    out.extend(
        input[start..end]
            .chars()
            .map(|c| if c == '\n' { '\n' } else { ' ' }),
    );
    out.push_str(&input[end..]);
    out
}

/// The edition-dispatching front door, called by `parse_query_diagnostic`.
pub fn dispatch(input: &str) -> Option<Result<QueryFile, QueryDiagnostic>> {
    let line = edition_line(input)?;
    Some(match line.edition {
        3 => parse_file(input),
        2 => crate::query::parser::parse_query_diagnostic_edition2(&blank(
            input, line.start, line.end,
        )),
        other => Err(QueryDiagnostic::parse(
            Q006,
            format!("unknown edition {other}; this release compiles editions 2 and 3"),
            Some(Position::at(input, line.start)),
        )
        .with_fix("write `gq 3;` or omit the edition line")),
    })
}

// ---------------------------------------------------------------------------
// Parsing

type R<T> = Result<T, QueryDiagnostic>;

fn at(input: &str, pair: &Pair<Rule>) -> Position {
    Position::at(input, pair.as_span().start())
}

fn perr(input: &str, pair: &Pair<Rule>, message: impl Into<String>) -> QueryDiagnostic {
    QueryDiagnostic::parse(Q001, message, Some(at(input, pair)))
}

pub fn pest_error(input: &str, err: pest::error::Error<Rule>) -> QueryDiagnostic {
    let byte = match err.location {
        InputLocation::Pos(pos) => pos,
        InputLocation::Span((start, _)) => start,
    };
    let message = match &err.variant {
        pest::error::ErrorVariant::ParsingError { positives, .. } => format!(
            "expected {}",
            positives
                .iter()
                .map(|rule| format!("{rule:?}"))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        pest::error::ErrorVariant::CustomError { message } => message.clone(),
    };
    QueryDiagnostic::parse(Q001, message, Some(Position::at(input, byte)))
}

/// Parse an edition-3 source into today's file shape.
pub fn parse_file(input: &str) -> R<QueryFile> {
    let file = Gq3Parser::parse(Rule::file, input)
        .map_err(|error| pest_error(input, error))?
        .next()
        .expect("file");
    let mut settings_span: Option<(usize, usize)> = None;
    let mut decls = Vec::new();
    let mut statement = None;
    let mut explain = None;
    for item in file.into_inner() {
        match item.as_rule() {
            Rule::edition => {
                let n: u32 = item
                    .as_str()
                    .trim_start_matches("gq")
                    .trim()
                    .trim_end_matches(';')
                    .trim()
                    .parse()
                    .unwrap_or(0);
                if n != 3 {
                    return Err(QueryDiagnostic::parse(
                        Q006,
                        format!("unknown edition {n}"),
                        Some(at(input, &item)),
                    ));
                }
            }
            Rule::setting_stmt => {
                let span = item.as_span();
                settings_span = Some(match settings_span {
                    None => (span.start(), span.end()),
                    Some((start, _)) => (start, span.end()),
                });
            }
            Rule::show_stmt | Rule::branch_stmt | Rule::commit_stmt | Rule::load_stmt => {
                statement = Some(item.as_span().start());
            }
            Rule::explain_stmt => {
                let inner = item.clone().into_inner().nth(1).expect("explained");
                match inner.as_rule() {
                    Rule::query_decl | Rule::mutation_decl => {
                        explain = Some(decl(input, inner)?);
                    }
                    _ => statement = Some(item.as_span().start()),
                }
            }
            Rule::declaration => decls.push(declaration(input, item)?),
            Rule::misplaced_edition => {
                return Err(QueryDiagnostic::parse(
                    Q007,
                    "an edition line must be the first statement of a source",
                    Some(at(input, &item)),
                )
                .with_fix("move `gq 3;` to the first line"));
            }
            Rule::missing_param_list => {
                let name = item
                    .clone()
                    .into_inner()
                    .nth(1)
                    .map(|p| p.as_str().to_string())
                    .unwrap_or_default();
                let keyword = item
                    .as_str()
                    .split_whitespace()
                    .next()
                    .unwrap_or("query")
                    .to_string();
                return Err(QueryDiagnostic::parse(
                    Q002,
                    format!("`{keyword} {name}` is missing its parameter list"),
                    Some(at(input, &item)),
                )
                .with_fix(format!("write `{keyword} {name}()`")));
            }
            Rule::statement_trailer => {
                return Err(QueryDiagnostic::parse(
                    crate::query::codes::Q004,
                    "a statement stands alone in its source",
                    Some(at(input, &item)),
                ));
            }
            _ => {}
        }
    }
    // Statements and settings are unchanged in edition 3: the edition-2
    // parser reads them from the source with the edition line blanked.
    let line = edition_line(input);
    let blanked = match line {
        Some(line) => blank(input, line.start, line.end),
        None => input.to_string(),
    };
    if statement.is_some() {
        return crate::query::parser::parse_query_diagnostic_edition2(&blanked);
    }
    let settings = match settings_span {
        None => Vec::new(),
        Some((start, end)) => {
            let only = format!("{}{}", " ".repeat(start), &input[start..end]);
            crate::query::parser::parse_query_diagnostic_edition2(&only)?.settings
        }
    };
    let body = match explain {
        Some(decl) => FileBody::Explain(decl),
        None => FileBody::Queries(decls),
    };
    Ok(QueryFile { settings, body })
}

fn declaration(input: &str, pair: Pair<Rule>) -> R<QueryDecl> {
    let inner = pair.into_inner().next().expect("declaration");
    match inner.as_rule() {
        Rule::query_decl | Rule::mutation_decl => decl(input, inner),
        Rule::view_decl => {
            let name = inner
                .clone()
                .into_inner()
                .nth(1)
                .map(|p| p.as_str().to_string())
                .unwrap_or_default();
            Ok(placeholder(
                format!("view:{name}"),
                slot("a `view` declaration", owner::DERIVED),
            ))
        }
        Rule::batch_decl => {
            let name = inner
                .clone()
                .into_inner()
                .nth(1)
                .map(|p| p.as_str().to_string())
                .unwrap_or_default();
            // The batch's members still have to parse: walk them for errors.
            for member in inner
                .into_inner()
                .filter(|p| p.as_rule() == Rule::declaration)
            {
                declaration(input, member)?;
            }
            Ok(placeholder(
                format!("batch:{name}"),
                slot("a `batch` declaration", owner::BATCH),
            ))
        }
        Rule::schema_decl => {
            let name = inner
                .clone()
                .into_inner()
                .next()
                .and_then(|d| d.into_inner().next())
                .map(|p| p.as_str().to_string())
                .unwrap_or_default();
            Ok(placeholder(
                format!("schema:{name}"),
                slot(
                    "a schema declaration in a query source",
                    owner::SCHEMA_IN_QUERY,
                ),
            ))
        }
        other => Err(perr(
            input,
            &inner,
            format!("unexpected declaration {other:?}"),
        )),
    }
}

fn placeholder(name: String, refusal: QueryDiagnostic) -> QueryDecl {
    QueryDecl {
        name,
        description: None,
        instruction: None,
        params: Vec::new(),
        match_clause: Vec::new(),
        return_clause: Vec::new(),
        distinct: false,
        order_clause: Vec::new(),
        limit: None,
        mutations: Vec::new(),
        write_mode: MutationWriteMode::Publish,
        gq3: Some(Box::new(Gq3Extras {
            refusals: vec![refusal],
            ..Gq3Extras::default()
        })),
    }
}

/// Conversion state for one declaration.
struct Cx<'a> {
    input: &'a str,
    x: Gq3Extras,
    arms: HashMap<(String, String), Expr>,
    in_match: bool,
    in_retrieve: bool,
}

impl Cx<'_> {
    fn refuse(&mut self, diagnostic: QueryDiagnostic) {
        self.x.refusals.push(diagnostic);
    }
}

#[derive(Clone, Copy)]
enum Scope<'a> {
    Alias,
    Property(&'a str),
}

fn decl(input: &str, pair: Pair<Rule>) -> R<QueryDecl> {
    let is_mutation_kw = pair.as_rule() == Rule::mutation_decl;
    let mut cx = Cx {
        input,
        x: Gq3Extras::default(),
        arms: HashMap::new(),
        in_match: false,
        in_retrieve: false,
    };
    let mut inner = pair.into_inner();
    let keyword = inner.next().expect("keyword");
    let name = inner.next().expect("name").as_str().to_string();
    let mut d = QueryDecl {
        name: name.clone(),
        description: None,
        instruction: None,
        params: Vec::new(),
        match_clause: Vec::new(),
        return_clause: Vec::new(),
        distinct: false,
        order_clause: Vec::new(),
        limit: None,
        mutations: Vec::new(),
        write_mode: MutationWriteMode::Publish,
        gq3: None,
    };
    let mut write_clause = None;
    for item in inner {
        match item.as_rule() {
            Rule::param_list => {
                for p in item.into_inner() {
                    d.params.push(param(&mut cx, p)?);
                }
            }
            Rule::annotation => annotation(&mut cx, &mut d, item)?,
            Rule::write_clause => write_clause = Some(item.into_inner().next().expect("clause")),
            Rule::decl_body => {
                let body = item.into_inner().next().expect("body");
                match body.as_rule() {
                    Rule::read_body => {
                        if is_mutation_kw {
                            cx.refuse(
                                QueryDiagnostic::typecheck(
                                    T62,
                                    format!("`mutation {name}` has a read body"),
                                )
                                .with_fix("declare a read with `query`")
                                .with_suggestion(Suggestion {
                                    applicability: Applicability::MachineApplicable,
                                    edits: vec![TextEdit {
                                        start: keyword.as_span().start(),
                                        end: keyword.as_span().end(),
                                        replacement: "query".into(),
                                    }],
                                }),
                            );
                        }
                        read_body(&mut cx, &mut d, body)?;
                    }
                    _ => {
                        if !is_mutation_kw {
                            cx.refuse(
                                QueryDiagnostic::typecheck(
                                    T62,
                                    format!(
                                        "`query {name}` writes; a write is declared with `mutation`"
                                    ),
                                )
                                .with_fix(format!("write `mutation {name}(…)`"))
                                .with_suggestion(Suggestion {
                                    applicability: Applicability::MachineApplicable,
                                    edits: vec![TextEdit {
                                        start: keyword.as_span().start(),
                                        end: keyword.as_span().end(),
                                        replacement: "mutation".into(),
                                    }],
                                }),
                            );
                        }
                        mutation_body(&mut cx, &mut d, body)?;
                    }
                }
            }
            _ => {}
        }
    }
    if let Some(clause) = write_clause {
        d.write_mode = match clause.as_rule() {
            Rule::as_commit_clause => MutationWriteMode::AsCommit,
            _ => MutationWriteMode::Unattached {
                base: clause
                    .into_inner()
                    .find(|p| matches!(p.as_rule(), Rule::string_lit | Rule::variable))
                    .map(|p| match p.as_rule() {
                        Rule::variable => Ok(UnattachedBase::Param(p.as_str()[1..].to_string())),
                        _ => string(input, &p).map(UnattachedBase::Literal),
                    })
                    .transpose()?,
            },
        };
    }
    d.gq3 = Some(Box::new(cx.x));
    Ok(d)
}

fn param(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<Param> {
    let mut inner = pair.into_inner();
    let var = inner.next().expect("var").as_str()[1..].to_string();
    let ty = inner.next().expect("type");
    let nullable = ty.as_str().trim_end().ends_with('?');
    let core = ty.into_inner().next().expect("core");
    let type_name = match core.as_rule() {
        Rule::kind_type => {
            cx.refuse(slot(
                &format!("a `{}` parameter", core.as_str()),
                owner::BRANCH,
            ));
            "String".to_string()
        }
        Rule::list_type => format!("[{}]", core.into_inner().next().expect("item").as_str()),
        Rule::vector_type => {
            let mut parts = core.into_inner();
            let n = parts.next().expect("dim").as_str().to_string();
            if parts.next().is_some() {
                cx.refuse(slot("a `Vector` parameter with a distance", owner::SEARCH));
            }
            format!("Vector({n})")
        }
        _ => core.as_str().to_string(),
    };
    if let Some(default) = inner.next() {
        let literal = literal(cx.input, default)?;
        cx.x.defaults.push((var.clone(), literal));
    }
    Ok(Param {
        name: var,
        type_name,
        nullable,
    })
}

fn annotation(cx: &mut Cx<'_>, d: &mut QueryDecl, pair: Pair<Rule>) -> R<()> {
    let mut inner = pair.clone().into_inner();
    let name = inner.next().expect("name").as_str().to_string();
    let args: Vec<Pair<Rule>> = inner
        .next()
        .map(|args| args.into_inner().collect())
        .unwrap_or_default();
    let first_string = || -> R<String> {
        let item = args
            .first()
            .ok_or_else(|| perr(cx.input, &pair, format!("@{name} needs a string")))?;
        let lit = item.clone().into_inner().next().expect("item");
        let lit = if lit.as_rule() == Rule::literal {
            lit.into_inner().next().expect("lit")
        } else {
            lit
        };
        string(cx.input, &lit)
    };
    match name.as_str() {
        "description" => d.description = Some(first_string()?),
        "instruction" => d.instruction = Some(first_string()?),
        n if RESERVED_ANNOTATIONS.contains(&n) => cx.refuse(slot(
            &format!("the annotation `@{n}`"),
            "RFC 0048 and the agent-first batch",
        )),
        n if UNSERVED_TYPED_ANNOTATIONS.contains(&n) => {
            let owner = if n == "if_commit" {
                owner::WRITES
            } else {
                "the agent-first batch"
            };
            cx.refuse(slot(&format!("the annotation `@{n}`"), owner))
        }
        _ => cx.x.annotations.push((name, pair.as_str().to_string())),
    }
    Ok(())
}

fn read_body(cx: &mut Cx<'_>, d: &mut QueryDecl, body: Pair<Rule>) -> R<()> {
    let sections: Vec<Pair<Rule>> = body.into_inner().collect();
    // Arms first: their names are columns of their bindings everywhere after `match`.
    for section in &sections {
        if section.as_rule() == Rule::retrieve_clause {
            for arm in section
                .clone()
                .into_inner()
                .filter(|p| p.as_rule() == Rule::retrieve_arm)
            {
                let mut parts = arm.into_inner();
                let variable = parts.next().expect("var").as_str()[1..].to_string();
                let name = parts.next().expect("name").as_str().to_string();
                let call_pair = parts.next().expect("call");
                cx.in_retrieve = true;
                let call = call(cx, call_pair.clone(), Scope::Alias);
                cx.in_retrieve = false;
                let call = call?;
                if !matches!(call, Expr::Nearest { .. } | Expr::Bm25 { .. }) {
                    cx.refuse(
                        QueryDiagnostic::typecheck(T65, format!("`{}` is not a ranking; a retrieve arm is `nearest(…)` or `bm25(…)` in this release", call_pair.as_str()))
                            .with_fix("put a predicate in `match`; a later RFC adds arm kinds as registry rows"),
                    );
                }
                cx.arms
                    .insert((variable.clone(), name.clone()), call.clone());
                cx.x.arms.push(Arm {
                    variable,
                    name,
                    call,
                });
            }
        }
    }
    for section in sections {
        match section.as_rule() {
            Rule::match_clause => {
                cx.in_match = true;
                for c in section.into_inner().filter(|p| p.as_rule() == Rule::clause) {
                    d.match_clause.push(clause(cx, c)?);
                }
                cx.in_match = false;
            }
            Rule::return_clause => {
                for p in section.into_inner() {
                    match p.as_rule() {
                        Rule::kw_distinct => d.distinct = true,
                        Rule::projection => d.return_clause.push(projection(cx, p)?),
                        _ => {}
                    }
                }
            }
            Rule::having_clause => {
                let e = section.into_inner().nth(1).expect("expr");
                cx.x.having = Some(expr(cx, e, Scope::Alias)?);
            }
            Rule::order_clause => {
                for o in section
                    .into_inner()
                    .filter(|p| p.as_rule() == Rule::ordering)
                {
                    let mut parts = o.into_inner();
                    let key_pair = parts.next().expect("key");
                    let key = expr(cx, key_pair.clone(), Scope::Alias)?;
                    let dir = parts.next();
                    let descending = dir.as_ref().is_some_and(|p| p.as_str() == "desc");
                    if matches!(key, Expr::Nearest { .. }) && dir.is_some() {
                        cx.refuse(
                            QueryDiagnostic::typecheck(
                                crate::query::codes::T54,
                                "a `nearest` arm takes no order modifier",
                            )
                            .with_fix("drop the modifier"),
                        );
                    }
                    d.order_clause.push(Ordering {
                        expr: key,
                        descending,
                        explicit: dir.is_some(),
                    });
                }
            }
            Rule::limit_clause => {
                let v = section.into_inner().nth(1).expect("limit");
                match v.as_rule() {
                    Rule::integer => {
                        d.limit = Some(
                            v.as_str()
                                .parse()
                                .map_err(|e| perr(cx.input, &v, format!("invalid limit: {e}")))?,
                        )
                    }
                    _ => {
                        cx.x.limit_param = Some(v.as_str()[1..].to_string());
                        d.limit = Some(LIMIT_PARAM_PLACEHOLDER);
                    }
                }
            }
            _ => {}
        }
    }
    check_order_against_arms(cx, d);
    Ok(())
}

/// With a `retrieve` clause, every arm is read by the leading `order` key
/// (an arm, or `rrf` over arms) in this release.
fn check_order_against_arms(cx: &mut Cx<'_>, d: &QueryDecl) {
    if cx.x.arms.is_empty() {
        return;
    }
    let leading = d.order_clause.first().map(|o| &o.expr);
    let all_used =
        cx.x.arms
            .iter()
            .all(|arm| leading.is_some_and(|key| contains_expr(key, &arm.call)));
    if !all_used {
        cx.refuse(
            QueryDiagnostic::typecheck(
                T65,
                "every retrieve arm must be read by the leading `order` key in this release",
            )
            .with_fix("lead `order` with the arm, or with `rrf(arm, arm)` over two arms"),
        );
    }
}

fn contains_expr(hay: &Expr, needle: &Expr) -> bool {
    if hay == needle {
        return true;
    }
    match hay {
        Expr::Rrf {
            primary, secondary, ..
        } => contains_expr(primary, needle) || contains_expr(secondary, needle),
        _ => false,
    }
}

fn mutation_body(cx: &mut Cx<'_>, d: &mut QueryDecl, body: Pair<Rule>) -> R<()> {
    for stmt in body.into_inner() {
        match stmt.as_rule() {
            Rule::mutation_stmt => {
                let s = stmt.into_inner().next().expect("stmt");
                d.mutations.push(mutation(cx, s)?);
            }
            Rule::returning_clause => cx.refuse(slot("`returning { … }`", owner::WRITES)),
            _ => {}
        }
    }
    Ok(())
}

fn type_name(cx: &mut Cx<'_>, pair: &Pair<Rule>) -> String {
    let text = pair.as_str();
    if text.contains("::") {
        cx.refuse(slot(
            &format!("the namespaced type reference `{text}`"),
            owner::NAMESPACES,
        ));
        return text.rsplit("::").next().unwrap_or(text).to_string();
    }
    if RESERVED_CATALOGUE_TYPES.contains(&text) {
        cx.refuse(slot(
            &format!("the catalogue type `{text}`"),
            owner::CATALOGUE,
        ));
    }
    text.to_string()
}

fn mutation(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<Mutation> {
    let rule = pair.as_rule();
    let mut parts = pair.into_inner().filter(|p| {
        !matches!(
            p.as_rule(),
            Rule::kw_insert | Rule::kw_update | Rule::kw_delete | Rule::kw_set | Rule::kw_where
        )
    });
    let ty = parts.next().expect("type");
    let type_name = type_name(cx, &ty);
    let mut assignments = Vec::new();
    let mut predicate = None;
    for p in parts {
        match p.as_rule() {
            Rule::mutation_assignment => {
                let mut a = p.into_inner();
                let property = a.next().expect("prop").as_str().to_string();
                let v = a.next().expect("value");
                let value = if v.as_rule() == Rule::null_value {
                    Expr::Literal(Literal::Null)
                } else {
                    expr(cx, v, Scope::Property(&type_name))?
                };
                assignments.push(MutationAssignment { property, value });
            }
            Rule::expr => predicate = Some(expr(cx, p, Scope::Property(&type_name))?),
            _ => {}
        }
    }
    Ok(match rule {
        Rule::insert_stmt => Mutation::Insert(InsertMutation {
            type_name,
            assignments,
        }),
        Rule::update_stmt => Mutation::Update(UpdateMutation {
            type_name,
            assignments,
            predicate: predicate.expect("where"),
        }),
        _ => Mutation::Delete(DeleteMutation {
            type_name,
            predicate: predicate.expect("where"),
        }),
    })
}

fn clauses(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<Vec<Clause>> {
    pair.into_inner()
        .filter(|p| p.as_rule() == Rule::clause)
        .map(|c| clause(cx, c))
        .collect()
}

fn clause(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<Clause> {
    let inner = pair.into_inner().next().expect("clause");
    match inner.as_rule() {
        Rule::arrow_traversal => Err(arrow_error(cx.input, inner)),
        Rule::negation => Ok(Clause::Subquery(Subquery::not_block(clauses(cx, inner)?))),
        Rule::optional_block => {
            cx.refuse(slot("`optional { … }`", owner::DERIVED));
            // Its clauses still have to be well formed.
            clauses(cx, inner)?;
            Ok(Clause::Filter(Expr::Literal(Literal::Bool(true))))
        }
        Rule::exists_block => {
            let block = inner.into_inner().nth(1).expect("block");
            Ok(Clause::Subquery(Subquery::exists_block(clauses(
                cx, block,
            )?)))
        }
        Rule::subquery_predicate => {
            let mut func = None;
            let mut arg = None;
            let mut block = None;
            let mut op = None;
            let mut right = None;
            for part in inner.into_inner() {
                match part.as_rule() {
                    Rule::agg_name => func = Some(agg(part.as_str())),
                    Rule::expr if block.is_none() => arg = Some(expr(cx, part, Scope::Alias)?),
                    Rule::subquery_block => block = Some(clauses(cx, part)?),
                    Rule::comp_op => op = Some(comp_op(part.as_str())),
                    Rule::expr => right = Some(expr(cx, part, Scope::Alias)?),
                    _ => {}
                }
            }
            Ok(Clause::Subquery(Subquery {
                keyword: BlockKeyword::Aggregate,
                clauses: block.expect("block"),
                func: func.expect("func"),
                arg,
                op: op.expect("op"),
                right: right.expect("right"),
            }))
        }
        Rule::binding => {
            let mut parts = inner.into_inner();
            let variable = parts.next().expect("var").as_str()[1..].to_string();
            let binding_type = parts.next().expect("type");
            let mut bt = binding_type.into_inner();
            let ty = bt.next().expect("type_ref");
            let type_name = type_name(cx, &ty);
            let mut prop_matches = Vec::new();
            for p in bt.chain(parts) {
                match p.as_rule() {
                    Rule::binding_args => cx.refuse(slot(
                        &format!("the view binding `{}{}`", type_name, p.as_str()),
                        owner::DERIVED,
                    )),
                    Rule::prop_match_list => {
                        for pm in p.into_inner().filter(|p| p.as_rule() == Rule::prop_match) {
                            let mut pm = pm.into_inner();
                            let prop_name = pm.next().expect("prop").as_str().to_string();
                            let value =
                                expr(cx, pm.next().expect("value"), Scope::Property(&type_name))?;
                            prop_matches.push(PropMatch { prop_name, value });
                        }
                    }
                    _ => {}
                }
            }
            Ok(Clause::Binding(Binding {
                variable,
                type_name,
                prop_matches,
            }))
        }
        Rule::traversal => traversal(cx, inner).map(Clause::Traversal),
        Rule::filter => {
            let e = expr(cx, inner.into_inner().next().expect("expr"), Scope::Alias)?;
            Ok(Clause::Filter(e.with_search_predicates_spelled()))
        }
        other => Err(perr(
            cx.input,
            &inner,
            format!("unexpected clause {other:?}"),
        )),
    }
}

/// `$a -e-> $b`, `$b <-e- $a`, `$a -e- $b`: the parse error whose fix is the
/// triple. A left arrow swaps the ends; `-e-` is the undirected `<e>`.
fn arrow_error(input: &str, pair: Pair<Rule>) -> QueryDiagnostic {
    let span = pair.as_span();
    let parts: Vec<Pair<Rule>> = pair.into_inner().collect();
    let left = parts.first().map(|p| p.as_str()).unwrap_or("$a");
    let right = parts.last().map(|p| p.as_str()).unwrap_or("$b");
    let open = parts
        .iter()
        .find(|p| p.as_rule() == Rule::arrow_open)
        .map(|p| p.as_str())
        .unwrap_or("-");
    let close = parts
        .iter()
        .find(|p| p.as_rule() == Rule::arrow_close)
        .map(|p| p.as_str())
        .unwrap_or("-");
    let binding = parts
        .iter()
        .find(|p| p.as_rule() == Rule::edge_binding)
        .and_then(|p| p.clone().into_inner().next())
        .map(|v| format!("{}:", v.as_str()))
        .unwrap_or_default();
    let sel = parts
        .iter()
        .find(|p| p.as_rule() == Rule::edge_sel)
        .map(|p| p.as_str())
        .unwrap_or("e");
    let bounds = parts
        .iter()
        .find(|p| p.as_rule() == Rule::traversal_bounds)
        .map(|p| p.as_str())
        .unwrap_or("");
    let fixed = match (open, close) {
        ("-", "->") => format!("{left} {binding}{sel}{bounds} {right}"),
        ("<-", "-") => format!("{right} {binding}{sel}{bounds} {left}"),
        ("-", "-") => format!("{left} {binding}<{sel}>{bounds} {right}"),
        _ => format!("{left} {binding}<{sel}>{bounds} {right}"),
    };
    QueryDiagnostic::parse(
        Q008,
        format!(
            "`{}` is an arrow traversal; GQ writes a traversal as a triple, source on the left",
            span.as_str()
        ),
        Some(Position::at(input, span.start())),
    )
    .with_fix(format!("write `{fixed}`"))
    .with_suggestion(Suggestion {
        applicability: Applicability::MachineApplicable,
        edits: vec![TextEdit {
            start: span.start(),
            end: span.end(),
            replacement: fixed,
        }],
    })
}

fn traversal(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<Traversal> {
    let mut parts = pair.into_inner();
    let src = parts.next().expect("src").as_str()[1..].to_string();
    let mut next = parts.next().expect("edge");
    let mut edge_binding = None;
    if next.as_rule() == Rule::edge_binding {
        edge_binding = Some(next.as_str().trim().trim_end_matches(':').trim()[1..].to_string());
        next = parts.next().expect("edge");
    }
    let (sel_pair, undirected) = if next.as_rule() == Rule::undirected_edge {
        let inner = next.into_inner().next().expect("sel");
        if inner.as_rule() == Rule::edge_alternatives {
            let members = inner
                .into_inner()
                .map(|m| edge_name(cx, m))
                .collect::<R<Vec<_>>>()?;
            return finish_traversal(
                src,
                edge_binding,
                EdgeSelector::Alternation(members),
                true,
                parts,
            );
        }
        (inner, true)
    } else {
        (next, false)
    };
    let selector = edge_selector(cx, sel_pair)?;
    finish_traversal(src, edge_binding, selector, undirected, parts)
}

fn finish_traversal(
    src: String,
    edge_binding: Option<String>,
    selector: EdgeSelector,
    undirected: bool,
    mut parts: pest::iterators::Pairs<'_, Rule>,
) -> R<Traversal> {
    let mut min_hops = 1;
    let mut max_hops = Some(1);
    let mut next = parts.next().expect("dst");
    if next.as_rule() == Rule::traversal_bounds {
        let mut b = next.into_inner();
        min_hops = b.next().expect("min").as_str().parse().unwrap_or(1);
        max_hops = b.next().map(|p| p.as_str().parse().unwrap_or(1));
        next = parts.next().expect("dst");
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

fn edge_selector(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<EdgeSelector> {
    let inner = if pair.as_rule() == Rule::edge_sel {
        pair.into_inner().next().expect("sel")
    } else {
        pair
    };
    Ok(match inner.as_rule() {
        Rule::edge_wildcard => EdgeSelector::Wildcard,
        Rule::edge_alternation => {
            let members = inner.into_inner().next().expect("members");
            EdgeSelector::Alternation(
                members
                    .into_inner()
                    .map(|m| edge_name(cx, m))
                    .collect::<R<Vec<_>>>()?,
            )
        }
        _ => EdgeSelector::Named(edge_name(cx, inner)?),
    })
}

fn edge_name(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<String> {
    match pair.as_rule() {
        Rule::catalogue_edge => {
            cx.refuse(slot(
                &format!("the catalogue edge `{}`", pair.as_str()),
                owner::CATALOGUE,
            ));
            Ok(pair.as_str()[1..].to_string())
        }
        Rule::string_lit => string(cx.input, &pair),
        _ => Ok(pair.as_str().to_string()),
    }
}

fn projection(cx: &mut Cx<'_>, pair: Pair<Rule>) -> R<Projection> {
    let mut parts = pair.into_inner();
    let e = expr(cx, parts.next().expect("expr"), Scope::Alias)?;
    let mut alias = None;
    for p in parts {
        match p.as_rule() {
            Rule::reserved_alias => {
                return Err(perr(
                    cx.input,
                    &p,
                    format!(
                        "`{}` is a reserved word and cannot be a return alias",
                        p.as_str()
                    ),
                ));
            }
            Rule::expr_ident => alias = Some(p.as_str().to_string()),
            _ => {}
        }
    }
    Ok(Projection { expr: e, alias })
}

fn agg(name: &str) -> AggFunc {
    match name {
        "count" => AggFunc::Count,
        "sum" => AggFunc::Sum,
        "avg" => AggFunc::Avg,
        "min" => AggFunc::Min,
        _ => AggFunc::Max,
    }
}

fn comp_op(text: &str) -> CompOp {
    match text {
        "=" => CompOp::Eq,
        "!=" => CompOp::Ne,
        ">" => CompOp::Gt,
        "<" => CompOp::Lt,
        ">=" => CompOp::Ge,
        _ => CompOp::Le,
    }
}

fn expr(cx: &mut Cx<'_>, pair: Pair<Rule>, scope: Scope<'_>) -> R<Expr> {
    match pair.as_rule() {
        Rule::expr | Rule::positional => expr(cx, pair.into_inner().next().expect("expr"), scope),
        Rule::or_expr | Rule::and_expr => {
            let op = if pair.as_rule() == Rule::or_expr {
                BinaryOp::Or
            } else {
                BinaryOp::And
            };
            let mut operands = pair
                .into_inner()
                .filter(|p| !matches!(p.as_rule(), Rule::kw_and | Rule::kw_or));
            let first = expr(cx, operands.next().expect("first"), scope)?;
            operands.try_fold(first, |left, right| {
                Ok(Expr::Binary {
                    left: Box::new(left),
                    op,
                    right: Box::new(expr(cx, right, scope)?),
                })
            })
        }
        Rule::not_expr => {
            let mut parts = pair.into_inner();
            let first = parts.next().expect("not");
            if first.as_rule() == Rule::kw_not {
                Ok(Expr::Not(Box::new(expr(
                    cx,
                    parts.next().expect("operand"),
                    scope,
                )?)))
            } else {
                expr(cx, first, scope)
            }
        }
        Rule::comparison => {
            let whole = pair.clone();
            let mut parts = pair.into_inner();
            let left_pair = parts.next().expect("left");
            let left = operand(cx, left_pair.clone(), scope)?;
            match parts.next() {
                None => Ok(left),
                Some(op) if op.as_rule() == Rule::infix_string_op => {
                    let right_pair = parts.next().expect("right");
                    let fixed = format!(
                        "{}({}, {})",
                        op.as_str(),
                        left_pair.as_str().trim(),
                        right_pair.as_str().trim()
                    );
                    let span = whole.as_span();
                    Err(QueryDiagnostic::parse(
                        Q009,
                        format!(
                            "`{}` is the infix spelling; edition 3 writes `{}` as a call",
                            span.as_str(),
                            op.as_str()
                        ),
                        Some(Position::at(cx.input, span.start())),
                    )
                    .with_fix(format!(
                        "write `{fixed}`, or `{} in {}` for list membership",
                        right_pair.as_str().trim(),
                        left_pair.as_str().trim()
                    ))
                    .with_suggestion(Suggestion {
                        applicability: Applicability::NeedsReview,
                        edits: vec![TextEdit {
                            start: span.start(),
                            end: span.end(),
                            replacement: fixed,
                        }],
                    }))
                }
                Some(op) if op.as_rule() == Rule::comp_op => {
                    let right = operand(cx, parts.next().expect("right"), scope)?;
                    Ok(Expr::comparison(left, comp_op(op.as_str()), right))
                }
                Some(op) if op.as_rule() == Rule::kw_in => {
                    let list = operand(cx, parts.next().expect("list"), scope)?;
                    Ok(Expr::In {
                        needle: Box::new(left),
                        list: Box::new(list),
                    })
                }
                Some(null_test) => Ok(Expr::IsNull {
                    expr: Box::new(left),
                    negated: null_test.into_inner().any(|k| k.as_rule() == Rule::kw_not),
                }),
            }
        }
        Rule::operand => operand(cx, pair, scope),
        other => Err(perr(
            cx.input,
            &pair,
            format!("unexpected expression {other:?}"),
        )),
    }
}

fn operand(cx: &mut Cx<'_>, pair: Pair<Rule>, scope: Scope<'_>) -> R<Expr> {
    let inner = pair.into_inner().next().expect("operand");
    match inner.as_rule() {
        Rule::expr => expr(cx, inner, scope),
        Rule::block_value => {
            cx.refuse(slot(
                &format!(
                    "the block value `{}`",
                    inner.as_str().split('{').next().unwrap_or("").trim()
                ),
                owner::DERIVED,
            ));
            Ok(Expr::Literal(Literal::Integer(0)))
        }
        Rule::count_call => {
            let mut parts = inner.into_inner();
            let func = agg(parts.next().expect("agg").as_str());
            let arg = parts.find(|p| p.as_rule() == Rule::expr).expect("arg");
            Ok(Expr::Aggregate {
                func,
                distinct: true,
                arg: Box::new(expr(cx, arg, scope)?),
            })
        }
        Rule::call => call(cx, inner, scope),
        Rule::prop_access => {
            let mut parts = inner.into_inner();
            let variable = parts.next().expect("var").as_str()[1..].to_string();
            let path: Vec<String> = parts.map(|p| p.as_str().to_string()).collect();
            if path.len() > 1 {
                let construct = if path
                    .last()
                    .is_some_and(|f| f == "@distance" || f == "@score")
                {
                    format!("the arm meta-field `${variable}.{}`", path.join("."))
                } else {
                    format!("the path `${variable}.{}`", path.join("."))
                };
                cx.refuse(slot(&construct, owner::SEARCH));
            }
            let property = path[0].clone();
            if property == "@type_id" {
                cx.refuse(slot("the meta-field `@type_id`", owner::POLY));
            }
            if property == "@distance" || property == "@score" {
                cx.refuse(slot(&format!("the meta-field `{property}`"), owner::SEARCH));
            }
            if let Some(call) = cx.arms.get(&(variable.clone(), property.clone())).cloned() {
                if cx.in_match {
                    cx.refuse(
                        QueryDiagnostic::typecheck(T64, format!("`${variable}.{property}` is a retrieve arm; arms are bound after `match`"))
                            .with_fix("filter on the arm in `having`"),
                    );
                }
                return Ok(call);
            }
            Ok(Expr::PropAccess { variable, property })
        }
        Rule::variable => Ok(Expr::Variable(inner.as_str()[1..].to_string())),
        Rule::literal => Ok(Expr::Literal(literal(cx.input, inner)?)),
        Rule::meta_field | Rule::expr_ident => Ok(match scope {
            Scope::Alias => Expr::AliasRef(inner.as_str().to_string()),
            Scope::Property(t) => Expr::mutation_property(t, inner.as_str()),
        }),
        Rule::reserved_property => Err(perr(
            cx.input,
            &inner,
            format!("`{}` is a reserved word", inner.as_str()),
        )),
        other => Err(perr(
            cx.input,
            &inner,
            format!("unexpected operand {other:?}"),
        )),
    }
}

/// A generic call, resolved against the function registry.
fn call(cx: &mut Cx<'_>, pair: Pair<Rule>, scope: Scope<'_>) -> R<Expr> {
    let text = pair.as_str().to_string();
    let mut parts = pair.into_inner();
    let name = parts.next().expect("name").as_str().to_string();
    let mut positional = Vec::new();
    let mut options = Vec::new();
    if let Some(args) = parts.next() {
        for arg in args.into_inner() {
            match arg.as_rule() {
                Rule::positional => positional.push(expr(cx, arg, scope)?),
                Rule::option => {
                    let mut o = arg.into_inner();
                    let key = o.next().expect("key").as_str().to_string();
                    options.push((key, expr(cx, o.next().expect("value"), scope)?));
                }
                _ => {}
            }
        }
    }
    match registry::resolve(&name, positional, options) {
        Ok(e @ (Expr::Nearest { .. } | Expr::Bm25 { .. })) if !cx.in_retrieve => {
            cx.refuse(
                QueryDiagnostic::typecheck(T65, format!("the ranking `{text}` is written outside `retrieve`; edition 3 binds a ranking as a retrieve arm"))
                    .with_fix("bind it in `retrieve { $d.score: … }` and read `$d.score` in `return`, `having` and `order`")
                    .with_expression(text.clone()),
            );
            Ok(e)
        }
        Ok(e) => Ok(e),
        Err(diagnostic) => {
            cx.refuse(diagnostic.with_expression(text));
            Ok(Expr::Literal(Literal::Bool(true)))
        }
    }
}

fn literal(input: &str, pair: Pair<Rule>) -> R<Literal> {
    let inner = if pair.as_rule() == Rule::literal {
        pair.into_inner().next().expect("literal")
    } else {
        pair
    };
    Ok(match inner.as_rule() {
        Rule::string_lit => Literal::String(string(input, &inner)?),
        Rule::integer => Literal::Integer(
            inner
                .as_str()
                .parse::<i64>()
                .map_err(|e| perr(input, &inner, e.to_string()))? as i128,
        ),
        Rule::float_lit => Literal::Float(
            inner
                .as_str()
                .parse()
                .map_err(|_| perr(input, &inner, "invalid float"))?,
        ),
        Rule::bool_lit => Literal::Bool(inner.as_str() == "true"),
        Rule::list_lit => Literal::List(
            inner
                .into_inner()
                .map(|item| {
                    let item = item.into_inner().next().expect("item");
                    if item.as_rule() == Rule::call {
                        // A constant call: `date("…")`, `datetime("…")`.
                        let mut parts = item.clone().into_inner();
                        let name = parts.next().expect("name").as_str();
                        let arg = parts
                            .next()
                            .and_then(|a| a.into_inner().next())
                            .and_then(|p| {
                                let s = p.as_str().trim();
                                s.strip_prefix('"')
                                    .and_then(|s| s.strip_suffix('"'))
                                    .map(str::to_string)
                            });
                        match (name, arg) {
                            ("date", Some(a)) => Ok(Literal::Date(a)),
                            ("datetime", Some(a)) => Ok(Literal::DateTime(a)),
                            _ => Err(perr(input, &item, "a list literal holds constants")),
                        }
                    } else {
                        literal(input, item)
                    }
                })
                .collect::<R<Vec<_>>>()?,
        ),
        other => return Err(perr(input, &inner, format!("unexpected literal {other:?}"))),
    })
}

fn string(input: &str, pair: &Pair<Rule>) -> R<String> {
    decode_string_literal(pair.as_str()).map_err(|e| perr(input, pair, e.to_string()))
}

/// Refusals the type checker raises before anything else (the slot codes),
/// and the arm-name collision check that needs the catalog.
pub fn check_refusals(
    query: &QueryDecl,
    property_of: impl Fn(&str, &str) -> bool,
    bound_type: impl Fn(&str) -> Option<String>,
) -> Result<(), QueryDiagnostic> {
    let Some(x) = &query.gq3 else { return Ok(()) };
    if let Some(first) = x.refusals.first() {
        return Err(first.clone());
    }
    for arm in &x.arms {
        if let Some(ty) = bound_type(&arm.variable)
            && property_of(&ty, &arm.name)
        {
            return Err(QueryDiagnostic::typecheck(
                T63,
                format!(
                    "the retrieve arm `${}.{}` collides with the property `{ty}.{}`",
                    arm.variable, arm.name, arm.name
                ),
            )
            .with_fix("give the arm a name that is not a property of its binding"));
        }
    }
    let _ = T61;
    Ok(())
}
