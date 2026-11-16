//! Randomized group-commit stress harness with an operation history and a
//! model-based checker (research, `research/group-commit-stress`).
//!
//! Writers run a seeded mix of operations against one graph through one or
//! more handles of this process, with optional cancellation (a write's future
//! dropped by a timeout), several actors, readers that record pinned
//! snapshots, and maintenance (optimize, index builds, cleanup, branch churn,
//! a schema apply) alongside. Every invocation and completion is recorded.
//! After the clocks stop, the checker verifies on a fresh handle:
//!
//! - no duplicate keys (Item.slug, Person.name, Tag.label) and no dangling
//!   edges, in the final state and in every reader snapshot;
//! - every acknowledged receipt names a commit that `list_commits` holds at
//!   that version, with the write's actor, and main's lineage is one chain;
//! - a model replay of every acknowledged write in commit-version order equals
//!   the final state, except on keys an in-doubt write touched;
//! - every reader snapshot at version V equals the model after V (batch
//!   atomicity: no partial batch is ever visible), on certain keys;
//! - read-your-writes on the writer's own handle right after its receipt.
//!
//! Configuration (`OG_GS_*`, defaults in brackets): `TARGET` [local] or an
//! `s3://` prefix, `WRITERS` [32], `SECS` [20], `SEED` [1], `HANDLES` [1],
//! `ACTORS` [1], `HOT` [64] hot Item keys, `PEOPLE` [24], `MIX` [see
//! `Mix::parse`], `CANCEL_PCT` [0] writes run under a random short timeout,
//! `CANCEL_MS` [40] its upper bound, `READERS` [2], `RYW_PCT` [10],
//! `OPTIMIZE_SECS` [0 = off], `INDEX_SECS` [0], `CLEANUP_SECS` [0],
//! `BRANCH_SECS` [0] side-branch churn, `SCHEMA_AT_SECS` [0] one schema apply,
//! `OUT` [unset] a JSONL file for the history and summary.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use arrow_array::{Array, Int64Array, RecordBatch, StringArray};
use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph::error::{CompletionEvidence, OmniError};
use omnigraph::settings::SessionSettings;

use super::helpers::{Session, mixed_params, params};

pub const SCHEMA: &str = r#"node Item {
    slug: String @key
    n: I64
}
node Person {
    name: String @key
}
edge Knows: Person -> Person
node Tag {
    slug: String @key
    label: String @unique
}
"#;

pub const SCHEMA_EVOLVED: &str = r#"node Item {
    slug: String @key
    n: I64
    note: String?
}
node Person {
    name: String @key
}
edge Knows: Person -> Person
node Tag {
    slug: String @key
    label: String @unique
}
"#;

pub const QUERIES: &str = r#"
query ins($slug: String, $n: I64) {
    insert Item { slug: $slug, n: $n }
}
query ins2($a: String, $b: String, $n: I64) {
    insert Item { slug: $a, n: $n }
    insert Item { slug: $b, n: $n }
}
query upd($slug: String, $n: I64) {
    update Item set { n: $n } where slug = $slug
}
query del($slug: String) {
    delete Item where slug = $slug
}
query person($name: String) {
    insert Person { name: $name }
}
query del_person($name: String) {
    delete Person where name = $name
}
query knows($from: String, $to: String) {
    insert Knows { from: $from, to: $to }
}
query multi($slug: String, $n: I64, $name: String) {
    insert Item { slug: $slug, n: $n }
    insert Person { name: $name }
}
query tag($slug: String, $label: String) {
    insert Tag { slug: $slug, label: $label }
}
"#;

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn env_num(name: &str, default: u64) -> u64 {
    env_or(name, &default.to_string())
        .parse()
        .unwrap_or_else(|_| panic!("{name} must be a number"))
}

/// xorshift64*: deterministic per writer from the run seed.
#[derive(Clone)]
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }
    pub fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    pub fn below(&mut self, n: u64) -> u64 {
        if n == 0 { 0 } else { self.next() % n }
    }
    pub fn pct(&mut self, p: u64) -> bool {
        self.below(100) < p
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum OpKind {
    /// Insert a writer-unique Item key.
    Fresh,
    /// Insert a hot Item key (an upsert; collides across writers).
    Hot,
    /// Two Item rows in one mutation (one hot, one fresh).
    Pair,
    Update,
    Delete,
    Person,
    DeletePerson,
    Knows,
    /// An Item and a Person in one mutation (two tables).
    Multi,
    /// A Tag with a non-key `@unique` label (ineligible).
    Tag,
}

impl OpKind {
    fn name(self) -> &'static str {
        match self {
            Self::Fresh => "fresh",
            Self::Hot => "hot",
            Self::Pair => "pair",
            Self::Update => "update",
            Self::Delete => "delete",
            Self::Person => "person",
            Self::DeletePerson => "del_person",
            Self::Knows => "knows",
            Self::Multi => "multi",
            Self::Tag => "tag",
        }
    }
}

#[derive(Clone)]
pub struct Mix(Vec<(OpKind, u64)>);

impl Mix {
    /// `fresh=40,hot=20,...`; default: an insert-heavy mix with every kind.
    pub fn parse(spec: &str) -> Self {
        let mut out = Vec::new();
        for part in spec.split(',').filter(|part| !part.is_empty()) {
            let (name, weight) = part.split_once('=').expect("MIX is name=weight,...");
            let kind = match name {
                "fresh" => OpKind::Fresh,
                "hot" => OpKind::Hot,
                "pair" => OpKind::Pair,
                "update" => OpKind::Update,
                "delete" => OpKind::Delete,
                "person" => OpKind::Person,
                "del_person" => OpKind::DeletePerson,
                "knows" => OpKind::Knows,
                "multi" => OpKind::Multi,
                "tag" => OpKind::Tag,
                other => panic!("unknown op kind {other}"),
            };
            out.push((kind, weight.parse().expect("weight is a number")));
        }
        Self(out)
    }
    fn pick(&self, rng: &mut Rng) -> OpKind {
        let total: u64 = self.0.iter().map(|(_, w)| w).sum();
        let mut roll = rng.below(total);
        for (kind, weight) in &self.0 {
            if roll < *weight {
                return *kind;
            }
            roll -= weight;
        }
        self.0[0].0
    }
}

pub const DEFAULT_MIX: &str =
    "fresh=40,hot=20,pair=5,update=8,delete=5,person=5,del_person=2,knows=8,multi=4,tag=3";

#[derive(Clone)]
pub struct Config {
    pub target: String,
    pub writers: usize,
    pub secs: u64,
    pub seed: u64,
    pub handles: usize,
    pub actors: usize,
    pub hot: u64,
    pub people: u64,
    pub mix: Mix,
    pub cancel_pct: u64,
    pub cancel_ms: u64,
    pub readers: usize,
    pub ryw_pct: u64,
    pub optimize_secs: u64,
    pub index_secs: u64,
    pub cleanup_secs: u64,
    pub branch_secs: u64,
    pub schema_at_secs: u64,
    pub out: Option<String>,
    /// Multi-process mode: write to this existing root (no init, no seed,
    /// no in-process check; histories are checked together offline).
    pub uri: Option<String>,
    /// Prefix of this process's fresh keys, unique per process.
    pub tag: String,
    /// Claim `main`'s writer epoch at this second (0 = never).
    pub claim_at_secs: u64,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            target: env_or("OG_GS_TARGET", "local"),
            writers: env_num("OG_GS_WRITERS", 32) as usize,
            secs: env_num("OG_GS_SECS", 20),
            seed: env_num("OG_GS_SEED", 1),
            handles: env_num("OG_GS_HANDLES", 1).max(1) as usize,
            actors: env_num("OG_GS_ACTORS", 1).max(1) as usize,
            hot: env_num("OG_GS_HOT", 64).max(1),
            people: env_num("OG_GS_PEOPLE", 24).max(2),
            mix: Mix::parse(&env_or("OG_GS_MIX", DEFAULT_MIX)),
            cancel_pct: env_num("OG_GS_CANCEL_PCT", 0),
            cancel_ms: env_num("OG_GS_CANCEL_MS", 40).max(1),
            readers: env_num("OG_GS_READERS", 2) as usize,
            ryw_pct: env_num("OG_GS_RYW_PCT", 10),
            optimize_secs: env_num("OG_GS_OPTIMIZE_SECS", 0),
            index_secs: env_num("OG_GS_INDEX_SECS", 0),
            cleanup_secs: env_num("OG_GS_CLEANUP_SECS", 0),
            branch_secs: env_num("OG_GS_BRANCH_SECS", 0),
            schema_at_secs: env_num("OG_GS_SCHEMA_AT_SECS", 0),
            out: std::env::var("OG_GS_OUT").ok(),
            uri: std::env::var("OG_GS_URI").ok(),
            tag: env_or("OG_GS_TAG", ""),
            claim_at_secs: env_num("OG_GS_CLAIM_AT_SECS", 0),
        }
    }
}

/// One invocation and its completion.
#[derive(Clone, Debug)]
pub struct Op {
    pub id: u64,
    pub writer: usize,
    pub handle: usize,
    pub actor: String,
    pub kind: OpKind,
    pub items: Vec<String>,
    pub person: Option<String>,
    pub edge: Option<(String, String)>,
    pub tag: Option<(String, String)>,
    pub n: i64,
    pub invoked_us: u64,
    pub completed_us: u64,
    pub outcome: Outcome,
    pub version: Option<u64>,
    pub commit: Option<String>,
    pub commit_actor: Option<String>,
    pub affected: (usize, usize),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    Ok,
    /// A definite refusal: nothing of it is published.
    Refused(String),
    /// Unknown: cancelled, or an error without definite evidence.
    InDoubt(String),
}

impl Outcome {
    fn label(&self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Refused(_) => "refused",
            Self::InDoubt(_) => "in_doubt",
        }
    }
}

fn classify(error: &OmniError) -> Outcome {
    let text = format!("{error}");
    match error.completion_evidence() {
        Some(CompletionEvidence::BeforeEffect) | Some(CompletionEvidence::Unpublished) => {
            Outcome::Refused(text)
        }
        _ if error.is_read_set_changed() => Outcome::Refused(text),
        // Typed refusals that publish nothing.
        _ if matches!(
            error,
            OmniError::KeyConflict { .. } | OmniError::Compiler(_) | OmniError::MergeConflicts(_)
        ) =>
        {
            Outcome::Refused(text)
        }
        _ => {
            let debug: String = format!("{error:?}").chars().take(160).collect();
            Outcome::InDoubt(format!("{text} [{debug}]"))
        }
    }
}

/// A reader observation: the pinned version and what it held.
#[derive(Clone, Debug)]
pub struct Observation {
    pub version: u64,
    pub head: Option<String>,
    pub items: BTreeMap<String, Vec<i64>>,
    pub people: BTreeMap<String, usize>,
    pub edges: Vec<(String, String)>,
    pub tags: Vec<(String, String)>,
}

pub struct Shared {
    pub cfg: Config,
    pub start: Instant,
    pub stop: AtomicBool,
    pub ops: Mutex<Vec<Op>>,
    pub next_id: AtomicU64,
    pub observations: Mutex<Vec<Observation>>,
    pub ryw_violations: Mutex<Vec<String>>,
    pub maintenance: Mutex<Vec<String>>,
    /// Side-branch writes merged into main: (merge version, keys).
    pub merges: Mutex<Vec<(u64, Vec<(String, i64)>)>>,
}

impl Shared {
    fn now_us(&self) -> u64 {
        self.start.elapsed().as_micros() as u64
    }
}

fn str_col<'a>(batch: &'a RecordBatch, name: &str) -> Option<&'a StringArray> {
    batch
        .column_by_name(name)
        .and_then(|column| column.as_any().downcast_ref::<StringArray>())
}

/// Read every table this harness writes at one pinned snapshot.
pub async fn observe(db: &Omnigraph, target: ReadTarget) -> omnigraph::error::Result<Observation> {
    use futures::TryStreamExt as _;
    let snap = db.snapshot_of(target).await?;
    let mut obs = Observation {
        version: snap.graph_manifest_version(),
        head: snap.graph_head(None).map(str::to_string),
        items: BTreeMap::new(),
        people: BTreeMap::new(),
        edges: Vec::new(),
        tags: Vec::new(),
    };
    for (table, kind) in [
        ("node:Item", 0),
        ("node:Person", 1),
        ("edge:Knows", 2),
        ("node:Tag", 3),
    ] {
        let ds = snap.open_dataset(table).await?;
        let batches: Vec<RecordBatch> = ds
            .scan()
            .try_into_stream()
            .await?
            .try_collect()
            .await
            .map_err(OmniError::storage)?;
        for batch in &batches {
            match kind {
                0 => {
                    let slugs = str_col(batch, "slug").expect("slug column");
                    let ns = batch
                        .column_by_name("n")
                        .and_then(|c| c.as_any().downcast_ref::<Int64Array>())
                        .expect("n column");
                    for row in 0..batch.num_rows() {
                        obs.items
                            .entry(slugs.value(row).to_string())
                            .or_default()
                            .push(ns.value(row));
                    }
                }
                1 => {
                    let names = str_col(batch, "name").expect("name column");
                    for row in 0..batch.num_rows() {
                        *obs.people.entry(names.value(row).to_string()).or_default() += 1;
                    }
                }
                2 => {
                    let src = str_col(batch, "__src")
                        .or_else(|| str_col(batch, "src"))
                        .expect("src");
                    let dst = str_col(batch, "__dst")
                        .or_else(|| str_col(batch, "dst"))
                        .expect("dst");
                    for row in 0..batch.num_rows() {
                        obs.edges
                            .push((src.value(row).to_string(), dst.value(row).to_string()));
                    }
                }
                _ => {
                    let slugs = str_col(batch, "slug").expect("tag slug");
                    let labels = str_col(batch, "label").expect("tag label");
                    for row in 0..batch.num_rows() {
                        obs.tags
                            .push((slugs.value(row).to_string(), labels.value(row).to_string()));
                    }
                }
            }
        }
    }
    obs.edges.sort();
    obs.tags.sort();
    Ok(obs)
}

async fn run_writer(shared: Arc<Shared>, sessions: Arc<Vec<Session>>, writer: usize) {
    let cfg = &shared.cfg;
    let mut rng = Rng::new(cfg.seed.wrapping_mul(1000).wrapping_add(writer as u64 + 1));
    let handle = writer % sessions.len();
    let session = &sessions[handle];
    let actor = format!("act-{}", writer % cfg.actors);
    let mut seq = 0u64;
    while !shared.stop.load(Ordering::Acquire) {
        seq += 1;
        let kind = cfg.mix.pick(&mut rng);
        let n = (writer as i64) * 1_000_000 + seq as i64;
        let fresh = format!("f{}{writer}-{seq}", cfg.tag);
        let hot = format!("h{}", rng.below(cfg.hot));
        let person_a = format!("p{}", rng.below(cfg.people));
        let person_b = format!("p{}", rng.below(cfg.people));
        let mut op = Op {
            id: shared.next_id.fetch_add(1, Ordering::Relaxed),
            writer,
            handle,
            actor: actor.clone(),
            kind,
            items: Vec::new(),
            person: None,
            edge: None,
            tag: None,
            n,
            invoked_us: 0,
            completed_us: 0,
            outcome: Outcome::Ok,
            version: None,
            commit: None,
            commit_actor: None,
            affected: (0, 0),
        };
        let (name, p) = match kind {
            OpKind::Fresh => {
                op.items = vec![fresh.clone()];
                ("ins", mixed_params(&[("$slug", &fresh)], &[("$n", n)]))
            }
            OpKind::Hot => {
                op.items = vec![hot.clone()];
                ("ins", mixed_params(&[("$slug", &hot)], &[("$n", n)]))
            }
            OpKind::Pair => {
                op.items = vec![hot.clone(), fresh.clone()];
                (
                    "ins2",
                    mixed_params(&[("$a", &hot), ("$b", &fresh)], &[("$n", n)]),
                )
            }
            OpKind::Update => {
                op.items = vec![hot.clone()];
                ("upd", mixed_params(&[("$slug", &hot)], &[("$n", n)]))
            }
            OpKind::Delete => {
                op.items = vec![hot.clone()];
                ("del", params(&[("$slug", &hot)]))
            }
            OpKind::Person => {
                op.person = Some(person_a.clone());
                ("person", params(&[("$name", &person_a)]))
            }
            OpKind::DeletePerson => {
                op.person = Some(person_a.clone());
                ("del_person", params(&[("$name", &person_a)]))
            }
            OpKind::Knows => {
                op.edge = Some((person_a.clone(), person_b.clone()));
                ("knows", params(&[("$from", &person_a), ("$to", &person_b)]))
            }
            OpKind::Multi => {
                op.items = vec![fresh.clone()];
                op.person = Some(person_a.clone());
                (
                    "multi",
                    mixed_params(&[("$slug", &fresh), ("$name", &person_a)], &[("$n", n)]),
                )
            }
            OpKind::Tag => {
                let label = format!("l{}", rng.below(cfg.hot));
                let slug = format!("t{}{writer}-{seq}", cfg.tag);
                op.tag = Some((slug.clone(), label.clone()));
                ("tag", params(&[("$slug", &slug), ("$label", &label)]))
            }
        };
        let cancel = cfg.cancel_pct > 0 && rng.pct(cfg.cancel_pct);
        let budget = Duration::from_millis(1 + rng.below(cfg.cancel_ms));
        op.invoked_us = shared.now_us();
        let call = session.mutate_as_with_receipt("main", QUERIES, name, &p, Some(&actor));
        let result = if cancel {
            match tokio::time::timeout(budget, call).await {
                Ok(result) => Some(result),
                Err(_) => None,
            }
        } else {
            Some(call.await)
        };
        op.completed_us = shared.now_us();
        match result {
            None => op.outcome = Outcome::InDoubt("cancelled".to_string()),
            Some(Ok(receipt)) => {
                op.affected = (receipt.result.affected_nodes, receipt.result.affected_edges);
                if let Some(commit) = &receipt.commit {
                    op.version = Some(commit.graph_manifest_version);
                    op.commit = Some(commit.graph_commit_id.clone());
                    op.commit_actor = commit.actor_id.clone();
                }
                op.outcome = Outcome::Ok;
            }
            Some(Err(error)) => op.outcome = classify(&error),
        }
        // Read-your-writes on this handle.
        if op.outcome == Outcome::Ok
            && let Some(version) = op.version
            && cfg.ryw_pct > 0
            && rng.pct(cfg.ryw_pct)
        {
            match observe(session, ReadTarget::branch("main")).await {
                Ok(obs) => {
                    let mut problems = Vec::new();
                    if obs.version < version {
                        problems.push(format!("read v{} below receipt v{version}", obs.version));
                    }
                    if matches!(kind, OpKind::Fresh | OpKind::Multi | OpKind::Pair)
                        && !obs.items.contains_key(&fresh)
                    {
                        problems.push(format!("own fresh key {fresh} missing at v{}", obs.version));
                    }
                    if !problems.is_empty() {
                        shared.ryw_violations.lock().unwrap().push(format!(
                            "op {} ({}): {}",
                            op.id,
                            kind.name(),
                            problems.join("; ")
                        ));
                    }
                }
                Err(error) => shared
                    .ryw_violations
                    .lock()
                    .unwrap()
                    .push(format!("op {}: read after write failed: {error}", op.id)),
            }
        }
        shared.ops.lock().unwrap().push(op);
    }
}

async fn run_reader(shared: Arc<Shared>, session: Session) {
    while !shared.stop.load(Ordering::Acquire) {
        match observe(&session, ReadTarget::branch("main")).await {
            Ok(obs) => shared.observations.lock().unwrap().push(obs),
            Err(error) => shared
                .maintenance
                .lock()
                .unwrap()
                .push(format!("reader error: {error}")),
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
}

async fn run_maintenance(shared: Arc<Shared>, session: Session) {
    let cfg = shared.cfg.clone();
    let mut next_optimize = cfg.optimize_secs;
    let mut next_index = cfg.index_secs;
    let mut next_cleanup = cfg.cleanup_secs;
    let mut next_branch = cfg.branch_secs;
    let mut schema_done = cfg.schema_at_secs == 0;
    let mut branch_seq = 0u64;
    let mut claimed = false;
    while !shared.stop.load(Ordering::Acquire) {
        tokio::time::sleep(Duration::from_millis(200)).await;
        let t = shared.start.elapsed().as_secs();
        let log = |line: String| {
            eprintln!("[gs-maint] t={t}s {line}");
            shared
                .maintenance
                .lock()
                .unwrap()
                .push(format!("t={t}s {line}"));
        };
        if cfg.optimize_secs > 0 && t >= next_optimize {
            next_optimize = t + cfg.optimize_secs;
            let started = Instant::now();
            match session.optimize_as(None, Default::default()).await {
                Ok(report) => log(
                    format!("optimize ok in {:?}: {report:?}", started.elapsed())
                        .chars()
                        .take(300)
                        .collect(),
                ),
                Err(error) => log(format!(
                    "optimize failed in {:?}: {error}",
                    started.elapsed()
                )),
            }
        }
        if cfg.index_secs > 0 && t >= next_index {
            next_index = t + cfg.index_secs;
            let started = Instant::now();
            match session.ensure_indices_as(None).await {
                Ok(pending) => log(format!(
                    "index build ok in {:?} (pending {})",
                    started.elapsed(),
                    pending.len()
                )),
                Err(error) => log(format!(
                    "index build failed in {:?}: {error}",
                    started.elapsed()
                )),
            }
        }
        if cfg.cleanup_secs > 0 && t >= next_cleanup {
            next_cleanup = t + cfg.cleanup_secs;
            let started = Instant::now();
            let options = omnigraph::db::CleanupPolicyOptions {
                keep_versions: Some(20),
                older_than: None,
            };
            match session.cleanup_as(options, None).await {
                Ok(stats) => log(format!(
                    "cleanup ok in {:?} ({} tables)",
                    started.elapsed(),
                    stats.len()
                )),
                Err(error) => log(format!(
                    "cleanup failed in {:?}: {error}",
                    started.elapsed()
                )),
            }
        }
        if cfg.branch_secs > 0 && t >= next_branch {
            next_branch = t + cfg.branch_secs;
            branch_seq += 1;
            let side = format!("side-{branch_seq}");
            let started = Instant::now();
            let outcome: omnigraph::error::Result<Option<u64>> = async {
                session.branch_create(&side).await?;
                let mut keys = Vec::new();
                for j in 0..3 {
                    let slug = format!("s{branch_seq}-{j}");
                    let n = 9_000_000 + (branch_seq as i64) * 10 + j;
                    session
                        .mutate(
                            &side,
                            QUERIES,
                            "ins",
                            &mixed_params(&[("$slug", &slug)], &[("$n", n)]),
                        )
                        .await?;
                    keys.push((slug, n));
                }
                let merged = session.branch_merge(&side, "main").await?;
                let version = merged.commit.as_ref().map(|c| c.graph_manifest_version);
                if let Some(version) = version {
                    shared.merges.lock().unwrap().push((version, keys));
                }
                session.branch_delete(&side).await?;
                Ok(version)
            }
            .await;
            match outcome {
                Ok(version) => log(format!(
                    "branch churn {side} ok in {:?}, merged at {version:?}",
                    started.elapsed()
                )),
                Err(error) => log(format!(
                    "branch churn {side} failed in {:?}: {error}",
                    started.elapsed()
                )),
            }
        }
        if cfg.claim_at_secs > 0 && t >= cfg.claim_at_secs && !claimed {
            claimed = true;
            let started = Instant::now();
            match session.claim_branch_writer("main").await {
                Ok(epoch) => log(format!(
                    "claimed main at epoch {epoch} in {:?}",
                    started.elapsed()
                )),
                Err(error) => log(format!("claim failed in {:?}: {error}", started.elapsed())),
            }
        }
        if !schema_done && t >= cfg.schema_at_secs {
            schema_done = true;
            let started = Instant::now();
            match session.apply_schema(SCHEMA_EVOLVED).await {
                Ok(_) => log(format!("schema apply ok in {:?}", started.elapsed())),
                Err(error) => log(format!(
                    "schema apply failed in {:?}: {error}",
                    started.elapsed()
                )),
            }
        }
    }
}

/// The model state: Item slug -> n, people, edges as a multiset.
#[derive(Clone, Default, Debug)]
pub struct Model {
    pub items: BTreeMap<String, i64>,
    pub people: BTreeSet<String>,
    pub edges: BTreeMap<(String, String), usize>,
}

impl Model {
    fn apply(&mut self, op: &Op) {
        match op.kind {
            OpKind::Fresh | OpKind::Hot | OpKind::Pair => {
                for key in &op.items {
                    self.items.insert(key.clone(), op.n);
                }
            }
            OpKind::Multi => {
                for key in &op.items {
                    self.items.insert(key.clone(), op.n);
                }
                if let Some(person) = &op.person {
                    self.people.insert(person.clone());
                }
            }
            OpKind::Update => {
                for key in &op.items {
                    if let Some(value) = self.items.get_mut(key) {
                        *value = op.n;
                    }
                }
            }
            OpKind::Delete => {
                for key in &op.items {
                    self.items.remove(key);
                }
            }
            OpKind::Person => {
                if let Some(person) = &op.person {
                    self.people.insert(person.clone());
                }
            }
            OpKind::DeletePerson => {
                if let Some(person) = &op.person {
                    self.people.remove(person);
                    self.edges
                        .retain(|(src, dst), _| src != person && dst != person);
                }
            }
            OpKind::Knows => {
                if let Some(edge) = &op.edge {
                    *self.edges.entry(edge.clone()).or_default() += 1;
                }
            }
            OpKind::Tag => {}
        }
    }
}

/// Keys an op may have touched: for in-doubt ops these become uncertain.
fn touched(op: &Op) -> (Vec<String>, Vec<String>) {
    let mut people = Vec::new();
    if let Some(person) = &op.person {
        people.push(person.clone());
    }
    if let Some((a, b)) = &op.edge {
        people.push(a.clone());
        people.push(b.clone());
    }
    (op.items.clone(), people)
}

pub struct Report {
    pub violations: Vec<String>,
    pub summary: serde_json::Value,
}

/// Global invariants that hold at every committed version.
fn invariants(obs: &Observation, label: &str, out: &mut Vec<String>) {
    for (slug, values) in &obs.items {
        if values.len() > 1 {
            out.push(format!(
                "{label}: duplicate Item key {slug} x{} {values:?}",
                values.len()
            ));
        }
    }
    for (name, count) in &obs.people {
        if *count > 1 {
            out.push(format!("{label}: duplicate Person key {name} x{count}"));
        }
    }
    for (src, dst) in &obs.edges {
        if !obs.people.contains_key(src) || !obs.people.contains_key(dst) {
            out.push(format!("{label}: dangling edge {src}->{dst}"));
        }
    }
    let mut labels: HashMap<&str, usize> = HashMap::new();
    for (_, tag_label) in &obs.tags {
        *labels.entry(tag_label.as_str()).or_default() += 1;
    }
    for (tag_label, count) in labels {
        if count > 1 {
            out.push(format!(
                "{label}: @unique Tag label {tag_label} held {count} times"
            ));
        }
    }
}

pub async fn check(shared: &Shared, uri: &str) -> Report {
    let mut violations = Vec::new();
    let ops = shared.ops.lock().unwrap().clone();
    let fresh = Omnigraph::open(uri)
        .await
        .expect("reopen graph for checking");
    let final_obs = observe(&fresh, ReadTarget::branch("main"))
        .await
        .expect("final observation");
    invariants(&final_obs, "final", &mut violations);

    // Receipts against the commit list.
    let commits = fresh.list_commits(None).await.expect("list commits");
    let _by_version: HashMap<u64, &omnigraph::db::GraphCommit> = commits
        .iter()
        .map(|c| (c.graph_manifest_version, c))
        .collect();
    let by_id: HashMap<&str, &omnigraph::db::GraphCommit> = commits
        .iter()
        .map(|c| (c.graph_commit_id.as_str(), c))
        .collect();
    let mut sorted: Vec<&omnigraph::db::GraphCommit> = commits.iter().collect();
    sorted.sort_by_key(|c| c.graph_manifest_version);
    for pair in sorted.windows(2) {
        if pair[1].parent_commit_id.as_deref() != Some(pair[0].graph_commit_id.as_str()) {
            violations.push(format!(
                "lineage: commit v{} parent {:?} is not v{} {}",
                pair[1].graph_manifest_version,
                pair[1].parent_commit_id,
                pair[0].graph_manifest_version,
                pair[0].graph_commit_id
            ));
        }
    }
    let mut writes_per_commit: HashMap<String, usize> = HashMap::new();
    for op in ops.iter().filter(|op| op.outcome == Outcome::Ok) {
        let Some(commit_id) = &op.commit else {
            continue;
        };
        *writes_per_commit.entry(commit_id.clone()).or_default() += 1;
        match by_id.get(commit_id.as_str()) {
            None => violations.push(format!(
                "op {}: receipt commit {commit_id} not in commit list",
                op.id
            )),
            Some(commit) => {
                if Some(commit.graph_manifest_version) != op.version {
                    violations.push(format!(
                        "op {}: receipt v{:?} but the commit is v{}",
                        op.id, op.version, commit.graph_manifest_version
                    ));
                }
                // A batch commit of several writes is checked by the
                // attribution oracle below.
                if commit.writes.is_empty() && commit.actor_id.as_deref() != Some(op.actor.as_str())
                {
                    violations.push(format!(
                        "op {}: actor {} but commit {} records {:?}",
                        op.id, op.actor, commit_id, commit.actor_id
                    ));
                }
            }
        }
    }

    // Uncertain keys: anything an in-doubt op may have touched.
    let mut uncertain_items: HashSet<String> = HashSet::new();
    let mut uncertain_people: HashSet<String> = HashSet::new();
    for op in ops
        .iter()
        .filter(|op| matches!(op.outcome, Outcome::InDoubt(_)))
    {
        let (items, people) = touched(op);
        uncertain_items.extend(items);
        uncertain_people.extend(people);
    }

    // Model replay in version order; one version holds one batch.
    let mut by_ver: BTreeMap<u64, Vec<&Op>> = BTreeMap::new();
    for op in ops.iter().filter(|op| op.outcome == Outcome::Ok) {
        if let Some(version) = op.version {
            by_ver.entry(version).or_default().push(op);
        }
    }
    let merges = shared.merges.lock().unwrap().clone();
    let mut merge_at: BTreeMap<u64, Vec<(String, i64)>> = BTreeMap::new();
    for (version, keys) in merges {
        merge_at.entry(version).or_default().extend(keys);
    }
    // Within one batch, no two writes may touch one key (admission rule 4).
    for (version, batch) in &by_ver {
        if batch.len() < 2 {
            continue;
        }
        let mut seen: HashMap<&str, u64> = HashMap::new();
        for op in batch {
            for key in &op.items {
                if let Some(other) = seen.insert(key.as_str(), op.id) {
                    violations.push(format!(
                        "v{version}: ops {other} and {} share key {key} in one commit",
                        op.id
                    ));
                }
            }
        }
    }
    let mut observations = shared.observations.lock().unwrap().clone();
    observations.sort_by_key(|obs| obs.version);
    let mut model = Model::default();
    // The seed state: everything at the first observed version is unknown to
    // the model, so the seed is loaded before the clock and is empty here.
    let versions: BTreeSet<u64> = by_ver.keys().chain(merge_at.keys()).copied().collect();
    let mut obs_iter = observations.iter().peekable();
    let compare = |model: &Model, obs: &Observation, out: &mut Vec<String>| {
        let label = format!("snapshot v{}", obs.version);
        invariants(obs, &label, out);
        let mut diffs = Vec::new();
        let keys: BTreeSet<&String> = model.items.keys().chain(obs.items.keys()).collect();
        for key in keys {
            if uncertain_items.contains(key) || key.starts_with("seed") {
                continue;
            }
            let want = model.items.get(key);
            let got = obs.items.get(key).and_then(|values| values.first());
            if want != got {
                diffs.push(format!("{key}: model {want:?} got {got:?}"));
            }
        }
        let people: BTreeSet<&String> = model.people.iter().chain(obs.people.keys()).collect();
        for person in people {
            if uncertain_people.contains(person) || person.starts_with("seed") {
                continue;
            }
            if model.people.contains(person) != obs.people.contains_key(person) {
                diffs.push(format!(
                    "person {person}: model {} got {}",
                    model.people.contains(person),
                    obs.people.contains_key(person)
                ));
            }
        }
        let mut got_edges: BTreeMap<(String, String), usize> = BTreeMap::new();
        for edge in &obs.edges {
            *got_edges.entry(edge.clone()).or_default() += 1;
        }
        let pairs: BTreeSet<&(String, String)> =
            model.edges.keys().chain(got_edges.keys()).collect();
        for pair in pairs {
            if uncertain_people.contains(&pair.0) || uncertain_people.contains(&pair.1) {
                continue;
            }
            let want = model.edges.get(pair).copied().unwrap_or(0);
            let got = got_edges.get(pair).copied().unwrap_or(0);
            if want != got {
                diffs.push(format!(
                    "edge {}->{}: model {want} got {got}",
                    pair.0, pair.1
                ));
            }
        }
        if !diffs.is_empty() {
            out.push(format!(
                "{label}: {} differences from the model, first {:?}",
                diffs.len(),
                diffs.iter().take(6).collect::<Vec<_>>()
            ));
        }
    };
    let first_version = versions.iter().next().copied().unwrap_or(0);
    for version in &versions {
        // Observations strictly before this version see the model so far.
        while let Some(obs) = obs_iter.peek() {
            if obs.version >= *version {
                break;
            }
            if obs.version >= first_version {
                compare(&model, obs, &mut violations);
            }
            obs_iter.next();
        }
        if let Some(batch) = by_ver.get(version) {
            for op in batch {
                model.apply(op);
            }
        }
        if let Some(keys) = merge_at.get(version) {
            for (key, n) in keys {
                model.items.insert(key.clone(), *n);
            }
        }
    }
    for obs in obs_iter {
        compare(&model, obs, &mut violations);
    }
    compare(&model, &final_obs, &mut violations);
    // Attribution of batch commits (RFC §Attribution).
    let attr_ops: Vec<super::attribution::AttrOp> = ops
        .iter()
        .map(|op| super::attribution::AttrOp {
            id: op.id,
            actor: Some(op.actor.clone()),
            outcome: op.outcome.label().to_string(),
            commit: op.commit.clone(),
            rows: super::attribution::inserted_rows(
                op.kind.name(),
                &op.items,
                op.person.as_deref(),
                op.edge.as_ref().map(|(a, b)| (a.as_str(), b.as_str())),
                op.tag.as_ref().map(|t| t.0.as_str()),
            ),
        })
        .collect();
    let attribution = super::attribution::check(&fresh, &attr_ops).await;
    violations.extend(
        attribution
            .violations
            .iter()
            .map(|line| format!("attribution: {line}")),
    );
    let ryw = shared.ryw_violations.lock().unwrap().clone();
    violations.extend(
        ryw.into_iter()
            .map(|line| format!("read-your-writes: {line}")),
    );

    // Summary.
    let secs = shared.cfg.secs.max(1) as f64;
    let ok = ops.iter().filter(|op| op.outcome == Outcome::Ok).count();
    let refused = ops
        .iter()
        .filter(|op| matches!(op.outcome, Outcome::Refused(_)))
        .count();
    let in_doubt = ops
        .iter()
        .filter(|op| matches!(op.outcome, Outcome::InDoubt(_)))
        .count();
    let mut latencies: Vec<u64> = ops
        .iter()
        .filter(|op| op.outcome == Outcome::Ok)
        .map(|op| op.completed_us - op.invoked_us)
        .collect();
    latencies.sort_unstable();
    let pct = |q: f64| -> u64 {
        if latencies.is_empty() {
            0
        } else {
            latencies[((q * latencies.len() as f64).ceil() as usize).clamp(1, latencies.len()) - 1]
        }
    };
    let mut by_kind: BTreeMap<&str, (usize, usize, usize)> = BTreeMap::new();
    for op in &ops {
        let entry = by_kind.entry(op.kind.name()).or_default();
        match op.outcome {
            Outcome::Ok => entry.0 += 1,
            Outcome::Refused(_) => entry.1 += 1,
            Outcome::InDoubt(_) => entry.2 += 1,
        }
    }
    let mut refusal_texts: BTreeMap<String, usize> = BTreeMap::new();
    for op in &ops {
        if let Outcome::Refused(text) | Outcome::InDoubt(text) = &op.outcome {
            let short: String = text.chars().take(90).collect();
            *refusal_texts.entry(short).or_default() += 1;
        }
    }
    let batched: Vec<usize> = writes_per_commit.values().copied().collect();
    let max_batch = batched.iter().copied().max().unwrap_or(0);
    let summary = serde_json::json!({
        "writers": shared.cfg.writers, "secs": shared.cfg.secs, "seed": shared.cfg.seed,
        "handles": shared.cfg.handles, "actors": shared.cfg.actors,
        "cancel_pct": shared.cfg.cancel_pct,
        "ops": ops.len(), "ok": ok, "refused": refused, "in_doubt": in_doubt,
        "ok_per_sec": ok as f64 / secs,
        "commits": writes_per_commit.len(),
        "writes_per_commit": if writes_per_commit.is_empty() { 0.0 } else { batched.iter().sum::<usize>() as f64 / batched.len() as f64 },
        "max_batch": max_batch,
        "p50_ms": pct(0.50) as f64 / 1000.0, "p99_ms": pct(0.99) as f64 / 1000.0,
        "max_ms": latencies.last().copied().unwrap_or(0) as f64 / 1000.0,
        "observations": observations.len(),
        "by_kind": by_kind.iter().map(|(k, (a, b, c))| (k.to_string(), serde_json::json!([a, b, c]))).collect::<serde_json::Map<_, _>>(),
        "errors": refusal_texts.iter().map(|(k, v)| (k.clone(), serde_json::json!(v))).collect::<serde_json::Map<_, _>>(),
        "violations": violations.len(),
        "attribution": attribution.summary,
    });
    if let Some(path) = &shared.cfg.out {
        use std::io::Write as _;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .expect("open OUT");
        for op in &ops {
            let line = serde_json::json!({
                "type": "op", "id": op.id, "writer": op.writer, "handle": op.handle, "actor": op.actor,
                "kind": op.kind.name(), "items": op.items, "person": op.person, "edge": op.edge,
                "tag": op.tag, "n": op.n, "invoked_us": op.invoked_us, "completed_us": op.completed_us,
                "outcome": op.outcome.label(),
                "error": match &op.outcome { Outcome::Ok => None, Outcome::Refused(t) | Outcome::InDoubt(t) => Some(t.clone()) },
                "version": op.version, "commit": op.commit, "commit_actor": op.commit_actor,
                "affected": [op.affected.0, op.affected.1],
            });
            writeln!(file, "{line}").unwrap();
        }
        for line in shared.maintenance.lock().unwrap().iter() {
            writeln!(
                file,
                "{}",
                serde_json::json!({"type": "maintenance", "line": line})
            )
            .unwrap();
        }
        for violation in &violations {
            writeln!(
                file,
                "{}",
                serde_json::json!({"type": "violation", "line": violation})
            )
            .unwrap();
        }
        writeln!(
            file,
            "{}",
            serde_json::json!({"type": "summary", "summary": summary})
        )
        .unwrap();
    }
    Report {
        violations,
        summary,
    }
}

pub fn seed_jsonl(cfg: &Config) -> String {
    let mut out = String::new();
    for i in 0..cfg.hot / 2 {
        out.push_str(&format!(
            "{{\"type\":\"Item\",\"data\":{{\"slug\":\"h{i}\",\"n\":{i}}}}}\n"
        ));
    }
    for i in 0..cfg.people / 2 {
        out.push_str(&format!(
            "{{\"type\":\"Person\",\"data\":{{\"name\":\"p{i}\"}}}}\n"
        ));
    }
    out
}

pub async fn run(cfg: Config) -> Report {
    let dir = tempfile::tempdir().unwrap();
    let uri = if cfg.target == "local" {
        dir.path().join("graph").to_string_lossy().into_owned()
    } else {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        format!(
            "{}/gs-{}-{nanos}",
            cfg.target.trim_end_matches('/'),
            cfg.seed
        )
    };
    let multi = cfg.uri.is_some();
    let uri = cfg.uri.clone().unwrap_or(uri);
    let base = if multi {
        Session::from_defaults(
            Arc::new(Omnigraph::open(&uri).await.expect("open existing root")),
            SessionSettings::default(),
        )
    } else {
        let base = Omnigraph::init(&uri, SCHEMA).await.expect("init");
        let base = Session::from_defaults(Arc::new(base), SessionSettings::default());
        base.load_jsonl(&seed_jsonl(&cfg), omnigraph::loader::LoadMode::Overwrite)
            .await
            .expect("seed load");
        base.ensure_indices_as(None).await.expect("indices");
        base
    };
    // The seed is part of the model's starting state.
    let seed_obs = observe(&base, ReadTarget::branch("main"))
        .await
        .expect("seed observation");
    let mut sessions = vec![base];
    for _ in 1..cfg.handles {
        let handle = Omnigraph::open(&uri).await.expect("open extra handle");
        sessions.push(Session::from_defaults(
            Arc::new(handle),
            SessionSettings::default(),
        ));
    }
    let sessions = Arc::new(sessions);
    let shared = Arc::new(Shared {
        cfg: cfg.clone(),
        start: Instant::now(),
        stop: AtomicBool::new(false),
        ops: Mutex::new(Vec::new()),
        next_id: AtomicU64::new(0),
        observations: Mutex::new(Vec::new()),
        ryw_violations: Mutex::new(Vec::new()),
        maintenance: Mutex::new(Vec::new()),
        merges: Mutex::new(Vec::new()),
    });
    // Seed rows enter the model as ops at the seed version.
    {
        let mut ops = shared.ops.lock().unwrap();
        for (slug, values) in &seed_obs.items {
            let id = u64::MAX - ops.len() as u64;
            ops.push(Op {
                id,
                writer: usize::MAX,
                handle: 0,
                actor: "seed".to_string(),
                kind: OpKind::Hot,
                items: vec![slug.clone()],
                person: None,
                edge: None,
                tag: None,
                n: values[0],
                invoked_us: 0,
                completed_us: 0,
                outcome: Outcome::Ok,
                version: Some(seed_obs.version),
                commit: None,
                commit_actor: None,
                affected: (1, 0),
            });
        }
        for name in seed_obs.people.keys() {
            let id = u64::MAX - ops.len() as u64;
            ops.push(Op {
                id,
                writer: usize::MAX,
                handle: 0,
                actor: "seed".to_string(),
                kind: OpKind::Person,
                items: Vec::new(),
                person: Some(name.clone()),
                edge: None,
                tag: None,
                n: 0,
                invoked_us: 0,
                completed_us: 0,
                outcome: Outcome::Ok,
                version: Some(seed_obs.version),
                commit: None,
                commit_actor: None,
                affected: (1, 0),
            });
        }
    }
    let mut tasks = Vec::new();
    for writer in 0..cfg.writers {
        tasks.push(tokio::spawn(run_writer(
            Arc::clone(&shared),
            Arc::clone(&sessions),
            writer,
        )));
    }
    for reader in 0..cfg.readers {
        let session = sessions[reader % sessions.len()].clone();
        tasks.push(tokio::spawn(run_reader(Arc::clone(&shared), session)));
    }
    let maintenance = tokio::spawn(run_maintenance(Arc::clone(&shared), sessions[0].clone()));
    tokio::time::sleep(Duration::from_secs(cfg.secs)).await;
    shared.stop.store(true, Ordering::Release);
    // A hung group never returns: bound the drain and report it.
    let drain = async {
        for task in tasks {
            let _ = task.await;
        }
        let _ = maintenance.await;
    };
    let mut hung = false;
    if tokio::time::timeout(Duration::from_secs(120), drain)
        .await
        .is_err()
    {
        hung = true;
    }
    if multi {
        // Histories are checked together offline; write this one out.
        let mut report = write_history_only(&shared).await;
        if hung {
            report
                .violations
                .push("liveness: writers did not finish within 120 s of the stop".to_string());
        }
        return report;
    }
    let mut report = check(&shared, &uri).await;
    if hung {
        report
            .violations
            .push("liveness: writers did not finish within 120 s of the stop".to_string());
    }
    report
}

/// Multi-process mode: the history without the in-process model check.
pub async fn write_history_only(shared: &Shared) -> Report {
    use std::io::Write as _;
    let ops = shared.ops.lock().unwrap().clone();
    let ok = ops.iter().filter(|op| op.outcome == Outcome::Ok).count();
    let mut errors: BTreeMap<String, usize> = BTreeMap::new();
    for op in &ops {
        if let Outcome::Refused(text) | Outcome::InDoubt(text) = &op.outcome {
            *errors.entry(text.chars().take(90).collect()).or_default() += 1;
        }
    }
    let summary = serde_json::json!({
        "ops": ops.len(), "ok": ok,
        "refused": ops.iter().filter(|op| matches!(op.outcome, Outcome::Refused(_))).count(),
        "in_doubt": ops.iter().filter(|op| matches!(op.outcome, Outcome::InDoubt(_))).count(),
        "errors": errors,
    });
    if let Some(path) = &shared.cfg.out {
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .expect("open OUT");
        for op in ops.iter().filter(|op| op.writer != usize::MAX) {
            let line = serde_json::json!({
                "type": "op", "id": format!("{}:{}", shared.cfg.tag, op.id), "writer": op.writer, "actor": op.actor,
                "kind": op.kind.name(), "items": op.items, "person": op.person, "edge": op.edge,
                "tag": op.tag, "n": op.n, "invoked_us": op.invoked_us, "completed_us": op.completed_us,
                "outcome": op.outcome.label(),
                "error": match &op.outcome { Outcome::Ok => None, Outcome::Refused(t) | Outcome::InDoubt(t) => Some(t.clone()) },
                "version": op.version, "commit": op.commit, "commit_actor": op.commit_actor,
            });
            writeln!(file, "{line}").unwrap();
        }
        for line in shared.maintenance.lock().unwrap().iter() {
            writeln!(
                file,
                "{}",
                serde_json::json!({"type": "maintenance", "line": line})
            )
            .unwrap();
        }
        writeln!(
            file,
            "{}",
            serde_json::json!({"type": "summary", "summary": summary})
        )
        .unwrap();
    }
    Report {
        violations: Vec::new(),
        summary,
    }
}
