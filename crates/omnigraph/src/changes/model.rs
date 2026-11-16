//! Public graph-vocabulary change model.
//!
//! These types are the deliberate SDK surface for per-commit entity changes
//! and the change feed. They speak graph vocabulary only: node/edge kind,
//! opaque graph type identity plus name, logical entity `id`, operation, and
//! exact logical before/after images with edge endpoints inside each image.
//! Physical vocabulary — table keys, incarnation numbers, Lance versions, row
//! addresses — never appears here.
//!
//! The `Serialize` derives define the canonical byte-accounting encoding used
//! to enforce page byte ceilings; wire DTOs live in `omnigraph-api-types` and
//! map from these types explicitly.

use serde::Serialize;

use crate::db::commit_graph::GraphCommit;

/// Default number of entity changes per bounded page.
pub const COMMIT_CHANGES_DEFAULT_CHANGES: usize = 1_000;
/// Public ceiling on entity changes per bounded page.
pub const COMMIT_CHANGES_MAX_CHANGES: usize = 8_192;
/// Default per-page serialized-byte PACKING target — how many small changes
/// share one page, not a wall a single change must fit under. A change whose two
/// images exceed the remaining budget is delivered on its own page (forward
/// progress; see `enumerate`), so this never makes a legal committed change
/// undeliverable.
pub const COMMIT_CHANGES_DEFAULT_BYTES: u64 = 4 * 1024 * 1024;
/// Public ceiling on the per-page byte packing target a caller may request.
/// This bounds how much a caller may pack per page; it is NOT a ceiling on a
/// single change's size — an `Update` serializes two entity images and managed
/// Blobs inline as base64 (~4/3), so one legal change can exceed this and is
/// still delivered solo. (Historically this equalled the write-path keyed
/// transaction budget, which wrongly implied a single change had to fit under it.)
pub const COMMIT_CHANGES_MAX_BYTES: u64 = 32 * 1024 * 1024;
/// Default number of graph commits one feed poll examines.
pub const CHANGE_FEED_DEFAULT_COMMITS_PER_POLL: usize = 128;
/// Public ceiling on graph commits examined by one feed poll.
pub const CHANGE_FEED_MAX_COMMITS_PER_POLL: usize = 512;

/// Logical entity namespace of one change.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangeEntityKind {
    Node,
    Edge,
}

impl ChangeEntityKind {
    /// Frozen ordering rank: nodes before edges within one commit block.
    pub(crate) fn rank(self) -> u8 {
        match self {
            Self::Node => 0,
            Self::Edge => 1,
        }
    }
}

impl From<super::EntityKind> for ChangeEntityKind {
    fn from(kind: super::EntityKind) -> Self {
        match kind {
            super::EntityKind::Node => Self::Node,
            super::EntityKind::Edge => Self::Edge,
        }
    }
}

/// Logical operation of one change.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ChangeOpKind {
    Insert,
    Update,
    Delete,
}

impl ChangeOpKind {
    /// Frozen ordering rank, part of the page-token v1 contract.
    pub(crate) fn rank(self) -> u8 {
        match self {
            Self::Insert => 0,
            Self::Update => 1,
            Self::Delete => 2,
        }
    }
}

/// Opaque graph-scoped type identity plus the graph-schema name. The `id`
/// survives a supported rename and changes after drop/re-add; it is never a
/// dataset, path, or incarnation identifier.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphTypeRef {
    pub id: String,
    pub name: String,
}

/// Edge endpoints as graph references, using the public mutation/load
/// vocabulary. Endpoints belong to each image, so an endpoint-moving update
/// has distinct before and after endpoints.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EntityEndpoints {
    pub from: String,
    pub to: String,
}

/// One exact logical entity image, decoded with the commit-era physical
/// schema. `properties` carries user-schema keys verbatim; the logical `id`
/// and edge endpoint fields are hoisted out of it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct EntityImage {
    pub properties: serde_json::Map<String, serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub endpoints: Option<EntityEndpoints>,
}

/// One entity change. Cause is stated once on the enclosing block.
///
/// An insert has only `after`; an update has exact `before` and `after`; a
/// delete has only `before`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphEntityChange {
    pub kind: ChangeEntityKind,
    pub entity_type: GraphTypeRef,
    pub id: String,
    pub op: ChangeOpKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub before: Option<EntityImage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub after: Option<EntityImage>,
    /// Research (group commit across actors): for a row a batch commit
    /// inserted, the actor of the write whose row-id range holds its `_rowid`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub actor_id: Option<String>,
    /// Research: the index of that write in the commit's write list.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub write: Option<u32>,
}

/// The commit cause of one change block, stated once.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ChangeCause {
    pub graph_commit_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent_commit_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub merged_parent_commit_id: Option<String>,
    /// Branch the commit originally landed on; `None` means main. The selected
    /// feed branch is request context and never rewrites this.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub authored_branch: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub actor_id: Option<String>,
    /// Authorship time in Unix epoch microseconds. Minted before dataset
    /// effects and stable across publisher retries — deliberately not
    /// labeled a commit or publication time.
    pub authored_at: i64,
}

impl From<&GraphCommit> for ChangeCause {
    fn from(commit: &GraphCommit) -> Self {
        let commit = &commit.decoded();
        Self {
            graph_commit_id: commit.graph_commit_id.clone(),
            parent_commit_id: commit.parent_commit_id.clone(),
            merged_parent_commit_id: commit.merged_parent_commit_id.clone(),
            authored_branch: commit.graph_branch.clone(),
            actor_id: commit.actor_id.clone(),
            authored_at: commit.created_at,
        }
    }
}

/// All entity changes one graph commit made relative to its first parent.
/// A physical-only commit produces an empty block.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphChangeBlock {
    pub cause: ChangeCause,
    pub changes: Vec<GraphEntityChange>,
}

/// One bounded page of a finite commit entity diff. `next_page_token` is
/// absent exactly when the block is complete; there is no separate
/// completeness flag and never a feed cursor.
#[derive(Debug, Clone)]
pub struct CommitChangesPage {
    pub block: GraphChangeBlock,
    pub next_page_token: Option<String>,
}

/// The two sides of a graph diff, as the caller names them. A side is a read
/// target: a live branch name, meaning its head when the diff is captured, or
/// a graph commit id (published, an unpublished proposal, or an unattached
/// snapshot).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GraphDiffSides {
    /// The net transition from `from`'s state to `to`'s state.
    Between { from: String, to: String },
    /// What `source` changed since its merge base with `target`: the merge
    /// base and the source head, captured together.
    Branch { source: String, target: String },
}

/// One graph diff request. `None` limits take the server-owned defaults; a
/// page token continues the response whose sides it was minted for and keeps
/// the endpoints that response resolved.
#[derive(Debug, Clone)]
pub struct GraphDiffRequest {
    pub sides: GraphDiffSides,
    pub scope: ChangeFeedScope,
    pub page_token: Option<String>,
    pub max_changes: Option<usize>,
    pub max_bytes: Option<u64>,
}

/// One bounded page of a graph diff: net entity changes between two resolved
/// graph commits, without a cause (a range has no single author). The
/// endpoints are the exact commits the first page resolved; every
/// continuation answers for them, whatever the named branches did since.
#[derive(Debug, Clone)]
pub struct GraphDiffPage {
    pub from_commit_id: String,
    pub to_commit_id: String,
    /// The merge base, present for [`GraphDiffSides::Branch`]; equal to
    /// `from_commit_id` there.
    pub base_commit_id: Option<String>,
    pub changes: Vec<GraphEntityChange>,
    pub next_page_token: Option<String>,
}

/// The merge base of a source and a target branch, with the heads it was
/// computed from.
#[derive(Debug, Clone)]
pub struct MergeBase {
    pub source_commit_id: String,
    pub target_commit_id: String,
    pub merge_base: GraphCommit,
}

/// What a branch control read answers (RFC 0030 §8), before a transport
/// renders it: one page of a diff, a merge base, or a merge's outcome.
#[derive(Debug, Clone)]
pub enum BranchReadAnswer {
    Diff(GraphDiffPage),
    MergeBase {
        source: String,
        target: String,
        base: MergeBase,
    },
    MergeDryRun {
        source: String,
        target: String,
        outcome: crate::db::MergeOutcome,
    },
}

/// Graph-vocabulary feed filter. Empty dimensions match everything; the
/// canonical digest of this scope binds page tokens and feed cursors.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ChangeFeedScope {
    pub kinds: Option<Vec<ChangeEntityKind>>,
    pub type_names: Option<Vec<String>>,
    pub ops: Option<Vec<ChangeOpKind>>,
}

impl ChangeFeedScope {
    pub(crate) fn wants_kind(&self, kind: ChangeEntityKind) -> bool {
        self.kinds
            .as_ref()
            .is_none_or(|kinds| kinds.contains(&kind))
    }

    pub(crate) fn wants_type_name(&self, name: &str) -> bool {
        self.type_names
            .as_ref()
            .is_none_or(|names| names.iter().any(|candidate| candidate == name))
    }

    pub(crate) fn wants_op(&self, op: ChangeOpKind) -> bool {
        self.ops.as_ref().is_none_or(|ops| ops.contains(&op))
    }
}

/// Explicit start mode for the first feed request. A missing cursor is never
/// an implicit alias for `Beginning`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChangeFeedStart {
    /// Capture the current head and position the cursor after it.
    Now,
    /// Begin after an exact commit on the captured branch's first-parent chain.
    AfterCommit(String),
    /// Begin before the chain's root, including inherited history on a named
    /// branch.
    Beginning,
}

/// Where one poll resumes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChangeFeedPosition {
    /// First request: choose an explicit start mode.
    Start(ChangeFeedStart),
    /// Resume the durable feed after the cursor's last complete commit.
    Cursor(String),
    /// Continue one interrupted bounded poll at its captured cut.
    PageToken(String),
}

/// One feed poll request. `None` limits take the server-owned defaults; the
/// byte and commit ceilings exist for SDK callers and tests — transports do
/// not expose them.
#[derive(Debug, Clone)]
pub struct ChangeFeedRequest {
    /// Branch whose first-parent history is polled; `None` means main.
    pub branch: Option<String>,
    pub position: ChangeFeedPosition,
    pub scope: ChangeFeedScope,
    pub max_changes: Option<usize>,
    pub max_bytes: Option<u64>,
    pub max_commits: Option<usize>,
}

/// How one feed page ended.
#[derive(Debug, Clone)]
pub enum ChangeFeedContinuation {
    /// The page ended inside a block. Continue with the page token; no durable
    /// cursor is advanced, so a caller can never checkpoint a partial block.
    MidBlock { page_token: String },
    /// The page ended after a complete commit. The cursor is durable;
    /// `caught_up` is true when the captured cut was reached.
    AtBlockBoundary { cursor: String, caught_up: bool },
}

/// One bounded feed poll result: zero or more complete blocks, plus a trailing
/// partial block exactly when the continuation is `MidBlock`.
#[derive(Debug, Clone)]
pub struct ChangeFeedPage {
    pub blocks: Vec<GraphChangeBlock>,
    pub continuation: ChangeFeedContinuation,
}

/// The baseline handshake: one exact entity snapshot and the cursor that
/// resumes the feed immediately after it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChangeBaseline {
    pub snapshot_commit_id: String,
    pub resume_cursor: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn operation_ranks_are_frozen() {
        assert_eq!(ChangeOpKind::Insert.rank(), 0);
        assert_eq!(ChangeOpKind::Update.rank(), 1);
        assert_eq!(ChangeOpKind::Delete.rank(), 2);
    }
}

/// Research (group commit across actors): give every row a batch commit
/// inserted the actor of the write whose stable row-id range holds its
/// `_rowid`, read at the commit's own snapshot. A one-write commit, and every
/// change that is not an insert, is left as it is: its block's cause names
/// the actor.
pub(crate) async fn attribute_batch_inserts(
    child: &crate::db::Snapshot,
    commit: &GraphCommit,
    changes: &mut [GraphEntityChange],
) -> crate::error::Result<()> {
    use arrow_array::Array as _;
    use futures::TryStreamExt as _;
    let commit = commit.decoded();
    if commit.writes.is_empty() {
        return Ok(());
    }
    let mut by_table: std::collections::BTreeMap<String, Vec<usize>> = Default::default();
    for (index, change) in changes.iter().enumerate() {
        if change.op != ChangeOpKind::Insert {
            continue;
        }
        let table_key = match change.kind {
            ChangeEntityKind::Node => format!("node:{}", change.entity_type.name),
            ChangeEntityKind::Edge => format!("edge:{}", change.entity_type.name),
        };
        by_table.entry(table_key).or_default().push(index);
    }
    for (table_key, indices) in by_table {
        let ranges: Vec<(u64, u64, Option<String>, u32)> = commit
            .writes
            .iter()
            .enumerate()
            .flat_map(|(position, write)| {
                let table_key = table_key.clone();
                write
                    .rows
                    .iter()
                    .filter(move |rows| rows.table_key == table_key)
                    .map(move |rows| {
                        (
                            rows.start,
                            rows.end,
                            write.actor_id.clone(),
                            position as u32,
                        )
                    })
            })
            .collect();
        if ranges.is_empty() {
            continue;
        }
        // Read only the fragments whose stable row ids meet the commit's
        // ranges for this table (the batch's own fragments at the commit's
        // snapshot), from the manifest's inline row-id sequences: the cost is
        // the batch's rows, not the table's.
        let dataset = child.open_lance_dataset(&table_key).await?;
        let lo = ranges.iter().map(|range| range.0).min().unwrap_or(0);
        let hi = ranges.iter().map(|range| range.1).max().unwrap_or(0);
        let mut fragments = Vec::new();
        for fragment in dataset.fragments().iter() {
            let meets = match fragment.row_id_meta.as_ref() {
                Some(lance_table::format::RowIdMeta::Inline(bytes)) => {
                    match lance_table::rowids::read_row_ids(&bytes[..]) {
                        Ok(sequence) => sequence.iter().any(|id| id >= lo && id < hi),
                        Err(_) => true,
                    }
                }
                // No inline sequence to judge by: read the fragment.
                _ => true,
            };
            if meets {
                fragments.push(fragment.clone());
            }
        }
        if fragments.is_empty() {
            continue;
        }
        let id_column = if dataset.schema().field("__id").is_some() {
            "__id"
        } else {
            "id"
        };
        let ids: Vec<String> = indices
            .iter()
            .map(|index| changes[*index].id.clone())
            .collect();
        let mut scanner = dataset.scan();
        scanner.with_fragments(fragments);
        scanner
            .project(&[id_column])
            .map_err(crate::error::OmniError::storage)?;
        scanner.with_row_id();
        scanner.filter_expr(
            datafusion::prelude::col(id_column).in_list(
                ids.iter()
                    .map(|id| datafusion::prelude::lit(id.clone()))
                    .collect(),
                false,
            ),
        );
        let batches: Vec<arrow_array::RecordBatch> = scanner
            .try_into_stream()
            .await
            .map_err(crate::error::OmniError::storage)?
            .try_collect()
            .await
            .map_err(crate::error::OmniError::storage)?;
        let mut row_of: std::collections::HashMap<String, u64> = Default::default();
        for batch in &batches {
            let id_values = batch
                .column_by_name(id_column)
                .and_then(|column| column.as_any().downcast_ref::<arrow_array::StringArray>());
            let row_ids = batch
                .column_by_name("_rowid")
                .and_then(|column| column.as_any().downcast_ref::<arrow_array::UInt64Array>());
            let (Some(id_values), Some(row_ids)) = (id_values, row_ids) else {
                continue;
            };
            for row in 0..batch.num_rows() {
                row_of.insert(id_values.value(row).to_string(), row_ids.value(row));
            }
        }
        for index in indices {
            let Some(row_id) = row_of.get(&changes[index].id) else {
                continue;
            };
            if let Some((_, _, actor, position)) = ranges
                .iter()
                .find(|(start, end, _, _)| *row_id >= *start && *row_id < *end)
            {
                changes[index].actor_id = actor.clone();
                changes[index].write = Some(*position);
            }
        }
    }
    Ok(())
}
