//! SPIKE (throwaway): the operation record's size bounds, RFC #1214 §2.2.

use omnigraph_cluster::{
    Admitted, MAX_MAINTENANCE_BYTES, MAX_MAINTENANCE_RECORD_BYTES, MaintenanceLedger,
    MaintenanceOperation, MaintenanceState, MaintenanceTableOutcome, OptimizeOptions,
};

fn id(n: u32) -> String {
    format!("01JC{:022}", n)
}

fn table(n: usize, detail: Option<String>) -> MaintenanceTableOutcome {
    MaintenanceTableOutcome {
        type_key: format!("node:SomeReasonablyNamedType{n:04}"),
        outcome: if detail.is_some() {
            "failed"
        } else {
            "optimized"
        }
        .to_string(),
        reason: None,
        detail,
        fragments_removed: 17,
        fragments_added: 1,
        duration_ms: 1234,
        source_bytes: Some(12_345_678),
    }
}

fn op(
    n: u32,
    tables: Vec<MaintenanceTableOutcome>,
    state: MaintenanceState,
) -> MaintenanceOperation {
    let mut op = MaintenanceOperation::accepted(
        id(n),
        "knowledge",
        OptimizeOptions::default(),
        "operator",
        None,
    );
    op.tables = tables;
    op.state = state;
    if state == MaintenanceState::Publishing {
        op.nonce = Some(id(90_000 + n));
        op.input_commit = Some("hb1.1.2.01JC0000000000000000000000".into());
        op.input_manifest_version = Some(42);
    }
    op
}

fn encoded(op: &MaintenanceOperation) -> usize {
    serde_json::to_vec(op).unwrap().len()
}

/// How many per-table outcomes fit one operation's 1 MiB bound.
#[test]
fn spike_tables_per_record() {
    let plain = encoded(&op(
        1,
        (0..1000).map(|n| table(n, None)).collect(),
        MaintenanceState::Succeeded,
    ));
    let per_plain = plain / 1000;
    let failed = encoded(&op(
        1,
        (0..1000)
            .map(|n| table(n, Some("x".repeat(2048))))
            .collect(),
        MaintenanceState::Succeeded,
    ));
    let per_failed = failed / 1000;
    eprintln!(
        "SPIKE record bytes per table: {per_plain} (optimized), {per_failed} (failed, 2 KiB detail); \
         tables per 1 MiB: {} / {}",
        MAX_MAINTENANCE_RECORD_BYTES / per_plain,
        MAX_MAINTENANCE_RECORD_BYTES / per_failed
    );
    // The production graph has 657 tables: it fits when outcomes are plain.
    assert!(MAX_MAINTENANCE_RECORD_BYTES / per_plain > 657);
    // ...but not when every table carries a long failure detail.
    assert!(MAX_MAINTENANCE_RECORD_BYTES / per_failed < 657);
    let mut ledger = MaintenanceLedger::new(id(0));
    ledger.active = Some(op(
        1,
        (0..657).map(|n| table(n, Some("x".repeat(2048)))).collect(),
        MaintenanceState::Publishing,
    ));
    let refused = ledger.validate();
    eprintln!("SPIKE 657 failed tables in the publishing write: {refused:?}");
    assert!(
        refused.is_err(),
        "the before-publish write itself is refused"
    );
}

/// Four unresolved records and an active one each within 1 MiB exceed the
/// 4 MiB total, which never evicts unresolved records: the record cannot
/// be written at all, so neither admission nor resolution can proceed.
#[test]
fn spike_per_record_and_total_bounds_do_not_compose() {
    let near = |n: u32, state| {
        let mut tables = Vec::new();
        let mut i = 0;
        loop {
            tables.push(table(i, Some("y".repeat(1500))));
            i += 1;
            if encoded(&op(n, tables.clone(), state)) > MAX_MAINTENANCE_RECORD_BYTES {
                tables.pop();
                break;
            }
        }
        // Pad the last detail so the record sits at the bound.
        let mut record = op(n, tables, state);
        let gap = MAX_MAINTENANCE_RECORD_BYTES - encoded(&record);
        let last = record.tables.last_mut().unwrap();
        last.detail = Some("y".repeat(1500 + gap));
        assert_eq!(encoded(&record), MAX_MAINTENANCE_RECORD_BYTES);
        record
    };
    let mut ledger = MaintenanceLedger::new(id(0));
    for n in 1..=3 {
        ledger
            .unresolved
            .push(near(n, MaintenanceState::Publishing));
    }
    ledger
        .validate()
        .expect("three unresolved near the bound fit");
    // The fourth run reaches `publishing` (its before-publish write fits),
    // then ends in doubt: moving it to the unresolved list is the write that
    // must land, and it is refused.
    ledger.active = Some(near(4, MaintenanceState::Publishing));
    let total = serde_json::to_vec(&ledger).unwrap().len();
    let verdict = ledger.validate();
    eprintln!(
        "SPIKE 3 unresolved + 1 publishing at 1 MiB each: total {total} bytes vs {MAX_MAINTENANCE_BYTES}: {verdict:?}"
    );
    assert!(total > MAX_MAINTENANCE_BYTES);
    assert!(verdict.is_err(), "the record is unwritable");
    // Settling one of them moves it to `terminal`, which keeps at least one
    // record, so even resolution cannot shrink the record below the bound.
    let mut settled = ledger.clone();
    settled.active = None;
    settled.settle(&id(1), omnigraph_cluster::Resolution::NotCommitted);
    let verdict = settled.validate();
    eprintln!(
        "SPIKE after one settles: {} bytes: {verdict:?}",
        serde_json::to_vec(&settled).unwrap().len()
    );
}

/// The idempotency window is the retained terminal list: after 32 later
/// operations, the same id and request is admitted again as new.
#[test]
fn spike_an_evicted_id_is_admitted_again() {
    let mut ledger = MaintenanceLedger::new(id(0));
    let first = op(1, Vec::new(), MaintenanceState::Accepted);
    assert_eq!(ledger.admit(first.clone()), Ok(Admitted::New));
    let mut done = first.clone();
    done.state = MaintenanceState::Succeeded;
    ledger.finish(done).unwrap();
    for n in 2..=33 {
        let next = op(n, Vec::new(), MaintenanceState::Accepted);
        assert_eq!(ledger.admit(next.clone()), Ok(Admitted::New));
        let mut done = next;
        done.state = MaintenanceState::Succeeded;
        ledger.finish(done).unwrap();
    }
    assert!(ledger.find("knowledge", &id(1)).is_none(), "evicted");
    let replay = ledger.admit(op(1, Vec::new(), MaintenanceState::Accepted));
    eprintln!("SPIKE resubmitting an evicted id: {replay:?}");
    assert_eq!(replay, Ok(Admitted::New), "the same id runs again");
}
