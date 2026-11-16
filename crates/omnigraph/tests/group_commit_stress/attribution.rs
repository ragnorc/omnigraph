//! Attribution oracle for batch commits that hold writes of several actors
//! (research, `research/group-commit-stress`, RFC §Attribution, rule 19).
//!
//! For every commit whose record carries a write list, it checks:
//!
//! - the commit's `actor_id` is the common actor when every write has one,
//!   and empty otherwise; each entry names a table at most once, with a
//!   non-empty half-open range; ranges of one table are disjoint across every
//!   entry of every commit;
//! - commit get (`commit_changes_page`, every page): every change is an
//!   insert, carries a write index and that write's actor, and the rows of
//!   each (write, table) number exactly the width of its range;
//! - row-id coverage at the commit's own snapshot: every row whose `_rowid`
//!   lies in the commit's ranges is a row the commit inserted, in the range of
//!   the write the change rows name, and the ranges hold exactly that many
//!   rows;
//! - each acknowledged write whose receipt names the commit matches exactly
//!   one entry, with its own actor and exactly its rows per table; an entry
//!   that matches no acknowledged write matches an in-doubt write; no entry
//!   matches only a refused write;
//! - the change feed, polled from the beginning, reports every batch row with
//!   the same write index and actor as commit get;
//! - at the current head, every surviving row whose `_rowid` lies in a batch
//!   range is a row that write inserted (attribution survives compaction,
//!   updates and deletes).
//!
//! An acknowledged write whose commit holds one write must find its actor in
//! the commit's `actor_id`.

use std::collections::{BTreeMap, HashMap, HashSet};

use arrow_array::{Array, RecordBatch, StringArray, UInt64Array};
use futures::{StreamExt as _, TryStreamExt as _};
use omnigraph::changes::{
    ChangeEntityKind, ChangeFeedContinuation, ChangeFeedPosition, ChangeFeedRequest,
    ChangeFeedScope, ChangeFeedStart, ChangeOpKind, GraphEntityChange,
};
use omnigraph::db::{Omnigraph, ReadTarget, SnapshotId};

/// One write of a history, reduced to what attribution needs.
#[derive(Clone, Debug)]
pub struct AttrOp {
    pub id: u64,
    pub actor: Option<String>,
    /// `ok`, `refused` or `in_doubt`.
    pub outcome: String,
    pub commit: Option<String>,
    /// The rows the write inserts, per table: a node's key, an edge's
    /// `from->to`. Sorted.
    pub rows: BTreeMap<String, Vec<String>>,
}

/// The rows an op of the stress mix inserts, per table.
pub fn inserted_rows(
    kind: &str,
    items: &[String],
    person: Option<&str>,
    edge: Option<(&str, &str)>,
    tag_slug: Option<&str>,
) -> BTreeMap<String, Vec<String>> {
    let mut rows: BTreeMap<String, Vec<String>> = BTreeMap::new();
    match kind {
        "fresh" | "hot" | "pair" => {
            rows.insert("node:Item".into(), items.to_vec());
        }
        "multi" | "fmulti" => {
            rows.insert("node:Item".into(), items.to_vec());
            if let Some(person) = person {
                rows.insert("node:Person".into(), vec![person.to_string()]);
            }
        }
        "person" => {
            if let Some(person) = person {
                rows.insert("node:Person".into(), vec![person.to_string()]);
            }
        }
        "knows" | "sknows" => {
            if let Some((from, to)) = edge {
                rows.insert("edge:Knows".into(), vec![format!("{from}->{to}")]);
            }
        }
        "tag" => {
            if let Some(slug) = tag_slug {
                rows.insert("node:Tag".into(), vec![slug.to_string()]);
            }
        }
        _ => {}
    }
    for values in rows.values_mut() {
        values.sort();
    }
    rows
}

/// Read op records from JSONL histories: the harness's `OUT` file or the
/// HTTP driver's history (`type: op` lines in either).
pub fn load_ops(paths: &[String]) -> Vec<AttrOp> {
    let mut out = Vec::new();
    let mut next = 0u64;
    for path in paths {
        let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("read {path}: {e}"));
        for line in text.lines() {
            let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
                continue;
            };
            if value["type"] != "op" {
                continue;
            }
            let Some(outcome) = value["outcome"].as_str() else {
                continue;
            };
            let items: Vec<String> = value["items"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default();
            let edge = value["edge"].as_array().and_then(|a| {
                Some((
                    a.first()?.as_str()?.to_string(),
                    a.get(1)?.as_str()?.to_string(),
                ))
            });
            let tag_slug = value["tag"]
                .as_array()
                .and_then(|a| a.first()?.as_str().map(str::to_string));
            let rows = inserted_rows(
                value["kind"].as_str().unwrap_or(""),
                &items,
                value["person"].as_str(),
                edge.as_ref().map(|(a, b)| (a.as_str(), b.as_str())),
                tag_slug.as_deref(),
            );
            next += 1;
            out.push(AttrOp {
                // Histories of several processes repeat ids; keep them unique.
                id: next,
                actor: value["actor"].as_str().map(str::to_string),
                outcome: outcome.to_string(),
                commit: value["commit"].as_str().map(str::to_string),
                rows,
            });
        }
    }
    out
}

fn sig_column(table_key: &str) -> &'static str {
    match table_key {
        "node:Person" => "name",
        _ => "slug",
    }
}

fn change_sig(change: &GraphEntityChange) -> String {
    match change.kind {
        ChangeEntityKind::Edge => change
            .after
            .as_ref()
            .and_then(|image| image.endpoints.as_ref())
            .map(|ends| format!("{}->{}", ends.from, ends.to))
            .unwrap_or_else(|| format!("?{}", change.id)),
        ChangeEntityKind::Node => change
            .after
            .as_ref()
            .and_then(|image| {
                image
                    .properties
                    .get("slug")
                    .or_else(|| image.properties.get("name"))
                    .and_then(|v| v.as_str().map(str::to_string))
            })
            .unwrap_or_else(|| change.id.clone()),
    }
}

fn change_table(change: &GraphEntityChange) -> String {
    match change.kind {
        ChangeEntityKind::Node => format!("node:{}", change.entity_type.name),
        ChangeEntityKind::Edge => format!("edge:{}", change.entity_type.name),
    }
}

/// (table, sig, write index, actor) of one reported row.
type Reported = (String, String, Option<u32>, Option<String>);

async fn table_rows_with_ids(
    db: &Omnigraph,
    target: ReadTarget,
    table_key: &str,
    range: Option<(u64, u64)>,
) -> omnigraph::error::Result<Vec<(String, u64)>> {
    // The raw Lance dataset at the version the target pins (test support's
    // raw opener), so a range read can pick fragments by their inline row-id
    // sequences instead of scanning the table.
    let snapshot = db.snapshot_of(target).await?;
    let entry = snapshot
        .dataset(table_key)
        .ok_or_else(|| {
            omnigraph::error::OmniError::manifest_internal(format!("no table {table_key}"))
        })?
        .clone();
    let pinned = snapshot
        .open_dataset(table_key)
        .await?
        .published_dataset_version();
    let uri = format!("{}/{}", db.uri().trim_end_matches('/'), entry.dataset_path);
    let dataset =
        super::helpers::open_dataset_head_exact(&uri, entry.native_dataset_branch.as_deref())
            .await
            .checkout_version(pinned)
            .await
            .map_err(omnigraph::error::OmniError::storage)?;
    let edge = table_key.starts_with("edge:");
    let columns: Vec<&str> = if edge {
        if dataset.schema().field("__src").is_some() {
            vec!["__src", "__dst"]
        } else {
            vec!["src", "dst"]
        }
    } else {
        vec![sig_column(table_key)]
    };
    let mut scanner = dataset.scan();
    if let Some((start, end)) = range {
        let fragments: Vec<_> = dataset
            .fragments()
            .iter()
            .filter(|fragment| match fragment.row_id_meta.as_ref() {
                Some(lance_table::format::RowIdMeta::Inline(bytes)) => {
                    lance_table::rowids::read_row_ids(&bytes[..])
                        .map_or(true, |ids| ids.iter().any(|id| id >= start && id < end))
                }
                _ => true,
            })
            .cloned()
            .collect();
        scanner.with_fragments(fragments);
        scanner
            .filter(&format!("_rowid >= {start} AND _rowid < {end}"))
            .map_err(omnigraph::error::OmniError::storage)?;
    }
    scanner
        .project(&columns)
        .map_err(omnigraph::error::OmniError::storage)?;
    scanner.with_row_id();
    let batches: Vec<RecordBatch> = scanner
        .try_into_stream()
        .await
        .map_err(omnigraph::error::OmniError::storage)?
        .try_collect()
        .await
        .map_err(omnigraph::error::OmniError::storage)?;
    let mut out = Vec::new();
    for batch in &batches {
        let ids = batch
            .column_by_name("_rowid")
            .and_then(|c| c.as_any().downcast_ref::<UInt64Array>())
            .expect("_rowid");
        let first = batch
            .column_by_name(columns[0])
            .and_then(|c| c.as_any().downcast_ref::<StringArray>())
            .expect("sig column");
        let second = columns.get(1).map(|name| {
            batch
                .column_by_name(name)
                .and_then(|c| c.as_any().downcast_ref::<StringArray>())
                .expect("dst")
        });
        for row in 0..batch.num_rows() {
            let sig = match second {
                Some(dst) => format!("{}->{}", first.value(row), dst.value(row)),
                None => first.value(row).to_string(),
            };
            out.push((sig, ids.value(row)));
        }
    }
    Ok(out)
}

pub struct AttrReport {
    pub violations: Vec<String>,
    pub summary: serde_json::Value,
}

/// Run every attribution check on `db` against the history `ops`.
pub async fn check(db: &Omnigraph, ops: &[AttrOp]) -> AttrReport {
    let mut v: Vec<String> = Vec::new();
    let commits = db.list_commits(None).await.expect("list commits");
    let by_id: HashMap<&str, &omnigraph::db::GraphCommit> = commits
        .iter()
        .map(|c| (c.graph_commit_id.as_str(), c))
        .collect();
    let batch_commits: Vec<&omnigraph::db::GraphCommit> =
        commits.iter().filter(|c| !c.writes.is_empty()).collect();

    // Receipts.
    let mut acked_by_commit: HashMap<&str, Vec<&AttrOp>> = HashMap::new();
    for op in ops.iter().filter(|op| op.outcome == "ok") {
        let Some(commit_id) = op.commit.as_deref() else {
            continue;
        };
        match by_id.get(commit_id) {
            None => v.push(format!(
                "op {}: receipt commit {commit_id} not in the commit list",
                op.id
            )),
            Some(commit) if commit.writes.is_empty() => {
                if commit.actor_id != op.actor {
                    v.push(format!(
                        "op {}: actor {:?} but one-write commit {commit_id} records {:?}",
                        op.id, op.actor, commit.actor_id
                    ));
                }
            }
            Some(_) => acked_by_commit.entry(commit_id).or_default().push(op),
        }
    }
    let in_doubt: Vec<&AttrOp> = ops.iter().filter(|op| op.outcome == "in_doubt").collect();
    let refused: Vec<&AttrOp> = ops.iter().filter(|op| op.outcome == "refused").collect();

    // Ranges: shape, common actor, global disjointness.
    let mut all_ranges: BTreeMap<String, Vec<(u64, u64, usize, usize)>> = BTreeMap::new();
    let mut entries = 0usize;
    let mut actors_hist: BTreeMap<usize, usize> = BTreeMap::new();
    let mut mixed = 0usize;
    for (ci, commit) in batch_commits.iter().enumerate() {
        let actors: HashSet<&Option<String>> = commit.writes.iter().map(|w| &w.actor_id).collect();
        *actors_hist.entry(actors.len()).or_default() += 1;
        let want_common = if actors.len() == 1 {
            commit.writes[0].actor_id.clone()
        } else {
            None
        };
        if actors.len() > 1 {
            mixed += 1;
        }
        if commit.actor_id != want_common {
            v.push(format!(
                "commit {}: actor_id {:?}, want {:?} ({} actors)",
                commit.graph_commit_id,
                commit.actor_id,
                want_common,
                actors.len()
            ));
        }
        for (wi, write) in commit.writes.iter().enumerate() {
            entries += 1;
            let mut tables = HashSet::new();
            if write.rows.is_empty() {
                v.push(format!(
                    "commit {} write {wi}: no rows",
                    commit.graph_commit_id
                ));
            }
            for rows in &write.rows {
                if !tables.insert(rows.table_key.as_str()) {
                    v.push(format!(
                        "commit {} write {wi}: table {} named twice (not one contiguous range)",
                        commit.graph_commit_id, rows.table_key
                    ));
                }
                if rows.start >= rows.end {
                    v.push(format!(
                        "commit {} write {wi}: empty range {}..{}",
                        commit.graph_commit_id, rows.start, rows.end
                    ));
                }
                all_ranges
                    .entry(rows.table_key.clone())
                    .or_default()
                    .push((rows.start, rows.end, ci, wi));
            }
        }
    }
    for (table, ranges) in all_ranges.iter_mut() {
        ranges.sort();
        for pair in ranges.windows(2) {
            if pair[1].0 < pair[0].1 {
                v.push(format!(
                    "{table}: ranges {}..{} ({} w{}) and {}..{} ({} w{}) overlap",
                    pair[0].0,
                    pair[0].1,
                    batch_commits[pair[0].2].graph_commit_id,
                    pair[0].3,
                    pair[1].0,
                    pair[1].1,
                    batch_commits[pair[1].2].graph_commit_id,
                    pair[1].3
                ));
            }
        }
    }

    // Commit get, coverage at the commit, op matching.
    let scope = ChangeFeedScope::default();
    let mut commit_get: HashMap<String, Vec<Reported>> = HashMap::new();
    // (commit index, write index, table) -> sigs
    let mut groups: HashMap<(usize, usize, String), Vec<String>> = HashMap::new();
    let mut rows_checked = 0usize;
    // One commit's checks; commits are checked 16 at a time.
    let scope = &scope;
    let acked_by_commit = &acked_by_commit;
    let in_doubt = &in_doubt;
    let refused = &refused;
    let per_commit = futures::stream::iter(batch_commits.iter().copied().enumerate())
        .map(|(ci, commit)| async move {
        let mut lv: Vec<String> = Vec::new();
        let mut lgroups: HashMap<(usize, usize, String), Vec<String>> = HashMap::new();
        let cid = commit.graph_commit_id.as_str();
        let mut changes = Vec::new();
        let mut token: Option<String> = None;
        loop {
            match db
                .commit_changes_page(cid, &scope, token.as_deref(), None, None)
                .await
            {
                Ok(page) => {
                    changes.extend(page.block.changes);
                    token = page.next_page_token;
                    if token.is_none() {
                        break;
                    }
                }
                Err(error) => {
                    lv.push(format!("commit {cid}: commit get failed: {error}"));
                    break;
                }
            }
        }
        let mut reported = Vec::new();
        for change in &changes {
            let table = change_table(change);
            let sig = change_sig(change);
            reported.push((
                table.clone(),
                sig.clone(),
                change.write,
                change.actor_id.clone(),
            ));
            if change.op != ChangeOpKind::Insert {
                lv.push(format!(
                    "commit {cid}: a {:?} of {table} {sig} in a batch commit",
                    change.op
                ));
                continue;
            }
            let Some(wi) = change.write.map(|w| w as usize) else {
                lv.push(format!(
                    "commit {cid}: {table} {sig} has no write (unattributed)"
                ));
                continue;
            };
            let Some(write) = commit.writes.get(wi) else {
                lv.push(format!(
                    "commit {cid}: {table} {sig} names write {wi} of {}",
                    commit.writes.len()
                ));
                continue;
            };
            if change.actor_id != write.actor_id {
                lv.push(format!(
                    "commit {cid}: {table} {sig} actor {:?}, write {wi} is {:?}",
                    change.actor_id, write.actor_id
                ));
            }
            lgroups.entry((ci, wi, table)).or_default().push(sig);
        }
        let checked = changes.len();
        // Widths.
        for (wi, write) in commit.writes.iter().enumerate() {
            for rows in &write.rows {
                let got = lgroups
                    .get(&(ci, wi, rows.table_key.clone()))
                    .map_or(0, Vec::len) as u64;
                if got != rows.end - rows.start {
                    lv.push(format!(
                        "commit {cid} write {wi} {}: range {}..{} holds {} ids but {got} rows were reported",
                        rows.table_key,
                        rows.start,
                        rows.end,
                        rows.end - rows.start
                    ));
                }
            }
        }
        for key in lgroups.keys().filter(|k| k.0 == ci) {
            let named = commit.writes[key.1]
                .rows
                .iter()
                .any(|r| r.table_key == key.2);
            if !named {
                lv.push(format!(
                    "commit {cid} write {}: rows in {} but no range for it",
                    key.1, key.2
                ));
            }
        }
        // Coverage at the commit's snapshot.
        let mut tables: BTreeMap<&str, Vec<(u64, u64, usize)>> = BTreeMap::new();
        for (wi, write) in commit.writes.iter().enumerate() {
            for rows in &write.rows {
                tables
                    .entry(rows.table_key.as_str())
                    .or_default()
                    .push((rows.start, rows.end, wi));
            }
        }
        for (table, ranges) in &tables {
            let lo = ranges.iter().map(|r| r.0).min().unwrap();
            let hi = ranges.iter().map(|r| r.1).max().unwrap();
            let width: u64 = ranges.iter().map(|r| r.1 - r.0).sum();
            match table_rows_with_ids(
                db,
                ReadTarget::snapshot(SnapshotId::new(cid)),
                table,
                Some((lo, hi)),
            )
            .await
            {
                Err(error) => lv.push(format!(
                    "commit {cid} {table}: coverage read failed: {error}"
                )),
                Ok(rows) => {
                    if rows.len() as u64 != width {
                        lv.push(format!(
                            "commit {cid} {table}: {} rows hold ids in {lo}..{hi}, ranges are {width} wide",
                            rows.len()
                        ));
                    }
                    for (sig, row_id) in rows {
                        let Some(&(_, _, wi)) =
                            ranges.iter().find(|r| row_id >= r.0 && row_id < r.1)
                        else {
                            lv.push(format!(
                                "commit {cid} {table}: row {sig} id {row_id} lies between ranges"
                            ));
                            continue;
                        };
                        let ok = lgroups
                            .get(&(ci, wi, table.to_string()))
                            .is_some_and(|sigs| sigs.contains(&sig));
                        if !ok {
                            lv.push(format!(
                                "commit {cid} {table}: row {sig} id {row_id} is in write {wi}'s range but commit get does not report it for that write"
                            ));
                        }
                    }
                }
            }
        }
        // Match entries to writes.
        let entry_rows: Vec<BTreeMap<String, Vec<String>>> = (0..commit.writes.len())
            .map(|wi| {
                let mut rows = BTreeMap::new();
                for r in &commit.writes[wi].rows {
                    let mut sigs = lgroups
                        .get(&(ci, wi, r.table_key.clone()))
                        .cloned()
                        .unwrap_or_default();
                    sigs.sort();
                    rows.insert(r.table_key.clone(), sigs);
                }
                rows
            })
            .collect();
        let mut taken = vec![false; commit.writes.len()];
        for op in acked_by_commit.get(cid).map(Vec::as_slice).unwrap_or(&[]) {
            let found = (0..commit.writes.len()).find(|wi| {
                !taken[*wi] && commit.writes[*wi].actor_id == op.actor && entry_rows[*wi] == op.rows
            });
            match found {
                Some(wi) => taken[wi] = true,
                None => {
                    let same_rows: Vec<_> = (0..commit.writes.len())
                        .filter(|wi| entry_rows[*wi] == op.rows)
                        .map(|wi| (wi, commit.writes[wi].actor_id.clone()))
                        .collect();
                    lv.push(format!(
                        "op {} (actor {:?}, rows {:?}): no write of its commit {cid} carries its actor and exactly its rows (entries with its rows: {same_rows:?})",
                        op.id, op.actor, op.rows
                    ));
                }
            }
        }
        for wi in (0..commit.writes.len()).filter(|wi| !taken[*wi]) {
            let actor = &commit.writes[wi].actor_id;
            let doubtful = in_doubt
                .iter()
                .any(|op| &op.actor == actor && op.rows == entry_rows[wi]);
            if !doubtful {
                let refused_match = refused
                    .iter()
                    .any(|op| &op.actor == actor && op.rows == entry_rows[wi]);
                lv.push(format!(
                    "commit {cid} write {wi} (actor {actor:?}, rows {:?}) matches no acknowledged or in-doubt write{}",
                    entry_rows[wi],
                    if refused_match { " (it matches a refused write)" } else { "" }
                ));
            }
        }
        reported.sort();
        (cid.to_string(), lv, lgroups, reported, checked)
        })
        .buffered(16)
        .collect::<Vec<_>>()
        .await;
    for (cid, lv, lgroups, reported, checked) in per_commit {
        v.extend(lv);
        groups.extend(lgroups);
        rows_checked += checked;
        commit_get.insert(cid, reported);
    }

    // The change feed.
    let mut feed: HashMap<String, Vec<Reported>> = HashMap::new();
    let mut position = ChangeFeedPosition::Start(ChangeFeedStart::Beginning);
    let mut polls = 0usize;
    loop {
        polls += 1;
        let request = ChangeFeedRequest {
            branch: None,
            position: position.clone(),
            scope: ChangeFeedScope::default(),
            max_changes: None,
            max_bytes: None,
            max_commits: None,
        };
        match db.poll_change_feed(request).await {
            Err(error) => {
                v.push(format!("change feed poll failed: {error}"));
                break;
            }
            Ok(page) => {
                for block in page.blocks {
                    let entry = feed.entry(block.cause.graph_commit_id.clone()).or_default();
                    for change in &block.changes {
                        entry.push((
                            change_table(change),
                            change_sig(change),
                            change.write,
                            change.actor_id.clone(),
                        ));
                    }
                }
                match page.continuation {
                    ChangeFeedContinuation::MidBlock { page_token } => {
                        position = ChangeFeedPosition::PageToken(page_token)
                    }
                    ChangeFeedContinuation::AtBlockBoundary { cursor, caught_up } => {
                        if caught_up {
                            break;
                        }
                        position = ChangeFeedPosition::Cursor(cursor);
                    }
                }
            }
        }
        if polls > 1_000_000 {
            v.push("change feed never caught up".into());
            break;
        }
    }
    for (cid, want) in &commit_get {
        let mut got = feed.get(cid).cloned().unwrap_or_default();
        got.sort();
        if &got != want {
            let first = want.iter().zip(got.iter()).find(|(a, b)| a != b);
            v.push(format!(
                "commit {cid}: the change feed reports {} rows, commit get {} (first difference {first:?})",
                got.len(),
                want.len()
            ));
        }
    }
    for (cid, rows) in &feed {
        if commit_get.contains_key(cid) {
            continue;
        }
        if let Some(row) = rows.iter().find(|r| r.2.is_some()) {
            v.push(format!(
                "commit {cid} has no write list but the feed names a write: {row:?}"
            ));
        }
    }

    // Head: surviving rows keep their attribution.
    let mut head_checked = 0usize;
    for (table, ranges) in &all_ranges {
        match table_rows_with_ids(db, ReadTarget::branch("main"), table, None).await {
            Err(error) => v.push(format!("head {table}: read failed: {error}")),
            Ok(rows) => {
                for (sig, row_id) in rows {
                    let at = ranges.partition_point(|r| r.0 <= row_id);
                    if at == 0 {
                        continue;
                    }
                    let (start, end, ci, wi) = ranges[at - 1];
                    if row_id >= end || row_id < start {
                        continue;
                    }
                    head_checked += 1;
                    let ok = groups
                        .get(&(ci, wi, table.clone()))
                        .is_some_and(|sigs| sigs.contains(&sig));
                    if !ok {
                        v.push(format!(
                            "head {table}: row {sig} id {row_id} lies in {} write {wi}'s range, which did not insert it",
                            batch_commits[ci].graph_commit_id
                        ));
                    }
                }
            }
        }
    }

    let summary = serde_json::json!({
        "commits": commits.len(),
        "batch_commits": batch_commits.len(),
        "mixed_actor_commits": mixed,
        "entries": entries,
        "actors_per_batch_commit": actors_hist,
        "batch_rows_checked": rows_checked,
        "head_rows_checked": head_checked,
        "feed_polls": polls,
        "violations": v.len(),
    });
    AttrReport {
        violations: v,
        summary,
    }
}
