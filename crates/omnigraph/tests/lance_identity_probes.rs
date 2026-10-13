//! Lance 11 behavior the integer-identity and polymorphic RFCs rely on:
//! packed `UInt64` endpoint filters served by scalar indexes, and row
//! addresses scoped to one dataset. Each probe prints `PROBE <id>: ...`; run
//! with `cargo test -p omnigraph-engine --test lance_identity_probes -- --nocapture`.

use std::sync::Arc;

use arrow_array::{RecordBatch, RecordBatchIterator, UInt64Array};
use arrow_schema::{DataType, Field, Schema};
use futures::TryStreamExt;
use lance::Dataset;
use lance::dataset::{WriteMode, WriteParams};
use lance::index::DatasetIndexExt;
use lance_index::IndexType;
use lance_index::scalar::ScalarIndexParams;

const ID_BITS: u32 = 48;

fn packed(type_id: u64, id: u64) -> u64 {
    (type_id << ID_BITS) | id
}

/// One `packed` column: 1,000 rows for each of type ids 3, 5 and 40000 (the
/// last one above 2^63 once packed).
async fn dataset(uri: &str) -> Dataset {
    let schema = Arc::new(Schema::new(vec![Field::new("packed", DataType::UInt64, false)]));
    let mut values = Vec::new();
    for type_id in [3u64, 5, 40000] {
        for id in 0..1000u64 {
            values.push(packed(type_id, id));
        }
    }
    let batch = RecordBatch::try_new(schema.clone(), vec![Arc::new(UInt64Array::from(values))]).unwrap();
    Dataset::write(
        RecordBatchIterator::new(vec![Ok(batch)], schema),
        uri,
        Some(WriteParams {
            mode: WriteMode::Create,
            ..Default::default()
        }),
    )
    .await
    .unwrap()
}

async fn probe(ds: &Dataset, label: &str, filter: &str, expected: usize) {
    let mut scanner = ds.scan();
    match scanner.filter(filter) {
        Ok(_) => {}
        Err(e) => {
            println!("PROBE {label}: filter refused: {e}");
            return;
        }
    }
    let plan = scanner.explain_plan(true).await.unwrap();
    let uses_index = plan.contains("ScalarIndexQuery") || plan.contains("MaterializeIndex");
    let rows: usize = match scanner.try_into_stream().await {
        Ok(stream) => stream
            .try_collect::<Vec<RecordBatch>>()
            .await
            .map(|batches| batches.iter().map(RecordBatch::num_rows).sum())
            .unwrap_or(usize::MAX),
        Err(_) => usize::MAX,
    };
    println!(
        "PROBE {label}: index={uses_index} rows={rows} expected={expected}{}",
        if uses_index { "" } else { " plan_head=" }
    );
    if !uses_index {
        println!("    {}", plan.lines().take(6).collect::<Vec<_>>().join(" | "));
    }
}

#[tokio::test]
async fn packed_uint64_filters_use_scalar_indexes() {
    for (kind, index_type) in [("btree", IndexType::BTree), ("bitmap", IndexType::Bitmap)] {
        let dir = tempfile::tempdir().unwrap();
        let uri = dir.path().join(format!("{kind}.lance"));
        let mut ds = dataset(uri.to_str().unwrap()).await;
        if let Err(e) = ds
            .create_index_builder(&["packed"], index_type, &ScalarIndexParams::default())
            .replace(true)
            .await
        {
            println!("PROBE {kind}.create_index: ERROR {e}");
            continue;
        }
        let lo = packed(3, 0);
        let hi = packed(3, (1 << ID_BITS) - 1);
        probe(&ds, &format!("{kind}.type_range_ge_le"), &format!("packed >= {lo} AND packed <= {hi}"), 1000).await;
        probe(&ds, &format!("{kind}.type_range_between"), &format!("packed BETWEEN {lo} AND {hi}"), 1000).await;
        probe(&ds, &format!("{kind}.equals"), &format!("packed = {}", packed(5, 7)), 1).await;
        probe(
            &ds,
            &format!("{kind}.in_list"),
            &format!("packed IN ({}, {}, {})", packed(3, 1), packed(5, 2), packed(5, 999)),
            3,
        )
        .await;
        // Above 2^63: type id 40000.
        let lo = packed(40000, 0);
        let hi = packed(40000, (1 << ID_BITS) - 1);
        probe(&ds, &format!("{kind}.type_range_above_2pow63"), &format!("packed >= {lo} AND packed <= {hi}"), 1000).await;
        probe(&ds, &format!("{kind}.equals_above_2pow63"), &format!("packed = {}", packed(40000, 9)), 1).await;
    }
}

#[tokio::test]
async fn row_addresses_are_scoped_to_one_dataset() {
    let dir = tempfile::tempdir().unwrap();
    let mut firsts = Vec::new();
    for name in ["a.lance", "b.lance"] {
        let ds = dataset(dir.path().join(name).to_str().unwrap()).await;
        let mut scanner = ds.scan();
        scanner.with_row_address();
        scanner.limit(Some(3), None).unwrap();
        let batches: Vec<RecordBatch> = scanner.try_into_stream().await.unwrap().try_collect().await.unwrap();
        let addrs = batches[0]
            .column_by_name("_rowaddr")
            .unwrap()
            .as_any()
            .downcast_ref::<UInt64Array>()
            .unwrap()
            .values()
            .to_vec();
        firsts.push(addrs);
    }
    println!("PROBE rowaddr: dataset_a={:?} dataset_b={:?} collide={}", firsts[0], firsts[1], firsts[0] == firsts[1]);
}
