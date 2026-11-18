#![recursion_limit = "256"]
//! Time of appending a few `@card(0..1)` edges over a large edge table whose
//! src BTREE covers all but one small fragment: the trade-off case of reading
//! the uncovered table whole instead of probing it.
use std::sync::Arc;
use std::time::Instant;
use omnigraph::Session;
use omnigraph::db::Omnigraph;
use omnigraph::loader::LoadMode;
use omnigraph_compiler::settings::SessionSettings;

#[tokio::test(flavor = "multi_thread")]
async fn tmp_card_tail_probe() {
    let dir = tempfile::tempdir().unwrap();
    let db = Session::from_defaults(
        Arc::new(
            Omnigraph::init(
                dir.path().to_str().unwrap(),
                "node Doc { slug: String @key }\nedge Link: Doc -> Doc @card(0..1)\n",
            )
            .await
            .unwrap(),
        ),
        SessionSettings::default(),
    );
    let big = 200_000usize;
    let docs: Vec<String> = (0..big)
        .map(|i| format!("e{i:07}"))
        .chain((0..100).map(|i| format!("n{i:04}")))
        .chain(["tail".to_string(), "t".to_string()])
        .map(|slug| serde_json::json!({"type": "Doc", "data": {"slug": slug}}).to_string())
        .collect();
    for chunk in docs.chunks(8_000) {
        db.load_jsonl(&chunk.join("\n"), LoadMode::Merge).await.unwrap();
    }
    let edges: Vec<String> = (0..big)
        .map(|i| serde_json::json!({"edge": "Link", "from": format!("e{i:07}"), "to": "t"}).to_string())
        .collect();
    for chunk in edges.chunks(8_000) {
        db.load_jsonl(&chunk.join("\n"), LoadMode::Append).await.unwrap();
    }
    db.db().ensure_indices_as(None).await.unwrap();
    let tail = serde_json::json!({"edge": "Link", "from": "tail", "to": "t"}).to_string();
    db.load_jsonl(&tail, LoadMode::Append).await.unwrap();
    for round in 0..3 {
        let few: Vec<String> = (0..10)
            .map(|i| serde_json::json!({"edge": "Link", "from": format!("n{:04}", round * 10 + i), "to": "t"}).to_string())
            .collect();
        let start = Instant::now();
        db.load_jsonl(&few.join("\n"), LoadMode::Append).await.unwrap();
        eprintln!("PROBE append 10 @card edges over {big} indexed edges + uncovered tail: {} ms", start.elapsed().as_millis());
    }
}
