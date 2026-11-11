//! The `match … where` variant of GQ 3, for the agent instrument's judge
//! only. The product's grammar does not change: this module holds a fork of
//! #1316's grammar (`grammar.pest`) and parser (`parse.rs`, with the
//! `statement.rs`, `foreign.rs` and `depth.rs` it uses), which build the
//! product's surface tree, so the product's type checker types a variant
//! text into exactly the core IR of its GQ 3 equivalent.
//!
//! A read body is `match { element, … } [where <expression>]`, then
//! `return`, `order` and `limit`; a write group is `match { … } [where …]`
//! and its statements. `where a and b` is a filter clause per top-level
//! conjunct after the last element; a conjunct `not exists { … }` is GQ 3's
//! `not { … }`.
//!
//! Diagnostics keep the product's codes. A fix the product prints in GQ 3
//! syntax the variant refuses is reprinted in variant syntax
//! ([`adapt_diagnostic`], and the fix chain in [`parse_source`]).

use pest_derive::Parser;
use serde_json::{Value, json};

use omnigraph_compiler::QueryDiagnostic;
use omnigraph_compiler::catalog::Catalog;
use omnigraph_compiler::query::check::check_file;
use omnigraph_compiler::query::diagnostic::{Applicability, Suggestion, TextEdit};
use omnigraph_compiler::query::syntax::{Body, Decl, DeclBody, SourceFile};

mod depth;
mod foreign;
mod parse;
pub mod print;
mod statement;

pub use print::Converter;

/// The variant grammar (`grammar.pest`).
#[derive(Parser)]
#[grammar = "agent/variant/grammar.pest"]
pub(crate) struct QueryParser;

/// How many refusals a fix chain follows before it stops.
const CHAIN: usize = 6;

/// Parse a variant source into the product's surface tree, or refuse it.
///
/// A refusal whose edits leave text the variant still refuses, but whose
/// next refusal carries edits too, is followed: the suggestion becomes the
/// one edit that takes the source to the first text the variant accepts. So
/// a GQ 3 fix a recognizer prints (a Cypher pattern rewritten to triples
/// and braces, say) reaches the agent in variant syntax.
pub fn parse_source(input: &str) -> Result<SourceFile, QueryDiagnostic> {
    depth::check_query_nesting(input)?;
    match parse::parse_source(input) {
        Ok(file) => Ok(file),
        Err(first) => Err(chain(input, first)),
    }
}

fn apply(input: &str, edits: &[TextEdit]) -> String {
    parse::apply_edits(input, edits)
}

/// The refusal of `input` with its edits followed until the text parses.
fn chain(input: &str, first: QueryDiagnostic) -> QueryDiagnostic {
    let Some(suggestion) = first.suggestion.as_deref().cloned() else {
        return first;
    };
    let mut text = apply(input, &suggestion.edits);
    let mut applicability = suggestion.applicability;
    for _ in 0..CHAIN {
        match parse::parse_source(&text) {
            Ok(_) => return with_whole_edit(first, input, &text, applicability),
            Err(next) => match next.suggestion {
                Some(s) if !s.edits.is_empty() => {
                    if s.applicability != Applicability::MachineApplicable {
                        applicability = s.applicability;
                    }
                    text = apply(&text, &s.edits);
                }
                _ => break,
            },
        }
    }
    // The chain does not end in variant text; fall back to converting a
    // GQ 3 text the first fix may have written.
    if let Some(converted) = gq3_text_to_variant(&apply(input, &suggestion.edits), None) {
        if parse::parse_source(&converted).is_ok() {
            return with_whole_edit(first, input, &converted, suggestion.applicability);
        }
    }
    first
}

/// `d` with one edit that takes `input` to `fixed`: the smallest replaced
/// range, its new text as the fix.
fn with_whole_edit(
    mut d: QueryDiagnostic,
    input: &str,
    fixed: &str,
    applicability: Applicability,
) -> QueryDiagnostic {
    if fixed == input {
        return d;
    }
    let a = input.as_bytes();
    let b = fixed.as_bytes();
    let mut start = a.iter().zip(b).take_while(|(x, y)| x == y).count();
    let mut end_a = a.len();
    let mut end_b = b.len();
    while end_a > start && end_b > start && a[end_a - 1] == b[end_b - 1] {
        end_a -= 1;
        end_b -= 1;
    }
    while !input.is_char_boundary(start) || !fixed.is_char_boundary(start) {
        start -= 1;
    }
    while !input.is_char_boundary(end_a) || !fixed.is_char_boundary(end_b) {
        end_a += 1;
        end_b += 1;
    }
    // Widen to whole lines, so the printed fix reads as text, not a splice.
    let line_start = input[..start].rfind('\n').map_or(0, |i| i + 1);
    let tail_a = input[end_a..].find('\n').map_or(input.len(), |i| end_a + i);
    let tail_b = fixed[end_b..].find('\n').map_or(fixed.len(), |i| end_b + i);
    let replacement = fixed[line_start..tail_b].to_string();
    d.fix = Some(replacement.trim().to_string());
    d.suggestion = Some(Box::new(Suggestion {
        applicability,
        edits: vec![TextEdit {
            start: line_start,
            end: tail_a,
            replacement,
        }],
    }));
    d
}

/// A GQ 3 source printed in the variant, when the product parses it.
pub fn gq3_text_to_variant(gq3: &str, catalog: Option<&Catalog>) -> Option<String> {
    let file = omnigraph_compiler::query::syntax::parse::parse_source(gq3).ok()?;
    Some(Converter::new(catalog).file(&file))
}

/// A fix text that is a GQ 3 `match { … }` (a clause list), printed as the
/// variant's `match { … } [where …]`.
fn gq3_match_to_variant(fix: &str) -> Option<String> {
    let fix = fix.trim();
    if !fix.starts_with("match {") || !fix.ends_with('}') {
        return None;
    }
    let source = format!("query __fix() {{ {fix} return {{ 1 as one }} }}");
    let file = omnigraph_compiler::query::syntax::parse::parse_source(&source).ok()?;
    let Body::Decls(decls) = &file.body else {
        return None;
    };
    let DeclBody::Read(body) = &decls.first()?.body else {
        return None;
    };
    let printed = Converter::new(None).read_body(body, "\n");
    printed.lines().next().map(str::to_string)
}

/// A type-check refusal of a variant text, with any fix the product prints
/// in GQ 3 syntax the variant refuses reprinted in variant syntax. The code,
/// message and position are the product's.
pub fn adapt_diagnostic(mut d: QueryDiagnostic, decl: &Decl, text: &str) -> QueryDiagnostic {
    match d.code.as_str() {
        // The block form of an aggregate in a filter: a GQ 3 clause list.
        "T8" => {
            if let Some(fix) = d.fix.as_deref().and_then(gq3_match_to_variant) {
                d.fix = Some(fix);
            }
        }
        "T77" => {
            if let Some(fix) = &d.fix {
                if let Some(var) = fix
                    .strip_prefix("bind it before the statement: `")
                    .and_then(|r| r.split(':').next())
                {
                    d.fix = Some(format!(
                        "bind it in the group's `match`: `match {{ {var}: <Type> }} where …`"
                    ));
                }
            }
        }
        "T65" => {
            if let Some(adapted) = ranking_fix(&d, decl, text) {
                return adapted;
            }
        }
        _ => {}
    }
    // Any other machine edit: kept when its result is variant text; a result
    // only GQ 3 reads is reprinted.
    if let Some(s) = d.suggestion.clone() {
        let fixed = apply(text, &s.edits);
        if parse::parse_source(&fixed).is_err() {
            if let Some(converted) = gq3_text_to_variant(&fixed, None) {
                if parse::parse_source(&converted).is_ok() {
                    return with_whole_edit(d, text, &converted, s.applicability);
                }
            }
        }
    }
    d
}

/// `T65`'s edit, which draws the ranking with `from` over the read's own
/// pattern: in the variant the pattern's `where` moves into the population
/// with it, as GQ 3's filters do.
fn ranking_fix(d: &QueryDiagnostic, decl: &Decl, text: &str) -> Option<QueryDiagnostic> {
    let suggestion = d.suggestion.as_ref()?;
    let DeclBody::Read(body) = &decl.body else {
        return None;
    };
    let inner = body.spans.match_inner.as_ref()?;
    let where_span = parse::where_span(inner.start);
    let mut edits = suggestion.edits.clone();
    // The GQ 3 prefix separates `let … = { … }` from `from rrf(…)` with two
    // spaces and runs into the first clause; the variant needs commas.
    for e in &mut edits {
        if e.start == inner.start && e.end == inner.start {
            if e.replacement.starts_with(" let $all") {
                e.replacement = e
                    .replacement
                    .replacen("  from rrf(", ", from rrf(", 1)
                    .trim_end()
                    .to_string()
                    + ", ";
            }
        }
    }
    if let Some((ws, we)) = where_span {
        let clause = text[ws..we].trim_end().to_string();
        let we = ws + clause.len();
        let suffix = edits
            .iter_mut()
            .find(|e| e.start == inner.end && e.end == inner.end && e.replacement.starts_with('}'));
        // `rrf` has no suffix: its pattern stays outside, correlated on the
        // ranked binding, with its `where`, and the product's edit stands.
        let e = suffix?;
        e.replacement = format!("}} {clause} {}", &e.replacement[1..]);
        edits.push(TextEdit {
            start: ws,
            end: we,
            replacement: String::new(),
        });
    }
    edits.sort_by_key(|e| (e.start, e.end));
    let fixed = apply(text, &edits);
    parse::parse_source(&fixed).ok()?;
    Some(with_whole_edit(
        d.clone(),
        text,
        &fixed,
        Applicability::MachineApplicable,
    ))
}

/// The lowering check of one GQ 3 ground truth: its mechanical conversion
/// to the variant, whether the variant parses it, and whether the two
/// type-check to equal core IR.
pub fn lowering_report(catalog: &Catalog, truth: &str) -> Value {
    let gq3 = match omnigraph_compiler::query::syntax::parse::parse_source(truth) {
        Ok(file) => file,
        Err(d) => return json!({"status": "gq3_refused", "diagnostic": d.message}),
    };
    let variant = Converter::new(Some(catalog)).file(&gq3);
    let parsed = match parse_source(&variant) {
        Ok(file) => file,
        Err(d) => {
            return json!({"status": "variant_refused", "variant": variant,
                          "diagnostic": serde_json::to_value(&d).unwrap_or(Value::Null)});
        }
    };
    let irs = |file: &SourceFile| -> Result<Vec<String>, String> {
        check_file(catalog, file)
            .into_iter()
            .map(|(_, r)| r.map(|c| format!("{:#?}", c.core)).map_err(|d| d.message))
            .collect()
    };
    match (irs(&gq3), irs(&parsed)) {
        (Ok(a), Ok(b)) if a == b => json!({"status": "equal", "variant": variant}),
        (Ok(a), Ok(b)) => json!({"status": "ir_differs", "variant": variant,
                                 "gq3_ir": a, "variant_ir": b}),
        (Err(e), _) => json!({"status": "gq3_check_refused", "message": e}),
        (_, Err(e)) => json!({"status": "variant_check_refused", "variant": variant, "message": e}),
    }
}
