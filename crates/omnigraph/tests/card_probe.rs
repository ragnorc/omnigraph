#![recursion_limit = "256"]
//! Peak RSS of appending 8,192 new
//! `@card(0..1)` edges over N unindexed existing edge fragments.
use std::sync::Arc;
use omnigraph::Session;
use omnigraph::db::Omnigraph;
use omnigraph::loader::LoadMode;
use omnigraph_compiler::settings::SessionSettings;

fn peak_rss_bytes() -> u64 {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::uninit();
    let usage = unsafe {
        assert_eq!(libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()), 0);
        usage.assume_init()
    };
    u64::try_from(usage.ru_maxrss).unwrap() * 1024
}

async fn card_append_rise(fragments: usize) { append_rise(fragments, "@card(0..1)", 8_192).await }

async fn append_rise(fragments: usize, card: &str, n: usize) {
    let dir = tempfile::tempdir().unwrap();
    let db = Session::from_defaults(
        Arc::new(
            Omnigraph::init(
                dir.path().to_str().unwrap(),
                &format!("node Doc {{ slug: String @key }}\nedge Link: Doc -> Doc {card}\n"),
            )
            .await
            .unwrap(),
        ),
        SessionSettings::default(),
    );
    let docs: Vec<String> = (0..fragments)
        .map(|i| format!("old{i:05}"))
        .chain((0..n).map(|i| format!("d{i:07}")))
        .chain(std::iter::once("t".to_string()))
        .map(|slug| serde_json::json!({"type": "Doc", "data": {"slug": slug}}).to_string())
        .collect();
    for chunk in docs.chunks(8_000) {
        db.load_jsonl(&chunk.join("\n"), LoadMode::Merge).await.unwrap();
    }
    for i in 0..fragments {
        let edge = serde_json::json!({"edge": "Link", "from": format!("old{i:05}"), "to": "t"}).to_string();
        db.load_jsonl(&edge, LoadMode::Append).await.unwrap();
    }
    let edges: Vec<String> = (0..n)
        .map(|i| serde_json::json!({"edge": "Link", "from": format!("d{i:07}"), "to": "t"}).to_string())
        .collect();
    let before = peak_rss_bytes();
    db.load_jsonl(&edges.join("\n"), LoadMode::Append).await.unwrap();
    let rise = peak_rss_bytes().saturating_sub(before);
    eprintln!("PROBE append {n} edges [{card}] over {fragments} unindexed edge fragments: peak RSS rise {} MiB", rise / (1024 * 1024));
}

#[tokio::test(flavor = "multi_thread")]
async fn tmp_card_probe_1() { card_append_rise(1).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_card_probe_64() { card_append_rise(64).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_nocard_probe_64() { append_rise(64, "", 8_192).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_nocard_probe_1() { append_rise(1, "", 8_192).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_nocard_small_64() { append_rise(64, "", 16).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_card_small_64() { append_rise(64, "@card(0..1)", 16).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_nocard_mid_64() { append_rise(64, "", 1_024).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_card_mid_64() { append_rise(64, "@card(0..1)", 1_024).await; }

async fn node_append_rise(fragments: usize, n: usize) {
    let dir = tempfile::tempdir().unwrap();
    let db = Session::from_defaults(
        Arc::new(
            Omnigraph::init(dir.path().to_str().unwrap(), "node Doc { slug: String @key }\n")
                .await
                .unwrap(),
        ),
        SessionSettings::default(),
    );
    for i in 0..fragments {
        let doc = serde_json::json!({"type": "Doc", "data": {"slug": format!("old{i:05}")}}).to_string();
        db.load_jsonl(&doc, LoadMode::Append).await.unwrap();
    }
    let docs: Vec<String> = (0..n)
        .map(|i| serde_json::json!({"type": "Doc", "data": {"slug": format!("d{i:07}")}}).to_string())
        .collect();
    let before = peak_rss_bytes();
    db.load_jsonl(&docs.join("\n"), LoadMode::Append).await.unwrap();
    let rise = peak_rss_bytes().saturating_sub(before);
    eprintln!("PROBE append {n} Doc nodes over {fragments} unindexed node fragments: peak RSS rise {} MiB", rise / (1024 * 1024));
}

#[tokio::test(flavor = "multi_thread")]
async fn tmp_node_probe_64() { node_append_rise(64, 8_192).await; }

#[tokio::test(flavor = "multi_thread")]
async fn tmp_node_probe_1() { node_append_rise(1, 8_192).await; }
