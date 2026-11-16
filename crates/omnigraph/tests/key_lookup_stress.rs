#![cfg(feature = "failpoints")]
#![recursion_limit = "512"]
//! Research (`research/key-lookup-stress`): the cap-free two-part key lookup
//! of RFC "Group commit with known-key admission" (Part 2, "Key lookups" and
//! "Known keys") under stress.
//!
//! - `kl_equivalence_randomized`: a seeded random workload over every key
//!   space (ids, a single and a composite `@unique` with nulls, edge `src`,
//!   `dst`, id → src under a bounded `@card`), with deletes, column patches,
//!   upserts, loads, compaction, index folds mid-run, map eviction, a
//!   simulated restart, files another process wrote, schema evolution (added
//!   nullable column, renamed `@unique` column). After every step the
//!   two-part lookup must equal a whole-table scan in every space, on every
//!   table, and the write oracles must hold (no duplicate key, `@unique`
//!   held, no dangling edge, `@card` held, no refusal a model does not
//!   predict). `OG_KL_SEED`, `OG_KL_STEPS` vary it.
//! - `kl_system_column_upgrade_keeps_known_keys`: the legacy `id` spelling,
//!   renamed in place to `__id`, keeps the files and their known keys.
//! - `kl_btree_staleness_*`: rows updated or deleted inside covered fragments,
//!   upserts, compaction and a lagging index, on the graph path; and the
//!   Lance-level delta-segment case no graph writer produces, with what Lance
//!   itself returns.
//! - `kl_child_writer`: the "another process" helper (no-op unless
//!   `KL_CHILD_URI` is set).

mod helpers;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;

use arrow_array::Array;
use arrow_array::cast::AsArray;
use arrow_array::{RecordBatch, RecordBatchIterator, StringArray};
use arrow_schema::{DataType, Field, Schema};
use futures::TryStreamExt;
use lance::Dataset;
use lance::dataset::{MergeInsertBuilder, WhenMatched, WhenNotMatched, WriteMode, WriteParams};
use lance::index::DatasetIndexExt;
use lance_file::version::LanceFileVersion;
use lance_index::IndexType;
use lance_index::optimize::OptimizeOptions;
use lance_index::scalar::ScalarIndexParams;
use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph::key_lookup;
use omnigraph::loader::LoadMode;
use serial_test::serial;

use helpers::{Session, session};

fn schema_source(email: &str, extra: bool) -> String {
    let rename = if email == "email" {
        String::new()
    } else {
        " @rename_from(\"email\")".to_string()
    };
    let extra = if extra { "    extra: String?\n" } else { "" };
    format!(
        "node Item {{\n    slug: String @key\n    {email}: String? @unique{rename}\n    n: I64\n    body: String?\n    emb: Vector(32)?\n{extra}}}\n\
         node Pair {{\n    k: String @key\n    a: String?\n    b: I64?\n    @unique(a, b)\n}}\n\
         node Person {{\n    name: String @key\n}}\n\
         edge Follows: Person -> Person @card(0..3)\n"
    )
}

fn queries(email: &str) -> String {
    format!(
        r#"
query set_n($slug: String, $n: I64) {{
    update Item set {{ n: $n }} where slug = $slug
}}
query set_email($slug: String, $email: String) {{
    update Item set {{ {email}: $email }} where slug = $slug
}}
query del_item($slug: String) {{
    delete Item where slug = $slug
}}
query follow($from: String, $to: String) {{
    insert Follows {{ from: $from, to: $to }}
}}
query del_person($name: String) {{
    delete Person where name = $name
}}
query del_pair($k: String) {{
    delete Pair where k = $k
}}
"#
    )
}

/// A seeded xorshift.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }
    fn chance(&mut self, percent: u64) -> bool {
        self.next() % 100 < percent
    }
}

fn quote(value: &str) -> String {
    format!("\"{value}\"")
}

/// The model: what the graph must hold.
#[derive(Default)]
struct Model {
    items: BTreeMap<String, (Option<String>, i64)>,
    pairs: BTreeMap<String, (Option<String>, Option<i64>)>,
    persons: HashSet<String>,
    /// Edge (src, dst) pairs, with multiplicity.
    follows: Vec<(String, String)>,
    /// Keys no longer held (or never), per table and space, for the
    /// staleness side of the comparison.
    gone: HashMap<String, HashMap<Vec<String>, Vec<Vec<String>>>>,
}

impl Model {
    fn gone(&mut self, table: &str, columns: &[&str], key: Vec<String>) {
        self.gone
            .entry(table.to_string())
            .or_default()
            .entry(columns.iter().map(|c| c.to_string()).collect())
            .or_default()
            .push(key);
    }
    #[allow(dead_code)]
    fn email_holder(&self, email: &str) -> Option<&String> {
        self.items
            .iter()
            .find(|(_, (e, _))| e.as_deref() == Some(email))
            .map(|(slug, _)| slug)
    }
    fn pair_holder(&self, a: &str, b: i64) -> bool {
        self.pairs
            .values()
            .any(|(pa, pb)| pa.as_deref() == Some(a) && *pb == Some(b))
    }
    fn out_degree(&self, src: &str) -> usize {
        self.follows.iter().filter(|(s, _)| s == src).count()
    }
}

#[derive(Default, Debug)]
struct Coverage {
    checks: usize,
    keys: usize,
    map_fragments: usize,
    scanned_fragments: usize,
    covered_spaces: usize,
    deletion_fragments: usize,
    multi_file_fragments: usize,
    refusals_expected: usize,
    events: BTreeMap<&'static str, usize>,
}

async fn check_all(
    db: &Session,
    model: &Model,
    email: &str,
    step: usize,
    seed: u64,
    coverage: &mut Coverage,
) {
    let snapshot = db.snapshot_of(ReadTarget::branch("main")).await.unwrap();
    let catalog = db.catalog();
    for table in ["node:Item", "node:Pair", "node:Person", "edge:Follows"] {
        let mut extra = model.gone.get(table).cloned().unwrap_or_default();
        // The renamed column's gone keys were recorded under the old name.
        if email != "email"
            && let Some(old) = extra.remove(&vec!["email".to_string()])
        {
            extra.insert(vec![email.to_string()], old);
        }
        let report = key_lookup::differential_check(
            &snapshot,
            &catalog,
            table,
            &extra,
            64,
            seed ^ (step as u64) << 8,
        )
        .await
        .unwrap();
        assert!(
            report.mismatches.is_empty(),
            "step {step} seed {seed}: two-part lookup differs from a full scan: {:#?}",
            report.mismatches
        );
        coverage.checks += report.spaces;
        coverage.keys += report.wanted_keys;
        coverage.map_fragments += report.map_fragments;
        coverage.scanned_fragments += report.scanned_fragments;
        coverage.covered_spaces += report.covered_spaces;
        coverage.deletion_fragments += report.deletion_fragments;
        coverage.multi_file_fragments += report.multi_file_fragments;
    }
}

/// The graph equals the model: every item, pair, person and edge.
async fn check_state(db: &Session, model: &Model, email: &str, step: usize) {
    let batches = helpers::read_table(db.db(), "node:Item").await;
    let mut items = BTreeMap::new();
    for batch in &batches {
        let slugs = batch.column_by_name("slug").unwrap().as_string::<i32>();
        let emails = batch.column_by_name(email).unwrap().as_string::<i32>();
        let ns = batch
            .column_by_name("n")
            .unwrap()
            .as_primitive::<arrow_array::types::Int64Type>();
        for row in 0..batch.num_rows() {
            let prior = items.insert(
                slugs.value(row).to_string(),
                (
                    emails.is_valid(row).then(|| emails.value(row).to_string()),
                    ns.value(row),
                ),
            );
            assert!(prior.is_none(), "step {step}: duplicate slug {}", slugs.value(row));
        }
    }
    assert_eq!(items, model.items, "step {step}: items differ from the model");
    let mut seen = HashSet::new();
    for (email, _) in items.values() {
        if let Some(email) = email {
            assert!(seen.insert(email.clone()), "step {step}: @unique email {email} held twice");
        }
    }
    let batches = helpers::read_table(db.db(), "node:Pair").await;
    let mut pairs = BTreeMap::new();
    for batch in &batches {
        let ks = batch.column_by_name("k").unwrap().as_string::<i32>();
        let a = batch.column_by_name("a").unwrap().as_string::<i32>();
        let b = batch
            .column_by_name("b")
            .unwrap()
            .as_primitive::<arrow_array::types::Int64Type>();
        for row in 0..batch.num_rows() {
            pairs.insert(
                ks.value(row).to_string(),
                (
                    a.is_valid(row).then(|| a.value(row).to_string()),
                    b.is_valid(row).then(|| b.value(row)),
                ),
            );
        }
    }
    assert_eq!(pairs, model.pairs, "step {step}: pairs differ from the model");
    let persons: HashSet<String> = helpers::collect_column_strings(
        &helpers::read_table(db.db(), "node:Person").await,
        "name",
    )
    .into_iter()
    .collect();
    assert_eq!(persons, model.persons, "step {step}: persons differ");
    let batches = helpers::read_table(db.db(), "edge:Follows").await;
    let mut follows = Vec::new();
    let catalog = db.catalog();
    let (src_col, dst_col) = (catalog.system_columns.src, catalog.system_columns.dst);
    for batch in &batches {
        let s = batch.column_by_name(src_col).unwrap().as_string::<i32>();
        let d = batch.column_by_name(dst_col).unwrap().as_string::<i32>();
        for row in 0..batch.num_rows() {
            let (s, d) = (s.value(row).to_string(), d.value(row).to_string());
            assert!(
                persons.contains(&s) && persons.contains(&d),
                "step {step}: dangling edge {s} -> {d}"
            );
            follows.push((s, d));
        }
    }
    follows.sort();
    let mut expected = model.follows.clone();
    expected.sort();
    assert_eq!(follows, expected, "step {step}: edges differ");
    let mut degree: HashMap<&str, usize> = HashMap::new();
    for (s, _) in &follows {
        *degree.entry(s).or_default() += 1;
    }
    assert!(
        degree.values().all(|d| *d <= 3),
        "step {step}: @card(0..3) broken"
    );
}

fn id_of(catalog: &omnigraph_compiler::catalog::Catalog) -> &'static str {
    catalog.system_columns.id
}

/// Run the child-process writer: `n` fresh items with `prefix` slugs.
fn run_child(uri: &str, prefix: &str, n: usize) {
    let status = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["kl_child_writer", "--exact", "--nocapture", "--test-threads=1"])
        .env("KL_CHILD_URI", uri)
        .env("KL_CHILD_PREFIX", prefix)
        .env("KL_CHILD_N", n.to_string())
        .status()
        .unwrap();
    assert!(status.success(), "child writer failed");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn kl_child_writer() {
    let Ok(uri) = std::env::var("KL_CHILD_URI") else {
        return;
    };
    let prefix = std::env::var("KL_CHILD_PREFIX").unwrap();
    let n: usize = std::env::var("KL_CHILD_N").unwrap().parse().unwrap();
    let db = session(Omnigraph::open(&uri).await.unwrap());
    for i in 0..n {
        let source = format!(
            "query q() {{ insert Item {{ slug: \"{prefix}-{i}\", n: {i} }} }}"
        );
        db.mutate("main", &source, "q", &Default::default())
            .await
            .unwrap();
    }
    // A Follows edge and a Pair, so the child's files cover those spaces too.
    let source = format!(
        "query q() {{ insert Pair {{ k: \"{prefix}-pair\", a: \"{prefix}\", b: 1 }} }}"
    );
    db.mutate("main", &source, "q", &Default::default())
        .await
        .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn kl_equivalence_randomized() {
    let seed: u64 = std::env::var("OG_KL_SEED")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(7);
    let steps: usize = std::env::var("OG_KL_STEPS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(160);
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    let mut email = "email".to_string();
    let mut extra = false;
    let db = session(
        Omnigraph::init(&uri, &schema_source(&email, extra))
            .await
            .unwrap(),
    );
    let mut rng = Rng(seed.wrapping_mul(0x2545_f491_4f6c_dd1d) | 1);
    let mut model = Model::default();
    let mut coverage = Coverage::default();
    let mut next_id = 0usize;
    let mut db = Arc::new(db);

    for step in 0..steps {
        let op = rng.below(100);
        let q = queries(&email);
        let event: &'static str;
        if op < 22 {
            // A batch of fresh items through the absence-proven writer.
            event = "insert_items";
            let k = 1 + rng.below(12);
            let mut body = String::new();
            let mut planned = Vec::new();
            for _ in 0..k {
                next_id += 1;
                let slug = format!("i{next_id}");
                let mail = (!rng.chance(25)).then(|| format!("e{next_id}"));
                let n = rng.below(1000) as i64;
                // A wide column, so an update of `n` or the email is
                // cheaper as a column patch than as a row rewrite.
                let wide = "w".repeat(400 + rng.below(400));
                let emb = format!(
                    "[{}]",
                    (0..32).map(|j| format!("{}.5", (j + next_id) % 9)).collect::<Vec<_>>().join(", ")
                );
                match &mail {
                    Some(m) => body.push_str(&format!(
                        "    insert Item {{ slug: {}, {email}: {}, n: {n}, body: {}, emb: {emb} }}\n",
                        quote(&slug),
                        quote(m),
                        quote(&wide)
                    )),
                    None => body.push_str(&format!(
                        "    insert Item {{ slug: {}, n: {n}, body: {}, emb: {emb} }}\n",
                        quote(&slug),
                        quote(&wide)
                    )),
                }
                planned.push((slug, mail, n));
            }
            let source = format!("query q() {{\n{body}}}");
            db.mutate("main", &source, "q", &Default::default())
                .await
                .unwrap();
            for (slug, mail, n) in planned {
                model.items.insert(slug, (mail, n));
            }
        } else if op < 27 {
            // A colliding insert: an existing email under a new slug must be
            // refused, a reused slug upserts (all present).
            event = "insert_collide";
            if let Some((slug, (mail, _))) = model
                .items
                .iter()
                .nth(rng.below(model.items.len()))
                .map(|(s, v)| (s.clone(), v.clone()))
            {
                if let Some(mail) = mail {
                    next_id += 1;
                    let source = format!(
                        "query q() {{ insert Item {{ slug: \"i{next_id}\", {email}: {}, n: 1 }} }}",
                        quote(&mail)
                    );
                    let result = db.mutate("main", &source, "q", &Default::default()).await;
                    assert!(
                        result.is_err(),
                        "step {step}: a duplicate @unique email {mail} was accepted"
                    );
                    coverage.refusals_expected += 1;
                }
                // Upsert of an existing slug with a new n (all present).
                let n = rng.below(1000) as i64;
                let keep = model.items[&slug].0.clone();
                let source = match &keep {
                    Some(m) => format!(
                        "query q() {{ insert Item {{ slug: {}, {email}: {}, n: {n} }} }}",
                        quote(&slug),
                        quote(m)
                    ),
                    None => format!(
                        "query q() {{ insert Item {{ slug: {}, n: {n} }} }}",
                        quote(&slug)
                    ),
                };
                db.mutate("main", &source, "q", &Default::default())
                    .await
                    .unwrap();
                model.items.insert(slug, (keep, n));
            }
        } else if op < 35 {
            // Column patch of n (second data file in the fragment).
            event = "patch_n";
            if let Some(slug) = model.items.keys().nth(rng.below(model.items.len())).cloned() {
                let n = rng.below(1000) as i64;
                db.mutate(
                    "main",
                    &q,
                    "set_n",
                    &helpers::mixed_params(&[("$slug", &slug)], &[("$n", n)]),
                )
                .await
                .unwrap();
                model.items.get_mut(&slug).unwrap().1 = n;
            }
        } else if op < 42 {
            // Change a unique email: the old value must read absent.
            event = "patch_email";
            if let Some(slug) = model.items.keys().nth(rng.below(model.items.len())).cloned() {
                next_id += 1;
                let mail = format!("e{next_id}");
                db.mutate(
                    "main",
                    &q,
                    "set_email",
                    &helpers::params(&[("$slug", &slug), ("$email", &mail)]),
                )
                .await
                .unwrap();
                if let Some(old) = model.items[&slug].0.clone() {
                    model.gone("node:Item", &["email"], vec![old]);
                }
                model.items.get_mut(&slug).unwrap().0 = Some(mail);
            }
        } else if op < 49 {
            event = "delete_item";
            if let Some(slug) = model.items.keys().nth(rng.below(model.items.len())).cloned() {
                db.mutate("main", &q, "del_item", &helpers::params(&[("$slug", &slug)]))
                    .await
                    .unwrap();
                let (mail, _) = model.items.remove(&slug).unwrap();
                let id_col = id_of(&db.catalog()).to_string();
                model.gone("node:Item", &[id_col.as_str()], vec![slug]);
                if let Some(mail) = mail {
                    model.gone("node:Item", &["email"], vec![mail]);
                }
            }
        } else if op < 56 {
            // Pairs: composite @unique with nulls.
            event = "insert_pair";
            next_id += 1;
            let k = format!("p{next_id}");
            let a = (!rng.chance(30)).then(|| format!("a{}", rng.below(6)));
            let b = (!rng.chance(30)).then(|| rng.below(6) as i64);
            let collides = matches!((&a, b), (Some(a), Some(b)) if model.pair_holder(a, b));
            let body = match (&a, b) {
                (Some(a), Some(b)) => format!("insert Pair {{ k: \"{k}\", a: \"{a}\", b: {b} }}"),
                (Some(a), None) => format!("insert Pair {{ k: \"{k}\", a: \"{a}\" }}"),
                (None, Some(b)) => format!("insert Pair {{ k: \"{k}\", b: {b} }}"),
                (None, None) => format!("insert Pair {{ k: \"{k}\" }}"),
            };
            let result = db
                .mutate("main", &format!("query q() {{ {body} }}"), "q", &Default::default())
                .await;
            if collides {
                assert!(result.is_err(), "step {step}: duplicate pair ({a:?}, {b:?}) accepted");
                coverage.refusals_expected += 1;
            } else {
                result.unwrap_or_else(|e| panic!("step {step}: false refusal of pair ({a:?},{b:?}): {e}"));
                model.pairs.insert(k, (a, b));
            }
        } else if op < 59 {
            event = "delete_pair";
            if let Some((k, (a, b))) = model
                .pairs
                .iter()
                .nth(rng.below(model.pairs.len()))
                .map(|(k, v)| (k.clone(), v.clone()))
            {
                db.mutate("main", &q, "del_pair", &helpers::params(&[("$k", &k)]))
                    .await
                    .unwrap();
                model.pairs.remove(&k);
                if let (Some(a), Some(b)) = (a, b) {
                    model.gone("node:Pair", &["a", "b"], vec![a, b.to_string()]);
                }
            }
        } else if op < 66 {
            event = "insert_persons";
            let k = 1 + rng.below(4);
            let mut body = String::new();
            let mut names = Vec::new();
            for _ in 0..k {
                next_id += 1;
                let name = format!("u{next_id}");
                body.push_str(&format!("    insert Person {{ name: \"{name}\" }}\n"));
                names.push(name);
            }
            db.mutate("main", &format!("query q() {{\n{body}}}"), "q", &Default::default())
                .await
                .unwrap();
            model.persons.extend(names);
        } else if op < 78 {
            // Edges under @card(0..3): the count lookup decides.
            event = "follow";
            if model.persons.len() >= 2 {
                let persons: Vec<String> = {
                    let mut p: Vec<_> = model.persons.iter().cloned().collect();
                    p.sort();
                    p
                };
                // Bias toward a few sources so the bound is reached.
                let from = persons[rng.below(persons.len().min(6))].clone();
                let to = persons[rng.below(persons.len())].clone();
                let result = db
                    .mutate(
                        "main",
                        &q,
                        "follow",
                        &helpers::params(&[("$from", &from), ("$to", &to)]),
                    )
                    .await;
                if model.out_degree(&from) >= 3 {
                    assert!(result.is_err(), "step {step}: @card(0..3) admitted a 4th edge of {from}");
                    coverage.refusals_expected += 1;
                } else {
                    result.unwrap_or_else(|e| panic!("step {step}: false @card refusal for {from}: {e}"));
                    model.follows.push((from, to));
                }
            }
        } else if op < 81 {
            event = "delete_person";
            if !model.persons.is_empty() {
                let mut p: Vec<_> = model.persons.iter().cloned().collect();
                p.sort();
                let name = p[rng.below(p.len())].clone();
                db.mutate("main", &q, "del_person", &helpers::params(&[("$name", &name)]))
                    .await
                    .unwrap();
                model.persons.remove(&name);
                let src_col = db.catalog().system_columns.src.to_string();
                let vacated: Vec<String> = model
                    .follows
                    .iter()
                    .filter(|(s, d)| s == &name || d == &name)
                    .map(|(s, _)| s.clone())
                    .collect();
                for s in vacated {
                    model.gone("edge:Follows", &[src_col.as_str()], vec![s]);
                }
                model.follows.retain(|(s, d)| s != &name && d != &name);
            }
        } else if op < 85 {
            // A load: another writer, files the map never knows.
            event = "load_append";
            let mut data = String::new();
            for _ in 0..(1 + rng.below(20)) {
                next_id += 1;
                let slug = format!("l{next_id}");
                let mail = format!("le{next_id}");
                data.push_str(&format!(
                    "{{\"type\":\"Item\",\"data\":{{\"slug\":\"{slug}\",\"{email}\":\"{mail}\",\"n\":7}}}}\n"
                ));
                model.items.insert(slug, (Some(mail), 7));
            }
            db.load_jsonl(&data, LoadMode::Merge).await.unwrap();
        } else if op < 88 {
            event = "optimize";
            db.optimize_as(None, omnigraph::db::OptimizeRequest::default())
                .await
                .unwrap();
        } else if op < 91 {
            event = "ensure_indices";
            db.ensure_indices_as(None).await.unwrap();
        } else if op < 93 {
            event = "restart";
            key_lookup::clear_known_keys();
            db = Arc::new(session(Omnigraph::open(&uri).await.unwrap()));
        } else if op < 95 {
            event = "evict";
            key_lookup::set_known_keys_budget(1 + rng.below(40_000));
        } else if op < 96 {
            event = "unbudget";
            key_lookup::set_known_keys_budget(256 << 20);
        } else if op < 98 {
            event = "child_process";
            next_id += 1;
            let prefix = format!("c{next_id}");
            let n = 1 + rng.below(5);
            run_child(&uri, &prefix, n);
            for i in 0..n {
                model.items.insert(format!("{prefix}-{i}"), (None, i as i64));
            }
            model.pairs.insert(format!("{prefix}-pair"), (Some(prefix.clone()), Some(1)));
            db = Arc::new(session(Omnigraph::open(&uri).await.unwrap()));
        } else {
            // Schema evolution: first an added nullable column, then a
            // rename of the @unique column (the field id survives).
            event = "schema";
            if !extra {
                extra = true;
            } else if email == "email" {
                email = "mail".to_string();
            } else {
                continue;
            }
            db.apply_schema(&schema_source(&email, extra)).await.unwrap();
            db = Arc::new(session(Omnigraph::open(&uri).await.unwrap()));
        }
        *coverage.events.entry(event).or_default() += 1;
        check_all(&db, &model, &email, step, seed, &mut coverage).await;
        if step % 10 == 9 || event == "schema" || event == "optimize" {
            check_state(&db, &model, &email, step).await;
        }
    }
    check_state(&db, &model, &email, steps).await;
    let (files, bytes, budget) = key_lookup::known_keys_stats();
    println!(
        "kl_equivalence seed={seed} steps={steps} {coverage:?} known_keys files={files} bytes={bytes} budget={budget} lookups={:?}",
        key_lookup::lookup_stats()
    );
    assert!(coverage.map_fragments > 0, "the map answered no fragment");
    assert!(coverage.scanned_fragments > 0, "no tail scan ran");
    assert!(coverage.covered_spaces > 0, "no index part ran");
    assert!(coverage.deletion_fragments > 0, "no deletion file was exercised");
    assert!(coverage.multi_file_fragments > 0, "no column patch was exercised");
}

const LEGACY_SCHEMA: &str = "node Item {\n    slug: String @key\n    email: String? @unique\n}\n\
                             node Person {\n    name: String @key\n}\n\
                             edge Knows: Person -> Person\n";

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn kl_system_column_upgrade_keeps_known_keys() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let db = session(
        Omnigraph::init_with_legacy_system_columns_for_tests(uri, LEGACY_SCHEMA)
            .await
            .unwrap(),
    );
    assert_eq!(db.catalog().system_columns.id, "id");
    for i in 0..6 {
        let source = format!(
            "query q() {{ insert Item {{ slug: \"s{i}\", email: \"m{i}\" }} insert Person {{ name: \"p{i}\" }} }}"
        );
        db.mutate("main", &source, "q", &Default::default())
            .await
            .unwrap();
    }
    for i in 0..5 {
        let source = format!(
            "query q() {{ insert Knows {{ from: \"p{i}\", to: \"p{}\" }} }}",
            i + 1
        );
        db.mutate("main", &source, "q", &Default::default())
            .await
            .unwrap();
    }
    let check = |db: Session, label: &'static str| async move {
        let snapshot = db.snapshot_of(ReadTarget::branch("main")).await.unwrap();
        let catalog = db.catalog();
        let mut map_fragments = 0;
        for table in ["node:Item", "node:Person", "edge:Knows"] {
            let report = key_lookup::differential_check(
                &snapshot,
                &catalog,
                table,
                &HashMap::new(),
                64,
                3,
            )
            .await
            .unwrap();
            assert!(report.mismatches.is_empty(), "{label}: {:#?}", report.mismatches);
            map_fragments += report.map_fragments;
        }
        println!("{label}: id column {} map fragments {map_fragments}", catalog.system_columns.id);
        map_fragments
    };
    let before = check(db, "legacy").await;
    let db = session(Omnigraph::open(uri).await.unwrap());
    db.upgrade_system_columns(omnigraph::db::SystemColumnUpgradeOptions::default())
        .await
        .unwrap();
    let db = session(Omnigraph::open(uri).await.unwrap());
    assert_eq!(db.catalog().system_columns.id, "__id");
    let after = check(db.clone(), "upgraded").await;
    assert!(before > 0, "the legacy writes were answered from the map");
    assert_eq!(
        after, before,
        "a rename-only Project keeps the files, so the map answers the same fragments"
    );
    // A write after the upgrade records under the new spelling.
    db.mutate(
        "main",
        "query q() { insert Item { slug: \"after\", email: \"after\" } }",
        "q",
        &Default::default(),
    )
    .await
    .unwrap();
    let dup = db
        .mutate(
            "main",
            "query q() { insert Item { slug: \"dup\", email: \"m3\" } }",
            "q",
            &Default::default(),
        )
        .await;
    assert!(dup.is_err(), "a pre-upgrade @unique email still refuses");
    check(db, "after-write").await;
}

// ---------------------------------------------------------------------------
// Staleness.

const STALE_SCHEMA: &str = "node Item {\n    slug: String @key\n    email: String? @unique\n    n: I64\n}\n";

/// What Lance itself returns for `email IN (value)` on a dataset: the rows
/// (slug, email), with and without `fast_search`.
async fn lance_returns(ds: &Dataset, column: &str, holder: &str, value: &str) -> (Vec<(String, String)>, Vec<(String, String)>) {
    let mut out = Vec::new();
    for fast in [false, true] {
        let mut scanner = ds.scan();
        scanner.project(&[holder, column]).unwrap();
        scanner
            .filter(&format!("{column} IN ('{value}')"))
            .unwrap();
        if fast {
            scanner.fast_search();
        }
        let batches: Vec<RecordBatch> = scanner
            .try_into_stream()
            .await
            .unwrap()
            .try_collect()
            .await
            .unwrap();
        let mut rows = Vec::new();
        for batch in &batches {
            let h = batch.column_by_name(holder).unwrap().as_string::<i32>();
            let c = batch.column_by_name(column).unwrap().as_string::<i32>();
            for row in 0..batch.num_rows() {
                rows.push((h.value(row).to_string(), c.value(row).to_string()));
            }
        }
        out.push(rows);
    }
    let fast = out.pop().unwrap();
    (out.pop().unwrap(), fast)
}

/// The graph path: rows updated, upserted and deleted inside fragments the
/// email and id BTREEs cover, then compaction, then a fold. At every state
/// the two-part lookup equals the scan, old values read absent, new values
/// present, and what Lance returns is printed.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn kl_btree_staleness_on_the_graph_path() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let db = session(Omnigraph::init(uri, STALE_SCHEMA).await.unwrap());
    let mut data = String::new();
    for i in 0..40 {
        data.push_str(&format!(
            "{{\"type\":\"Item\",\"data\":{{\"slug\":\"s{i}\",\"email\":\"m{i}\",\"n\":{i}}}}}\n"
        ));
    }
    db.load_jsonl(&data, LoadMode::Overwrite).await.unwrap();
    db.ensure_indices_as(None).await.unwrap();
    let q = queries("email");
    let id_col = db.catalog().system_columns.id.to_string();

    let probe = |db: &Session, label: String, absent: Vec<&'static str>, present: Vec<&'static str>| {
        let db_handle = Arc::clone(db.db());
        let catalog = db.catalog();
        let id_col = id_col.clone();
        async move {
            let snapshot_fut = db_handle.snapshot_of(ReadTarget::branch("main"));
            let snapshot = snapshot_fut.await.unwrap();
            let mut extra = HashMap::new();
            extra.insert(
                vec!["email".to_string()],
                absent
                    .iter()
                    .chain(present.iter())
                    .map(|v| vec![v.to_string()])
                    .collect::<Vec<_>>(),
            );
            let report =
                key_lookup::differential_check(&snapshot, &catalog, "node:Item", &extra, 1000, 1)
                    .await
                    .unwrap();
            assert!(report.mismatches.is_empty(), "{label}: {:#?}", report.mismatches);
            let ds = helpers::open_pinned_dataset_for_test(&db_handle, "main", "node:Item").await;
            let wanted: Vec<Vec<String>> = absent
                .iter()
                .chain(present.iter())
                .map(|v| vec![v.to_string()])
                .collect();
            let (lookup, scan) = key_lookup::lookup_strings_both(
                &ds,
                &["email".to_string()],
                &id_col,
                &wanted,
            )
            .await
            .unwrap();
            assert_eq!(lookup, scan, "{label}");
            for value in &absent {
                assert!(!lookup.contains_key(&vec![value.to_string()]), "{label}: {value} reported present");
            }
            for value in &present {
                assert!(lookup.contains_key(&vec![value.to_string()]), "{label}: {value} hidden");
            }
            for value in absent.iter().chain(present.iter()) {
                let (plain, fast) = lance_returns(&ds, "email", "slug", value).await;
                println!(
                    "[staleness] {label}: Lance `email IN ('{value}')` returns {plain:?}; with fast_search {fast:?} (fragments {}, deletion files {}, two-file fragments {})",
                    ds.fragments().len(),
                    ds.fragments().iter().filter(|f| f.deletion_file.is_some()).count(),
                    ds.fragments().iter().filter(|f| f.files.len() > 1).count(),
                );
            }
        }
    };

    // Column patch of a unique value inside a covered fragment.
    db.mutate("main", &q, "set_email", &helpers::params(&[("$slug", "s1"), ("$email", "m1-new")]))
        .await
        .unwrap();
    probe(&db, "patch".into(), vec!["m1"], vec!["m1-new", "m2"]).await;
    // An upsert of a covered row (insert of an existing key): the row moves.
    db.mutate(
        "main",
        "query q() { insert Item { slug: \"s2\", email: \"m2-up\", n: 99 } }",
        "q",
        &Default::default(),
    )
    .await
    .unwrap();
    probe(&db, "upsert".into(), vec!["m1", "m2"], vec!["m2-up", "m3"]).await;
    // A delete inside a covered fragment.
    db.mutate("main", &q, "del_item", &helpers::params(&[("$slug", "s3")]))
        .await
        .unwrap();
    probe(&db, "delete".into(), vec!["m1", "m2", "m3"], vec!["m4"]).await;
    // A lagging index: new rows the BTREE does not cover.
    db.mutate(
        "main",
        "query q() { insert Item { slug: \"t1\", email: \"fresh\", n: 1 } }",
        "q",
        &Default::default(),
    )
    .await
    .unwrap();
    probe(&db, "lagging".into(), vec!["m3"], vec!["fresh", "m4"]).await;
    // Compaction (stable row ids remap the index coverage), then a fold.
    db.optimize_as(None, omnigraph::db::OptimizeRequest::default())
        .await
        .unwrap();
    probe(&db, "compacted".into(), vec!["m1", "m2", "m3"], vec!["m1-new", "m2-up", "fresh"]).await;
    db.ensure_indices_as(None).await.unwrap();
    probe(&db, "folded".into(), vec!["m1", "m2", "m3"], vec!["m1-new", "m2-up", "fresh"]).await;
    // Updates and deletes after the fold, inside freshly covered fragments.
    db.mutate("main", &q, "set_email", &helpers::params(&[("$slug", "s4"), ("$email", "m4-new")]))
        .await
        .unwrap();
    db.mutate("main", &q, "del_item", &helpers::params(&[("$slug", "t1")]))
        .await
        .unwrap();
    probe(&db, "after-fold".into(), vec!["m4", "fresh"], vec!["m4-new", "m5"]).await;
}

fn raw_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("email", DataType::Utf8, true),
    ]))
}

fn raw_batch(rows: &[(&str, &str)]) -> RecordBatch {
    RecordBatch::try_new(
        raw_schema(),
        vec![
            Arc::new(StringArray::from(rows.iter().map(|r| r.0).collect::<Vec<_>>())),
            Arc::new(StringArray::from(rows.iter().map(|r| r.1).collect::<Vec<_>>())),
        ],
    )
    .unwrap()
}

async fn raw_upsert(ds: Dataset, rows: &[(&str, &str)]) -> Dataset {
    let batch = raw_batch(rows);
    let reader = RecordBatchIterator::new(vec![Ok(batch)], raw_schema());
    let mut builder = MergeInsertBuilder::try_new(Arc::new(ds), vec!["id".to_string()]).unwrap();
    builder
        .when_matched(WhenMatched::UpdateAll)
        .when_not_matched(WhenNotMatched::InsertAll);
    let (ds, _) = builder.try_build().unwrap().execute_reader(reader).await.unwrap();
    Arc::try_unwrap(ds).unwrap_or_else(|ds| (*ds).clone())
}

/// The Lance-level shapes no graph writer produces: on a stable-row-id
/// dataset, an update moves a row to a new fragment; once a delta segment of
/// the email BTREE covers that fragment, Lance's own `email IN (old)` returns
/// the moved row (carrying its new value). The two-part lookup matches the
/// returned values in memory, so the old value reads absent.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn kl_btree_staleness_delta_segment_lance_level() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().join("raw.lance");
    let uri = uri.to_str().unwrap();
    let mut ds = Dataset::write(
        RecordBatchIterator::new(
            vec![Ok(raw_batch(&[("a1", "x1"), ("a2", "x2"), ("a3", "x3")]))],
            raw_schema(),
        ),
        uri,
        Some(WriteParams {
            mode: WriteMode::Create,
            enable_stable_row_ids: true,
            data_storage_version: Some(LanceFileVersion::V2_2),
            ..Default::default()
        }),
    )
    .await
    .unwrap();
    for column in ["id", "email"] {
        ds.create_index(
            &[column],
            IndexType::BTree,
            Some(format!("{column}_btree")),
            &ScalarIndexParams::default(),
            true,
        )
        .await
        .unwrap();
    }
    let ds = raw_upsert(ds, &[("a1", "x1-new")]).await;
    let report = |ds: &Dataset, label: &'static str| {
        let ds = ds.clone();
        async move {
            let wanted = vec![vec!["x1".to_string()], vec!["x1-new".to_string()], vec!["x2".to_string()]];
            let (lookup, scan) = key_lookup::lookup_strings_both(&ds, &["email".to_string()], "id", &wanted)
                .await
                .unwrap();
            let (plain, fast) = lance_returns(&ds, "email", "id", "x1").await;
            let segments = ds
                .load_indices()
                .await
                .unwrap()
                .iter()
                .filter(|index| index.name == "email_btree")
                .count();
            println!(
                "[staleness-lance] {label}: email_btree segments {segments}; Lance `email IN ('x1')` returns {plain:?}, with fast_search {fast:?}; two-part lookup {lookup:?}; full scan {scan:?}"
            );
            assert_eq!(lookup, scan, "{label}");
            assert!(!lookup.contains_key(&vec!["x1".to_string()]), "{label}: old value reported present");
            assert!(lookup.contains_key(&vec!["x1-new".to_string()]), "{label}: new value hidden");
            (plain, fast)
        }
    };
    let (plain, _) = report(&ds, "one segment, updated row in the tail").await;
    assert!(plain.is_empty(), "with one segment Lance masks the old entry");
    let mut ds = ds;
    ds.optimize_indices(&OptimizeOptions::append()).await.unwrap();
    let (plain, fast) = report(&ds, "delta segment covers the moved row").await;
    assert_eq!(
        plain,
        vec![("a1".to_string(), "x1-new".to_string())],
        "Lance returns the moved row, at its new value, for its old value"
    );
    assert_eq!(plain, fast);
}

/// A `Rewrite` this process commits drops the known files it removed, and
/// lookups after it still equal a scan.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[serial]
async fn kl_rewrite_drops_known_files() {
    key_lookup::set_known_keys_budget(256 << 20);
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let db = session(Omnigraph::init(uri, STALE_SCHEMA).await.unwrap());
    for i in 0..20 {
        let source = format!(
            "query q() {{ insert Item {{ slug: \"r{i}\", email: \"re{i}\", n: {i} }} }}"
        );
        db.mutate("main", &source, "q", &Default::default())
            .await
            .unwrap();
    }
    let (files_before, bytes_before, _) = key_lookup::known_keys_stats();
    let dropped_before = key_lookup::lookup_stats()[6];
    db.optimize_as(None, omnigraph::db::OptimizeRequest::default())
        .await
        .unwrap();
    let (files_after, bytes_after, _) = key_lookup::known_keys_stats();
    let dropped = key_lookup::lookup_stats()[6] - dropped_before;
    println!(
        "rewrite: files {files_before} -> {files_after}, bytes {bytes_before} -> {bytes_after}, dropped {dropped}"
    );
    assert!(dropped >= 20, "the compacted files left the map");
    let snapshot = db.snapshot_of(ReadTarget::branch("main")).await.unwrap();
    let report =
        key_lookup::differential_check(&snapshot, &db.catalog(), "node:Item", &HashMap::new(), 64, 5)
            .await
            .unwrap();
    assert!(report.mismatches.is_empty(), "{:#?}", report.mismatches);
}

/// RFC rule 16: only the absence-proven insert writer records known keys.
/// Every `record_insert(` call in the engine sources sits in
/// `stage_absence_proven_strict_insert`, and the research scan fill is off
/// unless its switch is set.
#[test]
fn kl_only_the_absence_proven_writer_records() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut calls = Vec::new();
    let mut stack = vec![root];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                stack.push(path);
            } else if path.extension().is_some_and(|e| e == "rs") {
                let text = std::fs::read_to_string(&path).unwrap();
                for (i, line) in text.lines().enumerate() {
                    if line.contains("key_lookup::record_insert(") && !line.trim_start().starts_with("//") {
                        // The enclosing function: the nearest `fn` above.
                        let lines: Vec<&str> = text.lines().take(i).collect();
                        let owner = lines
                            .iter()
                            .rev()
                            .find_map(|l| l.trim_start().strip_prefix("async fn ").or(l.trim_start().strip_prefix("fn ")))
                            .map(|l| l.split('(').next().unwrap().to_string())
                            .unwrap_or_default();
                        calls.push((path.display().to_string(), owner));
                    }
                }
            }
        }
    }
    assert_eq!(calls.len(), 1, "{calls:?}");
    assert!(calls[0].0.ends_with("table_store.rs"), "{calls:?}");
    assert_eq!(calls[0].1, "stage_absence_proven_strict_insert", "{calls:?}");
}
