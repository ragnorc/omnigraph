//! Running GQ 3 text on a dataset's seed: the ground truths, the checks
//! after a write, and the agents' answers, on #1316's engine.

use omnigraph::Session;
use omnigraph::db::ReadTarget;
use omnigraph::error::OmniError;
use omnigraph_compiler::query::file::QueryDecl;
use omnigraph_compiler::{JsonParamMode, QueryResult, json_params_to_param_map};
use omnigraph_gqt_core::{canonical_json, parse_case};
use serde_json::{Value, json};

use super::task::{Dataset, Task};

/// Rows by position: the columns a read returns, then one array per row in
/// column order, a null where the engine omits a null cell. Positional rows
/// let an answer that names a column differently still be compared on its
/// values; `columns` is reported beside them.
#[derive(Debug, Clone, PartialEq)]
pub struct Rows {
    pub columns: Vec<String>,
    pub rows: Vec<Vec<Value>>,
}

impl Rows {
    fn from_result(result: &QueryResult) -> Result<Self, String> {
        let columns: Vec<String> = result
            .schema()
            .fields()
            .iter()
            .map(|f| f.name().clone())
            .collect();
        let Value::Array(objects) = result
            .to_rust_json()
            .map_err(|e| format!("rows did not render as JSON: {e}"))?
        else {
            return Err("the engine returned a non-array row set".into());
        };
        let rows = objects
            .iter()
            .map(|object| {
                columns
                    .iter()
                    .map(|c| object.get(c).cloned().unwrap_or(Value::Null))
                    .collect()
            })
            .collect();
        Ok(Rows { columns, rows })
    }

    /// The body of an `expect` section: `columns: [...]`, then one JSON
    /// array per row.
    pub fn parse(body: &str) -> Result<Self, String> {
        let mut lines = body.lines().filter(|l| !l.trim().is_empty());
        let first = lines
            .next()
            .ok_or("an expect section needs a `columns:` line")?;
        let columns = first
            .strip_prefix("columns:")
            .ok_or("an expect section starts with `columns:`")?;
        let columns: Vec<String> =
            serde_json::from_str(columns.trim()).map_err(|e| format!("columns: {e}"))?;
        let mut rows = Vec::new();
        for line in lines {
            let row: Vec<Value> =
                serde_json::from_str(line).map_err(|e| format!("row `{line}`: {e}"))?;
            if row.len() != columns.len() {
                return Err(format!(
                    "row `{line}` has {} cells for {} columns",
                    row.len(),
                    columns.len()
                ));
            }
            rows.push(row);
        }
        Ok(Rows { columns, rows })
    }

    pub fn render(&self) -> String {
        let mut out = format!(
            "columns: {}\n",
            serde_json::to_string(&self.columns).expect("strings serialize")
        );
        for row in &self.rows {
            out.push_str(&serde_json::to_string(row).expect("values serialize"));
            out.push('\n');
        }
        out
    }

    pub fn to_json(&self) -> Value {
        json!({"columns": self.columns, "rows": self.rows})
    }

    /// Equal values, row for row, in order or as a bag.
    pub fn same_values(&self, other: &Rows, ordered: bool) -> bool {
        let canon = |rows: &Rows| {
            let mut out: Vec<String> = rows
                .rows
                .iter()
                .map(|r| canonical_json(&Value::Array(r.clone())))
                .collect();
            if !ordered {
                out.sort();
            }
            out
        };
        self.columns.len() == other.columns.len() && canon(self) == canon(other)
    }

    /// The same columns and the same values, in order or as a bag: what a
    /// recorded expect must still match.
    pub fn matches(&self, other: &Rows, ordered: bool) -> bool {
        self.columns == other.columns && self.same_values(other, ordered)
    }

    /// The rows in a stable order for recording: as returned when the task
    /// orders them, sorted by their canonical form otherwise.
    pub fn recorded(mut self, ordered: bool) -> Self {
        if !ordered {
            self.rows
                .sort_by_cached_key(|r| canonical_json(&Value::Array(r.clone())));
        }
        self
    }
}

/// A failure to run: the engine's message and, for a compile or plan
/// refusal, its code (`P006`: a core node the planner does not lower).
#[derive(Debug, Clone)]
pub struct RunError {
    pub message: String,
    pub code: Option<String>,
}

impl RunError {
    fn plain(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            code: None,
        }
    }

    pub fn is_planner_gap(&self) -> bool {
        self.code.as_deref() == Some("P006")
    }
}

impl std::fmt::Display for RunError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match &self.code {
            Some(code) => write!(f, "{code}: {}", self.message),
            None => f.write_str(&self.message),
        }
    }
}

fn error_code(error: &OmniError) -> Option<String> {
    match error {
        OmniError::Completion { source, .. } => error_code(source),
        OmniError::Compiler(e) => e.diagnostic().map(|d| d.code.as_str().to_string()),
        _ => None,
    }
}

fn run_error(name: &str, error: OmniError) -> RunError {
    RunError {
        code: error_code(&error),
        message: format!("`{name}` failed: {error}"),
    }
}

/// A seeded graph of one dataset.
pub struct Graph {
    session: Session,
    _dir: tempfile::TempDir,
}

impl Graph {
    pub async fn open(dataset: &Dataset) -> Result<Self, String> {
        let text = format!(
            "# issue: none\n# notes: an agent instrument dataset.\n\n--- runner\ntimeout_ms: 10000\nenvironments:\n  - target: omnigraph-engine\n    \
             storage: local-filesystem\n\n--- schema\n{}\n\n--- seed\n{}\n",
            dataset.schema.trim_end(),
            dataset.seed.trim_end()
        );
        let case = parse_case(&format!("agent_{}", dataset.name), &text)
            .map_err(|e| format!("dataset `{}`: {e}", dataset.name))?;
        let engine = crate::engine_from_env()?;
        let (session, _uri, dir) = crate::open_case_store(&case, engine).await?;
        session
            .ensure_indices()
            .await
            .map_err(|e| format!("dataset `{}`: ensure_indices failed: {e}", dataset.name))?;
        Ok(Graph { session, _dir: dir })
    }

    /// Run the entry declaration (the last) of the GQ 3 `source`, reading
    /// or writing, with the task's parameters it declares.
    pub async fn run(
        &self,
        source: &str,
        params: &serde_json::Map<String, Value>,
    ) -> Result<Option<Rows>, RunError> {
        let decl = entry_declaration(source).map_err(RunError::plain)?;
        let mut supplied = serde_json::Map::new();
        for param in &decl.params {
            if let Some(value) = params.get(&param.name) {
                supplied.insert(param.name.clone(), value.clone());
            }
        }
        let map = json_params_to_param_map(
            Some(&Value::Object(supplied)),
            &decl.params,
            JsonParamMode::Standard,
        )
        .map_err(|e| RunError::plain(format!("params: {e}")))?;
        if decl.is_mutation() {
            self.session
                .mutate("main", source, &decl.name, &map)
                .await
                .map(|_| None)
                .map_err(|e| run_error(&decl.name, e))
        } else {
            let result = self
                .session
                .query(ReadTarget::branch("main"), source, &decl.name, &map)
                .await
                .map_err(|e| run_error(&decl.name, e))?;
            Rows::from_result(&result)
                .map(Some)
                .map_err(RunError::plain)
        }
    }
}

/// The declaration a source's caller runs: its last.
pub fn entry_declaration(source: &str) -> Result<QueryDecl, String> {
    let file = omnigraph_compiler::query::parser::parse_query(source).map_err(|e| e.to_string())?;
    let mut decls = file.into_declarations()?;
    decls
        .pop()
        .ok_or_else(|| "the source declares no query".to_string())
}

/// Run a GQ 3 source on `graph` with the task's parameters; for a write,
/// then run the task's check, whose rows answer. The graph must be fresh for
/// a write.
pub async fn run_answer(graph: &Graph, task: &Task, source: &str) -> Result<Rows, RunError> {
    let rows = graph.run(source, &task.params).await?;
    if let Some(check) = &task.check {
        if rows.is_some() {
            return Err(RunError::plain("a read where the task writes"));
        }
        return graph
            .run(check, &task.params)
            .await?
            .ok_or_else(|| RunError::plain("the check is not a read"));
    }
    rows.ok_or_else(|| RunError::plain("a write where the task reads"))
}
