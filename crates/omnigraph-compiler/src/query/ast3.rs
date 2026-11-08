//! SPIKE: the edition-3 parse tree (RFC 2026-11-07-gq-3, sections 3, 6, 7).
//!
//! A separate tree from [`super::ast`], so the shared edition-2 nodes, which
//! the planner, the GQT runner and the frozen reference engine match
//! exhaustively, are untouched. [`super::bridge3`] lowers the part of this
//! tree that has an edition-2 node into [`super::ast::QueryFile`] and refuses
//! every new node with one placeholder code, which is the type checker's
//! refusal of the new nodes in this prototype.
//!
//! Every node derives `PartialEq`, so a parse, print and parse round trip
//! compares whole trees ([`super::print3`]). Parentheses leave no node.

use super::ast::{AggFunc, CompOp, FileBody, Literal, MutationWriteMode, SettingStmt, Traversal};

/// A source that opened with `gq 3;`.
#[derive(Debug, Clone, PartialEq)]
pub struct File3 {
    pub settings: Vec<SettingStmt>,
    pub body: Body3,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Body3 {
    Decls(Vec<Decl3>),
    /// `show`, `branch`, `commit` or `load`: the edition-2 statement nodes,
    /// whose grammar edition 3 keeps.
    Statement(Box<StatementBody>),
    /// `explain` over one edition-3 declaration.
    Explain(Box<Decl3>),
}

/// Edition 2's statement bodies compare by their debug form: they do not
/// derive `PartialEq` and edition 3 changes none of them.
#[derive(Debug, Clone)]
pub struct StatementBody(pub FileBody);

impl PartialEq for StatementBody {
    fn eq(&self, other: &Self) -> bool {
        format!("{:?}", self.0) == format!("{:?}", other.0)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Decl3 {
    pub name: String,
    pub params: Vec<Param3>,
    pub annotations: Vec<Annotation3>,
    pub write_mode: MutationWriteMode,
    pub body: DeclBody3,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Param3 {
    pub name: String,
    /// The type as edition 2 spells it in `ast::Param::type_name`.
    pub type_name: String,
    pub nullable: bool,
    pub default: Option<ParamDefault>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ParamDefault {
    pub negative: bool,
    pub value: Literal,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Annotation3 {
    /// Without the `@`.
    pub name: String,
    /// `None`: written without parentheses.
    pub args: Option<Vec<AnnotationArg3>>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AnnotationArg3 {
    Positional(AnnotationValue3),
    Named(String, AnnotationValue3),
}

#[derive(Debug, Clone, PartialEq)]
pub enum AnnotationValue3 {
    Literal(Literal),
    Variable(String),
    MetaField(String),
    Ident(String),
}

/// The kind of a declaration is the shape of its body (A7).
#[derive(Debug, Clone, PartialEq)]
pub enum DeclBody3 {
    Read(ReadBody3),
    Write(WriteBody3),
}

#[derive(Debug, Clone, PartialEq)]
pub struct ReadBody3 {
    pub clauses: Vec<Clause3>,
    pub distinct: bool,
    pub projections: Vec<Projection3>,
    pub order: Vec<Ordering3>,
    pub limit: Option<Limit3>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Projection3 {
    pub expr: Expr3,
    pub alias: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Ordering3 {
    pub expr: Expr3,
    /// `None`: no direction written.
    pub descending: Option<bool>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Limit3 {
    Literal(u64),
    Param(String),
}

#[derive(Debug, Clone, PartialEq)]
pub struct WriteBody3 {
    /// One or more groups: each a run of clauses, then the statements that
    /// write over its rows (RFC section 2, the write groups).
    pub groups: Vec<WriteGroup3>,
}

impl WriteBody3 {
    /// Every group's clauses, in order.
    pub fn all_clauses(&self) -> Vec<&Clause3> {
        self.groups.iter().flat_map(|g| g.clauses.iter()).collect()
    }

    /// Every group's statements, in order.
    pub fn all_statements(&self) -> Vec<&Stmt3> {
        self.groups
            .iter()
            .flat_map(|g| g.statements.iter())
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct WriteGroup3 {
    pub clauses: Vec<Clause3>,
    pub statements: Vec<Stmt3>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Stmt3 {
    Insert {
        type_name: String,
        assignments: Vec<Assignment3>,
    },
    Update {
        variable: String,
        assignments: Vec<Assignment3>,
    },
    Delete {
        variable: String,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Assignment3 {
    pub property: String,
    pub value: Expr3,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Clause3 {
    /// `let $name = value`: a named value or a named relation.
    Let {
        name: String,
        value: LetValue3,
    },
    /// `from rel`: rows drawn from a relation.
    From(RelExpr3),
    Not(Vec<Clause3>),
    Optional(Vec<Clause3>),
    Binding {
        variable: String,
        type_name: String,
        prop_matches: Vec<(String, Expr3)>,
    },
    Traversal(TraversalEq),
    Filter(Expr3),
}

/// Edition 2's traversal node compared by its debug form.
#[derive(Debug, Clone)]
pub struct TraversalEq(pub Traversal);

impl PartialEq for TraversalEq {
    fn eq(&self, other: &Self) -> bool {
        format!("{:?}", self.0) == format!("{:?}", other.0)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum LetValue3 {
    Relation(Box<ReadBody3>),
    Expr(Expr3),
}

#[derive(Debug, Clone, PartialEq)]
pub enum RelExpr3 {
    Inline(Box<ReadBody3>),
    Call(Call3),
    Variable(String),
}

/// A correlated block: `count { … }`, `sum($x) { … }`, `exists { … }`.
#[derive(Debug, Clone, PartialEq)]
pub struct Block3 {
    pub func: BlockFunc3,
    pub arg: Option<Box<Expr3>>,
    pub clauses: Vec<Clause3>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockFunc3 {
    Agg(AggFunc),
    Exists,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArithOp {
    Add,
    Sub,
    Mul,
    Div,
    Rem,
}

impl ArithOp {
    pub fn symbol(self) -> &'static str {
        match self {
            Self::Add => "+",
            Self::Sub => "-",
            Self::Mul => "*",
            Self::Div => "/",
            Self::Rem => "%",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Expr3 {
    Literal(Literal),
    Variable(String),
    /// `$x.name` or `$x.@id`.
    Prop {
        variable: String,
        property: String,
    },
    /// A bare meta-field, `@id`.
    Meta(String),
    /// A bare identifier: a return alias in a read, a property in a
    /// mutation assignment, a field name as a ranking function's argument.
    Ident(String),
    Call(Call3),
    /// `count(distinct x)`; any aggregate name parses, the type checker
    /// keeps today's T57 for the others.
    AggDistinct {
        func: AggFunc,
        arg: Box<Expr3>,
    },
    Block(Box<Block3>),
    Neg(Box<Expr3>),
    Arith {
        op: ArithOp,
        left: Box<Expr3>,
        right: Box<Expr3>,
    },
    Compare {
        op: CompOp,
        left: Box<Expr3>,
        right: Box<Expr3>,
    },
    In {
        needle: Box<Expr3>,
        list: Box<Expr3>,
    },
    IsNull {
        expr: Box<Expr3>,
        negated: bool,
    },
    Not(Box<Expr3>),
    And(Box<Expr3>, Box<Expr3>),
    Or(Box<Expr3>, Box<Expr3>),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Call3 {
    pub name: String,
    pub args: Vec<Arg3>,
    pub options: Vec<(String, Expr3)>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Arg3 {
    Expr(Expr3),
    Inline(Box<ReadBody3>),
}
