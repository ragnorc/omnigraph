//! Accept or refuse an answer through the compiler's front door: the parser,
//! then the type checker of every declaration into the core IR, as the
//! engine compiles a source before it runs it.
//!
//! An arm is a language the agent writes; arm `gq3` parses with the
//! product's grammar.

use omnigraph_compiler::QueryDiagnostic;
use omnigraph_compiler::catalog::Catalog;
use omnigraph_compiler::ir::core::CoreDecl;
use omnigraph_compiler::query::check::check_file;
use omnigraph_compiler::query::file::FileBody;
use omnigraph_compiler::query::parser::parse_query_diagnostic;
use omnigraph_compiler::query::syntax::{Body, DeclBody, SourceFile};
use serde_json::{Value, json};

use super::normalize;

/// The language an answer is written in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Arm {
    /// GQ 3 as #1316 ships it.
    Gq3,
}

impl Arm {
    pub fn parse(text: &str) -> Result<Self, String> {
        match text {
            "gq3" => Ok(Arm::Gq3),
            other => Err(format!("arm `{other}` is not `gq3`")),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Arm::Gq3 => "gq3",
        }
    }

    pub const ALL: [Arm; 1] = [Arm::Gq3];
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Read,
    Write,
}

/// One accepted declaration with its core IR, normalised so that two
/// declarations differing only in the names of variables, aliases and the
/// declaration itself compare equal.
#[derive(Debug, Clone)]
pub struct Declaration {
    pub name: String,
    pub kind: Kind,
    pub params: Vec<String>,
    pub ir: String,
    /// The core IR before renaming, for the lowering test.
    pub raw_ir: String,
}

#[derive(Debug, Clone)]
pub enum Verdict {
    /// The declarations, the parse tree, and the GQ 3 text the engine runs:
    /// the answer itself for arm `gq3`, the printed tree for arm `where`.
    Accepted {
        decls: Vec<Declaration>,
        tree: SourceFile,
        gq3: String,
    },
    /// The diagnostic as the compiler reports it, in its JSON form.
    Refused(Value),
}

impl Verdict {
    pub fn to_json(&self) -> Value {
        match self {
            Verdict::Accepted { decls, gq3, .. } => json!({
                "accepted": true,
                "declarations": decls.iter().map(|d| json!({
                    "name": d.name,
                    "kind": match d.kind { Kind::Read => "read", Kind::Write => "write" },
                    "params": d.params,
                    "ir": d.ir,
                })).collect::<Vec<_>>(),
                "gq3": gq3,
            }),
            Verdict::Refused(diagnostic) => json!({
                "accepted": false,
                "diagnostic": diagnostic,
                "rendered": render(diagnostic),
            }),
        }
    }

    pub fn code(&self) -> Option<&str> {
        match self {
            Verdict::Refused(d) => d.get("code").and_then(Value::as_str),
            _ => None,
        }
    }

    pub fn is_accepted(&self) -> bool {
        matches!(self, Verdict::Accepted { .. })
    }
}

/// The diagnostic exactly as an agent would read it from `omnigraph lint
/// --json` or an HTTP refusal: the serialized four-field diagnostic.
pub fn render(diagnostic: &Value) -> String {
    serde_json::to_string_pretty(diagnostic).unwrap_or_else(|_| diagnostic.to_string())
}

pub(crate) fn diagnostic_json(d: &QueryDiagnostic) -> Value {
    serde_json::to_value(d).unwrap_or_else(|e| json!({"message": e.to_string()}))
}

/// The parse tree of `text` under `arm`, or the refusal.
pub fn parse(arm: Arm, text: &str) -> Result<SourceFile, QueryDiagnostic> {
    match arm {
        Arm::Gq3 => {
            let file = parse_query_diagnostic(text)?;
            match &file.body {
                FileBody::Queries(decls) if !decls.is_empty() => Ok(decls[0].syntax.file().clone()),
                FileBody::Queries(_) => Err(not_declarations("the answer declares no query")),
                _ => Err(not_declarations(
                    "the answer is a statement, not query declarations",
                )),
            }
        }
    }
}

fn not_declarations(message: &str) -> QueryDiagnostic {
    QueryDiagnostic::parse(omnigraph_compiler::query::codes::Q001, message, None)
}

/// Accept or refuse `text` under `arm` against `catalog`.
pub fn accept(arm: Arm, catalog: &Catalog, text: &str) -> Verdict {
    let tree = match parse(arm, text) {
        Ok(tree) => tree,
        Err(d) => return Verdict::Refused(diagnostic_json(&d)),
    };
    let Body::Decls(syntax_decls) = &tree.body else {
        return Verdict::Refused(json!({
            "message": "the answer is a statement, not query declarations",
        }));
    };
    if syntax_decls.is_empty() {
        return Verdict::Refused(json!({"message": "the answer declares no query"}));
    }
    let names = normalize::source_names(text);
    let mut out = Vec::with_capacity(syntax_decls.len());
    for ((_, checked), decl) in check_file(catalog, &tree).into_iter().zip(syntax_decls) {
        let core = match checked {
            Ok(checked) => checked.core,
            Err(d) => return Verdict::Refused(diagnostic_json(&d)),
        };
        let kind = match &core {
            CoreDecl::Read(_) => Kind::Read,
            CoreDecl::Write(_) => Kind::Write,
        };
        debug_assert_eq!(kind == Kind::Write, matches!(decl.body, DeclBody::Write(_)));
        let raw_ir = format!("{core:#?}");
        let params: Vec<String> = decl.params.iter().map(|p| p.name.clone()).collect();
        out.push(Declaration {
            name: decl.name.clone(),
            kind,
            ir: normalize::rename(&raw_ir, &names, &params),
            raw_ir,
            params,
        });
    }
    let gq3 = text.to_string();
    Verdict::Accepted {
        decls: out,
        tree,
        gq3,
    }
}
