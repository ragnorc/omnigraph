//! The GQ 3 agent instrument (RFC 2026-11-07-gq-3, stage M0): the data under
//! `crates/omnigraph-gqt/agent/` and the judge that accepts, types and runs
//! an agent's answer. `agent/README.md` states what each number means; the
//! `omnigraph-gqt-agent` binary is the runner's one door into this module.
//!
//! Both arms are judged the same way: accepted by the front door, then run
//! on the seed through #1316's engine and compared by rows with the task's
//! expected rows.

pub mod card;
mod exec;
mod front;
mod normalize;
pub mod task;
#[cfg(test)]
mod tests;
pub mod variant;

use std::collections::HashMap;

use omnigraph_compiler::build_catalog;
use omnigraph_compiler::catalog::Catalog;
use omnigraph_compiler::schema::parser::parse_schema;
use serde_json::{Value, json};

pub use exec::{Graph, Rows, RunError, entry_declaration, run_answer};
pub use front::{Arm, Declaration, Kind, Verdict, accept, parse, render};
pub use task::{Answer, Dataset, Expect, Instrument, Seed, Task};

pub fn catalog(dataset: &Dataset) -> Result<Catalog, String> {
    let schema =
        parse_schema(&dataset.schema).map_err(|e| format!("dataset `{}`: {e}", dataset.name))?;
    build_catalog(&schema).map_err(|e| format!("dataset `{}`: {e}", dataset.name))
}

/// Run a ground truth (GQ 3 text) on a fresh graph of the task's dataset.
pub async fn run_truth(
    instrument: &Instrument,
    task: &Task,
    truth: &str,
) -> Result<Rows, RunError> {
    let graph = Graph::open(&instrument.datasets[&task.dataset])
        .await
        .map_err(|message| RunError {
            message,
            code: None,
        })?;
    run_answer(&graph, task, truth).await
}

/// The rows a task's answer must return: the recorded ones, or, for a live
/// expect, the first edition-3 ground truth run now.
pub async fn expected_rows(instrument: &Instrument, task: &Task) -> Result<Rows, String> {
    match &task.expect {
        Some(Expect {
            pinned: Some(rows), ..
        }) => Ok(rows.clone()),
        Some(Expect { pinned: None, .. }) => run_truth(instrument, task, &task.edition3[0])
            .await
            .map_err(|e| format!("{}: the live truth fails: {e}", task.id)),
        None => Err(format!(
            "{}: no expected rows; run `omnigraph-gqt-agent record --write`",
            task.id
        )),
    }
}

/// Whether the entry declaration (the last) of an accepted answer calls
/// every other declaration, directly or through another: an answer of
/// several declarations is one query when its helpers serve its entry, and
/// several queries with prose glue otherwise.
fn entry_calls_every_helper(decls: &[Declaration], gq3: &str) -> bool {
    if decls.len() < 2 {
        return true;
    }
    let Ok(file) = omnigraph_compiler::query::parser::parse_query(gq3) else {
        return false;
    };
    let Ok(parsed) = file.into_declarations() else {
        return false;
    };
    let texts: HashMap<String, String> = parsed
        .iter()
        .map(|d| {
            (
                d.name.clone(),
                omnigraph_compiler::query::syntax::print::print_decl(
                    d.syntax.decl(),
                    Default::default(),
                ),
            )
        })
        .collect();
    let entry = &decls.last().expect("non-empty").name;
    let mut reached = vec![entry.clone()];
    let mut i = 0;
    while i < reached.len() {
        let text = texts.get(&reached[i]).cloned().unwrap_or_default();
        for d in decls {
            if !reached.contains(&d.name) && text.contains(&format!("{}(", d.name)) {
                reached.push(d.name.clone());
            }
        }
        i += 1;
    }
    decls.iter().all(|d| reached.contains(&d.name))
}

/// Judges answers against the instrument, keeping one catalog and one read
/// graph per dataset across answers.
pub struct Judge<'a> {
    instrument: &'a Instrument,
    catalogs: HashMap<String, Catalog>,
    graphs: HashMap<String, Graph>,
    expected: HashMap<String, Rows>,
    truths: HashMap<String, Vec<Vec<Declaration>>>,
}

impl<'a> Judge<'a> {
    pub fn new(instrument: &'a Instrument) -> Self {
        Judge {
            instrument,
            catalogs: HashMap::new(),
            graphs: HashMap::new(),
            expected: HashMap::new(),
            truths: HashMap::new(),
        }
    }

    fn catalog(&mut self, dataset: &str) -> Result<&Catalog, String> {
        if !self.catalogs.contains_key(dataset) {
            let built = catalog(&self.instrument.datasets[dataset])?;
            self.catalogs.insert(dataset.to_string(), built);
        }
        Ok(&self.catalogs[dataset])
    }

    async fn expected(&mut self, task: &Task) -> Result<Rows, String> {
        if !self.expected.contains_key(&task.id) {
            let rows = expected_rows(self.instrument, task).await?;
            self.expected.insert(task.id.clone(), rows);
        }
        Ok(self.expected[&task.id].clone())
    }

    /// Every edition-3 ground truth's declarations, as arm `gq3` accepts
    /// them.
    fn truths(&mut self, task: &Task) -> Result<Vec<Vec<Declaration>>, String> {
        if !self.truths.contains_key(&task.id) {
            let catalog = self.catalog(&task.dataset)?;
            let accepted = task
                .edition3
                .iter()
                .filter_map(|t| match accept(Arm::Gq3, catalog, t) {
                    Verdict::Accepted { decls, .. } => Some(decls),
                    Verdict::Refused(_) => None,
                })
                .collect();
            self.truths.insert(task.id.clone(), accepted);
        }
        Ok(self.truths[&task.id].clone())
    }

    /// Accept or refuse `text`; when accepted, run it on the seed and
    /// compare its rows. The result is one JSON object for the runner.
    pub async fn judge(&mut self, task_id: &str, arm: Arm, text: &str) -> Value {
        match self.judge_inner(task_id, arm, text).await {
            Ok(value) => value,
            Err(error) => json!({"error": error}),
        }
    }

    async fn judge_inner(&mut self, task_id: &str, arm: Arm, text: &str) -> Result<Value, String> {
        let instrument = self.instrument;
        let task = instrument
            .task(task_id)
            .ok_or_else(|| format!("no task `{task_id}`"))?;
        let verdict = accept(arm, self.catalog(&task.dataset)?, text);
        let mut out = verdict.to_json();
        let Verdict::Accepted { decls, gq3, .. } = &verdict else {
            return Ok(out);
        };
        out["queries"] = json!(decls.len());
        let truths = self.truths(task)?;
        out["ir_equal"] = json!(truths.iter().any(|t| same_ir(decls, t)));
        let (correct, reason) = self.correctness(task, decls, gq3, &mut out).await?;
        out["correct"] = json!(correct);
        out["correct_reason"] = json!(reason);
        Ok(out)
    }

    async fn correctness(
        &mut self,
        task: &Task,
        decls: &[Declaration],
        gq3: &str,
        out: &mut Value,
    ) -> Result<(Option<bool>, String), String> {
        if !entry_calls_every_helper(decls, gq3) {
            return Ok((
                None,
                "several queries: the glue between them is prose; judge by hand".into(),
            ));
        }
        let wants = if task.is_write() {
            Kind::Write
        } else {
            Kind::Read
        };
        if decls.last().expect("accepted answers declare a query").kind != wants {
            return Ok((
                Some(false),
                "a read where the task writes, or the reverse".into(),
            ));
        }
        let expected = match self.expected(task).await {
            Ok(rows) => rows,
            Err(reason) => {
                out["no_expected_rows"] = json!(reason);
                return Ok((
                    None,
                    "no expected rows: the task's first edition-3 truth does not run on this engine".into(),
                ));
            }
        };
        let dataset = &self.instrument.datasets[&task.dataset];
        let actual = if task.is_write() {
            run_answer(&Graph::open(dataset).await?, task, gq3).await
        } else {
            if !self.graphs.contains_key(&task.dataset) {
                let graph = Graph::open(dataset).await?;
                self.graphs.insert(task.dataset.clone(), graph);
            }
            run_answer(&self.graphs[&task.dataset], task, gq3).await
        };
        Ok(match actual {
            Ok(rows) => {
                let equal = rows.same_values(&expected, task.ordered());
                out["rows"] = rows.to_json();
                let reason = if equal {
                    "rows equal the expected rows on the seed"
                } else {
                    "rows differ from the expected rows on the seed"
                };
                (Some(equal), reason.into())
            }
            Err(error) => {
                out["execution_error"] = json!(error.to_string());
                if error.is_planner_gap() {
                    out["planner_gap"] = json!(true);
                    (
                        Some(false),
                        "accepted, but the planner does not lower it (P006)".into(),
                    )
                } else {
                    (
                        Some(false),
                        "accepted, but the engine refused it at run time".into(),
                    )
                }
            }
        })
    }
}

pub fn same_ir(answer: &[Declaration], truth: &[Declaration]) -> bool {
    answer.len() == truth.len()
        && answer
            .iter()
            .zip(truth)
            .all(|(a, t)| a.kind == t.kind && a.ir == t.ir)
}
