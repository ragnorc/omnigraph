//! SPIKE: the edition-2 → edition-3 rewriter of RFC PR #1202
//! (`omnigraph queries migrate`). It parses the source with the edition-2
//! grammar, emits byte-range edits (comments and layout survive), prepends
//! `gq 3;`, and proves each declaration by compiling both editions to typed
//! IR and comparing.

use std::collections::{HashMap, HashSet};

use pest::Parser;
use pest::iterators::Pair;

use crate::catalog::Catalog;
use crate::query::diagnostic::TextEdit;
use crate::query::parser::{QueryParser, Rule};

#[derive(Debug, Clone, Copy, Default)]
pub struct Options {
    /// Swap a triple written against an asymmetric edge's orientation (the
    /// RFC's `T5` fix). The RFC's rewriter table has no such row.
    pub swap_incoming: bool,
}

#[derive(Debug, Clone)]
pub struct Migration {
    pub text: String,
    pub edits: Vec<TextEdit>,
    /// Observable differences the edits cause that IR equality does not see
    /// (a renamed result column).
    pub notes: Vec<String>,
    pub proof: Vec<(String, Proof)>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Proof {
    /// Both editions compile to equal typed IR.
    Equal,
    /// Both compile, to different IR; the detail names the first difference.
    Differs(String),
    /// The edition-2 source did not compile against the catalog.
    OriginalRefused(String),
    /// The rewritten source did not compile.
    RewriteRefused(String),
    /// Not a declaration (a statement), or no catalog to check against.
    NotChecked,
}

#[derive(Debug, Clone)]
pub struct MigrateError(pub String);

/// Rewrite one edition-2 source. `catalog` resolves list-valued `contains`
/// operands, arm-name collisions and incoming triples, and runs the proof.
pub fn migrate(
    source: &str,
    catalog: Option<&Catalog>,
    options: Options,
) -> Result<Migration, MigrateError> {
    if super::edition_line(source).is_some_and(|line| line.edition == 3) {
        return Ok(Migration {
            text: source.to_string(),
            edits: Vec::new(),
            notes: Vec::new(),
            proof: Vec::new(),
        });
    }
    let file = QueryParser::parse(Rule::query_file, source)
        .map_err(|e| MigrateError(format!("edition-2 parse: {e}")))?
        .next()
        .expect("file");
    let original = crate::query::parser::parse_query_diagnostic_edition2(source)
        .map_err(|e| MigrateError(format!("edition-2 parse: {e}")))?;
    let mut edits = Vec::new();
    let mut notes = Vec::new();
    let decls: Vec<_> = match &original.body {
        crate::query::ast::FileBody::Queries(d) => d.clone(),
        crate::query::ast::FileBody::Explain(d) => vec![d.clone()],
        _ => Vec::new(),
    };
    for pair in descendants(file.clone()).filter(|p| p.as_rule() == Rule::query_decl) {
        let name = pair
            .clone()
            .into_inner()
            .next()
            .map(|p| p.as_str().to_string())
            .unwrap_or_default();
        let decl = decls.iter().find(|d| d.name == name);
        rewrite_decl(source, pair, decl, catalog, options, &mut edits, &mut notes)?;
    }
    let mut text = source.to_string();
    let mut sorted = edits.clone();
    sorted.sort_by(|a, b| b.start.cmp(&a.start));
    for window in sorted.windows(2) {
        if window[1].end > window[0].start {
            return Err(MigrateError(format!(
                "overlapping edits at {}..{}",
                window[1].start, window[1].end
            )));
        }
    }
    for edit in &sorted {
        text.replace_range(edit.start..edit.end, &edit.replacement);
    }
    text.insert_str(0, "gq 3;\n");
    let proof = match catalog {
        Some(catalog) => prove(&original, &text, catalog),
        None => decls
            .iter()
            .map(|d| (d.name.clone(), Proof::NotChecked))
            .collect(),
    };
    Ok(Migration {
        text,
        edits,
        notes,
        proof,
    })
}

fn descendants(pair: Pair<'_, Rule>) -> impl Iterator<Item = Pair<'_, Rule>> {
    let mut stack = vec![pair];
    std::iter::from_fn(move || {
        let next = stack.pop()?;
        let children: Vec<_> = next.clone().into_inner().collect();
        stack.extend(children.into_iter().rev());
        Some(next)
    })
}

fn squash(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect()
}

fn rewrite_decl(
    source: &str,
    pair: Pair<'_, Rule>,
    decl: Option<&crate::query::ast::QueryDecl>,
    catalog: Option<&Catalog>,
    options: Options,
    edits: &mut Vec<TextEdit>,
    notes: &mut Vec<String>,
) -> Result<(), MigrateError> {
    let start = pair.as_span().start();
    let is_write = descendants(pair.clone()).any(|p| p.as_rule() == Rule::mutation_body);
    if is_write {
        edits.push(TextEdit {
            start,
            end: start + "query".len(),
            replacement: "mutation".into(),
        });
    }
    // Binding types, for list `contains` and arm-name collisions.
    let (var_types, param_lists, checked) = binding_info(decl, catalog);
    let property_is_list = |var: &str, prop: &str| -> bool {
        let Some(catalog) = catalog else { return false };
        let Some(ty) = var_types.get(var) else {
            return false;
        };
        catalog
            .node_types
            .get(ty)
            .and_then(|n| n.properties.get(prop))
            .or_else(|| {
                catalog
                    .edge_types
                    .get(ty)
                    .and_then(|e| e.properties.get(prop))
            })
            .is_some_and(|p| p.list)
    };
    let mutation_target = descendants(pair.clone())
        .find(|p| {
            matches!(
                p.as_rule(),
                Rule::update_stmt | Rule::delete_stmt | Rule::insert_stmt
            )
        })
        .and_then(|p| p.into_inner().next().map(|t| t.as_str().to_string()));

    for cmp in descendants(pair.clone()).filter(|p| p.as_rule() == Rule::comparison) {
        let parts: Vec<_> = cmp.clone().into_inner().collect();
        if parts.len() == 3
            && parts[1].as_rule() == Rule::filter_op
            && matches!(parts[1].as_str(), "contains" | "starts_with")
        {
            let (left, op, right) = (
                parts[0].as_str().trim(),
                parts[1].as_str(),
                parts[2].as_str().trim(),
            );
            let list = op == "contains" && {
                if let Some((var, prop)) = left.strip_prefix('$').and_then(|l| l.split_once('.')) {
                    property_is_list(var, prop)
                } else if let Some(var) = left.strip_prefix('$') {
                    param_lists.contains(var)
                } else if let (Some(target), Some(catalog)) = (&mutation_target, catalog) {
                    catalog
                        .node_types
                        .get(target)
                        .and_then(|n| n.properties.get(left))
                        .is_some_and(|p| p.list)
                } else {
                    false
                }
            };
            let replacement = if list {
                format!("{right} in {left}")
            } else {
                format!("{op}({left}, {right})")
            };
            edits.push(TextEdit {
                start: cmp.as_span().start(),
                end: cmp.as_span().end(),
                replacement,
            });
        }
    }
    for call in descendants(pair.clone()).filter(|p| p.as_rule() == Rule::fuzzy_call) {
        let args: Vec<_> = call.into_inner().collect();
        if args.len() == 3 {
            edits.push(TextEdit {
                start: args[2].as_span().start(),
                end: args[2].as_span().end(),
                replacement: format!("max_edits: {}", args[2].as_str()),
            });
        }
    }

    // Rankings: the leading `order` key becomes `retrieve` arms.
    let Some(order) = descendants(pair.clone()).find(|p| p.as_rule() == Rule::order_clause) else {
        return incoming(source, &pair, checked.as_ref(), options, edits);
    };
    let Some(first) = order
        .clone()
        .into_inner()
        .find(|p| p.as_rule() == Rule::ordering)
    else {
        return Ok(());
    };
    let rank = descendants(first.clone()).find(|p| {
        matches!(
            p.as_rule(),
            Rule::nearest_ordering | Rule::bm25_call | Rule::rrf_call
        )
    });
    let Some(rank) = rank.filter(|r| {
        squash(r.as_str())
            == squash(
                first
                    .clone()
                    .into_inner()
                    .next()
                    .map(|p| p.as_str())
                    .unwrap_or(""),
            )
    }) else {
        return incoming(source, &pair, checked.as_ref(), options, edits);
    };
    // Arms: (call text squashed) -> (var, name)
    let mut arms: Vec<(String, String, String, String)> = Vec::new(); // squashed call, var, name, original text
    let mut taken: HashSet<(String, String)> = HashSet::new();
    let mut add_arm = |call: &Pair<'_, Rule>, arms: &mut Vec<(String, String, String, String)>| {
        let kind = if call.as_rule() == Rule::nearest_ordering {
            "nearest"
        } else {
            "bm25"
        };
        let var = descendants(call.clone())
            .find(|p| p.as_rule() == Rule::variable)
            .map(|v| v.as_str()[1..].to_string())
            .unwrap_or_default();
        let mut name = kind.to_string();
        let mut n = 1;
        while taken.contains(&(var.clone(), name.clone()))
            || property_exists(catalog, &var_types, &var, &name)
        {
            n += 1;
            name = format!("{kind}{n}");
        }
        taken.insert((var.clone(), name.clone()));
        arms.push((squash(call.as_str()), var, name, call.as_str().to_string()));
    };
    let mut rrf_k = None;
    match rank.as_rule() {
        Rule::rrf_call => {
            let parts: Vec<_> = rank.clone().into_inner().collect();
            for part in &parts[..2] {
                let inner = part.clone().into_inner().next().unwrap_or(part.clone());
                add_arm(&inner, &mut arms);
            }
            if parts.len() == 3 {
                rrf_k = Some(parts[2].clone());
            }
        }
        _ => add_arm(&rank, &mut arms),
    }
    // Every written ranking call matching an arm becomes the arm reference.
    for call in descendants(pair.clone())
        .filter(|p| matches!(p.as_rule(), Rule::nearest_ordering | Rule::bm25_call))
    {
        if let Some((_, var, name, _)) = arms.iter().find(|(sq, ..)| *sq == squash(call.as_str())) {
            edits.push(TextEdit {
                start: call.as_span().start(),
                end: call.as_span().end(),
                replacement: format!("${var}.{name}"),
            });
        }
    }
    if let Some(k) = rrf_k {
        edits.push(TextEdit {
            start: k.as_span().start(),
            end: k.as_span().end(),
            replacement: format!("k: {}", k.as_str()),
        });
    }
    // A projected ranking with no alias was named `<var>._distance` /
    // `<var>._score`; the arm column is `<var>.<arm>`.
    if let Some(ret) = descendants(pair.clone()).find(|p| p.as_rule() == Rule::return_clause) {
        for proj in ret.into_inner().filter(|p| p.as_rule() == Rule::projection) {
            let parts: Vec<_> = proj.clone().into_inner().collect();
            if parts.len() == 1
                && let Some((_, var, name, _)) = arms
                    .iter()
                    .find(|(sq, ..)| *sq == squash(parts[0].as_str()))
            {
                let old = if name.starts_with("nearest") {
                    "_distance"
                } else {
                    "_score"
                };
                notes.push(format!(
                    "result column `{var}.{old}` is renamed `{var}.{name}`"
                ));
            }
        }
    }
    let match_end = descendants(pair.clone())
        .find(|p| p.as_rule() == Rule::match_clause)
        .map(|m| m.as_span().end())
        .unwrap_or(start);
    let indent = line_indent(source, match_end);
    let arm_lines: Vec<String> = arms
        .iter()
        .map(|(_, var, name, text)| format!("${var}.{name}: {text}"))
        .collect();
    edits.push(TextEdit {
        start: match_end,
        end: match_end,
        replacement: format!("\n{indent}retrieve {{ {} }}", arm_lines.join(", ")),
    });
    incoming(source, &pair, checked.as_ref(), options, edits)
}

fn property_exists(
    catalog: Option<&Catalog>,
    var_types: &HashMap<String, String>,
    var: &str,
    name: &str,
) -> bool {
    let (Some(catalog), Some(ty)) = (catalog, var_types.get(var)) else {
        return false;
    };
    catalog
        .node_types
        .get(ty)
        .is_some_and(|n| n.properties.contains_key(name))
}

fn line_indent(source: &str, at: usize) -> String {
    let line_start = source[..at].rfind('\n').map_or(0, |i| i + 1);
    source[line_start..]
        .chars()
        .take_while(|c| *c == ' ' || *c == '\t')
        .collect()
}

type Info = (
    HashMap<String, String>,
    HashSet<String>,
    Option<crate::query::typecheck::CheckedReadQuery>,
);

fn binding_info(decl: Option<&crate::query::ast::QueryDecl>, catalog: Option<&Catalog>) -> Info {
    let mut var_types = HashMap::new();
    let mut lists = HashSet::new();
    let Some(decl) = decl else {
        return (var_types, lists, None);
    };
    for p in &decl.params {
        if p.type_name.starts_with('[') {
            lists.insert(p.name.clone());
        }
    }
    let Some(catalog) = catalog else {
        return (var_types, lists, None);
    };
    let checked = if decl.mutations.is_empty() {
        crate::query::typecheck::typecheck_query(catalog, decl).ok()
    } else {
        None
    };
    if let Some(checked) = &checked {
        for (var, bound) in checked.bindings() {
            match bound {
                crate::query::typecheck::BoundVariable::Node { type_name } => {
                    var_types.insert(var.clone(), type_name.clone());
                }
                crate::query::typecheck::BoundVariable::Edge { type_names } => {
                    if let [one] = &type_names[..] {
                        var_types.insert(var.clone(), one.clone());
                    }
                }
            }
        }
    }
    (var_types, lists, checked)
}

/// With `swap_incoming`, a named traversal the type checker resolved
/// against its edge's orientation is rewritten with its ends swapped.
fn incoming(
    _source: &str,
    pair: &Pair<'_, Rule>,
    checked: Option<&crate::query::typecheck::CheckedReadQuery>,
    options: Options,
    edits: &mut Vec<TextEdit>,
) -> Result<(), MigrateError> {
    let Some(checked) = checked else {
        return Ok(());
    };
    let mut all: Vec<&crate::query::typecheck::ResolvedTraversal> = checked.traversals().collect();
    all.extend(checked.nested_traversals());
    let incoming: Vec<(String, String)> = all
        .into_iter()
        .filter(|t| {
            matches!(t.edges, crate::traversal::EdgeSelection::Named(_))
                && t.src_type != t.dst_type
                && t.edges
                    .members()
                    .iter()
                    .all(|m| m.direction == crate::types::Direction::In)
        })
        .map(|t| (t.src.clone(), t.dst.clone()))
        .collect();
    if incoming.is_empty() || !options.swap_incoming {
        return Ok(());
    }
    for t in descendants(pair.clone()).filter(|p| p.as_rule() == Rule::traversal) {
        if t.clone().into_inner().any(|p| {
            matches!(
                p.as_rule(),
                Rule::edge_alternation | Rule::edge_wildcard | Rule::undirected_edge
            )
        }) {
            continue;
        }
        let parts: Vec<_> = t.clone().into_inner().collect();
        let (first, last) = (parts.first().unwrap(), parts.last().unwrap());
        let key = (
            first.as_str()[1..].to_string(),
            last.as_str()[1..].to_string(),
        );
        if incoming.contains(&key) {
            edits.push(TextEdit {
                start: first.as_span().start(),
                end: first.as_span().end(),
                replacement: last.as_str().to_string(),
            });
            edits.push(TextEdit {
                start: last.as_span().start(),
                end: last.as_span().end(),
                replacement: first.as_str().to_string(),
            });
        }
    }
    Ok(())
}

/// Compile every declaration of both sources and compare typed IR.
pub fn prove(
    original: &crate::query::ast::QueryFile,
    rewritten: &str,
    catalog: &Catalog,
) -> Vec<(String, Proof)> {
    let new = match crate::query::parser::parse_query_diagnostic(rewritten) {
        Ok(file) => file,
        Err(e) => {
            return declarations(original)
                .iter()
                .map(|d| {
                    (
                        d.name.clone(),
                        Proof::RewriteRefused(format!("parse: {e} (fix: {:?})", e.fix)),
                    )
                })
                .collect();
        }
    };
    let new_decls = declarations(&new);
    declarations(original)
        .iter()
        .map(|old| {
            let Some(new) = new_decls.iter().find(|d| d.name == old.name) else {
                return (
                    old.name.clone(),
                    Proof::RewriteRefused("declaration missing".into()),
                );
            };
            (old.name.clone(), prove_decl(old, new, catalog))
        })
        .collect()
}

fn declarations(file: &crate::query::ast::QueryFile) -> Vec<crate::query::ast::QueryDecl> {
    match &file.body {
        crate::query::ast::FileBody::Queries(d) => d.clone(),
        crate::query::ast::FileBody::Explain(d) => vec![d.clone()],
        _ => Vec::new(),
    }
}

pub fn compile_ir(
    decl: &crate::query::ast::QueryDecl,
    catalog: &Catalog,
) -> Result<String, String> {
    use crate::query::typecheck::{CheckedQuery, typecheck_query_decl};
    match typecheck_query_decl(catalog, decl).map_err(|e| e.to_string())? {
        CheckedQuery::Read(read) => {
            let ir = crate::ir::lower::lower_query(catalog, &read).map_err(|e| e.to_string())?;
            Ok(format!(
                "{:?}|{:?}|{:?}|{:?}|{:?}|{:?}|{:?}",
                ir.params,
                ir.pipeline,
                ir.return_exprs,
                ir.distinct,
                ir.order_by,
                ir.limit,
                ir.having
            ))
        }
        CheckedQuery::Mutation(m) => {
            let ir =
                crate::ir::lower::lower_mutation_query(catalog, &m).map_err(|e| e.to_string())?;
            Ok(format!("{ir:?}"))
        }
    }
}

fn prove_decl(
    old: &crate::query::ast::QueryDecl,
    new: &crate::query::ast::QueryDecl,
    catalog: &Catalog,
) -> Proof {
    let a = match compile_ir(old, catalog) {
        Ok(ir) => ir,
        Err(e) => return Proof::OriginalRefused(e),
    };
    let b = match compile_ir(new, catalog) {
        Ok(ir) => ir,
        Err(e) => return Proof::RewriteRefused(e),
    };
    if a == b {
        Proof::Equal
    } else {
        let at = a
            .chars()
            .zip(b.chars())
            .position(|(x, y)| x != y)
            .unwrap_or(a.len().min(b.len()));
        let from = at.saturating_sub(60);
        Proof::Differs(format!(
            "edition 2: …{}… / edition 3: …{}…",
            a.get(from..(at + 80).min(a.len())).unwrap_or(""),
            b.get(from..(at + 80).min(b.len())).unwrap_or("")
        ))
    }
}
