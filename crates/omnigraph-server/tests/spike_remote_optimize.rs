//! SPIKE (throwaway): stress tests for RFC #1214, remote optimize through the
//! serving owner. Every test runs a real production server in a child
//! process (this binary re-executed), on a local `file://` cluster root, and
//! drives it over HTTP. `OMNIGRAPH_SPIKE_PAUSE_AT` parks or delays the child
//! at named points (`omnigraph::spike::pause`), so a test can kill it or send
//! SIGTERM at each step of a run.
//!
//! Run: `cargo test -p omnigraph-server --test spike_remote_optimize -- --test-threads=1 --nocapture`

use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{Receiver, channel};
use std::time::{Duration, Instant};

use omnigraph_server::api::{HTTP_API_CONTRACT, HTTP_API_CONTRACT_HEADER};
use serde_json::{Value, json};

#[allow(dead_code)]
mod support;
use support::*;

const ROOT: &str = "OMNIGRAPH_SPIKE_TEST_ROOT";

const POLICY: &str = r#"
version: 1
groups:
  operators: [operator]
rules:
  - id: maintain
    allow:
      actors: { group: operators }
      actions: [maintain, read, change]
      branch_scope: any
  - id: schema
    allow:
      actors: { group: operators }
      actions: [schema_apply]
"#;

fn cluster_dir(graphs: &[&str], edges: bool) -> tempfile::TempDir {
    let temp = tempfile::tempdir().unwrap();
    let schema = if edges {
        "node Person {\n  name: String @key\n}\nedge Knows: Person -> Person\n"
    } else {
        "node Person {\n  name: String @key\n}\n"
    };
    fs::write(temp.path().join("people.pg"), schema).unwrap();
    fs::write(temp.path().join("policy.yaml"), POLICY).unwrap();
    write_cluster_yaml(temp.path(), graphs);
    temp
}

fn write_cluster_yaml(dir: &Path, graphs: &[&str]) {
    let mut yaml = String::from("version: 1\ngraphs:\n");
    for graph in graphs {
        yaml.push_str(&format!("  {graph}:\n    schema: ./people.pg\n"));
    }
    yaml.push_str(&format!(
        "policies:\n  access:\n    file: ./policy.yaml\n    applies_to: [{}]\n",
        graphs.join(", ")
    ));
    fs::write(dir.join("cluster.yaml"), yaml).unwrap();
}

/// Three loads leave three small fragments for a run to compact.
async fn seed(dir: &Path, graph: &str, edges: bool) {
    let db = session(
        omnigraph::db::Omnigraph::open(dir.join(format!("graphs/{graph}.omni")).to_str().unwrap())
            .await
            .unwrap(),
    );
    for name in ["Ada", "Grace", "Edsger"] {
        db.load_jsonl(
            &json!({"type": "Person", "data": {"name": name}}).to_string(),
            omnigraph::loader::LoadMode::Merge,
        )
        .await
        .unwrap();
    }
    if edges {
        for (from, to) in [("Ada", "Grace"), ("Grace", "Edsger")] {
            db.load_jsonl(
                &json!({"edge": "Knows", "from": from, "to": to}).to_string(),
                omnigraph::loader::LoadMode::Merge,
            )
            .await
            .unwrap();
        }
    }
}

/// A killed owner keeps a local cluster lock (exact-id unlock), so an
/// operator unlocks before the successor starts.
async fn unlock(dir: &Path) {
    let caller = omnigraph_cluster::DeploymentCaller::storage_owner(None);
    let captured = omnigraph_cluster::capture_deployment(dir).unwrap();
    let status = omnigraph_cluster::deployment_status(captured.canonical_root(), None, &caller)
        .await
        .unwrap();
    if let Some(lock_id) = status.lock_id {
        omnigraph_cluster::force_unlock_storage_root(captured.canonical_root(), &lock_id)
            .await
            .unwrap();
    }
}

struct Server {
    child: Child,
    base: String,
    lines: std::sync::Mutex<Receiver<String>>,
    http: reqwest::Client,
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn spawn(root: &Path, env: &[(&str, &str)]) -> Server {
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args(["--exact", "spike_served_child", "--ignored", "--nocapture"])
        .env(ROOT, root)
        .env("OMNIGRAPH_MAINTENANCE_ROUTES", "true")
        .env(
            "OMNIGRAPH_SERVER_BEARER_TOKENS_JSON",
            r#"{"operator":"op-secret"}"#,
        )
        .env_remove("OMNIGRAPH_SERVER_BEARER_TOKEN")
        .env_remove("OMNIGRAPH_SERVER_BEARER_TOKENS_FILE")
        .env_remove("OMNIGRAPH_SERVER_BEARER_TOKENS_AWS_SECRET")
        .env_remove("OMNIGRAPH_SPIKE_PAUSE_AT")
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    for (key, value) in env {
        command.env(key, value);
    }
    let mut child = command.spawn().unwrap();
    let stdout = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(address) = line.strip_prefix(omnigraph_server::LISTEN_ADDR_PREFIX) {
                let _ = tx.send(format!("LISTEN {address}"));
            } else if line.starts_with("SPIKE_PAUSED ") {
                let _ = tx.send(line);
            }
        }
    });
    let first = rx
        .recv_timeout(Duration::from_secs(60))
        .expect("the child starts its listener");
    let address = first.strip_prefix("LISTEN ").unwrap().to_string();
    Server {
        child,
        base: format!("http://{address}"),
        lines: std::sync::Mutex::new(rx),
        http: reqwest::Client::new(),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "instrument: subprocess helper for the spike suite"]
async fn spike_served_child() {
    let Some(root) = std::env::var_os(ROOT).map(PathBuf::from) else {
        return;
    };
    let mut config = cluster_settings(&root).await.unwrap();
    config.bind = "127.0.0.1:0".into();
    omnigraph_server::serve(config).await.unwrap();
}

impl Server {
    async fn send(&self, request: reqwest::RequestBuilder) -> (u16, Value) {
        let response = request
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .bearer_auth("op-secret")
            .send()
            .await
            .unwrap();
        let status = response.status().as_u16();
        (status, response.json().await.unwrap_or(Value::Null))
    }

    fn submit_request(&self, graph: &str, body: &Value) -> reqwest::RequestBuilder {
        self.http
            .post(format!("{}/graphs/{graph}/maintenance/optimize", self.base))
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .bearer_auth("op-secret")
            .json(body)
    }

    async fn submit(&self, graph: &str, body: Value) -> (u16, Value) {
        self.send(
            self.http
                .post(format!("{}/graphs/{graph}/maintenance/optimize", self.base))
                .json(&body),
        )
        .await
    }

    async fn lookup(&self, graph: &str, id: &str) -> (u16, Value) {
        self.send(
            self.http
                .get(format!("{}/graphs/{graph}/maintenance/{id}", self.base)),
        )
        .await
    }

    async fn load(&self, graph: &str, name: &str) -> (u16, Value) {
        self.send(
            self.http
                .post(format!("{}/graphs/{graph}/load", self.base))
                .json(&json!({
                    "data": json!({"type": "Person", "data": {"name": name}}).to_string()
                })),
        )
        .await
    }

    async fn wait_ready(&mut self) {
        let deadline = Instant::now() + Duration::from_secs(60);
        loop {
            if let Ok(response) = self.http.get(format!("{}/readyz", self.base)).send().await
                && response.status().is_success()
            {
                return;
            }
            assert!(self.child.try_wait().unwrap().is_none(), "server exited");
            assert!(Instant::now() < deadline, "server never became ready");
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }

    fn wait_paused(&self, point: &str) {
        let line = self
            .lines
            .lock()
            .unwrap()
            .recv_timeout(Duration::from_secs(60))
            .unwrap_or_else(|_| panic!("the child never paused at {point}"));
        assert_eq!(line, format!("SPIKE_PAUSED {point}"));
    }

    async fn wait_terminal(&self, graph: &str, id: &str) -> Value {
        let deadline = Instant::now() + Duration::from_secs(90);
        loop {
            let (status, found) = self.lookup(graph, id).await;
            assert_eq!(status, 200, "{found}");
            let state = found["operation"]["state"]
                .as_str()
                .unwrap_or("")
                .to_string();
            if matches!(
                state.as_str(),
                "succeeded" | "failed" | "cancelled" | "interrupted"
            ) {
                return found;
            }
            assert!(Instant::now() < deadline, "the run never finished: {found}");
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    fn kill(mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }

    fn sigterm(&self) {
        let status = Command::new("kill")
            .args(["-TERM", &self.child.id().to_string()])
            .status()
            .unwrap();
        assert!(status.success());
    }

    fn wait_exit(&mut self, within: Duration) -> Option<i32> {
        let deadline = Instant::now() + within;
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                return status.code();
            }
            if Instant::now() > deadline {
                return None;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

fn ulid(n: u32) -> String {
    format!("01JC{:022}", n)
}

fn record(dir: &Path) -> Value {
    serde_json::from_slice(&fs::read(dir.join("__cluster/maintenance.json")).unwrap()).unwrap()
}

async fn main_head(dir: &Path, graph: &str) -> (String, u64) {
    let db = omnigraph::db::Omnigraph::open_read_only(
        dir.join(format!("graphs/{graph}.omni")).to_str().unwrap(),
    )
    .await
    .unwrap();
    let commits = db.list_commits(None).await.unwrap();
    let head = commits[0].clone();
    (head.graph_commit_id, commits.len() as u64)
}

async fn person_count(dir: &Path, graph: &str) -> usize {
    let db = omnigraph::db::Omnigraph::open_read_only(
        dir.join(format!("graphs/{graph}.omni")).to_str().unwrap(),
    )
    .await
    .unwrap();
    let snapshot = db
        .snapshot_of(omnigraph::db::ReadTarget::branch("main"))
        .await
        .unwrap();
    snapshot
        .open_dataset("node:Person")
        .await
        .unwrap()
        .count_rows(None)
        .await
        .unwrap()
}

/// Kill the owner at every step of a run; a successor resolves the record
/// from main's history and never runs the work again.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_kill_at_each_step_resolves_from_history() {
    // (pause point, expected state after restart, expect a published commit)
    let cases = [
        ("server.before_accept_write", "unknown", false),
        ("server.after_accept_write", "interrupted", false),
        ("server.after_running_write", "interrupted", false),
        ("optimize.after_table_effect", "interrupted", false),
        ("server.after_publishing_write", "publishing", false),
        ("optimize.after_publication", "succeeded", true),
        ("server.before_terminal_write", "succeeded", true),
    ];
    for (point, expected, published) in cases {
        let temp = cluster_dir(&["knowledge"], false);
        apply_cluster_fixture(temp.path()).await;
        seed(temp.path(), "knowledge", false).await;
        let (head_before, commits_before) = main_head(temp.path(), "knowledge").await;
        let id = ulid(1);

        let mut server = spawn(temp.path(), &[("OMNIGRAPH_SPIKE_PAUSE_AT", point)]);
        server.wait_ready().await;
        // The submit may block at an acceptance pause; it runs detached.
        let pending = server
            .submit_request("knowledge", &json!({ "maintenance_id": id }))
            .timeout(Duration::from_secs(120))
            .send();
        let pending = tokio::spawn(pending);
        server.wait_paused(point);
        server.kill();
        let _ = pending.await;
        unlock(temp.path()).await;

        let mut server = spawn(temp.path(), &[]);
        server.wait_ready().await;
        let (status, found) = server.lookup("knowledge", &id).await;
        assert_eq!(status, 200, "{point}: {found}");
        let state = if found["lookup"] == "unknown" {
            "unknown".to_string()
        } else {
            found["operation"]["state"].as_str().unwrap().to_string()
        };
        let (head_after, commits_after) = main_head(temp.path(), "knowledge").await;
        eprintln!(
            "SPIKE kill@{point}: state={state} published={} head_moved={} commits {}->{}",
            found["operation"]["published_commit"],
            head_after != head_before,
            commits_before,
            commits_after
        );
        assert_eq!(state, expected, "{point}: {found}");
        if published {
            assert_eq!(
                found["operation"]["published_commit"].as_str(),
                Some(head_after.as_str()),
                "{point}: the resolved commit is main's head: {found}"
            );
        } else {
            assert_eq!(head_after, head_before, "{point}: nothing published");
        }
        assert_eq!(person_count(temp.path(), "knowledge").await, 3, "{point}");

        // A new id after the restart is never blocked by the old run: it
        // runs, and its own publication settles a `publishing` record.
        let (status, body) = server
            .submit("knowledge", json!({ "maintenance_id": ulid(2) }))
            .await;
        assert!(matches!(status, 200 | 202), "{point}: {status} {body}");
        let terminal = server.wait_terminal("knowledge", &ulid(2)).await;
        assert_eq!(
            terminal["operation"]["state"], "succeeded",
            "{point}: {terminal}"
        );
        if expected == "publishing" {
            // The successor's own publication moved main: the earlier record
            // is now `failed` with `not_committed`.
            let (_, old) = server.lookup("knowledge", &id).await;
            eprintln!(
                "SPIKE kill@{point}: after a later publication: {}",
                old["operation"]["state"]
            );
            assert_eq!(old["operation"]["state"], "failed", "{old}");
            assert_eq!(old["operation"]["reason"], "not_committed", "{old}");
        }
        // The same id is never executed again after restart.
        if expected != "unknown" {
            let (status, again) = server
                .submit("knowledge", json!({ "maintenance_id": id }))
                .await;
            assert_eq!(status, 200, "{point}: {again}");
            assert_eq!(
                again["operation"]["state"],
                found["operation"]["state"]
                    .as_str()
                    .map(|s| if s == "publishing" { "failed" } else { s })
                    .unwrap(),
                "{point}"
            );
        }
        server.kill();
    }
}

/// Same id in parallel answers one operation; different ids while one is
/// active answer 429 with the active id. A lost response is answered by a
/// lookup, and a retry of the same id answers the record.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_concurrent_submissions_and_lost_response() {
    let temp = cluster_dir(&["knowledge"], false);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", false).await;
    let mut server = spawn(
        temp.path(),
        &[(
            "OMNIGRAPH_SPIKE_PAUSE_AT",
            "optimize.after_table_effect:3000",
        )],
    );
    server.wait_ready().await;

    let id = ulid(10);
    let same = (0..8).map(|_| {
        server
            .submit_request("knowledge", &json!({ "maintenance_id": id }))
            .send()
    });
    let answers = futures::future::join_all(same).await;
    let mut statuses = Vec::new();
    for answer in answers {
        let answer = answer.unwrap();
        statuses.push(answer.status().as_u16());
        let body: Value = answer.json().await.unwrap();
        assert_eq!(body["operation"]["maintenance_id"], id, "{body}");
    }
    eprintln!("SPIKE same-id x8 statuses: {statuses:?}");
    assert!(statuses.iter().all(|s| *s == 202), "{statuses:?}");

    let others = (0..8).map(|n| {
        server
            .submit_request("knowledge", &json!({ "maintenance_id": ulid(20 + n) }))
            .send()
    });
    let answers = futures::future::join_all(others).await;
    let mut statuses = Vec::new();
    for answer in answers {
        statuses.push(answer.unwrap().status().as_u16());
    }
    eprintln!("SPIKE different-id x8 while active: {statuses:?}");
    assert!(statuses.iter().all(|s| *s == 429), "{statuses:?}");

    // A lost response: the caller drops the request at once, then looks up.
    let lost_id = ulid(40);
    let terminal = server.wait_terminal("knowledge", &id).await;
    assert_eq!(terminal["operation"]["state"], "succeeded", "{terminal}");
    let lost = server
        .submit_request("knowledge", &json!({ "maintenance_id": lost_id }))
        .timeout(Duration::from_millis(1))
        .send()
        .await;
    eprintln!(
        "SPIKE lost response: {:?}",
        lost.as_ref().map(|r| r.status())
    );
    // Either the request reached the server (the record holds it) or it was
    // dropped before it was read (`unknown`). A retry under the same id is
    // then the record's answer, or the first acceptance: never a second run.
    let deadline = Instant::now() + Duration::from_secs(5);
    let landed = loop {
        let (_, found) = server.lookup("knowledge", &lost_id).await;
        if found["lookup"] == "found" {
            break true;
        }
        if Instant::now() > deadline {
            break false;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    };
    eprintln!("SPIKE lost response: landed before the retry = {landed}");
    let (status, retried) = server
        .submit("knowledge", json!({ "maintenance_id": lost_id }))
        .await;
    if !matches!(status, 200 | 202) {
        let (_, found) = server.lookup("knowledge", &lost_id).await;
        let on_disk = record(temp.path());
        eprintln!(
            "SPIKE lost response: retry refused {status} {retried}; lookup {found}; record active {}",
            on_disk["active"]
        );
        // Does the slot ever clear without a restart?
        tokio::time::sleep(Duration::from_secs(10)).await;
        let (status2, body2) = server
            .submit("knowledge", json!({ "maintenance_id": ulid(41) }))
            .await;
        eprintln!(
            "SPIKE lost response: a new id 10 s later: {status2} {}",
            body2["error"]
        );
        panic!("a dropped submission left the active slot held");
    }
    let terminal = server.wait_terminal("knowledge", &lost_id).await;
    let (status, again) = server
        .submit("knowledge", json!({ "maintenance_id": lost_id }))
        .await;
    assert_eq!(status, 200);
    assert_eq!(again["operation"], terminal["operation"]);
    let (_, commits) = main_head(temp.path(), "knowledge").await;
    eprintln!("SPIKE lost response: retry answered the record; commits on main {commits}");
    server.kill();
}

/// Fill the unresolved list with in-doubt publications on graph `knowledge`,
/// then show: a fifth admission is refused; a write on another graph does not
/// settle them; a write on `knowledge` settles them, but only after as many
/// bounded walk attempts as its commits since the doubt divided by the limit.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_unresolved_list_fills_and_only_its_own_graph_settles_it() {
    let temp = cluster_dir(&["knowledge", "other"], false);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", false).await;
    seed(temp.path(), "other", false).await;
    let walk = [("OMNIGRAPH_MAINTENANCE_RESOLUTION_WALK_LIMIT", "4")];
    for round in 0..4u32 {
        let mut server = spawn(
            temp.path(),
            &[
                ("OMNIGRAPH_SPIKE_PAUSE_AT", "server.after_publishing_write"),
                walk[0],
            ],
        );
        server.wait_ready().await;
        let pending = tokio::spawn(
            server
                .submit_request("knowledge", &json!({ "maintenance_id": ulid(100 + round) }))
                .send(),
        );
        server.wait_paused("server.after_publishing_write");
        let _ = pending.await;
        server.kill();
        unlock(temp.path()).await;
    }
    let mut server = spawn(temp.path(), &walk);
    server.wait_ready().await;
    let (status, refused) = server
        .submit("knowledge", json!({ "maintenance_id": ulid(200) }))
        .await;
    eprintln!("SPIKE fifth admission: {status} {}", refused["error"]);
    assert_eq!(status, 409, "{refused}");
    assert_eq!(
        record(temp.path())["unresolved"].as_array().unwrap().len(),
        4
    );

    // Maintenance on the other graph is refused too: the list is cluster-wide.
    let (status, refused) = server
        .submit("other", json!({ "maintenance_id": ulid(201) }))
        .await;
    eprintln!("SPIKE other-graph admission with 4 unresolved on knowledge: {status}");
    assert_eq!(status, 409, "{refused}");
    // A write on the other graph settles nothing.
    for n in 0..3 {
        let (status, body) = server.load("other", &format!("o{n}")).await;
        assert_eq!(status, 200, "{body}");
    }
    let (status, _) = server
        .submit("other", json!({ "maintenance_id": ulid(202) }))
        .await;
    assert_eq!(status, 409, "a write on another graph settles nothing");

    // Ten writes on knowledge, then admission attempts with a walk limit of 4.
    for n in 0..10 {
        let (status, body) = server.load("knowledge", &format!("k{n}")).await;
        assert_eq!(status, 200, "{body}");
    }
    let mut attempts = 0;
    loop {
        attempts += 1;
        let (status, body) = server
            .submit(
                "knowledge",
                json!({ "maintenance_id": ulid(300 + attempts) }),
            )
            .await;
        let unresolved = record(temp.path())["unresolved"].clone();
        eprintln!(
            "SPIKE attempt {attempts}: {status}; unresolved reasons {:?}",
            unresolved.as_array().map(|ops| ops
                .iter()
                .map(|op| op["reason"].clone())
                .collect::<Vec<_>>())
        );
        if matches!(status, 200 | 202) {
            break;
        }
        assert_eq!(status, 409, "{body}");
        assert!(attempts < 10, "never settled");
    }
    // 10 loads + 1 commit per unresolved... the input head's child is the
    // first load: 10 commits deep, so pages of 4 reach it on the third.
    assert_eq!(attempts, 3, "ceil(10 / 4) attempts settle a 10-deep child");
    for round in 0..4u32 {
        let (_, old) = server.lookup("knowledge", &ulid(100 + round)).await;
        assert_eq!(old["operation"]["state"], "failed", "{old}");
        assert_eq!(old["operation"]["reason"], "not_committed", "{old}");
    }
    server.kill();
}

/// Removing a graph from the cluster leaves its unresolved records with no
/// main head that can ever move: with four of them, maintenance is refused
/// on every other graph for good.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_deleted_graph_wedges_the_unresolved_list() {
    let temp = cluster_dir(&["knowledge", "other"], false);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", false).await;
    seed(temp.path(), "other", false).await;
    for round in 0..4u32 {
        let mut server = spawn(
            temp.path(),
            &[("OMNIGRAPH_SPIKE_PAUSE_AT", "server.after_publishing_write")],
        );
        server.wait_ready().await;
        let pending = tokio::spawn(
            server
                .submit_request("knowledge", &json!({ "maintenance_id": ulid(500 + round) }))
                .send(),
        );
        server.wait_paused("server.after_publishing_write");
        let _ = pending.await;
        server.kill();
        unlock(temp.path()).await;
    }
    // One more start moves the fourth (still in the active slot) to the list.
    {
        let mut server = spawn(temp.path(), &[]);
        server.wait_ready().await;
        let (status, _) = server
            .submit("other", json!({ "maintenance_id": ulid(600) }))
            .await;
        assert_eq!(status, 409);
        server.kill();
        unlock(temp.path()).await;
    }
    // Delete graph `knowledge` with a direct deployment while stopped.
    write_cluster_yaml(temp.path(), &["other"]);
    let caller = omnigraph_cluster::DeploymentCaller::storage_owner(Some("operator".into()));
    let applied = omnigraph_cluster::apply_deployment(temp.path(), None, &caller, |_, _, _| {})
        .await
        .unwrap();
    assert!(
        matches!(applied, omnigraph_cluster::DeploymentLookup::Complete { ref result } if result.converged),
        "{applied:?}"
    );
    unlock(temp.path()).await;
    assert!(
        !temp.path().join("graphs/knowledge.omni").exists(),
        "the graph root is deleted"
    );
    let mut server = spawn(temp.path(), &[]);
    server.wait_ready().await;
    for n in 0..3 {
        let (status, body) = server.load("other", &format!("o{n}")).await;
        assert_eq!(status, 200, "{body}");
    }
    let (status, refused) = server
        .submit("other", json!({ "maintenance_id": ulid(601) }))
        .await;
    eprintln!(
        "SPIKE after deleting graph knowledge: admission on other = {status} {}; unresolved = {}",
        refused["error"],
        record(temp.path())["unresolved"]
            .as_array()
            .map_or(0, |u| u.len())
    );
    assert_eq!(
        status, 409,
        "the list stays full with no graph that can settle it"
    );
    server.kill();
}

/// SIGTERM while the acceptance write is in flight.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_shutdown_during_the_acceptance_write() {
    for point in ["server.before_accept_write", "server.after_accept_write"] {
        let temp = cluster_dir(&["knowledge"], false);
        apply_cluster_fixture(temp.path()).await;
        seed(temp.path(), "knowledge", false).await;
        let id = ulid(700);
        let mut server = spawn(
            temp.path(),
            &[("OMNIGRAPH_SPIKE_PAUSE_AT", &format!("{point}:2000"))],
        );
        server.wait_ready().await;
        let pending = tokio::spawn(
            server
                .submit_request("knowledge", &json!({ "maintenance_id": id }))
                .timeout(Duration::from_secs(60))
                .send(),
        );
        server.wait_paused(point);
        server.sigterm();
        let answer = pending.await.unwrap();
        let answer = match answer {
            Ok(response) => format!(
                "{} {}",
                response.status(),
                response.text().await.unwrap_or_default()
            ),
            Err(error) => format!("transport error: {error}"),
        };
        let exit = server.wait_exit(Duration::from_secs(60));
        let on_disk = fs::read(temp.path().join("__cluster/maintenance.json"))
            .ok()
            .map(|bytes| serde_json::from_slice::<Value>(&bytes).unwrap());
        eprintln!(
            "SPIKE sigterm@{point}: caller saw [{answer}]; exit {exit:?}; record active = {}",
            on_disk
                .as_ref()
                .map_or("<no record>".to_string(), |r| r["active"]["state"]
                    .to_string())
        );
        drop(server);
        unlock(temp.path()).await;
        let mut server = spawn(temp.path(), &[]);
        server.wait_ready().await;
        let (_, found) = server.lookup("knowledge", &id).await;
        eprintln!(
            "SPIKE sigterm@{point}: after restart lookup = {} {}",
            found["lookup"], found["operation"]["state"]
        );
        assert_ne!(
            found["operation"]["state"], "succeeded",
            "no run started after SIGTERM"
        );
        server.kill();
    }
}

/// SIGTERM after the edge scan, before the artifact write is issued: the
/// write is skipped, the graph commit stands and the server exits cleanly.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_artifact_write_is_skipped_under_a_pending_shutdown() {
    let temp = cluster_dir(&["knowledge"], true);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", true).await;
    let artifact = temp
        .path()
        .join("graphs/knowledge.omni/__graph_index/csr-current.bin");
    let before = fs::metadata(&artifact).ok().map(|m| m.modified().unwrap());
    let id = ulid(800);
    let mut server = spawn(
        temp.path(),
        &[(
            "OMNIGRAPH_SPIKE_PAUSE_AT",
            "optimize.before_artifact_write:2000",
        )],
    );
    server.wait_ready().await;
    let (status, body) = server
        .submit("knowledge", json!({ "maintenance_id": id }))
        .await;
    assert!(matches!(status, 200 | 202), "{body}");
    server.wait_paused("optimize.before_artifact_write");
    server.sigterm();
    let exit = server.wait_exit(Duration::from_secs(60));
    let after = fs::metadata(&artifact).ok().map(|m| m.modified().unwrap());
    let on_disk = record(temp.path());
    let op = &on_disk["terminal"][0];
    eprintln!(
        "SPIKE artifact under shutdown: exit {exit:?}; state {}; artifact outcome {} {}; artifact file changed {}",
        op["state"],
        op["artifact"]["outcome"],
        op["artifact"]["detail"],
        before != after
    );
    assert_eq!(exit, Some(0), "a clean shutdown");
    assert_eq!(op["state"], "succeeded", "{on_disk}");
    assert!(op["published_commit"].is_string(), "{on_disk}");
    assert_eq!(op["artifact"]["outcome"], "skipped", "{on_disk}");
    assert_eq!(before, after, "no artifact write was issued");
}

/// Writers on main racing served optimize runs: every write succeeds; how
/// many runs fail because a write landed between their authority capture
/// and their gates.
#[tokio::test(flavor = "multi_thread", worker_threads = 8)]
async fn spike_writers_race_served_optimize() {
    let temp = cluster_dir(&["knowledge"], false);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", false).await;
    let mut server = spawn(temp.path(), &[]);
    server.wait_ready().await;
    let server = std::sync::Arc::new(server);
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut writers = Vec::new();
    for w in 0..3 {
        let server = server.clone();
        let stop = stop.clone();
        writers.push(tokio::spawn(async move {
            let mut latencies = Vec::new();
            let mut n = 0;
            while !stop.load(std::sync::atomic::Ordering::SeqCst) {
                let started = Instant::now();
                let (status, body) = server.load("knowledge", &format!("w{w}-{n}")).await;
                assert_eq!(status, 200, "{body}");
                latencies.push(started.elapsed());
                n += 1;
            }
            latencies
        }));
    }
    let mut outcomes = std::collections::BTreeMap::<String, u32>::new();
    let mut gate_holds = Vec::new();
    for run in 0..10u32 {
        let id = ulid(900 + run);
        let (status, body) = server
            .submit("knowledge", json!({ "maintenance_id": id }))
            .await;
        assert!(matches!(status, 200 | 202), "{body}");
        let terminal = server.wait_terminal("knowledge", &id).await;
        let op = &terminal["operation"];
        let key = format!(
            "{}{}",
            op["state"].as_str().unwrap(),
            op["reason"]
                .as_str()
                .map(|r| format!(" ({})", r.chars().take(90).collect::<String>()))
                .unwrap_or_default()
        );
        *outcomes.entry(key).or_default() += 1;
        gate_holds.push(op["timings"]["gate_hold_ms"].clone());
    }
    stop.store(true, std::sync::atomic::Ordering::SeqCst);
    let mut latencies = Vec::new();
    for writer in writers {
        latencies.extend(writer.await.unwrap());
    }
    latencies.sort();
    let p = |q: f64| latencies[((latencies.len() as f64 - 1.0) * q) as usize];
    eprintln!(
        "SPIKE writers race: {} writes all ok; latency p50 {:?} p99 {:?} max {:?}; optimize outcomes {outcomes:?}; gate holds ms {gate_holds:?}",
        latencies.len(),
        p(0.5),
        p(0.99),
        latencies.last().unwrap()
    );
}

fn cli() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/debug/omnigraph")
}

/// §3.6 row 1: a direct optimize on the served cluster root is refused by
/// the cluster lock. (The served CLI leg is `system_local::local_cli_optimize_runs_served_maintenance`.)
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_cli_walking_skeleton() {
    let temp = cluster_dir(&["knowledge"], false);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", false).await;
    let mut server = spawn(temp.path(), &[]);
    server.wait_ready().await;
    let home = tempfile::tempdir().unwrap();
    let run = |args: &[&str]| {
        let out = Command::new(cli())
            .args(args)
            .env("OMNIGRAPH_HOME", home.path())
            .output()
            .unwrap();
        (
            out.status.code(),
            String::from_utf8_lossy(&out.stdout).to_string(),
            String::from_utf8_lossy(&out.stderr).to_string(),
        )
    };
    let root = temp.path().to_str().unwrap();
    let (code, stdout, stderr) = run(&[
        "optimize",
        "--target",
        root,
        "--graph",
        "knowledge",
        "--json",
    ]);
    eprintln!(
        "SPIKE cli direct optimize on the served root: exit {code:?} stdout: {stdout} stderr: {stderr}"
    );
    assert_ne!(code, Some(0));
    assert!(format!("{stdout}{stderr}").contains("state_lock_held"));
    server.kill();
}

/// Split brain on one host: the owner is still mid-run when an operator
/// force-unlocks and a successor starts. The successor records the run
/// `interrupted`; when the old owner reaches its before-publish write, the
/// lock check and the record's conditional put refuse it, so nothing
/// publishes and `interrupted` stays true.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_old_owner_is_fenced_by_the_record_after_a_takeover() {
    let temp = cluster_dir(&["knowledge"], false);
    apply_cluster_fixture(temp.path()).await;
    seed(temp.path(), "knowledge", false).await;
    let (head_before, _) = main_head(temp.path(), "knowledge").await;
    let id = ulid(1000);
    let mut old = spawn(
        temp.path(),
        &[(
            "OMNIGRAPH_SPIKE_PAUSE_AT",
            "optimize.after_table_effect:6000",
        )],
    );
    old.wait_ready().await;
    let (status, body) = old
        .submit("knowledge", json!({ "maintenance_id": id }))
        .await;
    assert!(matches!(status, 200 | 202), "{body}");
    old.wait_paused("optimize.after_table_effect");
    unlock(temp.path()).await;
    let mut new = spawn(temp.path(), &[]);
    new.wait_ready().await;
    let (_, seen) = new.lookup("knowledge", &id).await;
    eprintln!(
        "SPIKE takeover: successor sees {}",
        seen["operation"]["state"]
    );
    assert_eq!(seen["operation"]["state"], "interrupted", "{seen}");
    // Let the old owner reach its before-publish write and finish.
    tokio::time::sleep(Duration::from_secs(8)).await;
    let (head_after, _) = main_head(temp.path(), "knowledge").await;
    let (_, after) = new.lookup("knowledge", &id).await;
    let old_exit = old.child.try_wait().unwrap().map(|s| s.code());
    eprintln!(
        "SPIKE takeover: after the old owner resumed: state {} head moved {} old owner exited {:?}",
        after["operation"]["state"],
        head_after != head_before,
        old_exit
    );
    assert_eq!(after["operation"]["state"], "interrupted");
    assert_eq!(
        head_after, head_before,
        "the fenced owner published nothing"
    );
    old.kill();
    new.kill();
}

/// A record from another ledger at this root is refused and never
/// overwritten.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_foreign_record_is_refused() {
    let temp = cluster_dir(&["knowledge"], false);
    apply_cluster_fixture(temp.path()).await;
    let foreign = json!({"version": 1, "ledger_id": "01JC9999999999999999999999"});
    fs::write(
        temp.path().join("__cluster/maintenance.json"),
        foreign.to_string(),
    )
    .unwrap();
    let mut server = spawn(temp.path(), &[]);
    server.wait_ready().await;
    let (status, refused) = server
        .submit("knowledge", json!({ "maintenance_id": ulid(1100) }))
        .await;
    let (lstatus, _) = server.lookup("knowledge", &ulid(1100)).await;
    eprintln!(
        "SPIKE foreign record: submit {status} {}; lookup {lstatus}",
        refused["error"]
    );
    assert_eq!(status, 409);
    assert_eq!(lstatus, 409);
    assert_eq!(record(temp.path()), foreign, "never overwritten");
    server.kill();
}

/// "Disconnection stops waiting, not execution": the caller disconnects
/// while the handler is between the acceptance write and the run's
/// registration as an owned operation.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn spike_disconnect_between_acceptance_and_registration() {
    for point in ["server.before_accept_write", "server.after_accept_write"] {
        let temp = cluster_dir(&["knowledge"], false);
        apply_cluster_fixture(temp.path()).await;
        seed(temp.path(), "knowledge", false).await;
        let id = ulid(1200);
        let mut server = spawn(
            temp.path(),
            &[("OMNIGRAPH_SPIKE_PAUSE_AT", &format!("{point}:1500"))],
        );
        server.wait_ready().await;
        let dropped = server
            .submit_request("knowledge", &json!({ "maintenance_id": id }))
            .timeout(Duration::from_millis(300))
            .send()
            .await;
        assert!(dropped.is_err(), "the caller gave up");
        tokio::time::sleep(Duration::from_secs(3)).await;
        let (_, found) = server.lookup("knowledge", &id).await;
        let state = if found["lookup"] == "unknown" {
            "unknown".to_string()
        } else {
            found["operation"]["state"].as_str().unwrap().to_string()
        };
        let state = if state == "unknown" || state == "interrupted" {
            state
        } else {
            server.wait_terminal("knowledge", &id).await["operation"]["state"]
                .as_str()
                .unwrap()
                .to_string()
        };
        let (status, _) = server
            .submit("knowledge", json!({ "maintenance_id": ulid(1201) }))
            .await;
        eprintln!(
            "SPIKE disconnect@{point}: the dropped submission ended {state}; a new id then answers {status}"
        );
        server.kill();
    }
}
