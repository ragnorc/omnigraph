//! The mechanical conversion of a GQ 3 parse tree to variant text: each
//! clause list becomes `match { element, … } where <conjuncts>`, the
//! elements in written order and the filters after them in written order. A
//! binding's braces become comparisons (`$x.p = v`, or `v in $x.p` over a
//! list property, as GQ 3's braces read one), and `not { … }` becomes the
//! conjunct `not exists { … }`.
//!
//! The expression printer follows the product's (`query/syntax/print.rs`),
//! changing only how a block is written.

use std::collections::HashMap;

use omnigraph_compiler::catalog::Catalog;
use omnigraph_compiler::query::file::{MutationWriteMode, UnattachedBase};
use omnigraph_compiler::query::syntax::print::literal;
use omnigraph_compiler::query::syntax::*;
use omnigraph_compiler::query::value::{AggFunc, CompOp};

/// Converts against a catalog, which says which braces test list
/// membership.
pub struct Converter<'a> {
    catalog: Option<&'a Catalog>,
    /// Variable to node type, for the braces of the scope being printed.
    types: Vec<HashMap<String, String>>,
}

impl<'a> Converter<'a> {
    pub fn new(catalog: Option<&'a Catalog>) -> Self {
        Self {
            catalog,
            types: vec![HashMap::new()],
        }
    }

    pub fn file(&mut self, file: &SourceFile) -> String {
        let Body::Decls(decls) = &file.body else {
            return String::new();
        };
        decls
            .iter()
            .map(|d| self.decl(d))
            .collect::<Vec<_>>()
            .join("\n\n")
            + "\n"
    }

    pub fn decl(&mut self, d: &Decl) -> String {
        let mut out = format!("query {}(", d.name);
        let params: Vec<String> = d
            .params
            .iter()
            .map(|p| {
                let mut s = format!(
                    "${}: {}{}",
                    p.name,
                    p.type_name,
                    if p.nullable { "?" } else { "" }
                );
                if let Some(default) = &p.default {
                    s.push_str(&format!(
                        " = {}{}",
                        if default.negative { "-" } else { "" },
                        literal(&default.value)
                    ));
                }
                s
            })
            .collect();
        out.push_str(&params.join(", "));
        out.push(')');
        for a in &d.annotations {
            out.push_str(&format!(" @{}", a.name));
            if let Some(args) = &a.args {
                let args: Vec<String> = args
                    .iter()
                    .map(|arg| match arg {
                        AnnotationArg::Positional(v) => annotation_value(v),
                        AnnotationArg::Named(k, v) => format!("{k} = {}", annotation_value(v)),
                    })
                    .collect();
                out.push_str(&format!("({})", args.join(", ")));
            }
        }
        match &d.write_mode {
            MutationWriteMode::Publish => {}
            MutationWriteMode::AsCommit => out.push_str(" as commit"),
            MutationWriteMode::Unattached { base } => {
                out.push_str(" as unattached branch");
                match base {
                    None => {}
                    Some(UnattachedBase::Literal(s)) => out.push_str(&format!(
                        " on {}",
                        literal(&omnigraph_compiler::Literal::String(s.clone()))
                    )),
                    Some(UnattachedBase::Param(p)) => out.push_str(&format!(" on ${p}")),
                }
            }
        }
        out.push_str(" {\n");
        match &d.body {
            DeclBody::Read(body) => {
                out.push_str("  ");
                out.push_str(&self.read_body(body, "\n  "));
            }
            DeclBody::Write(body) => {
                for group in &body.groups {
                    out.push_str("  ");
                    if !group.clauses.is_empty() {
                        out.push_str(&self.match_where(&group.clauses));
                        out.push_str("\n  ");
                    }
                    let stmts: Vec<String> =
                        group.statements.iter().map(|s| self.statement(s)).collect();
                    out.push_str(&stmts.join("\n  "));
                    out.push('\n');
                }
            }
        }
        if matches!(d.body, DeclBody::Read(_)) {
            out.push('\n');
        }
        out.push('}');
        out
    }

    fn statement(&mut self, s: &Stmt) -> String {
        let assignments = |c: &mut Self, a: &[Assignment]| {
            a.iter()
                .map(|a| format!("{}: {}", a.property, c.expr(&a.value)))
                .collect::<Vec<_>>()
                .join(", ")
        };
        match s {
            Stmt::Insert {
                type_name,
                assignments: a,
            } => format!("insert {type_name} {{ {} }}", assignments(self, a)),
            Stmt::Update {
                variable,
                assignments: a,
            } => format!("update ${variable} set {{ {} }}", assignments(self, a)),
            Stmt::Delete { variable } => format!("delete ${variable}"),
        }
    }

    /// `match { … } [where …] return { … } [order { … }] [limit n]`, the
    /// parts joined by `sep`.
    pub fn read_body(&mut self, body: &ReadBody, sep: &str) -> String {
        self.types.push(HashMap::new());
        let mut out = self.match_where(&body.clauses);
        out.push_str(sep);
        out.push_str("return ");
        if body.distinct {
            out.push_str("distinct ");
        }
        let items: Vec<String> = body
            .projections
            .iter()
            .map(|p| {
                let e = self.expr(&p.expr);
                match &p.alias {
                    Some(a) => format!("{e} as {a}"),
                    None => e,
                }
            })
            .collect();
        out.push_str(&format!("{{ {} }}", items.join(", ")));
        if !body.order.is_empty() {
            let keys: Vec<String> = body
                .order
                .iter()
                .map(|o| match o.descending {
                    None => self.expr(&o.expr),
                    Some(true) => format!("{} desc", self.expr(&o.expr)),
                    Some(false) => format!("{} asc", self.expr(&o.expr)),
                })
                .collect();
            out.push_str(sep);
            out.push_str(&format!("order {{ {} }}", keys.join(", ")));
        }
        match &body.limit {
            None => {}
            Some(Limit::Literal(n)) => out.push_str(&format!("{sep}limit {n}")),
            Some(Limit::Param(p)) => out.push_str(&format!("{sep}limit ${p}")),
        }
        self.types.pop();
        out
    }

    /// The elements and the `where` conjuncts of a clause list.
    fn split(&mut self, clauses: &[Clause]) -> (Vec<String>, Vec<String>) {
        let mut elements = Vec::new();
        let mut preds = Vec::new();
        for c in clauses {
            match c {
                Clause::Binding {
                    variable,
                    type_name,
                    prop_matches,
                } => {
                    self.types
                        .last_mut()
                        .expect("a scope")
                        .insert(variable.clone(), type_name.clone());
                    elements.push(format!("${variable}: {type_name}"));
                    for (prop, value) in prop_matches {
                        let v = self.expr_at(value, Prec::Sum);
                        if self.is_list(type_name, prop)
                            && !matches!(value, Expr::Literal(omnigraph_compiler::Literal::List(_)))
                        {
                            preds.push(format!("{v} in ${variable}.{prop}"));
                        } else {
                            preds.push(format!("${variable}.{prop} = {v}"));
                        }
                    }
                }
                Clause::Filter(e) => preds.push(self.conjunct(e)),
                Clause::Not(cs) => preds.push(format!("not exists {}", self.braced(cs))),
                Clause::Optional(cs) => elements.push(format!("optional {}", self.braced(cs))),
                Clause::Let { name, value } => elements.push(match value {
                    LetValue::Relation(body) => format!("let ${name} = {}", self.inline(body)),
                    LetValue::Expr(e) => format!("let ${name} = {}", self.expr(e)),
                }),
                Clause::From(rel) => elements.push(format!("from {}", self.relation(rel))),
                Clause::Traversal(t) => elements.push(traversal(t)),
            }
        }
        (elements, preds)
    }

    fn is_list(&self, type_name: &str, prop: &str) -> bool {
        self.catalog
            .and_then(|c| c.node_types.get(type_name))
            .and_then(|n| n.properties.get(prop))
            .is_some_and(|p| p.list)
    }

    /// `match { … } [where …]`.
    fn match_where(&mut self, clauses: &[Clause]) -> String {
        let (elements, preds) = self.split(clauses);
        let mut out = format!("match {{ {} }}", elements.join(", "));
        if !preds.is_empty() {
            out.push_str(&format!(" where {}", preds.join(" and ")));
        }
        out
    }

    /// `{ elements [where …] }` of a block or `optional`.
    fn braced(&mut self, clauses: &[Clause]) -> String {
        self.types.push(HashMap::new());
        let (elements, preds) = self.split(clauses);
        self.types.pop();
        if preds.is_empty() {
            format!("{{ {} }}", elements.join(", "))
        } else {
            format!(
                "{{ {} where {} }}",
                elements.join(", "),
                preds.join(" and ")
            )
        }
    }

    fn conjunct(&mut self, e: &Expr) -> String {
        self.expr_at(e, Prec::And)
    }

    fn inline(&mut self, body: &ReadBody) -> String {
        format!("{{ {} }}", self.read_body(body, " "))
    }

    fn relation(&mut self, rel: &RelExpr) -> String {
        match rel {
            RelExpr::Inline(body) => self.inline(body),
            RelExpr::Call(c) => self.call(c),
            RelExpr::Variable(v) => format!("${v}"),
        }
    }

    fn call(&mut self, c: &Call) -> String {
        let mut parts: Vec<String> = c
            .args
            .iter()
            .map(|a| match a {
                Arg::Expr(e) => self.expr(e),
                Arg::Inline(body) => self.inline(body),
            })
            .collect();
        parts.extend(
            c.options
                .iter()
                .map(|(k, v)| format!("{k}: {}", self.expr(v))),
        );
        format!("{}({})", c.name, parts.join(", "))
    }

    fn block(&mut self, b: &Block) -> String {
        let head = match (b.func, &b.arg) {
            (BlockFunc::Exists, _) => "exists".to_string(),
            (BlockFunc::Agg(f), None) => agg(f).to_string(),
            (BlockFunc::Agg(f), Some(a)) => format!("{}({})", agg(f), self.expr(a)),
        };
        format!("{head} {}", self.braced(&b.clauses))
    }

    fn expr_at(&mut self, e: &Expr, min: Prec) -> String {
        if prec(e) < min {
            format!("({})", self.expr(e))
        } else {
            self.expr(e)
        }
    }

    pub fn expr(&mut self, e: &Expr) -> String {
        match e {
            Expr::Literal(l) => literal(l),
            Expr::Variable(v) => format!("${v}"),
            Expr::Prop { variable, property } => format!("${variable}.{property}"),
            Expr::Meta(m) => format!("@{m}"),
            Expr::Ident(i) => i.clone(),
            Expr::Call(c) => self.call(c),
            Expr::AggDistinct { func, arg } => {
                format!("{}(distinct {})", agg(*func), self.expr(arg))
            }
            Expr::Block(b) => self.block(b),
            Expr::Neg(inner) => format!("-{}", self.expr_at(inner, Prec::Unary)),
            Expr::Arith { op, left, right } => {
                let own = prec(e);
                let right_min = if own == Prec::Sum {
                    Prec::Product
                } else {
                    Prec::Unary
                };
                format!(
                    "{} {} {}",
                    self.expr_at(left, own),
                    op.symbol(),
                    self.expr_at(right, right_min)
                )
            }
            Expr::Compare { op, left, right } => format!(
                "{} {} {}",
                self.expr_at(left, Prec::Sum),
                comp(*op),
                self.expr_at(right, Prec::Sum)
            ),
            Expr::In { needle, list, .. } => format!(
                "{} in {}",
                self.expr_at(needle, Prec::Sum),
                self.expr_at(list, Prec::Sum)
            ),
            Expr::IsNull {
                expr: inner,
                negated,
            } => format!(
                "{} is {}null",
                self.expr_at(inner, Prec::Sum),
                if *negated { "not " } else { "" }
            ),
            Expr::Not(inner) => format!("not {}", self.expr_at(inner, Prec::Not)),
            Expr::And(l, r) => format!(
                "{} and {}",
                self.expr_at(l, Prec::And),
                self.expr_at(r, Prec::Not)
            ),
            Expr::Or(l, r) => format!(
                "{} or {}",
                self.expr_at(l, Prec::Or),
                self.expr_at(r, Prec::And)
            ),
            Expr::InfixString {
                op, left, right, ..
            } => format!(
                "{} {} {}",
                self.expr_at(left, Prec::Sum),
                op.name(),
                self.expr_at(right, Prec::Sum)
            ),
        }
    }
}

fn annotation_value(v: &AnnotationValue) -> String {
    match v {
        AnnotationValue::Literal(l) => literal(l),
        AnnotationValue::Variable(v) => format!("${v}"),
        AnnotationValue::MetaField(m) => format!("@{m}"),
        AnnotationValue::Ident(i) => i.clone(),
    }
}

const RESERVED: &[&str] = &["and", "or", "not", "is", "null", "in"];

fn edge(name: &str) -> String {
    if RESERVED.contains(&name)
        || matches!(
            name,
            "let" | "from" | "true" | "false" | "insert" | "update" | "delete"
        )
    {
        format!("\"{name}\"")
    } else {
        name.to_string()
    }
}

fn traversal(t: &Traversal) -> String {
    let mut out = format!("${} ", t.src);
    if let Some(b) = &t.edge_binding {
        out.push_str(&format!("${b}:"));
    }
    let sel = match &t.selector {
        EdgeSelector::Wildcard => "*".to_string(),
        EdgeSelector::Named(n) => edge(n),
        EdgeSelector::Alternation(ns) if t.undirected => {
            ns.iter().map(|n| edge(n)).collect::<Vec<_>>().join(" | ")
        }
        EdgeSelector::Alternation(ns) => format!(
            "({})",
            ns.iter().map(|n| edge(n)).collect::<Vec<_>>().join(" | ")
        ),
    };
    if t.undirected {
        out.push_str(&format!("<{sel}>"));
    } else {
        out.push_str(&sel);
    }
    if (t.min_hops, t.max_hops) != (1, Some(1)) {
        match t.max_hops {
            Some(max) => out.push_str(&format!("{{{},{max}}}", t.min_hops)),
            None => out.push_str(&format!("{{{},}}", t.min_hops)),
        }
    }
    out.push_str(&format!(" ${}", t.dst));
    out
}

fn agg(f: AggFunc) -> &'static str {
    match f {
        AggFunc::Count => "count",
        AggFunc::Sum => "sum",
        AggFunc::Avg => "avg",
        AggFunc::Min => "min",
        AggFunc::Max => "max",
    }
}

fn comp(op: CompOp) -> &'static str {
    match op {
        CompOp::Eq => "=",
        CompOp::Ne => "!=",
        CompOp::Gt => ">",
        CompOp::Lt => "<",
        CompOp::Ge => ">=",
        CompOp::Le => "<=",
        CompOp::Contains | CompOp::StringContains => "contains",
        CompOp::StartsWith => "starts_with",
    }
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Prec {
    Or,
    And,
    Not,
    Comparison,
    Sum,
    Product,
    Unary,
    Atom,
}

fn prec(e: &Expr) -> Prec {
    match e {
        Expr::Or(..) => Prec::Or,
        Expr::And(..) => Prec::And,
        Expr::Not(_) => Prec::Not,
        Expr::Compare { .. } | Expr::In { .. } | Expr::IsNull { .. } | Expr::InfixString { .. } => {
            Prec::Comparison
        }
        Expr::Arith {
            op: ArithOp::Add | ArithOp::Sub,
            ..
        } => Prec::Sum,
        Expr::Arith { .. } => Prec::Product,
        Expr::Neg(_) => Prec::Unary,
        _ => Prec::Atom,
    }
}
