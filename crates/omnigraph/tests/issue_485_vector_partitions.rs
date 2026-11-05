//! #485: every vector index the engine builds has one IVF partition, so an ANN
//! scan can never prune. This test is the reproduction proof: it fails on main.

mod helpers;

use lance::index::DatasetIndexExt;
use omnigraph::db::Omnigraph;
use omnigraph::loader::LoadMode;

use helpers::*;

const ROWS: usize = 20_000;

fn partitions_of(stats: &str) -> usize {
    fn find(v: &serde_json::Value) -> Option<u64> {
        match v {
            serde_json::Value::Object(m) => m
                .get("num_partitions")
                .and_then(|x| x.as_u64())
                .or_else(|| m.values().find_map(find)),
            serde_json::Value::Array(a) => a.iter().find_map(find),
            _ => None,
        }
    }
    find(&serde_json::from_str(stats).unwrap()).expect("num_partitions in index stats") as usize
}

#[tokio::test(flavor = "multi_thread")]
async fn issue_485_vector_index_is_sized_from_the_table() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap();
    let schema = "node Doc {\n    slug: String @key\n    embedding: Vector(4) @index\n}\n";
    let seed = (0..ROWS)
        .map(|row| {
            format!(
                r#"{{"type":"Doc","data":{{"slug":"n{row:05}","embedding":[{row}.0,{}.0,0.0,1.0]}}}}"#,
                row % 97
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    let db = session(Omnigraph::init(uri, schema).await.unwrap());
    db.load_jsonl(&seed, LoadMode::Overwrite).await.unwrap();
    db.ensure_indices().await.unwrap();

    let ds = open_pinned_dataset_for_test(&db, "main", "node:Doc").await;
    let indices = ds.load_indices().await.unwrap();
    eprintln!(
        "ISSUE485 indices={:?}",
        indices.iter().map(|i| i.name.clone()).collect::<Vec<_>>()
    );
    let name = indices
        .iter()
        .find(|i| i.name.to_lowercase().contains("embedding") || i.name.contains("vector"))
        .expect("embedding index")
        .name
        .clone();
    let stats = ds.index_statistics(&name).await.unwrap();
    let partitions = partitions_of(&stats);
    eprintln!("ISSUE485 rows={ROWS} index={name} num_partitions={partitions}");
    let target = (ROWS as f64).sqrt().floor() as usize; // 141
    assert!(
        partitions >= target / 2,
        "{ROWS} vectors were indexed into {partitions} IVF partition(s); a pruning index needs about sqrt(rows) = {target}"
    );
}
