//! Round 2 stress probes of optimize's writer yield (PR #1282, RFC 1.2:
//! "A writer that prepares again adds its own preparation, and no further
//! runs"). Harness only: these are deterministic seam scenarios that ask
//! what the yield does in the cases its unit tests do not reach. Each test
//! prints what it measured; an assertion that fails is a finding.
//!
//! Run: `engine_writer_yield_stress --ignored --test-threads 1 --nocapture`.

#[path = "../crates/omnigraph/tests/helpers/mod.rs"]
mod helpers;

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use helpers::{MUTATION_QUERIES, mixed_params, mutate_branch, mutate_main};
use omnigraph::db::{
    AuthorityCapture, Omnigraph, OptimizeCancellation, OptimizeLimits, OptimizeRequest,
};
use omnigraph::seams::catalog;
use omnigraph::seams::{
    Behavior, Decide, DecideSeam, Decision, FailScenario, Global, Hold, Installed,
};

/// Counts every crossing of a seam, parks the listed crossings, and panics
/// at `panic_at` (after its park, if it is also parked).
struct Script {
    crossings: Arc<AtomicU64>,
    holds: Arc<Vec<(u64, Arc<Hold>)>>,
    panic_at: Option<u64>,
}
impl Behavior for Script {
    fn uninstalling(&self) {
        for (_, hold) in self.holds.iter() {
            hold.release();
        }
    }
}
impl Decide for Script {
    fn decide(&self, name: &'static str) -> Decision {
        let n = self.crossings.fetch_add(1, Ordering::SeqCst) + 1;
        let decision = match self.holds.iter().find(|(at, _)| *at == n) {
            Some((_, hold)) => hold.decide(name),
            None => Decision::Pass,
        };
        if self.panic_at == Some(n) {
            panic!("stress: writer panics while preparing again");
        }
        decision
    }
}
struct Parked {
    count: Arc<AtomicU64>,
    holds: Arc<Vec<(u64, Arc<Hold>)>>,
    _installed: Installed<dyn Decide, Global<dyn Decide>>,
}
impl Parked {
    fn new(seam: &'static DecideSeam, at: &[u64], panic_at: Option<u64>) -> Self {
        let count = Arc::new(AtomicU64::new(0));
        let holds = Arc::new(
            at.iter()
                .map(|n| (*n, Arc::new(Hold::default())))
                .collect::<Vec<_>>(),
        );
        let installed = seam.install(Arc::new(Script {
            crossings: Arc::clone(&count),
            holds: Arc::clone(&holds),
            panic_at,
        }));
        Self {
            count,
            holds,
            _installed: installed,
        }
    }
    fn hold(&self, n: u64) -> &Hold {
        self.holds
            .iter()
            .find(|(at, _)| *at == n)
            .map(|(_, h)| h.as_ref())
            .expect("parked crossing")
    }
    async fn wait_until_reached(&self, n: u64) {
        for _ in 0..4000 {
            if self.hold(n).reached() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        panic!("crossing {n} never reached");
    }
    fn release(&self, n: u64) {
        self.hold(n).release();
    }
    fn count(&self) -> u64 {
        self.count.load(Ordering::SeqCst)
    }
}

async fn seed(dir: &tempfile::TempDir) {
    let seed = helpers::init_and_load(dir).await;
    helpers::commit_many(&seed, 4).await;
}

async fn add_fragments(db: &helpers::Session, tag: &str) {
    for i in 0..3 {
        mutate_main(
            db,
            MUTATION_QUERIES,
            "insert_person",
            &mixed_params(&[("$name", &format!("{tag}-{i}"))], &[("$age", 1)]),
        )
        .await
        .unwrap();
    }
}

fn insert(
    db: Arc<helpers::Session>,
    name: &'static str,
) -> tokio::task::JoinHandle<omnigraph::error::Result<omnigraph_compiler::result::MutationResult>> {
    tokio::spawn(async move {
        mutate_main(
            &db,
            MUTATION_QUERIES,
            "insert_person",
            &mixed_params(&[("$name", name)], &[("$age", 7)]),
        )
        .await
    })
}

fn optimize(
    db: Arc<helpers::Session>,
    request: OptimizeRequest,
) -> tokio::task::JoinHandle<omnigraph::error::Result<omnigraph::db::OptimizeReport>> {
    tokio::spawn(async move { db.optimize_as(None, request).await })
}

/// As the served path asks: authority captured again under the gates.
fn yield_limit(limit: Duration) -> OptimizeRequest {
    OptimizeRequest::default()
        .with_limits(OptimizeLimits::default().with_max_writer_yield(limit))
        .with_authority(AuthorityCapture::UnderGates)
}

async fn wait_for(count: &AtomicU64, at_least: u64) {
    for _ in 0..2000 {
        if count.load(Ordering::SeqCst) >= at_least {
            return;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    panic!("never reached {at_least}");
}

fn counter(
    seam: &'static DecideSeam,
) -> (Arc<AtomicU64>, Installed<dyn Decide, Global<dyn Decide>>) {
    let count = Arc::new(AtomicU64::new(0));
    let observer = {
        let count = Arc::clone(&count);
        seam.observe(move || {
            count.fetch_add(1, Ordering::SeqCst);
        })
    };
    (count, observer)
}

/// The writer `W` first prepared before run A published. When A finishes,
/// `W` is queued at main's gates behind another writer `X` (which prepared
/// after A and is committing). `W` has not yet seen `ReadSetChanged`, so it
/// holds no mark. Run B starts now: its yield sees no mark and B queues for
/// the gates behind `W`. `W` then takes the gates, finds main moved, marks,
/// releases them and prepares again, and B takes the gates and publishes
/// over it. `W` prepares a third time: one write waited through two runs.
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "instrument: round-2 writer-yield probe"]
async fn wy_writer_queued_behind_a_writer_is_overtaken_by_the_next_run() {
    let _scenario = FailScenario::setup();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    seed(&dir).await;
    let opt = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    // One handle for every actor, as the server holds one per graph.
    let wdb = Arc::clone(&opt);
    let xdb = Arc::clone(&opt);
    let (yields, _o1) = counter(&catalog::GATE_WRITER_YIELD_ACQUIRE);
    let (branch_waits, _o2) = counter(&catalog::GATE_BRANCH_ACQUIRE);
    // W's first attempt parks before its gates; X's table commit parks under them.
    let pre_gate = Parked::new(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE, &[1], None);
    let under_gate = Parked::new(&catalog::MUTATION_POST_TABLE_COMMIT, &[1], None);

    let w = insert(Arc::clone(&wdb), "w-overtaken");
    pre_gate.wait_until_reached(1).await;
    let a = opt
        .optimize_as(None, OptimizeRequest::default())
        .await
        .unwrap();
    assert!(
        a.graph_commit_id.is_some(),
        "A publishes over W's first attempt"
    );

    let x = insert(Arc::clone(&xdb), "x-holds-the-gates");
    under_gate.wait_until_reached(1).await;
    // W leaves its first preparation and queues at the gates behind X.
    let waits_before = branch_waits.load(Ordering::SeqCst);
    pre_gate.release(1);
    wait_for(&branch_waits, waits_before + 1).await;
    tokio::time::sleep(Duration::from_millis(100)).await;

    // More fragments are not needed: B has work left only if A left some;
    // either way B publishes when it has work. Give it work.
    let b = optimize(Arc::clone(&opt), yield_limit(Duration::from_secs(30)));
    wait_for(&yields, 2).await;
    tokio::time::sleep(Duration::from_millis(200)).await;
    under_gate.release(1);
    x.await.unwrap().expect("X commits");
    let w_result = w.await.unwrap();
    let b = b.await.unwrap().unwrap();
    let w_attempts = pre_gate.count() - 1; // X crossed once
    println!(
        "RESULT overtaken: W attempts {w_attempts} (W ok: {}), B writer_yield {:?}, B published {:?}, B input {:?}",
        w_result.is_ok(),
        b.writer_yield,
        b.graph_commit_id,
        b.input_commit
    );
    assert!(w_result.is_ok());
    assert!(
        b.graph_commit_id.is_none() || w_attempts <= 2,
        "W prepared {w_attempts} times: run B overtook a writer run A had already sent back"
    );
}

/// A writer that is preparing again (marked) is dropped (its task aborted).
/// The run waiting for it must stop waiting then, not at its limit.
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "instrument: round-2 writer-yield probe"]
async fn wy_dropped_marked_writer_ends_the_wait() {
    let _scenario = FailScenario::setup();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    seed(&dir).await;
    let opt = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    let wdb = Arc::clone(&opt);
    let (yields, _o1) = counter(&catalog::GATE_WRITER_YIELD_ACQUIRE);
    let parks = Parked::new(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE, &[1, 2], None);
    let w = insert(Arc::clone(&wdb), "w-dropped");
    parks.wait_until_reached(1).await;
    opt.optimize_as(None, OptimizeRequest::default())
        .await
        .unwrap();
    parks.release(1);
    parks.wait_until_reached(2).await; // marked, preparing again
    add_fragments(&opt, "more").await;
    let started = Instant::now();
    let b = optimize(Arc::clone(&opt), yield_limit(Duration::from_secs(30)));
    wait_for(&yields, 2).await;
    tokio::time::sleep(Duration::from_millis(300)).await;
    w.abort();
    parks.release(2); // the parked poll returns; the abort lands at its next await
    let b = tokio::time::timeout(Duration::from_secs(20), b)
        .await
        .expect("the run stops waiting once the marked writer is dropped")
        .unwrap()
        .unwrap();
    println!(
        "RESULT dropped: B writer_yield {:?}, B took {:?}, W aborted: {:?}",
        b.writer_yield,
        started.elapsed(),
        w.await.err().map(|e| e.is_cancelled())
    );
    assert!(b.writer_yield < Duration::from_secs(5));
}

/// A writer that panics while it is preparing again ends the run's wait.
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "instrument: round-2 writer-yield probe"]
async fn wy_panicking_marked_writer_ends_the_wait() {
    let _scenario = FailScenario::setup();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    seed(&dir).await;
    let opt = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    let wdb = Arc::clone(&opt);
    let (yields, _o1) = counter(&catalog::GATE_WRITER_YIELD_ACQUIRE);
    // W parks at its first attempt; A publishes over it; W marks and parks
    // at its second; B starts waiting; W is released and panics there.
    let parks = Parked::new(
        &catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE,
        &[1, 2],
        Some(2),
    );
    let w = insert(Arc::clone(&wdb), "w-panics");
    parks.wait_until_reached(1).await;
    opt.optimize_as(None, OptimizeRequest::default())
        .await
        .unwrap();
    parks.release(1);
    parks.wait_until_reached(2).await;
    let started = Instant::now();
    let b = optimize(Arc::clone(&opt), yield_limit(Duration::from_secs(30)));
    wait_for(&yields, 2).await;
    tokio::time::sleep(Duration::from_millis(300)).await;
    parks.release(2);
    let w = w.await;
    println!(
        "RESULT panic: W joined: panicked={}",
        w.as_ref().err().is_some_and(|e| e.is_panic())
    );
    let b = tokio::time::timeout(Duration::from_secs(20), b)
        .await
        .expect("the run stops waiting once the panicked writer's mark drops")
        .unwrap()
        .unwrap();
    println!(
        "RESULT panic: B writer_yield {:?} took {:?} yields {}",
        b.writer_yield,
        started.elapsed(),
        yields.load(Ordering::SeqCst)
    );
    assert!(
        b.writer_yield < Duration::from_secs(5),
        "a panicked writer leaked its mark"
    );
}

/// A run cancelled while it waits for a writer. The RFC: the wait "ends on
/// the run's cancellation". Does the run then return, or does it go on to
/// take the writer gates (and wait for them) before it notices?
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "instrument: round-2 writer-yield probe"]
async fn wy_cancelled_during_the_yield() {
    let _scenario = FailScenario::setup();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    seed(&dir).await;
    let opt = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    // One handle for every actor, as the server holds one per graph.
    let wdb = Arc::clone(&opt);
    let xdb = Arc::clone(&opt);
    let rdb = Arc::clone(&opt);
    let (yields, _o1) = counter(&catalog::GATE_WRITER_YIELD_ACQUIRE);
    let (yield_ends, _o2) = counter(&catalog::GATE_WRITER_YIELD_HOLD);
    let pre_gate = Parked::new(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE, &[1, 2], None);
    let under_gate = Parked::new(&catalog::MUTATION_POST_TABLE_COMMIT, &[1], None);

    let w = insert(Arc::clone(&wdb), "w-marked");
    pre_gate.wait_until_reached(1).await;
    let a = opt
        .optimize_as(None, OptimizeRequest::default())
        .await
        .unwrap();
    println!("cancel: A published {:?}", a.graph_commit_id);
    assert!(a.graph_commit_id.is_some());
    pre_gate.release(1);
    for _ in 0..30 {
        if pre_gate.hold(2).reached() || under_gate.hold(1).reached() || w.is_finished() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    println!(
        "cancel: W crossings {}, under-gate reached {}, W finished {}",
        pre_gate.count(),
        under_gate.hold(1).reached(),
        w.is_finished()
    );
    pre_gate.wait_until_reached(2).await; // W marked, no gate held
    let x = insert(Arc::clone(&xdb), "x-under-gates"); // crosses pre_gate #3, passes
    under_gate.wait_until_reached(1).await; // X holds main's gates

    let commits_before = opt.list_commits(Some("main")).await.unwrap().len();
    let cancellation = OptimizeCancellation::new();
    let b = optimize(
        Arc::clone(&opt),
        yield_limit(Duration::from_secs(30)).with_cancellation(cancellation.clone()),
    );
    wait_for(&yields, 2).await;
    // Reads during the yield.
    let t = Instant::now();
    let rows = helpers::count_rows(&rdb, "node:Person").await;
    let read_during_yield = t.elapsed();
    tokio::time::sleep(Duration::from_millis(200)).await;
    let cancelled_at = Instant::now();
    cancellation.cancel();
    wait_for(&yield_ends, 2).await;
    let yield_ended_after = cancelled_at.elapsed();
    tokio::time::sleep(Duration::from_millis(1500)).await;
    let returned_while_x_held_gates = b.is_finished();
    under_gate.release(1);
    x.await.unwrap().expect("X commits");
    let b = b.await.unwrap();
    let returned_after = cancelled_at.elapsed();
    pre_gate.release(2);
    let _ = w.await;
    let commits_after = opt.list_commits(Some("main")).await.unwrap().len();
    println!(
        "RESULT cancel: read during yield {read_during_yield:?} ({rows} rows); yield ended {yield_ended_after:?} after cancel; returned while X held the gates: {returned_while_x_held_gates}; returned {returned_after:?} after cancel; outcome {:?}; commits {} -> {} (X's write is one)",
        b.as_ref().map(|r| (
            r.writer_yield,
            r.graph_commit_id.clone(),
            format!("{:?}", r.cancellation),
            r.tables
                .iter()
                .map(|t| format!("{}={:?}", t.type_key, t.outcome))
                .collect::<Vec<_>>()
        )),
        commits_before,
        commits_after
    );
    assert!(read_during_yield < Duration::from_secs(2));
    assert_eq!(
        commits_after,
        commits_before + 2,
        "X's and W's writes, and no optimize publication"
    );
    assert!(
        returned_while_x_held_gates,
        "a run cancelled during its yield waited for a writer's gates before returning"
    );
}

/// `max_writer_yield` of zero and of a few ms: the run does not wait (or
/// waits that long), publishes over the marked writer, and the writer
/// prepares once more and commits.
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "instrument: round-2 writer-yield probe"]
async fn wy_small_limits() {
    for limit_ms in [0_u64, 5, 50] {
        let _scenario = FailScenario::setup();
        let dir = tempfile::tempdir().unwrap();
        let uri = dir.path().to_str().unwrap().to_string();
        seed(&dir).await;
        let opt = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
        let wdb = Arc::clone(&opt);
        let parks = Parked::new(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE, &[1, 2], None);
        let w = insert(Arc::clone(&wdb), "w-small");
        parks.wait_until_reached(1).await;
        opt.optimize_as(None, OptimizeRequest::default())
            .await
            .unwrap();
        parks.release(1);
        parks.wait_until_reached(2).await;
        add_fragments(&opt, &format!("small{limit_ms}")).await;
        let b = opt
            .optimize_as(None, yield_limit(Duration::from_millis(limit_ms)))
            .await
            .unwrap();
        parks.release(2);
        let w = w.await.unwrap();
        println!(
            "RESULT limit {limit_ms} ms: B writer_yield {:?}, B published {}, W ok {}, W attempts {}",
            b.writer_yield,
            b.graph_commit_id.is_some(),
            w.is_ok(),
            parks.count()
        );
        assert!(b.writer_yield < Duration::from_millis(limit_ms + 200));
        assert!(w.is_ok());
    }
}

/// A writer preparing again on a branch does not hold a run on main.
#[tokio::test(flavor = "multi_thread", worker_threads = 6)]
#[ignore = "instrument: round-2 writer-yield probe"]
async fn wy_branch_writer_does_not_hold_main() {
    let _scenario = FailScenario::setup();
    let dir = tempfile::tempdir().unwrap();
    let uri = dir.path().to_str().unwrap().to_string();
    seed(&dir).await;
    let opt = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    opt.branch_create("feature").await.unwrap();
    let w1 = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    let w2 = Arc::new(helpers::session(Omnigraph::open(&uri).await.unwrap()));
    let parks = Parked::new(&catalog::MUTATION_POST_STAGE_PRE_EFFECT_GATE, &[1, 3], None);
    let t1 = {
        let w1 = Arc::clone(&w1);
        tokio::spawn(async move {
            mutate_branch(
                &w1,
                "feature",
                MUTATION_QUERIES,
                "insert_person",
                &mixed_params(&[("$name", "b1")], &[("$age", 1)]),
            )
            .await
        })
    };
    parks.wait_until_reached(1).await;
    mutate_branch(
        &w2,
        "feature",
        MUTATION_QUERIES,
        "insert_person",
        &mixed_params(&[("$name", "b2")], &[("$age", 2)]),
    )
    .await
    .unwrap(); // crossing 2
    parks.release(1);
    parks.wait_until_reached(3).await; // w1 marked on `feature`
    let b = opt
        .optimize_as(None, yield_limit(Duration::from_secs(30)))
        .await
        .unwrap();
    parks.release(3);
    t1.await.unwrap().unwrap();
    println!("RESULT branch: main run writer_yield {:?}", b.writer_yield);
    assert!(b.writer_yield < Duration::from_secs(1));
}
