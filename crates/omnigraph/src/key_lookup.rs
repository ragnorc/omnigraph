//! Research (RFC "Group commit with known-key admission", Part 2 "Key
//! lookups" and "Known keys"): the one committed read every footprint check
//! makes, a lookup of exact keys in one key space of one pinned table.
//!
//! A key space is the set of values one check compares in one table: the ids,
//! the tuples of one `@unique` group, an edge table's `src` or `dst`. A lookup
//! returns, for each wanted key the pinned table holds, the ids of the rows
//! holding it (for the id space, the id itself once per row), and reads in two
//! parts:
//!
//! 1. the fragments the one BTREE on the space's first column covers, through
//!    that index only: a filter `first IN (..)` with Lance's `fast_search`,
//!    which restricts a filtered scan carrying an index query to the index's
//!    applicable fragments (`Scanner::fast_search` sets
//!    `FilteredReadOptions::with_only_indexed_fragments`, `scanner.rs`;
//!    `FilteredReadExec::apply_index_to_fragment` then skips every fragment
//!    outside `EvaluatedIndex::applicable_fragments`, the union of the named
//!    index's segment bitmaps, `scalar_logical.rs` `scalar_index_fragment_bitmap`);
//! 2. the uncovered tail: a fragment the known keys answer comes from memory;
//!    every other one is read in one scan restricted to it
//!    (`Scanner::with_fragments`) that projects only the key and id columns,
//!    whose values are matched against the wanted set in memory.
//!
//! No key list is planned per uncovered fragment and no row cap applies. The
//! returned rows of part 1 are matched against the wanted set in memory too,
//! so an index entry that points at a row whose value has since changed (the
//! stale-segment case of `docs/dev/lance.md` "Index segments") is never
//! reported at its old value.
//!
//! The known keys are a process-wide map from a data file's resolved location
//! to the exact keys this process wrote into it, per key space, recorded only
//! by the absence-proven insert writer for a staged write with exactly one
//! data file. They answer only a fragment with one data file that lists every
//! field of the space, no deletion file and no overlay. A byte budget bounds
//! them, oldest first; a `Rewrite` this process commits drops the files it
//! removed. The map changes cost, never a result.

use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, LazyLock, Mutex};

use arrow_array::{ArrayRef, RecordBatch};
use datafusion::prelude::{ident, lit};
use datafusion::scalar::ScalarValue;
use futures::TryStreamExt;
use lance::Dataset;
use lance::dataset::transaction::{Operation, Transaction};
use lance_table::format::Fragment;

use crate::error::{OmniError, Result};
use crate::loader::canonical_scalar_key;
use crate::table_store::{ID_SCAN_BATCH_BYTES, ID_SCAN_BATCH_ROWS, TableStore};

/// Keys per index-part filter.
const PROBE_CHUNK_KEYS: usize = 8_192;

/// Default byte budget of the known keys (`OMNIGRAPH_KNOWN_KEYS_BUDGET_BYTES`).
const DEFAULT_BUDGET_BYTES: usize = 256 * 1024 * 1024;

/// Per-key and per-file overhead counted against the budget, beyond the key
/// and holder bytes: hash-map entries, vector headers, the inverted index.
const ENTRY_OVERHEAD_BYTES: usize = 96;
const FILE_OVERHEAD_BYTES: usize = 256;

/// The holders found for each wanted key.
pub(crate) type Holders = HashMap<Vec<String>, Vec<String>>;

/// One key space of one table: its key columns (current names) and the
/// holder column whose value a lookup reports for each row holding a key:
/// the id column, or for an edge table's id → src space, `src`.
#[derive(Debug, Clone)]
pub(crate) struct KeySpace {
    pub(crate) columns: Vec<String>,
    pub(crate) holder: String,
}

impl KeySpace {
    pub(crate) fn ids(id_column: &str) -> Self {
        Self {
            columns: vec![id_column.to_string()],
            holder: id_column.to_string(),
        }
    }

    pub(crate) fn of(columns: &[String], holder: &str) -> Self {
        Self {
            columns: columns.to_vec(),
            holder: holder.to_string(),
        }
    }

    fn is_id_space(&self) -> bool {
        self.columns.len() == 1 && self.columns[0] == self.holder
    }

    fn projection(&self) -> Vec<&str> {
        let mut projection = vec![self.holder.as_str()];
        for column in &self.columns {
            if column != &self.holder {
                projection.push(column.as_str());
            }
        }
        projection
    }

    /// The stable identity of the space in `ds`: its key columns' field ids,
    /// then the id column's. Field ids survive renames and differ after a
    /// drop and re-add, so a renamed column keeps its known keys and a new
    /// column of an old name does not inherit them.
    fn field_ids(&self, ds: &Dataset) -> Result<Vec<i32>> {
        let mut ids = Vec::with_capacity(self.columns.len() + 1);
        for column in self.columns.iter().chain(std::iter::once(&self.holder)) {
            let field = ds.schema().field(column).ok_or_else(|| {
                OmniError::manifest_internal(format!(
                    "key lookup: column '{column}' is not in the table schema"
                ))
            })?;
            ids.push(field.id);
        }
        Ok(ids)
    }
}

/// Typed and canonical forms of the space's ids: `ScalarValue::Utf8` for a
/// string column.
pub(crate) fn utf8_keys<'a>(values: impl IntoIterator<Item = &'a String>) -> Vec<(Vec<String>, ScalarValue)> {
    values
        .into_iter()
        .map(|value| (vec![value.clone()], ScalarValue::Utf8(Some(value.clone()))))
        .collect()
}

// ---------------------------------------------------------------------------
// Statistics (research instrument).

pub(crate) static LOOKUPS: AtomicU64 = AtomicU64::new(0);
/// Uncovered fragments answered from the known keys.
pub(crate) static FRAGMENTS_FROM_MAP: AtomicU64 = AtomicU64::new(0);
/// Uncovered fragments read by a tail scan.
pub(crate) static FRAGMENTS_SCANNED: AtomicU64 = AtomicU64::new(0);
/// Rows read by tail scans.
pub(crate) static TAIL_ROWS_SCANNED: AtomicU64 = AtomicU64::new(0);
/// Lookups that ran an index part.
pub(crate) static INDEX_PARTS: AtomicU64 = AtomicU64::new(0);
pub(crate) static EVICTED_FILES: AtomicU64 = AtomicU64::new(0);
/// Nanoseconds in tail analyses (memo misses) and in whole lookups.
pub(crate) static ANALYSIS_NANOS: AtomicU64 = AtomicU64::new(0);
pub(crate) static ANALYSES: AtomicU64 = AtomicU64::new(0);
pub(crate) static LOOKUP_NANOS: AtomicU64 = AtomicU64::new(0);
pub(crate) static DROPPED_FILES: AtomicU64 = AtomicU64::new(0);

/// `(files, bytes, budget)` of the known keys.
pub fn known_keys_stats() -> (usize, usize, usize) {
    let map = MAP.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    (map.files.len(), map.bytes, map.budget)
}

/// `(analyses, analysis ns, lookup ns)`, for the research harness.
pub fn lookup_timing() -> [u64; 3] {
    [
        ANALYSES.load(Ordering::Relaxed),
        ANALYSIS_NANOS.load(Ordering::Relaxed),
        LOOKUP_NANOS.load(Ordering::Relaxed),
    ]
}

/// The lookup counters, for the research harness.
pub fn lookup_stats() -> [u64; 7] {
    [
        LOOKUPS.load(Ordering::Relaxed),
        INDEX_PARTS.load(Ordering::Relaxed),
        FRAGMENTS_FROM_MAP.load(Ordering::Relaxed),
        FRAGMENTS_SCANNED.load(Ordering::Relaxed),
        TAIL_ROWS_SCANNED.load(Ordering::Relaxed),
        EVICTED_FILES.load(Ordering::Relaxed),
        DROPPED_FILES.load(Ordering::Relaxed),
    ]
}

// ---------------------------------------------------------------------------
// The known keys.

/// Research switch, on unless `OMNIGRAPH_KNOWN_KEYS=0`.
pub(crate) fn known_keys_enabled() -> bool {
    static ENABLED: LazyLock<bool> =
        LazyLock::new(|| std::env::var("OMNIGRAPH_KNOWN_KEYS").map_or(true, |value| value != "0"));
    *ENABLED
}

/// One space of one table: the dataset root and the space's field ids.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct SpaceKey {
    root: String,
    fields: Vec<i32>,
}

/// One recorded data file.
struct FileRecord {
    handle: u64,
    spaces: Vec<SpaceKey>,
    keys: Vec<(SpaceKey, Vec<Vec<String>>)>,
    bytes: usize,
}

#[derive(Default)]
struct SpaceIndex {
    /// Each key, with the recorded files holding it and their holder ids.
    by_key: HashMap<Vec<String>, Vec<(u64, Vec<String>)>>,
}

struct KnownKeys {
    budget: usize,
    bytes: usize,
    next_handle: u64,
    files: HashMap<String, FileRecord>,
    handles: HashMap<u64, String>,
    order: VecDeque<u64>,
    spaces: HashMap<SpaceKey, SpaceIndex>,
    /// Bumped when a file leaves the map other than oldest first.
    drop_generation: u64,
    /// The `@unique` groups declared per dataset root, which the recorder
    /// records beside the ids (and `src`/`dst` of an edge table).
    declared: HashMap<String, Vec<Vec<String>>>,
}

impl KnownKeys {
    fn new() -> Self {
        let budget = std::env::var("OMNIGRAPH_KNOWN_KEYS_BUDGET_BYTES")
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(DEFAULT_BUDGET_BYTES);
        Self {
            budget,
            bytes: 0,
            next_handle: 1,
            files: HashMap::new(),
            handles: HashMap::new(),
            order: VecDeque::new(),
            spaces: HashMap::new(),
            drop_generation: 0,
            declared: HashMap::new(),
        }
    }

    /// The smallest handle still in the map, oldest first; every handle at
    /// or above it is present unless dropped (`drop_generation`).
    fn min_live_handle(&self) -> u64 {
        self.order.front().copied().unwrap_or(self.next_handle)
    }

    fn remove_handle(&mut self, handle: u64) -> bool {
        let Some(location) = self.handles.remove(&handle) else {
            return false;
        };
        let Some(record) = self.files.remove(&location) else {
            return false;
        };
        self.bytes = self.bytes.saturating_sub(record.bytes);
        for (space, keys) in record.keys {
            if let Some(index) = self.spaces.get_mut(&space) {
                for key in keys {
                    if let Some(entries) = index.by_key.get_mut(&key) {
                        entries.retain(|(entry, _)| *entry != handle);
                        if entries.is_empty() {
                            index.by_key.remove(&key);
                        }
                    }
                }
                if index.by_key.is_empty() {
                    self.spaces.remove(&space);
                }
            }
        }
        true
    }

    fn evict_to_budget(&mut self) {
        while self.bytes > self.budget {
            let Some(oldest) = self.order.pop_front() else {
                break;
            };
            if self.remove_handle(oldest) {
                EVICTED_FILES.fetch_add(1, Ordering::Relaxed);
            }
        }
        // Skip dropped handles at the front so `min_live_handle` is exact.
        while let Some(front) = self.order.front() {
            if self.handles.contains_key(front) {
                break;
            }
            self.order.pop_front();
        }
    }
}

static MAP: LazyLock<Mutex<KnownKeys>> = LazyLock::new(|| Mutex::new(KnownKeys::new()));

fn map() -> std::sync::MutexGuard<'static, KnownKeys> {
    MAP.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Test hook: empty the known keys (as a restarted process has them).
pub fn clear_known_keys() {
    let mut map = map();
    let declared = std::mem::take(&mut map.declared);
    let budget = map.budget;
    let generation = map.drop_generation;
    let next = map.next_handle;
    *map = KnownKeys::new();
    map.declared = declared;
    map.budget = budget;
    map.drop_generation = generation + 1;
    map.next_handle = next;
}

/// Test hook: set the byte budget, evicting at once.
pub fn set_known_keys_budget(bytes: usize) {
    let mut map = map();
    map.budget = bytes;
    map.evict_to_budget();
}

/// Declare the `@unique` groups of the table rooted at `root`, so the
/// recorder records their tuples. Idempotent and cheap.
pub(crate) fn declare_unique_groups(root: &str, groups: &[Vec<String>]) {
    if !known_keys_enabled() {
        return;
    }
    let root = normalize_root(root);
    let mut map = map();
    match map.declared.get(&root) {
        Some(existing) if existing.as_slice() == groups => {}
        _ => {
            map.declared.insert(root, groups.to_vec());
        }
    }
}

/// One spelling of a dataset root: Lance reports `file:///x` from one
/// opener and `/x` from another for the same local root.
fn normalize_root(uri: &str) -> String {
    let trimmed = uri.strip_prefix("file://").unwrap_or(uri);
    trimmed.trim_end_matches('/').to_string()
}

fn root_of(ds: &Dataset) -> String {
    normalize_root(ds.uri())
}

/// The resolved object location of a fragment's data file: the base it
/// lives in (the dataset root, or a registered base path), and its path.
fn file_location(ds: &Dataset, file: &lance_table::format::DataFile) -> String {
    let root = match file.base_id {
        None => root_of(ds),
        Some(base) => match ds.manifest.base_paths.get(&base) {
            Some(path) => normalize_root(&path.path),
            // An unknown base names no recorded file.
            None => return format!("\u{1}unknown-base:{base}:{}", file.path),
        },
    };
    format!("{root}\u{0}{}", file.path)
}

/// Record the keys of an absence-proven insert's one staged data file, for
/// every key space of its table: the ids, the declared `@unique` groups, and
/// an edge table's `src` and `dst`. Called by
/// `TableStore::stage_absence_proven_strict_insert` alone (rule 16), after
/// the file is written and before any manifest can name it.
pub(crate) fn record_insert(
    ds: &Dataset,
    fragment: &Fragment,
    batch: &RecordBatch,
    id_column: &str,
    edge_columns: Option<(&str, &str)>,
) -> Result<()> {
    if !known_keys_enabled() {
        return Ok(());
    }
    let [file] = fragment.files.as_slice() else {
        return Ok(());
    };
    if file.base_id.is_some() || fragment.deletion_file.is_some() {
        return Ok(());
    }
    let root = root_of(ds);
    let mut spaces = vec![KeySpace::ids(id_column)];
    if let Some((src, dst)) = edge_columns
        && batch.column_by_name(src).is_some()
        && batch.column_by_name(dst).is_some()
    {
        spaces.push(KeySpace::of(&[src.to_string()], id_column));
        spaces.push(KeySpace::of(&[dst.to_string()], id_column));
        spaces.push(KeySpace::of(&[id_column.to_string()], src));
    }
    let declared = map().declared.get(&root).cloned().unwrap_or_default();
    for group in &declared {
        if group.iter().all(|column| batch.column_by_name(column).is_some()) {
            spaces.push(KeySpace::of(group, id_column));
        }
    }
    let mut keys = Vec::with_capacity(spaces.len());
    let mut bytes = FILE_OVERHEAD_BYTES + file.path.len() + root.len();
    let mut recorded_fields: Vec<Vec<i32>> = Vec::new();
    for space in &spaces {
        let Ok(fields) = space.field_ids(ds) else {
            continue;
        };
        if !fields.iter().all(|field| file.fields.contains(field))
            || recorded_fields.contains(&fields)
        {
            continue;
        }
        recorded_fields.push(fields.clone());
        let columns: Vec<ArrayRef> = space
            .columns
            .iter()
            .map(|column| batch.column_by_name(column).cloned().expect("checked above"))
            .collect();
        let Some(holders) = batch.column_by_name(&space.holder) else {
            continue;
        };
        let mut tuples: Vec<(Vec<String>, String)> = Vec::with_capacity(batch.num_rows());
        for row in 0..batch.num_rows() {
            let Some(holder) = canonical_scalar_key(holders, row)? else {
                continue;
            };
            let Some(key) = crate::loader::composite_unique_key(&columns, row)? else {
                continue;
            };
            tuples.push((key, holder));
        }
        let space_key = SpaceKey {
            root: root.clone(),
            fields,
        };
        for (key, holder) in &tuples {
            bytes += ENTRY_OVERHEAD_BYTES
                + key.iter().map(String::len).sum::<usize>()
                + if space.is_id_space() { 0 } else { holder.len() };
        }
        keys.push((space_key, space.is_id_space(), tuples));
    }
    let location = file_location(ds, file);
    let mut map = map();
    if map.files.contains_key(&location) {
        return Ok(());
    }
    let handle = map.next_handle;
    map.next_handle += 1;
    let mut record = FileRecord {
        handle,
        spaces: Vec::new(),
        keys: Vec::new(),
        bytes,
    };
    for (space_key, id_space, tuples) in keys {
        let index = map.spaces.entry(space_key.clone()).or_default();
        let mut space_keys = Vec::with_capacity(tuples.len());
        let mut grouped: HashMap<Vec<String>, Vec<String>> = HashMap::new();
        for (key, holder) in tuples {
            let holders = grouped.entry(key).or_default();
            if !id_space {
                holders.push(holder);
            }
        }
        for (key, holders) in grouped {
            index
                .by_key
                .entry(key.clone())
                .or_default()
                .push((handle, holders));
            space_keys.push(key);
        }
        record.spaces.push(space_key.clone());
        record.keys.push((space_key, space_keys));
    }
    debug_assert_eq!(record.handle, handle);
    map.bytes += record.bytes;
    map.files.insert(location.clone(), record);
    map.handles.insert(handle, location);
    map.order.push_back(handle);
    map.evict_to_budget();
    Ok(())
}

/// Drop the files a `Rewrite` this process commits on `ds` removed: no later
/// version lists them, so their entries only hold memory.
pub(crate) fn forget_rewritten(ds: &Dataset, transaction: &Transaction) {
    if !known_keys_enabled() {
        return;
    }
    let Operation::Rewrite { groups, .. } = &transaction.operation else {
        return;
    };
    let locations: Vec<String> = groups
        .iter()
        .flat_map(|group| group.old_fragments.iter())
        .flat_map(|fragment| fragment.files.iter())
        .map(|file| file_location(ds, file))
        .collect();
    if locations.is_empty() {
        return;
    }
    let mut map = map();
    if std::env::var("OG_KL_DEBUG").is_ok() {
        eprintln!(
            "[kl] forget {} locations, first {:?}, map sample {:?}",
            locations.len(),
            locations.first(),
            map.files.keys().next()
        );
    }
    let mut dropped = false;
    for location in locations {
        if let Some(handle) = map.files.get(&location).map(|record| record.handle)
            && map.remove_handle(handle)
        {
            DROPPED_FILES.fetch_add(1, Ordering::Relaxed);
            dropped = true;
        }
    }
    if dropped {
        map.drop_generation += 1;
        map.evict_to_budget();
    }
}


/// Research switch (`OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN=1`, off by default).
fn fill_from_scan() -> bool {
    static ENABLED: LazyLock<bool> = LazyLock::new(|| {
        known_keys_enabled()
            && std::env::var("OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN").is_ok_and(|value| value == "1")
    });
    *ENABLED
}

/// Every key of every row of a tail-scan batch whose fragment `filled`
/// tracks, by `_rowaddr`'s fragment id.
fn collect_fill(
    batch: &RecordBatch,
    space: &KeySpace,
    filled: &mut HashMap<u32, Vec<(Vec<String>, String)>>,
) -> Result<()> {
    use arrow_array::Array;
    let Some(addrs) = batch
        .column_by_name("_rowaddr")
        .and_then(|column| column.as_any().downcast_ref::<arrow_array::UInt64Array>())
    else {
        return Err(OmniError::manifest_internal("key lookup: tail scan has no _rowaddr"));
    };
    let holders = batch
        .column_by_name(&space.holder)
        .cloned()
        .ok_or_else(|| OmniError::manifest_internal("key lookup: no holder column"))?;
    let columns: Vec<ArrayRef> = space
        .columns
        .iter()
        .map(|column| batch.column_by_name(column).cloned().expect("projected"))
        .collect();
    for row in 0..batch.num_rows() {
        if addrs.is_null(row) {
            continue;
        }
        let fragment = (addrs.value(row) >> 32) as u32;
        let Some(keys) = filled.get_mut(&fragment) else {
            continue;
        };
        let Some(holder) = canonical_scalar_key(&holders, row)? else {
            continue;
        };
        let Some(key) = crate::loader::composite_unique_key(&columns, row)? else {
            continue;
        };
        keys.push((key, holder));
    }
    Ok(())
}

/// Record the keys a tail scan read from whole fragments (research switch).
fn record_scanned(
    space_key: SpaceKey,
    id_space: bool,
    locations: HashMap<u32, String>,
    mut filled: HashMap<u32, Vec<(Vec<String>, String)>>,
) {
    let mut map = map();
    for (fragment, location) in locations {
        let tuples = filled.remove(&fragment).unwrap_or_default();
        if map
            .files
            .get(&location)
            .is_some_and(|record| record.spaces.contains(&space_key))
        {
            continue;
        }
        let mut bytes = ENTRY_OVERHEAD_BYTES;
        let mut grouped: HashMap<Vec<String>, Vec<String>> = HashMap::new();
        for (key, holder) in tuples {
            bytes += ENTRY_OVERHEAD_BYTES
                + key.iter().map(String::len).sum::<usize>()
                + if id_space { 0 } else { holder.len() };
            let holders = grouped.entry(key).or_default();
            if !id_space {
                holders.push(holder);
            }
        }
        let handle = match map.files.get(&location) {
            Some(record) => record.handle,
            None => {
                let handle = map.next_handle;
                map.next_handle += 1;
                map.files.insert(
                    location.clone(),
                    FileRecord {
                        handle,
                        spaces: Vec::new(),
                        keys: Vec::new(),
                        bytes: FILE_OVERHEAD_BYTES + location.len(),
                    },
                );
                map.handles.insert(handle, location.clone());
                map.order.push_back(handle);
                map.bytes += FILE_OVERHEAD_BYTES + location.len();
                handle
            }
        };
        let mut keys = Vec::with_capacity(grouped.len());
        {
            let index = map.spaces.entry(space_key.clone()).or_default();
            for (key, holders) in grouped {
                index.by_key.entry(key.clone()).or_default().push((handle, holders));
                keys.push(key);
            }
        }
        let record = map.files.get_mut(&location).expect("inserted above");
        record.spaces.push(space_key.clone());
        record.keys.push((space_key.clone(), keys));
        record.bytes += bytes;
        map.bytes += bytes;
    }
    map.evict_to_budget();
}

// ---------------------------------------------------------------------------
// The tail analysis, once per table version and space.

/// Which fragments of one pinned version each part of a lookup reads.
struct TailAnalysis {
    /// Whether the space's first column has one BTREE whose coverage the
    /// index part reads (`covered` may still be empty).
    indexed: bool,
    covered: usize,
    /// Uncovered fragments the known keys answer, by file handle.
    known: HashSet<u64>,
    min_known_handle: u64,
    /// Uncovered fragments a tail scan reads.
    scan: Vec<u64>,
    drop_generation: u64,
}

impl TailAnalysis {
    fn valid(&self, map: &KnownKeys) -> bool {
        self.known.is_empty()
            || (self.drop_generation == map.drop_generation
                && self.min_known_handle >= map.min_live_handle())
    }
}

const MEMO_ENTRIES: usize = 512;

#[derive(Default)]
struct Memo {
    map: HashMap<String, Arc<TailAnalysis>>,
    order: VecDeque<String>,
}

static MEMO: LazyLock<Mutex<Memo>> = LazyLock::new(Mutex::default);

fn memo_key(ds: &Dataset, fields: &[i32]) -> String {
    format!(
        "{}\u{0}{}\u{0}{:?}",
        root_of(ds),
        ds.manifest_location().path,
        fields
    )
}

/// The fragments of `ds` the one BTREE on `column` covers, when exactly one
/// index that can answer an `IN` list keys the column (full-text and vector
/// indexes cannot, and are ignored), it is a BTREE in every segment, every
/// segment reports its bitmap and every fragment records `physical_rows`
/// (Lance disables scalar indexes for the whole scan otherwise). `None`
/// otherwise: the whole table is then the tail.
async fn btree_coverage(ds: &Dataset, column: &str) -> Result<Option<BTreeSet<u64>>> {
    use lance::index::DatasetIndexExt;
    let Some(field_id) = ds.schema().field(column).map(|field| field.id) else {
        return Ok(None);
    };
    if ds.fragments().is_empty()
        || !ds
            .fragments()
            .iter()
            .all(|fragment| fragment.physical_rows.is_some())
    {
        return Ok(None);
    }
    let indices = ds.load_indices().await.map_err(OmniError::storage)?;
    let mut by_name: HashMap<&str, Vec<&lance_table::format::IndexMetadata>> = HashMap::new();
    for index in indices.iter().filter(|index| {
        !lance_index::is_system_index(index)
            && index.keyed_field() == Some(field_id)
            && !crate::dataset_index::is_full_text_index(index)
            && !index.index_details.as_ref().is_some_and(|details| {
                lance::index::scalar::IndexDetails(details.clone()).is_vector()
            })
    }) {
        by_name.entry(index.name.as_str()).or_default().push(index);
    }
    if by_name.len() != 1 {
        return Ok(None);
    }
    let segments = by_name.into_values().next().expect("one index");
    if !segments
        .iter()
        .all(|segment| crate::dataset_index::is_btree_index(segment))
    {
        return Ok(None);
    }
    let current = crate::dataset_index::current_fragments(ds);
    let coverage = crate::dataset_index::SegmentCoverage::of(segments, &current);
    if !coverage.known {
        return Ok(None);
    }
    Ok(Some(coverage.covered))
}

/// A fragment the known keys may answer: exactly one data file, in the
/// dataset's own base or a registered one, listing every field of the space,
/// no deletion file and no overlay.
fn answerable(fragment: &Fragment, fields: &[i32]) -> bool {
    if fragment.deletion_file.is_some() || !fragment.overlays.is_empty() {
        return false;
    }
    let [file] = fragment.files.as_slice() else {
        return false;
    };
    fields.iter().all(|field| file.fields.contains(field))
}

async fn tail_analysis(ds: &Dataset, space: &KeySpace, fields: &[i32]) -> Result<Arc<TailAnalysis>> {
    let key = memo_key(ds, fields);
    let hit = MEMO
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .map
        .get(&key)
        .cloned();
    if let Some(hit) = hit
        && hit.valid(&map())
    {
        return Ok(hit);
    }
    let analysis_started = std::time::Instant::now();
    let coverage = btree_coverage(ds, &space.columns[0]).await?;
    let indexed = coverage.is_some();
    let covered = coverage.unwrap_or_default();
    let uncovered: Vec<&Fragment> = ds
        .fragments()
        .iter()
        .filter(|fragment| !covered.contains(&fragment.id))
        .collect();
    let mut known = HashSet::new();
    let mut min_known_handle = u64::MAX;
    let mut scan = Vec::new();
    let drop_generation;
    {
        let map = map();
        drop_generation = map.drop_generation;
        let space_key_root = root_of(ds);
        for fragment in uncovered {
            let handle = if known_keys_enabled() && answerable(fragment, fields) {
                map.files
                    .get(&file_location(ds, &fragment.files[0]))
                    .filter(|record| {
                        record
                            .spaces
                            .iter()
                            .any(|space| space.fields == fields && space.root == space_key_root)
                    })
                    .map(|record| record.handle)
            } else {
                None
            };
            match handle {
                Some(handle) => {
                    min_known_handle = min_known_handle.min(handle);
                    known.insert(handle);
                }
                None => scan.push(fragment.id),
            }
        }
    }
    ANALYSES.fetch_add(1, Ordering::Relaxed);
    ANALYSIS_NANOS.fetch_add(analysis_started.elapsed().as_nanos() as u64, Ordering::Relaxed);
    let analysis = Arc::new(TailAnalysis {
        indexed,
        covered: covered.len(),
        known,
        min_known_handle,
        scan,
        drop_generation,
    });
    let mut memo = MEMO
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if memo.map.insert(key.clone(), Arc::clone(&analysis)).is_none() {
        memo.order.push_back(key);
    }
    while memo.order.len() > MEMO_ENTRIES {
        if let Some(oldest) = memo.order.pop_front() {
            memo.map.remove(&oldest);
        }
    }
    Ok(analysis)
}

// ---------------------------------------------------------------------------
// The lookup.

/// Match one scanned batch's rows against `wanted`, appending each holding
/// row's id to `holders`. A row with a null id or a null key column holds no
/// key.
fn match_batch(
    batch: &RecordBatch,
    space: &KeySpace,
    wanted: &HashSet<&Vec<String>>,
    holders: &mut Holders,
) -> Result<()> {
    let ids = batch.column_by_name(&space.holder).ok_or_else(|| {
        OmniError::manifest_internal(format!(
            "key lookup: scan has no '{}' column",
            space.holder
        ))
    })?;
    let columns: Vec<ArrayRef> = space
        .columns
        .iter()
        .map(|column| {
            batch.column_by_name(column).cloned().ok_or_else(|| {
                OmniError::manifest_internal(format!("key lookup: scan has no '{column}' column"))
            })
        })
        .collect::<Result<_>>()?;
    for row in 0..batch.num_rows() {
        let Some(id) = canonical_scalar_key(ids, row)? else {
            continue;
        };
        let Some(key) = crate::loader::composite_unique_key(&columns, row)? else {
            continue;
        };
        if wanted.contains(&key) {
            holders.entry(key).or_default().push(id);
        }
    }
    Ok(())
}

/// The known part of a lookup, read under the map's lock: `None` when a
/// file the analysis counts as known has left the map since.
fn known_part(
    ds: &Dataset,
    space: &KeySpace,
    fields: &[i32],
    analysis: &TailAnalysis,
    wanted: &[(Vec<String>, ScalarValue)],
) -> Option<Holders> {
    let map = map();
    if !analysis.valid(&map) {
        return None;
    }
    let mut from_map = Holders::new();
    if analysis.known.is_empty() {
        return Some(from_map);
    }
    let space_key = SpaceKey {
        root: root_of(ds),
        fields: fields.to_vec(),
    };
    let Some(index) = map.spaces.get(&space_key) else {
        return Some(from_map);
    };
    let mut seen = HashSet::new();
    for (key, _) in wanted {
        // A repeated wanted key is one key: the scans match rows against a
        // set, so the map must not report its holders twice (a repeated src
        // would double its @card count).
        if !seen.insert(key) {
            continue;
        }
        let Some(entries) = index.by_key.get(key) else {
            continue;
        };
        for (handle, file_holders) in entries {
            if !analysis.known.contains(handle) {
                continue;
            }
            let found = from_map.entry(key.clone()).or_default();
            if space.is_id_space() {
                found.push(key[0].clone());
            } else {
                found.extend(file_holders.iter().cloned());
            }
        }
    }
    Some(from_map)
}

/// The holders of each of `wanted` (canonical tuples with the typed value of
/// their first column) in the pinned table `ds`, read in the two parts the
/// module documents.
pub(crate) async fn lookup(
    ds: &Dataset,
    space: &KeySpace,
    wanted: &[(Vec<String>, ScalarValue)],
) -> Result<Holders> {
    let mut holders = Holders::new();
    if wanted.is_empty() || ds.fragments().is_empty() {
        return Ok(holders);
    }
    LOOKUPS.fetch_add(1, Ordering::Relaxed);
    let lookup_started = std::time::Instant::now();
    let result = lookup_inner(ds, space, wanted).await;
    LOOKUP_NANOS.fetch_add(lookup_started.elapsed().as_nanos() as u64, Ordering::Relaxed);
    static TIMING: LazyLock<bool> = LazyLock::new(|| std::env::var("OG_KL_TIMING").is_ok());
    if *TIMING && LOOKUPS.load(Ordering::Relaxed) % 200 == 0 {
        let [analyses, analysis_ns, lookup_ns] = lookup_timing();
        eprintln!(
            "[kl-timing] lookups {} analyses {analyses} analysis_ms {} lookup_ms {} stats {:?} known {:?}",
            LOOKUPS.load(Ordering::Relaxed),
            analysis_ns / 1_000_000,
            lookup_ns / 1_000_000,
            lookup_stats(),
            known_keys_stats()
        );
    }
    result
}

async fn lookup_inner(
    ds: &Dataset,
    space: &KeySpace,
    wanted: &[(Vec<String>, ScalarValue)],
) -> Result<Holders> {
    let mut holders = Holders::new();
    let fields = space.field_ids(ds)?;
    let wanted_set: HashSet<&Vec<String>> = wanted.iter().map(|(key, _)| key).collect();
    let projection = space.projection();

    // The tail analysis and the known part read the map under one lock, so
    // an analysis is used only while every file it counts as known is still
    // there.
    let mut analysis = tail_analysis(ds, space, &fields).await?;
    let mut attempts = 0;
    let from_map = loop {
        if let Some(found) = known_part(ds, space, &fields, &analysis, wanted) {
            break found;
        }
        attempts += 1;
        if attempts == 3 {
            return Err(OmniError::manifest_internal(
                "key lookup: the known keys changed under three tail analyses",
            ));
        }
        MEMO.lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .map
            .remove(&memo_key(ds, &fields));
        analysis = tail_analysis(ds, space, &fields).await?;
    };
    FRAGMENTS_FROM_MAP.fetch_add(analysis.known.len() as u64, Ordering::Relaxed);

    // Part 1: the covered fragments, through the index only.
    if analysis.indexed && analysis.covered > 0 {
        INDEX_PARTS.fetch_add(1, Ordering::Relaxed);
        let mut seen = HashSet::new();
        let firsts: Vec<&ScalarValue> = wanted
            .iter()
            .filter(|(key, _)| seen.insert(key[0].as_str()))
            .map(|(_, typed)| typed)
            .collect();
        let tail_empty = analysis.known.is_empty() && analysis.scan.is_empty();
        for chunk in firsts.chunks(PROBE_CHUNK_KEYS) {
            let filter = ident(space.columns[0].as_str())
                .in_list(chunk.iter().map(|value| lit((*value).clone())).collect(), false);
            let mut stream =
                TableStore::scan_stream_with(ds, Some(&projection), None, None, false, |scanner| {
                    scanner.filter_expr(filter);
                    if !tail_empty {
                        scanner.fast_search();
                    }
                    Ok(())
                })
                .await?;
            while let Some(batch) = stream.try_next().await.map_err(OmniError::storage)? {
                match_batch(&batch, space, &wanted_set, &mut holders)?;
            }
        }
    }

    // Part 2: the uncovered fragments the map does not answer, one scan.
    if !analysis.scan.is_empty() {
        let scan: HashSet<u64> = analysis.scan.iter().copied().collect();
        let fragments: Vec<Fragment> = ds
            .fragments()
            .iter()
            .filter(|fragment| scan.contains(&fragment.id))
            .cloned()
            .collect();
        FRAGMENTS_SCANNED.fetch_add(fragments.len() as u64, Ordering::Relaxed);
        // Research (`OMNIGRAPH_KNOWN_KEYS_FILL_FROM_SCAN=1`, off by default:
        // RFC rule 16 lets only the absence-proven writer record): a scanned
        // fragment the map could answer (one data file listing the space's
        // fields, no deletion file, no overlay) has all its rows in this scan,
        // so its keys in this space are recorded, and the next lookup answers
        // it from memory.
        let fill: HashMap<u32, String> = if fill_from_scan() {
            fragments
                .iter()
                .filter(|fragment| answerable(fragment, &fields) && fragment.files[0].base_id.is_none())
                .map(|fragment| (fragment.id as u32, file_location(ds, &fragment.files[0])))
                .collect()
        } else {
            HashMap::new()
        };
        let with_addr = !fill.is_empty();
        let mut stream = TableStore::scan_stream_with(
            ds,
            Some(&projection),
            None,
            None,
            false,
            move |scanner| {
                scanner.with_fragments(fragments);
                scanner.batch_size(ID_SCAN_BATCH_ROWS);
                scanner.batch_size_bytes(ID_SCAN_BATCH_BYTES);
                if with_addr {
                    scanner.with_row_address();
                }
                Ok(())
            },
        )
        .await?;
        let mut filled: HashMap<u32, Vec<(Vec<String>, String)>> =
            fill.keys().map(|id| (*id, Vec::new())).collect();
        while let Some(batch) = stream.try_next().await.map_err(OmniError::storage)? {
            TAIL_ROWS_SCANNED.fetch_add(batch.num_rows() as u64, Ordering::Relaxed);
            match_batch(&batch, space, &wanted_set, &mut holders)?;
            if with_addr {
                collect_fill(&batch, space, &mut filled)?;
            }
        }
        if with_addr {
            let space_key = SpaceKey {
                root: root_of(ds),
                fields: fields.clone(),
            };
            record_scanned(space_key, space.is_id_space(), fill, filled);
        }
    }

    for (key, found) in from_map {
        holders.entry(key).or_default().extend(found);
    }
    Ok(holders)
}

/// The reference a lookup must equal: one scan of the whole pinned table,
/// matched in memory. Tests and the research harness compare against it.
pub(crate) async fn full_scan_lookup(
    ds: &Dataset,
    space: &KeySpace,
    wanted: &[(Vec<String>, ScalarValue)],
) -> Result<Holders> {
    let mut holders = Holders::new();
    if wanted.is_empty() || ds.fragments().is_empty() {
        return Ok(holders);
    }
    let wanted_set: HashSet<&Vec<String>> = wanted.iter().map(|(key, _)| key).collect();
    let projection = space.projection();
    let mut stream = TableStore::scan_stream_bounded(
        ds,
        Some(&projection),
        None,
        None,
        false,
        ID_SCAN_BATCH_ROWS,
        ID_SCAN_BATCH_BYTES,
    )
    .await?;
    while let Some(batch) = stream.try_next().await.map_err(OmniError::storage)? {
        match_batch(&batch, space, &wanted_set, &mut holders)?;
    }
    Ok(holders)
}

#[cfg(test)]
#[path = "key_lookup/tests.rs"]
mod tests;

// ---------------------------------------------------------------------------
// Research instrument: the randomized differential check.

/// What one [`differential_check`] compared.
#[derive(Debug, Default, Clone)]
pub struct DifferentialReport {
    pub spaces: usize,
    pub wanted_keys: usize,
    pub present_keys: usize,
    pub fragments: usize,
    pub covered_spaces: usize,
    pub map_fragments: usize,
    pub scanned_fragments: usize,
    pub deletion_fragments: usize,
    pub multi_file_fragments: usize,
    pub mismatches: Vec<String>,
}

/// The typed form of a canonical value for a column of `data_type`.
fn typed(data_type: &arrow_schema::DataType, canonical: &str) -> Option<ScalarValue> {
    use arrow_schema::DataType;
    Some(match data_type {
        DataType::Utf8 => ScalarValue::Utf8(Some(canonical.to_string())),
        DataType::LargeUtf8 => ScalarValue::LargeUtf8(Some(canonical.to_string())),
        DataType::Int64 => ScalarValue::Int64(Some(canonical.parse().ok()?)),
        DataType::Int32 => ScalarValue::Int32(Some(canonical.parse().ok()?)),
        DataType::UInt32 => ScalarValue::UInt32(Some(canonical.parse().ok()?)),
        DataType::UInt64 => ScalarValue::UInt64(Some(canonical.parse().ok()?)),
        DataType::Boolean => ScalarValue::Boolean(Some(canonical.parse().ok()?)),
        _ => return None,
    })
}

/// Compare the two-part [`lookup`] with [`full_scan_lookup`] on the table
/// `table_key` of `snapshot`, in every key space of the table: the ids, each
/// `@unique` group of `catalog`, and an edge table's `src`, `dst` and
/// id → src. The wanted keys are every key the table holds (up to `max_keys`
/// per space, chosen by `seed`), `extra` keys per space (old values of
/// updated or deleted rows, for the staleness cell), and absent keys.
/// Holder lists compare as multisets.
pub async fn differential_check(
    snapshot: &crate::db::Snapshot,
    catalog: &omnigraph_compiler::catalog::Catalog,
    table_key: &str,
    extra: &HashMap<Vec<String>, Vec<Vec<String>>>,
    max_keys: usize,
    seed: u64,
) -> Result<DifferentialReport> {
    let mut report = DifferentialReport::default();
    if snapshot.dataset(table_key).is_none() {
        return Ok(report);
    }
    let ds = snapshot.open_lance_dataset(table_key).await?;
    let system = catalog.system_columns;
    let mut spaces: Vec<KeySpace> = vec![KeySpace::ids(system.id)];
    if let Some(name) = table_key.strip_prefix("node:") {
        if let Some(node) = catalog.node_types.get(name) {
            for group in &node.unique_constraints {
                spaces.push(KeySpace::of(group, system.id));
            }
        }
    } else if let Some(name) = table_key.strip_prefix("edge:") {
        spaces.push(KeySpace::of(&[system.src.to_string()], system.id));
        spaces.push(KeySpace::of(&[system.dst.to_string()], system.id));
        spaces.push(KeySpace::of(&[system.id.to_string()], system.src));
        if let Some(edge) = catalog.edge_types.get(name) {
            for group in &edge.unique_constraints {
                spaces.push(KeySpace::of(group, system.id));
            }
        }
    }
    report.fragments = ds.fragments().len();
    report.deletion_fragments = ds
        .fragments()
        .iter()
        .filter(|fragment| fragment.deletion_file.is_some())
        .count();
    report.multi_file_fragments = ds
        .fragments()
        .iter()
        .filter(|fragment| fragment.files.len() > 1)
        .count();
    let mut rng = seed.wrapping_mul(0x9e37_79b9_7f4a_7c15) | 1;
    let mut next = move || {
        rng ^= rng << 13;
        rng ^= rng >> 7;
        rng ^= rng << 17;
        rng
    };
    for space in &spaces {
        report.spaces += 1;
        let schema = ds.schema();
        let Some(first_type) = schema
            .field(&space.columns[0])
            .map(|field| field.data_type())
        else {
            report
                .mismatches
                .push(format!("{table_key}: space {:?} has no column", space.columns));
            continue;
        };
        // Every key the table holds, by a whole-table scan.
        let mut present: Vec<(Vec<String>, ScalarValue)> = Vec::new();
        {
            let projection = space.projection();
            let mut stream = TableStore::scan_stream_bounded(
                &ds,
                Some(&projection),
                None,
                None,
                false,
                ID_SCAN_BATCH_ROWS,
                ID_SCAN_BATCH_BYTES,
            )
            .await?;
            let mut seen = HashSet::new();
            while let Some(batch) = stream.try_next().await.map_err(OmniError::storage)? {
                let columns: Vec<ArrayRef> = space
                    .columns
                    .iter()
                    .map(|column| batch.column_by_name(column).cloned().expect("projected"))
                    .collect();
                for row in 0..batch.num_rows() {
                    if let Some(key) = crate::loader::composite_unique_key(&columns, row)?
                        && seen.insert(key.clone())
                    {
                        let first = ScalarValue::try_from_array(&columns[0], row)
                            .map_err(|error| OmniError::manifest_internal(error.to_string()))?;
                        present.push((key, first));
                    }
                }
            }
        }
        // A seeded sample of the present keys.
        while present.len() > max_keys {
            let at = (next() as usize) % present.len();
            present.swap_remove(at);
        }
        report.present_keys += present.len();
        let mut wanted = present;
        for key in extra.get(&space.columns).into_iter().flatten() {
            if let Some(first) = typed(&first_type, &key[0]) {
                wanted.push((key.clone(), first));
            }
        }
        for n in 0..4 {
            let absent: Vec<String> = space
                .columns
                .iter()
                .enumerate()
                .map(|(i, _)| format!("absent-{seed}-{n}-{i}-{}", next() % 1000))
                .collect();
            if let Some(first) = typed(&first_type, &absent[0]) {
                wanted.push((absent, first));
            }
        }
        report.wanted_keys += wanted.len();
        let before = lookup_stats();
        let got = lookup(&ds, space, &wanted).await?;
        let after = lookup_stats();
        report.covered_spaces += usize::from(after[1] > before[1]);
        report.map_fragments += (after[2] - before[2]) as usize;
        report.scanned_fragments += (after[3] - before[3]) as usize;
        let expected = full_scan_lookup(&ds, space, &wanted).await?;
        let normalize = |holders: &Holders| -> std::collections::BTreeMap<Vec<String>, Vec<String>> {
            holders
                .iter()
                .map(|(key, found)| {
                    let mut found = found.clone();
                    found.sort();
                    (key.clone(), found)
                })
                .collect()
        };
        let (got, expected) = (normalize(&got), normalize(&expected));
        if got != expected {
            let mut detail = Vec::new();
            for (key, found) in &expected {
                if got.get(key) != Some(found) {
                    detail.push(format!("{key:?}: lookup {:?}, scan {found:?}", got.get(key)));
                }
            }
            for (key, found) in &got {
                if !expected.contains_key(key) {
                    detail.push(format!("{key:?}: lookup {found:?}, scan none"));
                }
            }
            detail.truncate(8);
            report.mismatches.push(format!(
                "{table_key} {:?} -> {}: {}",
                space.columns,
                space.holder,
                detail.join("; ")
            ));
        }
    }
    Ok(report)
}

/// [`lookup`] and [`full_scan_lookup`] over a raw pinned Lance dataset, for
/// string key columns: the research tests' door to the Lance-level shapes
/// (delta index segments, raw updates) that no graph writer produces.
/// Returns `(two-part, full scan)`, each holder list sorted.
pub async fn lookup_strings_both(
    ds: &Dataset,
    columns: &[String],
    holder: &str,
    wanted: &[Vec<String>],
) -> Result<(
    std::collections::BTreeMap<Vec<String>, Vec<String>>,
    std::collections::BTreeMap<Vec<String>, Vec<String>>,
)> {
    let space = KeySpace::of(columns, holder);
    let wanted: Vec<(Vec<String>, ScalarValue)> = wanted
        .iter()
        .map(|key| (key.clone(), ScalarValue::Utf8(Some(key[0].clone()))))
        .collect();
    let sorted = |holders: Holders| {
        holders
            .into_iter()
            .map(|(key, mut found)| {
                found.sort();
                (key, found)
            })
            .collect()
    };
    Ok((
        sorted(lookup(ds, &space, &wanted).await?),
        sorted(full_scan_lookup(ds, &space, &wanted).await?),
    ))
}
