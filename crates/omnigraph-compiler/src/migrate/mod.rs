//! SPIKE (`spike/gq3-rewriter`): `omnigraph queries migrate`, the edition-2
//! to edition-3 rewriter of the GQ 3 RFC (`docs/rfcs/2026-11-07-gq-3.md`,
//! section 6, "The rewriter").
//!
//! The source is parsed with the edition-2 grammar; every rewrite is a byte
//! range edit `(start, end, replacement)` over the spans the parser records
//! on the declaration ([`DeclSpans`]). A replacement is built from pieces:
//! literal text the row writes, and source ranges it copies (with the edits
//! nested inside them applied), so the source is never re-printed and
//! comments and layout outside the rewritten tokens survive. The edits are
//! applied highest offset first, and `gq 3;` is prepended.
//!
//! The edition-3 parser does not exist in this tree, so the rewrite is not
//! compiled here. What is proved per source ([`Migration::proof`]): the
//! top-level edits never overlap; every edit is rendered exactly once (no
//! edit is lost inside a deleted range or duplicated by a copy); every edit
//! and every source range it copies lies inside the construct it names, and
//! the literal text it writes uses only the row's vocabulary and names the
//! construct already holds; and no edition-2 spelling of a row survives in
//! the output, which is what a second run would rewrite (idempotence).

use std::collections::{BTreeMap, BTreeSet, HashMap};

use crate::catalog::Catalog;
use crate::query::ast::{
    Clause, DeclSpans, Expr, FileBody, Mutation, QueryDecl, Span, SpannedExpr,
};
use crate::query::parser::parse_query_diagnostic;

#[cfg(test)]
mod corpus_tests;
#[cfg(test)]
mod tests;

/// The seven rows of the rewriter table, the list `contains` and the two
/// infix string predicates counted apart.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Row {
    InfixContains,
    InfixStartsWith,
    ListContains,
    FuzzyMaxEdits,
    Nearest,
    Bm25,
    Rrf,
    Delete,
    Update,
}

impl Row {
    pub const ALL: [Row; 9] = [
        Row::InfixContains,
        Row::InfixStartsWith,
        Row::ListContains,
        Row::FuzzyMaxEdits,
        Row::Nearest,
        Row::Bm25,
        Row::Rrf,
        Row::Delete,
        Row::Update,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Row::InfixContains => "infix contains -> contains()",
            Row::InfixStartsWith => "infix starts_with -> starts_with()",
            Row::ListContains => "list contains -> in",
            Row::FuzzyMaxEdits => "fuzzy 3rd arg -> max_edits:",
            Row::Nearest => "nearest -> from nearest()",
            Row::Bm25 => "bm25 -> from bm25()",
            Row::Rrf => "rrf -> let + from rrf()",
            Row::Delete => "delete T where -> $x: T phi delete $x",
            Row::Update => "update T set where -> $x: T phi update $x",
        }
    }

    /// The words a row's literal text may write besides names the construct
    /// already holds and the variables the row generates.
    fn vocabulary(self) -> &'static [&'static str] {
        match self {
            Row::InfixContains => &["contains"],
            Row::InfixStartsWith => &["starts_with"],
            Row::ListContains => &["in"],
            Row::FuzzyMaxEdits => &["max_edits"],
            Row::Nearest => &["from", "nearest", "match", "return", "k", "as", "_distance"],
            Row::Bm25 => &["from", "bm25", "match", "return", "as", "_score", "desc"],
            Row::Rrf => &[
                "let", "from", "rrf", "nearest", "bm25", "match", "return", "k", "as", "_rrf",
                "desc",
            ],
            Row::Delete | Row::Update => &["@src", "@dst"],
        }
    }
}

/// A piece of a replacement: text the row writes, or a source range it
/// copies with the edits inside that range applied.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Piece {
    Lit(String),
    /// A copy that carries the edits nested inside the range (a moved
    /// predicate keeps its own rewrites).
    Src(Span),
    /// A verbatim copy: the original bytes, without the edits inside them.
    Raw(Span),
}

#[derive(Debug, Clone)]
pub struct Edit {
    pub span: Span,
    pub pieces: Vec<Piece>,
    pub row: Row,
    /// The construct the edit rewrites; the edit's range and every range it
    /// copies lie inside it.
    pub construct: Span,
    /// Variables the row generated (`$x`, `$distance`, `$p`, …), the only
    /// `$` names its literal text may introduce.
    pub generated: Vec<String>,
    /// Creation order, the tie-break between two insertions at one offset.
    seq: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refusal {
    pub decl: String,
    pub row: Option<Row>,
    pub construct: Span,
    pub reason: String,
}

/// An observation about a rewrite that the edits themselves do not show: a
/// result column whose name changes, a population whose shape section 5
/// refuses, an edition-2 default the table does not state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Note {
    pub decl: String,
    pub row: Row,
    pub kind: &'static str,
    pub detail: String,
}

#[derive(Debug, Clone, Default)]
pub struct DeclReport {
    pub name: String,
    pub is_write: bool,
    pub rows: BTreeMap<Row, usize>,
}

#[derive(Debug, Clone, Default)]
pub struct Proof {
    pub non_overlapping: bool,
    pub each_edit_rendered_once: bool,
    pub confined: bool,
    pub no_residue: bool,
    pub failures: Vec<String>,
}

impl Proof {
    pub fn holds(&self) -> bool {
        self.non_overlapping && self.each_edit_rendered_once && self.confined && self.no_residue
    }
}

#[derive(Debug, Clone)]
pub struct Migration {
    /// The edition-3 text. Written only when `refusals` is empty.
    pub text: String,
    pub edits: Vec<Edit>,
    pub decls: Vec<DeclReport>,
    pub refusals: Vec<Refusal>,
    pub notes: Vec<Note>,
    pub proof: Proof,
    /// The source already carried `gq 3;`; nothing was done.
    pub already_edition_3: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MigrateError(pub String);

impl std::fmt::Display for MigrateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

pub const EDITION_LINE: &str = "gq 3;";

/// Whether `source` opens with an edition line naming edition 3.
pub fn is_edition_3(source: &str) -> bool {
    let rest = source.trim_start_matches(['\u{feff}', ' ', '\t', '\r', '\n']);
    rest.strip_prefix("gq")
        .map(|r| r.trim_start())
        .and_then(|r| r.strip_prefix('3'))
        .map(|r| r.trim_start())
        .is_some_and(|r| r.starts_with(';'))
}

/// Rewrite one edition-2 source. `catalog` decides whether a `contains`
/// reads a list or a String and whether a write targets an edge; without
/// one, a `contains` is refused.
pub fn migrate(source: &str, catalog: Option<&Catalog>) -> Result<Migration, MigrateError> {
    if is_edition_3(source) {
        return Ok(Migration {
            text: source.to_string(),
            edits: Vec::new(),
            decls: Vec::new(),
            refusals: Vec::new(),
            notes: Vec::new(),
            proof: Proof {
                non_overlapping: true,
                each_edit_rendered_once: true,
                confined: true,
                no_residue: true,
                failures: Vec::new(),
            },
            already_edition_3: true,
        });
    }
    let file = parse_query_diagnostic(source)
        .map_err(|d| MigrateError(format!("edition-2 parse: {d}")))?;
    let decls: Vec<&QueryDecl> = match &file.body {
        FileBody::Queries(decls) => decls.iter().collect(),
        FileBody::Explain(decl) => vec![decl],
        _ => Vec::new(),
    };
    let mut cx = Cx {
        source,
        catalog,
        edits: Vec::new(),
        refusals: Vec::new(),
        notes: Vec::new(),
        seq: 0,
    };
    let mut reports = Vec::new();
    for decl in decls {
        let before = cx.edits.len();
        rewrite_decl(&mut cx, decl);
        let mut report = DeclReport {
            name: decl.name.clone(),
            is_write: !decl.mutations.is_empty(),
            rows: BTreeMap::new(),
        };
        for (row, constructs) in rows_by_construct(&cx.edits[before..]) {
            report.rows.insert(row, constructs);
        }
        reports.push(report);
    }
    let Cx {
        edits,
        refusals,
        notes,
        ..
    } = cx;
    let (body, mut proof) = apply(source, &edits);
    let text = format!("{EDITION_LINE}\n{body}");
    proof.confined = check_confinement(source, &edits, &mut proof.failures);
    proof.no_residue = check_no_residue(&text, &mut proof.failures);
    Ok(Migration {
        text,
        edits,
        decls: reports,
        refusals,
        notes,
        proof,
        already_edition_3: false,
    })
}

/// One count per rewritten construct: the edits of one construct share its
/// span and row.
fn rows_by_construct(edits: &[Edit]) -> BTreeMap<Row, usize> {
    let mut seen: BTreeSet<(Row, Span)> = BTreeSet::new();
    for edit in edits {
        seen.insert((edit.row, edit.construct));
    }
    let mut counts = BTreeMap::new();
    for (row, construct) in seen {
        // A nested construct of the same row (a bare-name prefix inside a
        // write's predicate) belongs to its enclosing construct.
        let nested = edits
            .iter()
            .any(|e| e.row == row && e.construct != construct && e.construct.contains(construct));
        if !nested {
            *counts.entry(row).or_insert(0) += 1;
        }
    }
    counts
}

struct Cx<'a> {
    source: &'a str,
    catalog: Option<&'a Catalog>,
    edits: Vec<Edit>,
    refusals: Vec<Refusal>,
    notes: Vec<Note>,
    seq: usize,
}

impl Cx<'_> {
    fn edit(
        &mut self,
        span: Span,
        pieces: Vec<Piece>,
        row: Row,
        construct: Span,
        generated: &[&str],
    ) {
        self.seq += 1;
        self.edits.push(Edit {
            span,
            pieces,
            row,
            construct,
            generated: generated.iter().map(|g| g.to_string()).collect(),
            seq: self.seq,
        });
    }

    fn refuse(
        &mut self,
        decl: &QueryDecl,
        row: Option<Row>,
        construct: Span,
        reason: impl Into<String>,
    ) {
        self.refusals.push(Refusal {
            decl: decl.name.clone(),
            row,
            construct,
            reason: reason.into(),
        });
    }

    fn note(&mut self, decl: &QueryDecl, row: Row, kind: &'static str, detail: impl Into<String>) {
        self.notes.push(Note {
            decl: decl.name.clone(),
            row,
            kind,
            detail: detail.into(),
        });
    }

    fn text(&self, span: Span) -> &str {
        span.text(self.source)
    }
}

fn lit(s: impl Into<String>) -> Piece {
    Piece::Lit(s.into())
}

/// Every name a declaration spells with `$`: parameters and variables.
fn dollar_names(decl: &QueryDecl) -> BTreeSet<String> {
    let mut names: BTreeSet<String> = decl.params.iter().map(|p| p.name.clone()).collect();
    for e in &decl.spans.exprs {
        if let SpannedExpr::Variable { name } | SpannedExpr::PropAccess { variable: name, .. } =
            &e.node
        {
            names.insert(name.clone());
        }
    }
    fn walk(clauses: &[Clause], names: &mut BTreeSet<String>) {
        for c in clauses {
            match c {
                Clause::Binding(b) => {
                    names.insert(b.variable.clone());
                }
                Clause::Traversal(t) => {
                    names.insert(t.src.clone());
                    names.insert(t.dst.clone());
                    if let Some(e) = &t.edge_binding {
                        names.insert(e.clone());
                    }
                }
                Clause::Subquery(s) => walk(&s.clauses, names),
                Clause::Filter(_) => {}
            }
        }
    }
    walk(&decl.match_clause, &mut names);
    names
}

fn fresh(base: &str, taken: &BTreeSet<String>) -> String {
    if !taken.contains(base) {
        return base.to_string();
    }
    (2..)
        .map(|n| format!("{base}{n}"))
        .find(|c| !taken.contains(c))
        .expect("an unbounded range has a free name")
}

/// What a variable names, as far as the rewriter needs: a node or edge type.
#[derive(Debug, Clone, PartialEq, Eq)]
enum VarType {
    Node(String),
    /// An edge binding over one edge type or an alternation of several.
    Edge(Vec<String>),
}

/// Variable types from bindings at every depth and from the endpoints of
/// single named edges, to a fixpoint. Shadowing inside a block is ignored.
fn var_types(decl: &QueryDecl, catalog: &Catalog) -> HashMap<String, VarType> {
    let mut types = HashMap::new();
    let mut traversals = Vec::new();
    fn walk<'a>(
        clauses: &'a [Clause],
        types: &mut HashMap<String, VarType>,
        traversals: &mut Vec<&'a crate::query::ast::Traversal>,
    ) {
        for c in clauses {
            match c {
                Clause::Binding(b) => {
                    types.insert(b.variable.clone(), VarType::Node(b.type_name.clone()));
                }
                Clause::Traversal(t) => traversals.push(t),
                Clause::Subquery(s) => walk(&s.clauses, types, traversals),
                Clause::Filter(_) => {}
            }
        }
    }
    walk(&decl.match_clause, &mut types, &mut traversals);
    for _ in 0..=traversals.len() {
        let mut changed = false;
        for t in &traversals {
            if let (Some(binding), crate::query::ast::EdgeSelector::Alternation(names)) =
                (&t.edge_binding, &t.selector)
                && !types.contains_key(binding)
            {
                let members: Option<Vec<String>> = names
                    .iter()
                    .map(|n| catalog.lookup_edge_by_name(n).map(|e| e.name.clone()))
                    .collect();
                if let Some(members) = members {
                    types.insert(binding.clone(), VarType::Edge(members));
                    changed = true;
                }
            }
            let crate::query::ast::EdgeSelector::Named(name) = &t.selector else {
                continue;
            };
            let Some(edge) = catalog.lookup_edge_by_name(name) else {
                continue;
            };
            if let Some(binding) = &t.edge_binding
                && !types.contains_key(binding)
            {
                types.insert(binding.clone(), VarType::Edge(vec![edge.name.clone()]));
                changed = true;
            }
            if t.min_hops != 1 || t.max_hops != Some(1) {
                continue;
            }
            let node = |v: &str| match types.get(v) {
                Some(VarType::Node(ty)) => Some(ty.clone()),
                _ => None,
            };
            let (src, dst) = (node(&t.src), node(&t.dst));
            // Either written order resolves against the edge.
            let inferred = match (&src, &dst) {
                (Some(s), None) if *s == edge.from_type => {
                    Some((t.dst.clone(), edge.to_type.clone()))
                }
                (Some(s), None) if *s == edge.to_type => {
                    Some((t.dst.clone(), edge.from_type.clone()))
                }
                (None, Some(d)) if *d == edge.to_type => {
                    Some((t.src.clone(), edge.from_type.clone()))
                }
                (None, Some(d)) if *d == edge.from_type => {
                    Some((t.src.clone(), edge.to_type.clone()))
                }
                (None, None) => {
                    types.insert(t.src.clone(), VarType::Node(edge.from_type.clone()));
                    Some((t.dst.clone(), edge.to_type.clone()))
                }
                _ => None,
            };
            if let Some((var, ty)) = inferred {
                types.insert(var, VarType::Node(ty));
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    types
}

fn rewrite_decl(cx: &mut Cx<'_>, decl: &QueryDecl) {
    let types = cx.catalog.map(|c| var_types(decl, c)).unwrap_or_default();
    let spans = &decl.spans;
    // The predicates of the writes, where a bare name names the target.
    let write_targets: Vec<(Span, &str)> = decl
        .mutations
        .iter()
        .zip(&spans.mutations)
        .filter_map(|(m, s)| {
            let ty = match m {
                Mutation::Update(u) => u.type_name.as_str(),
                Mutation::Delete(d) => d.type_name.as_str(),
                Mutation::Insert(_) => return None,
            };
            s.predicate.map(|p| (p, ty))
        })
        .collect();

    // Rows 1-4: infix string predicates, the list `contains`, `fuzzy`.
    for e in &spans.exprs {
        match &e.node {
            SpannedExpr::Infix { op, left, right } => {
                let op = *op;
                use crate::query::ast::CompOp;
                if op == CompOp::StartsWith {
                    cx.edit(
                        e.span,
                        vec![
                            lit("starts_with("),
                            Piece::Src(*left),
                            lit(", "),
                            Piece::Src(*right),
                            lit(")"),
                        ],
                        Row::InfixStartsWith,
                        e.span,
                        &[],
                    );
                } else if op == CompOp::Contains {
                    match operand_is_list(cx, decl, &types, &write_targets, *left) {
                        Some(true) => cx.edit(
                            e.span,
                            vec![Piece::Src(*right), lit(" in "), Piece::Src(*left)],
                            Row::ListContains,
                            e.span,
                            &[],
                        ),
                        Some(false) => cx.edit(
                            e.span,
                            vec![lit("contains("), Piece::Src(*left), lit(", "), Piece::Src(*right), lit(")")],
                            Row::InfixContains,
                            e.span,
                            &[],
                        ),
                        None => cx.refuse(
                            decl,
                            Some(Row::InfixContains),
                            e.span,
                            format!(
                                "`{}`: whether the left operand is a list (`in`) or a String (`contains()`) needs the schema",
                                cx.text(e.span)
                            ),
                        ),
                    }
                }
            }
            SpannedExpr::Call { name, args } if name == "fuzzy" && args.len() == 3 => {
                cx.edit(
                    args[2],
                    vec![lit("max_edits: "), Piece::Src(args[2])],
                    Row::FuzzyMaxEdits,
                    e.span,
                    &[],
                );
            }
            _ => {}
        }
    }

    if decl.mutations.is_empty() {
        rewrite_ranking(cx, decl);
    } else {
        rewrite_writes(cx, decl);
    }
}

/// `Some(true)` for a list operand, `Some(false)` for a String, `None` when
/// the schema is needed and absent or the operand is not a property or
/// parameter the rewriter can type.
fn operand_is_list(
    cx: &Cx<'_>,
    decl: &QueryDecl,
    types: &HashMap<String, VarType>,
    write_targets: &[(Span, &str)],
    left: Span,
) -> Option<bool> {
    let text = cx.text(left).trim();
    if text.starts_with('[') {
        return Some(true);
    }
    if text.starts_with('"') {
        return Some(false);
    }
    let node = decl
        .spans
        .exprs
        .iter()
        .find(|e| e.span == left)
        .map(|e| e.node.clone())?;
    let catalog = cx.catalog;
    let prop_list = |ty: &VarType, prop: &str| -> Option<bool> {
        let catalog = catalog?;
        match ty {
            VarType::Node(t) => catalog
                .query_node_type(t)?
                .properties
                .get(prop)
                .map(|p| p.list),
            // Every member must agree; a disagreement is the type checker's.
            VarType::Edge(members) => {
                let kinds: Option<BTreeSet<bool>> = members
                    .iter()
                    .map(|m| {
                        catalog
                            .edge_types
                            .get(m)?
                            .properties
                            .get(prop)
                            .map(|p| p.list)
                    })
                    .collect();
                let kinds = kinds?;
                (kinds.len() == 1).then(|| *kinds.iter().next().unwrap())
            }
        }
    };
    match node {
        SpannedExpr::Variable { name } => decl
            .params
            .iter()
            .rev()
            .find(|p| p.name == name)
            .map(|p| p.type_name.starts_with('[')),
        SpannedExpr::PropAccess { variable, property } => {
            prop_list(types.get(&variable)?, &property)
        }
        SpannedExpr::BareName { name } => {
            let (_, ty) = write_targets.iter().find(|(span, _)| span.contains(left))?;
            let catalog = catalog?;
            let ty = if catalog.edge_types.contains_key(*ty) {
                VarType::Edge(vec![ty.to_string()])
            } else {
                VarType::Node(ty.to_string())
            };
            prop_list(&ty, &name)
        }
        _ => None,
    }
}

/// The variables a match clause binds at its top level, in first-appearance
/// order: bindings, traversal endpoints and edge bindings. Variables bound
/// inside `not`, `exists` and the count blocks are local to the block.
fn population(decl: &QueryDecl) -> Vec<(String, bool)> {
    let mut vars: Vec<(String, bool)> = Vec::new();
    let push = |name: &str, edge: bool, vars: &mut Vec<(String, bool)>| {
        if !vars.iter().any(|(v, _)| v == name) {
            vars.push((name.to_string(), edge));
        }
    };
    for c in &decl.match_clause {
        match c {
            Clause::Binding(b) => push(&b.variable, false, &mut vars),
            Clause::Traversal(t) => {
                push(&t.src, false, &mut vars);
                if let Some(e) = &t.edge_binding {
                    push(e, true, &mut vars);
                }
                push(&t.dst, false, &mut vars);
            }
            Clause::Filter(_) | Clause::Subquery(_) => {}
        }
    }
    vars
}

fn block_local_variables(decl: &QueryDecl) -> BTreeSet<String> {
    let top: BTreeSet<String> = population(decl).into_iter().map(|(v, _)| v).collect();
    let mut local = BTreeSet::new();
    fn walk(clauses: &[Clause], local: &mut BTreeSet<String>, depth: usize) {
        for c in clauses {
            match c {
                Clause::Binding(b) if depth > 0 => {
                    local.insert(b.variable.clone());
                }
                Clause::Traversal(t) if depth > 0 => {
                    local.insert(t.src.clone());
                    local.insert(t.dst.clone());
                    if let Some(e) = &t.edge_binding {
                        local.insert(e.clone());
                    }
                }
                Clause::Subquery(s) => walk(&s.clauses, local, depth + 1),
                _ => {}
            }
        }
    }
    walk(&decl.match_clause, &mut local, 0);
    local.retain(|v| !top.contains(v));
    local
}

fn squash(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect()
}

fn is_ranking(expr: &Expr) -> bool {
    matches!(
        expr,
        Expr::Nearest { .. } | Expr::Bm25 { .. } | Expr::Rrf { .. }
    )
}

fn contains_ranking(expr: &Expr) -> bool {
    match expr {
        Expr::Nearest { .. } | Expr::Bm25 { .. } | Expr::Rrf { .. } => true,
        Expr::Binary { left, right, .. } => contains_ranking(left) || contains_ranking(right),
        Expr::Not(e) | Expr::IsNull { expr: e, .. } => contains_ranking(e),
        Expr::In { needle, list } => contains_ranking(needle) || contains_ranking(list),
        Expr::Aggregate { arg, .. } => contains_ranking(arg),
        _ => false,
    }
}

/// The ranked variable and property of a `nearest` or `bm25` call.
fn ranked_field(expr: &Expr) -> Option<(&str, &str)> {
    match expr {
        Expr::Nearest {
            variable, property, ..
        } => Some((variable, property)),
        Expr::Bm25 { field, .. } => match field.as_ref() {
            Expr::PropAccess { variable, property } => Some((variable, property)),
            _ => None,
        },
        _ => None,
    }
}

/// The span and arguments of the ranking call recorded at `span`.
fn call_at<'d>(spans: &'d DeclSpans, span: Span) -> Option<(&'d str, &'d [Span])> {
    spans.exprs.iter().find_map(|e| match &e.node {
        SpannedExpr::Call { name, args } if e.span == span => {
            Some((name.as_str(), args.as_slice()))
        }
        _ => None,
    })
}

/// Rows 5-7: a `nearest`, `bm25` or `rrf` in `order` or `return` moves into
/// `from` over the match clause as a population.
fn rewrite_ranking(cx: &mut Cx<'_>, decl: &QueryDecl) {
    let spans = &decl.spans;
    let in_order = decl.order_clause.iter().any(|o| contains_ranking(&o.expr));
    let in_return = decl.return_clause.iter().any(|p| contains_ranking(&p.expr));
    if !in_order && !in_return {
        return;
    }
    let (Some(match_inner), Some(body)) = (spans.match_inner, Some(spans.body)) else {
        return;
    };
    // The window: the leading order key when it is a ranking; else a lone
    // `bm25` in `return` is an exact score with no window.
    let leading = decl.order_clause.first().filter(|o| is_ranking(&o.expr));
    let window_expr: &Expr = match leading {
        Some(o) => &o.expr,
        None => {
            if in_order {
                cx.refuse(
                    decl,
                    None,
                    spans.order_clause.unwrap_or(body),
                    "a ranking that is not the leading `order` key; the table moves only the leading key",
                );
                return;
            }
            let rankings: Vec<&Expr> = decl
                .return_clause
                .iter()
                .filter(|p| is_ranking(&p.expr))
                .map(|p| &p.expr)
                .collect();
            match rankings.first() {
                Some(first @ Expr::Bm25 { .. }) => first,
                _ => {
                    cx.refuse(
                        decl,
                        None,
                        spans.return_clause.unwrap_or(body),
                        "a `nearest` or `rrf` in `return` with no ranking `order` key has no window to size",
                    );
                    return;
                }
            }
        }
    };
    let row = match window_expr {
        Expr::Nearest { .. } => Row::Nearest,
        Expr::Bm25 { .. } => Row::Bm25,
        _ => Row::Rrf,
    };
    // Where the window's call is written: the leading order key, or the
    // first projection holding it.
    let window_span = match leading {
        Some(_) => spans.orderings[0].key,
        None => decl
            .return_clause
            .iter()
            .zip(&spans.projections)
            .find(|(p, _)| std::ptr::eq(&p.expr, window_expr))
            .map(|(_, s)| s.expr)
            .unwrap_or(body),
    };
    let window_text = squash(cx.text(window_span));

    // Every ranking call in `return` and `order` that is not inside another
    // ranking call must be the window; each becomes the score column.
    let mut occurrences = Vec::new();
    let regions: Vec<Span> = spans
        .return_clause
        .into_iter()
        .chain(spans.order_clause)
        .collect();
    let calls: Vec<(Span, &str)> = spans
        .exprs
        .iter()
        .filter_map(|e| match &e.node {
            SpannedExpr::Call { name, .. }
                if matches!(name.as_str(), "nearest" | "bm25" | "rrf")
                    && regions.iter().any(|r| r.contains(e.span)) =>
            {
                Some((e.span, name.as_str()))
            }
            _ => None,
        })
        .collect();
    for (span, _) in &calls {
        let nested = calls
            .iter()
            .any(|(outer, _)| outer != span && outer.contains(*span));
        if nested {
            continue;
        }
        if squash(cx.text(*span)) != window_text {
            cx.refuse(
                decl,
                Some(row),
                *span,
                format!(
                    "`{}` is a second ranking beside the window `{}`; the table rewrites one ranking per read",
                    cx.text(*span),
                    cx.text(window_span)
                ),
            );
            return;
        }
        occurrences.push(*span);
    }
    // Rankings anywhere else (a match filter) are not moved.
    if spans.exprs.iter().any(|e| {
        matches!(&e.node, SpannedExpr::Call { name, .. } if matches!(name.as_str(), "nearest" | "bm25" | "rrf"))
            && match_inner.contains(e.span)
    }) {
        cx.refuse(decl, Some(row), match_inner, "a ranking call inside `match`");
        return;
    }

    let taken = dollar_names(decl);
    let score = match row {
        Row::Nearest => "distance",
        _ => "score",
    };
    if taken.contains(score) {
        cx.refuse(
            decl,
            Some(row),
            body,
            format!("`${score}` is already a name in this read; the score column would collide (T68), and edition 3 renames only through a wrapper read"),
        );
        return;
    }

    let pop = population(decl);
    let node_columns: Vec<&str> = pop
        .iter()
        .filter(|(_, e)| !e)
        .map(|(v, _)| v.as_str())
        .collect();
    let edge_columns: Vec<&str> = pop
        .iter()
        .filter(|(_, e)| *e)
        .map(|(v, _)| v.as_str())
        .collect();
    if node_columns.len() != 1 {
        cx.note(
            decl,
            row,
            "population-node-columns",
            format!(
                "population returns {} node columns ({}); section 5 requires exactly one, and the bare field names none of them",
                node_columns.len(),
                node_columns.iter().map(|v| format!("${v}")).collect::<Vec<_>>().join(", ")
            ),
        );
    }
    if !edge_columns.is_empty() {
        cx.note(
            decl,
            row,
            "population-edge-column",
            format!(
                "population returns edge binding(s) {}",
                edge_columns
                    .iter()
                    .map(|v| format!("${v}"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        );
    }
    let locals = block_local_variables(decl);
    if !locals.is_empty() {
        cx.note(
            decl,
            row,
            "population-block-locals",
            format!(
                "variables bound only inside a block ({}) stay out of the population",
                locals
                    .iter()
                    .map(|v| format!("${v}"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        );
    }
    let aggregate = decl
        .return_clause
        .iter()
        .any(|p| matches!(p.expr, Expr::Aggregate { .. }));
    if aggregate {
        cx.note(
            decl,
            row,
            "ranking-under-aggregate",
            "an aggregate over a ranked read: in edition 2 the ranking key selects the rows the aggregate reads and orders no group; the rewrite drops it from `order` (the window or score is in `from`), and `limit` stays a cut of the groups",
        );
    }
    let return_list = format!(
        "return {{ {} }}",
        pop.iter()
            .map(|(v, _)| format!("${v}"))
            .collect::<Vec<_>>()
            .join(", ")
    );

    let limit = spans.limit_value.map(|s| cx.text(s).to_string());
    // Arms: (kind, ranked var, field, query span)
    let arm = |expr: &Expr, span: Span| -> Option<(&'static str, String, String, Span)> {
        let (var, field) = ranked_field(expr)?;
        let (_, args) = call_at(spans, span)?;
        let kind = if matches!(expr, Expr::Nearest { .. }) {
            "nearest"
        } else {
            "bm25"
        };
        Some((kind, var.to_string(), field.to_string(), *args.get(1)?))
    };
    let mut generated: Vec<String> = vec![score.to_string()];
    let insertion_before: Vec<Piece>;
    let insertion_after: Vec<Piece>;
    match window_expr {
        Expr::Nearest { .. } | Expr::Bm25 { .. } => {
            let Some((kind, var, field, query)) = arm(window_expr, window_span) else {
                cx.refuse(
                    decl,
                    Some(row),
                    window_span,
                    "a ranking whose field is not `$var.property`",
                );
                return;
            };
            note_ranked_var(cx, decl, row, &var, &node_columns);
            let mut tail = vec![
                lit(format!("}} {return_list} }}, {field}, ")),
                Piece::Src(query),
            ];
            if kind == "nearest" {
                let Some(n) = &limit else {
                    cx.refuse(
                        decl,
                        Some(row),
                        window_span,
                        "a `nearest` window with no `limit` to size `k`",
                    );
                    return;
                };
                tail.push(lit(format!(", k: {n}")));
            }
            tail.push(lit(") "));
            insertion_before = vec![lit(format!(" from {kind}({{ match {{"))];
            insertion_after = tail;
        }
        Expr::Rrf {
            primary, secondary, ..
        } => {
            let Some((_, args)) = call_at(spans, window_span) else {
                cx.refuse(
                    decl,
                    Some(row),
                    window_span,
                    "an `rrf` whose arms the parser did not record",
                );
                return;
            };
            let (Some(a0), Some(a1)) = (arm(primary, args[0]), arm(secondary, args[1])) else {
                cx.refuse(
                    decl,
                    Some(row),
                    window_span,
                    "an `rrf` arm whose field is not `$var.property`",
                );
                return;
            };
            if squash(cx.text(args[0])) == squash(cx.text(args[1])) {
                cx.refuse(
                    decl,
                    Some(row),
                    window_span,
                    format!(
                        "duplicate arms `{}`: two arms with the same population, field and query are an edition-3 type error (section 4, Fusion)",
                        cx.text(args[0])
                    ),
                );
                return;
            }
            if a0.1 != a1.1 {
                cx.refuse(
                    decl,
                    Some(row),
                    window_span,
                    format!(
                        "arms over different bindings (`${}`, `${}`): arms over different node columns are an edition-3 type error",
                        a0.1, a1.1
                    ),
                );
                return;
            }
            note_ranked_var(cx, decl, row, &a0.1, &node_columns);
            let p = fresh("p", &taken);
            if p != "p" {
                cx.note(
                    decl,
                    row,
                    "rrf-let-name",
                    format!(
                        "the table's `let $p` collides with a name in this read; written `${p}`"
                    ),
                );
            }
            generated.push(p.clone());
            let window_k = match &limit {
                Some(n) => n.clone(),
                None => {
                    cx.note(
                        decl,
                        row,
                        "rrf-no-limit",
                        "no `limit`: the edition-2 nearest arm asks for RRF_NEAREST_ARM_K = 100 candidates, written `k: 100`; the table names only `k: <limit>`",
                    );
                    "100".to_string()
                }
            };
            let fused_k = args.get(2).map(|k| Piece::Src(*k));
            let arm_call = |a: &(&'static str, String, String, Span)| -> Vec<Piece> {
                let mut pieces = vec![lit(format!("{}(${p}, {}, ", a.0, a.2)), Piece::Src(a.3)];
                if a.0 == "nearest" {
                    pieces.push(lit(format!(", k: {window_k}")));
                }
                pieces.push(lit(")"));
                pieces
            };
            let mut tail = vec![lit(format!("}} {return_list} }}  from rrf("))];
            tail.extend(arm_call(&a0));
            tail.push(lit(", "));
            tail.extend(arm_call(&a1));
            tail.push(lit(", k: "));
            match fused_k {
                Some(k) => tail.push(k),
                None => tail.push(lit("60")),
            }
            tail.push(lit(") "));
            insertion_before = vec![lit(format!(" let ${p} = {{ match {{"))];
            insertion_after = tail;
        }
        _ => return,
    }
    let generated_refs: Vec<&str> = generated.iter().map(String::as_str).collect();
    let match_clause = spans.match_clause.unwrap_or(body);
    cx.edit(
        Span::new(match_inner.start, match_inner.start),
        insertion_before,
        row,
        body,
        &generated_refs,
    );
    cx.edit(
        Span::new(match_inner.end, match_inner.end),
        insertion_after,
        row,
        body,
        &generated_refs,
    );
    let _ = match_clause;

    // The score column replaces every occurrence of the window's call.
    let unaliased_column = match row {
        Row::Nearest => "_distance",
        Row::Bm25 => "_score",
        _ => "_rrf",
    };
    for span in occurrences {
        // Under an aggregate the ranking key orders no group in edition 2:
        // the key is removed with its separator, or the whole `order`.
        if aggregate
            && let Some(i) = spans.orderings.iter().position(|o| o.key == span)
        {
            let removed = if spans.orderings.len() == 1 {
                let from = spans
                    .limit_clause
                    .filter(|l| l.end <= spans.order_clause.map_or(0, |o| o.start))
                    .map(|l| l.end)
                    .or(spans.return_clause.map(|r| r.end))
                    .unwrap_or(spans.order_clause.unwrap_or(span).start);
                Span::new(from, spans.order_clause.unwrap_or(span).end)
            } else if i == 0 {
                Span::new(spans.orderings[0].whole.start, spans.orderings[1].whole.start)
            } else {
                Span::new(spans.orderings[i - 1].whole.end, spans.orderings[i].whole.end)
            };
            let gap = removed.text(cx.source);
            if gap.contains("//") || gap.contains("/*") {
                cx.refuse(decl, Some(row), removed, "a comment inside the ranking key the rewrite removes");
                continue;
            }
            cx.edit(removed, Vec::new(), row, body, &generated_refs);
            continue;
        }
        let mut pieces = vec![lit(format!("${score}"))];
        // A bare rank key, leading or not, reads as the rank's own
        // direction: `bm25` and `rrf` descending.
        let bare_key = decl
            .order_clause
            .iter()
            .zip(&spans.orderings)
            .any(|(o, s)| s.key == span && !o.explicit);
        if bare_key && matches!(row, Row::Bm25 | Row::Rrf) {
            pieces.push(lit(" desc"));
            cx.note(
                decl,
                row,
                "bare-rank-key-desc",
                format!("bare `{}` key sorted descending in edition 2; `${score}` alone sorts ascending, so the rewrite writes `desc`", cx.text(span)),
            );
        }
        if let Some((proj, pspan)) = decl
            .return_clause
            .iter()
            .zip(&spans.projections)
            .find(|(_, s)| s.expr == span)
            && proj.alias.is_none()
        {
            pieces.push(lit(format!(" as {unaliased_column}")));
            let (var, column) = proj.expr.score_column().unwrap_or(("?", "?"));
            cx.note(
                decl,
                row,
                "result-column-renamed",
                format!(
                    "unaliased `{}`: today's column `{var}.{column}` becomes `{unaliased_column}`, the alias, not `{var}.{column}`",
                    cx.text(pspan.expr)
                ),
            );
        }
        cx.edit(span, pieces, row, span, &generated_refs);
    }
}

fn note_ranked_var(cx: &mut Cx<'_>, decl: &QueryDecl, row: Row, var: &str, node_columns: &[&str]) {
    if node_columns.len() > 1 {
        cx.note(
            decl,
            row,
            "ranked-variable-dropped",
            format!("the ranking read `${var}`; the generated bare field does not say which of the population's node columns it names"),
        );
    }
}

/// Rows 8-9: `delete T where φ` and `update T set { … } where φ`.
fn rewrite_writes(cx: &mut Cx<'_>, decl: &QueryDecl) {
    let spans = &decl.spans;
    let statements = decl.mutations.len();
    let taken = dollar_names(decl);
    for (mutation, mspans) in decl.mutations.iter().zip(&spans.mutations) {
        let (row, type_name) = match mutation {
            Mutation::Delete(d) => (Row::Delete, d.type_name.as_str()),
            Mutation::Update(u) => (Row::Update, u.type_name.as_str()),
            Mutation::Insert(_) => continue,
        };
        if statements > 1 {
            cx.refuse(
                decl,
                Some(row),
                mspans.stmt,
                format!(
                    "a body of {statements} statements: edition 3 runs every statement once per row of the one clause relation, so a second `$x: T  φ` clause would multiply or empty the other statements' rows"
                ),
            );
            continue;
        }
        let (Some(predicate), Some(where_kw)) = (mspans.predicate, mspans.where_keyword) else {
            cx.refuse(
                decl,
                Some(row),
                mspans.stmt,
                "the parser recorded no `where` span",
            );
            continue;
        };
        let is_edge = cx
            .catalog
            .is_some_and(|c| c.edge_types.contains_key(type_name));
        if is_edge {
            cx.note(
                decl,
                row,
                "edge-binding",
                format!("target `{type_name}` is an edge type; `$x: {type_name}` binds an edge by type, a form the RFC's `binding` rule does not say it admits"),
            );
        }
        let x = fresh("x", &taken);
        if x != "x" {
            cx.note(
                decl,
                row,
                "write-variable-name",
                format!("`$x` is taken in this declaration; written `${x}`"),
            );
        }
        // φ′: every bare name and meta-field of φ prefixed `$x.`; the legacy
        // edge endpoints `from` and `to` become `$x.@src` and `$x.@dst`.
        for e in &spans.exprs {
            let SpannedExpr::BareName { name } = &e.node else {
                continue;
            };
            if !predicate.contains(e.span) {
                continue;
            }
            let pieces = match (is_edge, name.as_str()) {
                (true, "from") => {
                    cx.note(
                        decl,
                        row,
                        "legacy-endpoint",
                        "bare `from` is `@src`: prefixing gives `$x.from`, which names no property",
                    );
                    vec![lit(format!("${x}.@src"))]
                }
                (true, "to") => {
                    cx.note(
                        decl,
                        row,
                        "legacy-endpoint",
                        "bare `to` is `@dst`: prefixing gives `$x.to`, which names no property",
                    );
                    vec![lit(format!("${x}.@dst"))]
                }
                _ => vec![lit(format!("${x}.")), Piece::Src(e.span)],
            };
            cx.edit(e.span, pieces, row, predicate, &[x.as_str()]);
        }
        // The removed range ` where φ` drops no comment outside φ.
        let removed_from = match mutation {
            Mutation::Update(_) => match mspans.set_block {
                Some(set) => set.end,
                None => {
                    cx.refuse(
                        decl,
                        Some(row),
                        mspans.stmt,
                        "the parser recorded no `set { … }` span",
                    );
                    continue;
                }
            },
            _ => mspans.type_name.end,
        };
        let gap = &cx.source[removed_from..predicate.start];
        if gap.contains("//") || gap.contains("/*") {
            cx.refuse(
                decl,
                Some(row),
                Span::new(removed_from, predicate.start),
                "a comment between the statement and its predicate would be dropped by the move",
            );
            continue;
        }
        let _ = where_kw;
        let separator = statement_separator(cx.source, mspans.stmt.start);
        let construct = mspans.stmt;
        cx.edit(
            Span::new(construct.start, construct.start),
            vec![
                lit(format!("${x}: ")),
                Piece::Raw(mspans.type_name),
                lit("  "),
                Piece::Src(predicate),
                lit(separator),
            ],
            row,
            construct,
            &[x.as_str()],
        );
        cx.edit(
            mspans.type_name,
            vec![lit(format!("${x}"))],
            row,
            construct,
            &[x.as_str()],
        );
        cx.edit(
            Span::new(removed_from, predicate.end),
            Vec::new(),
            row,
            construct,
            &[x.as_str()],
        );
    }
}

/// A newline and the statement's indent when it starts its line, two spaces
/// otherwise.
fn statement_separator(source: &str, at: usize) -> String {
    let line_start = source[..at].rfind('\n').map_or(0, |i| i + 1);
    let prefix = &source[line_start..at];
    if prefix.chars().all(|c| c == ' ' || c == '\t') {
        format!("\n{prefix}")
    } else {
        "  ".to_string()
    }
}

/// Apply the edits: the maximal ones highest offset first, each rendered
/// with the edits nested in the ranges it copies. Checks that the maximal
/// edits never overlap and that every edit is rendered exactly once.
fn apply(source: &str, edits: &[Edit]) -> (String, Proof) {
    let mut proof = Proof {
        non_overlapping: true,
        each_edit_rendered_once: true,
        ..Proof::default()
    };
    let mut rendered = vec![0usize; edits.len()];
    let all: Vec<usize> = (0..edits.len()).collect();
    let top = maximal(edits, &all, Span::new(0, source.len()), None);
    // Overlap among the maximal edits.
    for pair in top.windows(2) {
        let (a, b) = (&edits[pair[0]], &edits[pair[1]]);
        if a.span.end > b.span.start {
            proof.non_overlapping = false;
            proof.failures.push(format!(
                "overlapping edits {}..{} ({:?}) and {}..{} ({:?})",
                a.span.start, a.span.end, a.row, b.span.start, b.span.end, b.row
            ));
        }
    }
    let mut text = source.to_string();
    for &i in top.iter().rev() {
        let replacement = render_edit(source, edits, i, &mut rendered, 0);
        let e = &edits[i];
        if e.span.end <= text.len() {
            text.replace_range(e.span.start..e.span.end, &replacement);
        }
    }
    for (i, count) in rendered.iter().enumerate() {
        if *count != 1 {
            proof.each_edit_rendered_once = false;
            let e = &edits[i];
            proof.failures.push(format!(
                "edit {}..{} ({:?}) rendered {count} times",
                e.span.start, e.span.end, e.row
            ));
        }
    }
    (text, proof)
}

/// The edits of `pool` inside `within` that no other edit of the pool
/// contains, ordered by start; an insertion before a replacement at the
/// same offset, then creation order. `skip` is the edit being rendered.
fn maximal(edits: &[Edit], pool: &[usize], within: Span, skip: Option<usize>) -> Vec<usize> {
    let inside: Vec<usize> = pool
        .iter()
        .copied()
        .filter(|&i| Some(i) != skip && within.contains(edits[i].span))
        .collect();
    let contains = |outer: &Edit, inner: &Edit| -> bool {
        let (o, n) = (outer.span, inner.span);
        if o == n {
            // Equal ranges: the earlier edit is the outer one, except that an
            // insertion never contains anything.
            return o.start != o.end && outer.seq < inner.seq;
        }
        o.start <= n.start
            && n.end <= o.end
            && o.start != o.end
            // An insertion at a replacement's boundary sits beside it.
            && !(n.start == n.end && (n.start == o.start || n.start == o.end))
    };
    let mut top: Vec<usize> = inside
        .iter()
        .copied()
        .filter(|&i| {
            !inside
                .iter()
                .any(|&j| j != i && contains(&edits[j], &edits[i]))
        })
        .collect();
    top.sort_by_key(|&i| {
        let e = &edits[i];
        (e.span.start, e.span.end != e.span.start, e.span.end, e.seq)
    });
    top
}

fn render_edit(
    source: &str,
    edits: &[Edit],
    i: usize,
    rendered: &mut [usize],
    depth: usize,
) -> String {
    rendered[i] += 1;
    let mut out = String::new();
    if depth > 32 {
        return out;
    }
    for piece in &edits[i].pieces {
        match piece {
            Piece::Lit(s) => out.push_str(s),
            Piece::Src(span) => {
                out.push_str(&render_span(source, edits, *span, i, rendered, depth + 1))
            }
            Piece::Raw(span) => out.push_str(span.text(source)),
        }
    }
    out
}

fn render_span(
    source: &str,
    edits: &[Edit],
    span: Span,
    owner: usize,
    rendered: &mut [usize],
    depth: usize,
) -> String {
    // The edits a copy carries: those inside the copied range, other than
    // the copying edit and the edits that contain it.
    let owner_span = edits[owner].span;
    let pool: Vec<usize> = (0..edits.len())
        .filter(|&j| {
            j != owner
                && !(edits[j].span.start <= owner_span.start
                    && owner_span.end <= edits[j].span.end
                    && edits[j].span != owner_span
                    && edits[j].span.start != edits[j].span.end)
        })
        .collect();
    let children = maximal(edits, &pool, span, Some(owner));
    let mut out = String::new();
    let mut at = span.start;
    for j in children {
        let e = &edits[j];
        if e.span.start < at {
            continue;
        }
        out.push_str(&source[at..e.span.start]);
        out.push_str(&render_edit(source, edits, j, rendered, depth));
        at = e.span.end;
    }
    out.push_str(&source[at..span.end]);
    out
}

/// Every edit and every range it copies lie inside its construct; its
/// literal text uses the row's vocabulary, the names the construct already
/// holds and the variables the row generated.
fn check_confinement(source: &str, edits: &[Edit], failures: &mut Vec<String>) -> bool {
    let mut ok = true;
    for e in edits {
        let construct = e.construct;
        if !construct.contains(e.span) {
            ok = false;
            failures.push(format!(
                "edit {}..{} lies outside its construct {}..{}",
                e.span.start, e.span.end, construct.start, construct.end
            ));
        }
        let held: BTreeSet<String> = words(construct.text(source)).into_iter().collect();
        for piece in &e.pieces {
            match piece {
                Piece::Src(s) | Piece::Raw(s) => {
                    if !construct.contains(*s) {
                        ok = false;
                        failures.push(format!(
                            "edit {}..{} copies {}..{} from outside its construct",
                            e.span.start, e.span.end, s.start, s.end
                        ));
                    }
                }
                Piece::Lit(text) => {
                    for word in words(text) {
                        let allowed = e.row.vocabulary().contains(&word.as_str())
                            || held.contains(&word)
                            || word.strip_prefix('$').is_some_and(|v| {
                                e.generated.iter().any(|g| g == v) || held.contains(&word)
                            })
                            || word.chars().all(|c| c.is_ascii_digit());
                        if !allowed {
                            ok = false;
                            failures.push(format!("edit {}..{} ({:?}) writes `{word}`, which neither its row nor its construct holds", e.span.start, e.span.end, e.row));
                        }
                    }
                }
            }
        }
    }
    ok
}

/// The identifier-like words of a text: `$name`, `@name` and bare words,
/// outside string literals and comments.
fn words(text: &str) -> Vec<String> {
    tokens(text)
        .into_iter()
        .filter_map(|t| match t {
            Token::Word(w) => Some(w),
            _ => None,
        })
        .collect()
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum Token {
    Word(String),
    Str,
    Punct(char),
}

/// A small lexer over GQ text: words (with a leading `$` or `@`), string
/// literals, punctuation; whitespace and comments dropped.
fn tokens(text: &str) -> Vec<Token> {
    let bytes: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        let c = bytes[i];
        if c.is_whitespace() {
            i += 1;
        } else if c == '/' && bytes.get(i + 1) == Some(&'/') {
            while i < bytes.len() && bytes[i] != '\n' {
                i += 1;
            }
        } else if c == '/' && bytes.get(i + 1) == Some(&'*') {
            i += 2;
            while i < bytes.len() && !(bytes[i] == '*' && bytes.get(i + 1) == Some(&'/')) {
                i += 1;
            }
            i += 2;
        } else if c == '"' {
            i += 1;
            while i < bytes.len() && bytes[i] != '"' {
                if bytes[i] == '\\' {
                    i += 1;
                }
                i += 1;
            }
            i += 1;
            out.push(Token::Str);
        } else if c.is_alphanumeric() || c == '_' || c == '$' || c == '@' {
            let start = i;
            i += 1;
            while i < bytes.len() && (bytes[i].is_alphanumeric() || bytes[i] == '_') {
                i += 1;
            }
            out.push(Token::Word(bytes[start..i].iter().collect()));
        } else {
            out.push(Token::Punct(c));
            i += 1;
        }
    }
    out
}

/// No edition-2 spelling of a row survives: no infix `contains` or
/// `starts_with`, no `fuzzy` with a third positional argument, no ranking
/// call inside `order` or `return`, no `where` after a write statement. A
/// second run would find nothing to rewrite.
fn check_no_residue(text: &str, failures: &mut Vec<String>) -> bool {
    let toks = tokens(text);
    let mut ok = true;
    let word = |i: usize| match toks.get(i) {
        Some(Token::Word(w)) => Some(w.as_str()),
        _ => None,
    };
    let punct = |i: usize, c: char| toks.get(i) == Some(&Token::Punct(c));
    // The keyword that opened each enclosing brace and parenthesis.
    let mut open: Vec<(char, Option<String>)> = Vec::new();
    for i in 0..toks.len() {
        let before = i.checked_sub(1);
        match &toks[i] {
            Token::Word(w) => {
                let w = w.as_str();
                // Infix: an operand on each side, no `(` after; not a
                // property after `.`, an assignment name, an alias, or the
                // edge of a traversal `$a contains $b`.
                if matches!(w, "contains" | "starts_with")
                    && !punct(i + 1, '(')
                    && !punct(i + 1, ':')
                    && !before.is_some_and(|b| punct(b, '.') || word(b) == Some("as"))
                    && !(before.and_then(word).is_some_and(|b| b.starts_with('$'))
                        && word(i + 1).is_some_and(|n| n.starts_with('$')))
                {
                    ok = false;
                    failures.push(format!("residue: infix `{w}`"));
                }
                if matches!(w, "nearest" | "bm25" | "rrf")
                    && punct(i + 1, '(')
                    && !before.is_some_and(|b| punct(b, '.'))
                    && open
                        .iter()
                        .any(|(c, o)| *c == '{' && matches!(o.as_deref(), Some("order" | "return")))
                    && !open
                        .iter()
                        .any(|(c, o)| *c == '(' && o.as_deref() == Some("from"))
                {
                    ok = false;
                    failures.push(format!("residue: `{w}(` inside `order` or `return`"));
                }
                if w == "where" && !before.is_some_and(|b| punct(b, '.')) {
                    let statement = toks[..i].iter().rev().find_map(|t| match t {
                        Token::Word(k)
                            if matches!(
                                k.as_str(),
                                "delete" | "update" | "insert" | "where" | "match"
                            ) =>
                        {
                            Some(k.as_str())
                        }
                        _ => None,
                    });
                    if matches!(statement, Some("delete" | "update")) {
                        ok = false;
                        failures.push("residue: `where` on a write statement".into());
                    }
                }
            }
            Token::Punct(c @ ('{' | '(' | '[')) => {
                // `from nearest(` opens under `from`: the call's own keyword
                // is the word before `(`, its enclosing `from` the one before.
                let owner = before.and_then(word).map(str::to_string);
                let owner = if *c == '('
                    && before.and_then(|b| b.checked_sub(1)).and_then(word) == Some("from")
                {
                    Some("from".to_string())
                } else {
                    owner
                };
                open.push((*c, owner));
            }
            Token::Punct('}' | ')' | ']') => {
                open.pop();
            }
            _ => {}
        }
    }
    // `fuzzy(a, b, c)` with a positional third argument.
    let mut i = 0;
    while i < toks.len() {
        if word(i) == Some("fuzzy") && punct(i + 1, '(') {
            let mut depth = 0;
            let mut commas = 0;
            let mut third_named = false;
            let mut j = i + 1;
            while j < toks.len() {
                match &toks[j] {
                    Token::Punct('(' | '[' | '{') => depth += 1,
                    Token::Punct(')' | ']' | '}') => {
                        depth -= 1;
                        if depth == 0 {
                            break;
                        }
                    }
                    Token::Punct(',') if depth == 1 => {
                        commas += 1;
                        if commas == 2 {
                            third_named = punct(j + 2, ':');
                        }
                    }
                    _ => {}
                }
                j += 1;
            }
            if commas >= 2 && !third_named {
                ok = false;
                failures.push("residue: `fuzzy` with a positional third argument".into());
            }
            i = j;
        }
        i += 1;
    }
    ok
}

/// The rows an edition-2 `delete` or `update` selects, as an edition-2 read:
/// the binding form and φ′ the rewrite writes, with φ′ kept in edition-2
/// spelling so today's compiler runs it. An edge target, which edition 2
/// cannot bind by type, is bound through its traversal `$s $x:edge $d`.
#[derive(Debug, Clone)]
pub struct WriteSelection {
    pub decl: String,
    pub row: Row,
    pub type_name: String,
    pub is_edge: bool,
    /// `query __gq3_selection(<params>) { match { <binding>  φ′ } return { $x.@id as id } }`
    pub selection_read: String,
    /// `query __gq3_all() { match { <binding> } return { $x.@id as id, $x } }`:
    /// every entity of the target type with its whole row.
    pub all_read: String,
}

pub fn write_selections(source: &str, catalog: &Catalog) -> Vec<WriteSelection> {
    let Ok(file) = parse_query_diagnostic(source) else {
        return Vec::new();
    };
    let FileBody::Queries(decls) = &file.body else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for decl in decls {
        if decl.mutations.len() != 1 {
            continue;
        }
        let (row, type_name) = match &decl.mutations[0] {
            Mutation::Delete(d) => (Row::Delete, d.type_name.clone()),
            Mutation::Update(u) => (Row::Update, u.type_name.clone()),
            Mutation::Insert(_) => continue,
        };
        let Some(predicate) = decl.spans.mutations[0].predicate else {
            continue;
        };
        let is_edge = catalog.edge_types.contains_key(&type_name);
        let x = fresh("x", &dollar_names(decl));
        let mut phi = String::new();
        let mut at = predicate.start;
        for e in &decl.spans.exprs {
            let SpannedExpr::BareName { name } = &e.node else {
                continue;
            };
            if !predicate.contains(e.span) || e.span.start < at {
                continue;
            }
            phi.push_str(&source[at..e.span.start]);
            match (is_edge, name.as_str()) {
                (true, "from") => phi.push_str(&format!("${x}.@src")),
                (true, "to") => phi.push_str(&format!("${x}.@dst")),
                _ => phi.push_str(&format!("${x}.{name}")),
            }
            at = e.span.end;
        }
        phi.push_str(&source[at..predicate.end]);
        let binding = if is_edge {
            let ident = catalog
                .edge_name_index
                .iter()
                .find(|(_, key)| **key == type_name)
                .map(|(ident, _)| ident.clone())
                .unwrap_or_else(|| {
                    let mut c = type_name.chars();
                    c.next()
                        .map(|f| f.to_lowercase().collect::<String>() + c.as_str())
                        .unwrap_or_default()
                });
            let s = fresh("s", &dollar_names(decl));
            let d = fresh("d", &dollar_names(decl));
            // Edition 2 refuses a traversal with neither endpoint bound
            // (T5), so the source endpoint is bound by its type.
            let from = catalog
                .edge_types
                .get(&type_name)
                .map(|e| e.from_type.clone())
                .unwrap_or_default();
            format!("${s}: {from}  ${s} ${x}:{ident} ${d}")
        } else {
            format!("${x}: {type_name}")
        };
        let params = decl
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
            .collect::<Vec<_>>()
            .join(", ");
        out.push(WriteSelection {
            decl: decl.name.clone(),
            row,
            type_name: type_name.clone(),
            is_edge,
            selection_read: format!(
                "query __gq3_selection({params}) {{ match {{ {binding}  {phi} }} return {{ ${x}.@id as id }} }}"
            ),
            all_read: format!("query __gq3_all() {{ match {{ {binding} }} return {{ ${x}.@id as id, ${x} }} }}"),
        });
    }
    out
}

/// The source with only the edits of `rows` applied and no edition line:
/// the edition-2 text a row whose output edition 2 already parses (the list
/// `contains` to `in`) produces, for a typed-IR comparison by today's
/// compiler.
pub fn apply_rows_in_edition_2(
    source: &str,
    catalog: Option<&Catalog>,
    rows: &[Row],
) -> Result<String, MigrateError> {
    let m = migrate(source, catalog)?;
    let kept: Vec<Edit> = m
        .edits
        .into_iter()
        .filter(|e| rows.contains(&e.row))
        .collect();
    let (text, proof) = apply(source, &kept);
    if !proof.non_overlapping || !proof.each_edit_rendered_once {
        return Err(MigrateError(format!("{:?}", proof.failures)));
    }
    Ok(text)
}

/// A declaration's typed IR as a comparable string: the read pipeline,
/// projections, order and limit, or the mutation IR.
pub fn typed_ir(decl: &QueryDecl, catalog: &Catalog) -> Result<String, String> {
    use crate::query::typecheck::{CheckedQuery, typecheck_query_decl};
    match typecheck_query_decl(catalog, decl).map_err(|e| e.to_string())? {
        CheckedQuery::Read(read) => {
            let ir = crate::ir::lower::lower_query(catalog, &read).map_err(|e| e.to_string())?;
            Ok(format!(
                "{:?}|{:?}|{:?}|{:?}|{:?}|{:?}",
                ir.params, ir.pipeline, ir.return_exprs, ir.distinct, ir.order_by, ir.limit
            ))
        }
        CheckedQuery::Mutation(m) => crate::ir::lower::lower_mutation_query(catalog, &m)
            .map(|ir| format!("{ir:?}"))
            .map_err(|e| e.to_string()),
    }
}
