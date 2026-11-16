//! In-source checks of the known keys' bookkeeping: answers equal a full
//! scan across recording, eviction, a deletion file and an index, and the
//! byte budget holds.

use std::sync::Arc;

use arrow_array::{RecordBatch, RecordBatchIterator, StringArray};
use arrow_schema::{DataType, Field, Schema};
use lance::Dataset;
use lance::dataset::{WriteMode, WriteParams};
use lance::index::DatasetIndexExt;
use lance_file::version::LanceFileVersion;
use lance_index::IndexType;
use lance_index::scalar::ScalarIndexParams;

use super::*;

fn schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("id", DataType::Utf8, false),
        Field::new("email", DataType::Utf8, true),
    ]))
}

fn batch(rows: &[(String, Option<String>)]) -> RecordBatch {
    RecordBatch::try_new(
        schema(),
        vec![
            Arc::new(StringArray::from(
                rows.iter().map(|r| r.0.clone()).collect::<Vec<_>>(),
            )),
            Arc::new(StringArray::from(
                rows.iter().map(|r| r.1.clone()).collect::<Vec<_>>(),
            )),
        ],
    )
    .unwrap()
}

async fn append(uri: &str, rows: &[(String, Option<String>)], mode: WriteMode) -> Dataset {
    Dataset::write(
        RecordBatchIterator::new(vec![Ok(batch(rows))], schema()),
        uri,
        Some(WriteParams {
            mode,
            enable_stable_row_ids: true,
            data_storage_version: Some(LanceFileVersion::V2_2),
            ..Default::default()
        }),
    )
    .await
    .unwrap()
}

fn rows(range: std::ops::Range<usize>) -> Vec<(String, Option<String>)> {
    range
        .map(|i| (format!("k{i}"), (i % 3 != 0).then(|| format!("m{}", i % 17))))
        .collect()
}

async fn assert_equal(ds: &Dataset, label: &str) {
    for (columns, holder) in [
        (vec!["id".to_string()], "id"),
        (vec!["email".to_string()], "id"),
        (vec!["id".to_string()], "email"),
    ] {
        let wanted: Vec<Vec<String>> = (0..200)
            .map(|i| {
                if columns[0] == "id" {
                    vec![format!("k{i}")]
                } else {
                    vec![format!("m{i}")]
                }
            })
            .collect();
        let (lookup, scan) = lookup_strings_both(ds, &columns, holder, &wanted)
            .await
            .unwrap();
        assert_eq!(lookup, scan, "{label}: {columns:?} -> {holder}");
    }
}

#[tokio::test]
async fn known_keys_answer_equal_a_full_scan_through_eviction_deletion_and_an_index() {
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().join("t.lance");
    let uri = uri.to_str().unwrap();
    declare_unique_groups(
        // The recorder reads the declared groups of the dataset's own uri.
        &append(uri, &rows(0..10), WriteMode::Create).await.uri().to_string(),
        &[vec!["email".to_string()]],
    );
    let mut ds = Dataset::open(uri).await.unwrap();
    // Record every fragment after the first, as the absence-proven writer
    // would; the first stays unknown (another writer's file).
    for chunk in 1..12 {
        let range = chunk * 10..chunk * 10 + 10;
        ds = append(uri, &rows(range.clone()), WriteMode::Append).await;
        let fragment = ds.fragments().last().unwrap().clone();
        record_insert(&ds, &fragment, &batch(&rows(range)), "id", Some(("id", "email")))
            .unwrap();
    }
    let before = lookup_stats();
    assert_equal(&ds, "recorded").await;
    let after = lookup_stats();
    assert!(after[2] > before[2], "the map answered fragments");
    assert!(after[3] > before[3], "the unknown fragment was scanned");

    // An index on the id and email columns covering every fragment so far.
    for column in ["id", "email"] {
        ds.create_index(
            &[column],
            IndexType::BTree,
            None,
            &ScalarIndexParams::default(),
            true,
        )
        .await
        .unwrap();
    }
    ds = append(uri, &rows(120..130), WriteMode::Append).await;
    let fragment = ds.fragments().last().unwrap().clone();
    record_insert(&ds, &fragment, &batch(&rows(120..130)), "id", Some(("id", "email"))).unwrap();
    assert_equal(&ds, "indexed with a known tail").await;

    // A deletion file on a recorded tail fragment: it leaves the map's answer.
    ds.delete("id = 'k125'").await.unwrap();
    assert_equal(&ds, "deleted inside a recorded fragment").await;

    // Eviction to a tiny budget: still equal, bytes within budget.
    set_known_keys_budget(2_000);
    let (_, bytes, budget) = known_keys_stats();
    assert!(bytes <= budget, "{bytes} > {budget}");
    assert_equal(&ds, "evicted").await;
    set_known_keys_budget(DEFAULT_BUDGET_BYTES);
}
