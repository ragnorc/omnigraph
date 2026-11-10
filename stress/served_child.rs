//! A production `omnigraph-server` on a cluster root with seams armed from a
//! control directory, for the crash and fault campaigns (stress/).
//!
//! Run (one test, ignored by default):
//!   STRESS_ROOT=<cluster root> STRESS_CTL=<dir> STRESS_BIND=127.0.0.1:PORT \
//!     <test binary> --exact stress_served_child --ignored --nocapture
//!
//! The child polls `STRESS_CTL` for `*.cmd` files, each holding one line
//! `<seam> <action> [n]`, applies it and renames the file to `*.done`:
//!
//!   hold         park the first crossing; writes `<seam>.reached` when parked,
//!                releases when `<seam>.release` exists
//!   fail_once N  fail the N-th crossing (1-based)
//!   fail_always  fail every crossing
//!   panic        panic at every crossing (a simulated process death)
//!   clear        remove the seam's behavior
//!
//! Every other server setting comes from the environment, as for the binary.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use omnigraph::seams::catalog as seams;

use omnigraph::seams::{Decide as DecideBehavior, DecideSeam as Decide, Global, Hold, Installed};

fn seam(name: &str) -> Option<&'static Decide> {
    Some(match name {
        "optimize.post_authority_capture_pre_gates" => {
            &seams::OPTIMIZE_POST_AUTHORITY_CAPTURE_PRE_GATES
        }
        "optimize.before_compact" => &seams::OPTIMIZE_BEFORE_COMPACT,
        "optimize.post_table_effect" => &seams::OPTIMIZE_POST_TABLE_EFFECT,
        "optimize.post_phase_b_pre_manifest_commit" => {
            &seams::OPTIMIZE_POST_PHASE_B_PRE_MANIFEST_COMMIT
        }
        "optimize.configuration_clear" => &seams::OPTIMIZE_CONFIGURATION_CLEAR,
        "graph_publish.after_manifest_commit" => &seams::GRAPH_PUBLISH_AFTER_MANIFEST_COMMIT,
        "graph_publish.before_commit_append" => &seams::GRAPH_PUBLISH_BEFORE_COMMIT_APPEND,
        "publish.pre_merge" => &seams::PUBLISH_PRE_MERGE,
        "publish.post_merge_pre_ack" => &seams::PUBLISH_POST_MERGE_PRE_ACK,
        "publish.read_back" => &seams::PUBLISH_READ_BACK,
        "publish.load_state" => &seams::PUBLISH_LOAD_STATE,
        "mutation.post_finalize_pre_publisher" => &seams::MUTATION_POST_FINALIZE_PRE_PUBLISHER,
        // the cluster's deployment seams, by their catalog names
        other => return omnigraph_cluster::seams::catalog::decide(other),
    })
}

enum Armed {
    Installed(#[allow(dead_code)] Installed<dyn DecideBehavior, Global<dyn DecideBehavior>>),
}

fn control_loop(ctl: PathBuf) {
    let mut armed: HashMap<String, Armed> = HashMap::new();
    loop {
        if let Ok(entries) = std::fs::read_dir(&ctl) {
            let mut cmds: Vec<_> = entries
                .filter_map(Result::ok)
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|x| x == "cmd"))
                .collect();
            cmds.sort();
            for cmd in cmds {
                let text = std::fs::read_to_string(&cmd).unwrap_or_default();
                let mut parts = text.split_whitespace();
                let (Some(name), Some(action)) = (parts.next(), parts.next()) else {
                    let _ = std::fs::rename(&cmd, cmd.with_extension("bad"));
                    continue;
                };
                let n: u64 = parts.next().and_then(|v| v.parse().ok()).unwrap_or(1);
                let Some(s) = seam(name) else {
                    let _ =
                        std::fs::write(cmd.with_extension("bad"), format!("unknown seam {name}"));
                    let _ = std::fs::remove_file(&cmd);
                    continue;
                };
                armed.remove(name);
                match action {
                    "hold" => {
                        let (installed, hold) = s.hold();
                        let reached = ctl.join(format!("{name}.reached"));
                        let release = ctl.join(format!("{name}.release"));
                        let hold: Arc<Hold> = hold;
                        std::thread::spawn(move || {
                            loop {
                                if hold.reached() && !reached.exists() {
                                    let _ = std::fs::write(&reached, b"");
                                }
                                if release.exists() {
                                    hold.release();
                                    return;
                                }
                                std::thread::sleep(Duration::from_millis(5));
                            }
                        });
                        armed.insert(name.to_string(), Armed::Installed(installed));
                    }
                    "fail_once" => {
                        armed.insert(name.to_string(), Armed::Installed(s.fail_once_at(n)));
                    }
                    "fail_always" => {
                        armed.insert(name.to_string(), Armed::Installed(s.fail_always()));
                    }
                    "panic" => {
                        armed.insert(name.to_string(), Armed::Installed(s.panic_at()));
                    }
                    _ => {}
                }
                let _ = std::fs::rename(&cmd, cmd.with_extension("done"));
            }
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "hunt: stress/ seam-armed served child, launched by the stress harnesses"]
async fn stress_served_child() {
    let Some(root) = std::env::var_os("STRESS_ROOT").map(PathBuf::from) else {
        return;
    };
    omnigraph_server::init_tracing();
    let _scenario = omnigraph::seams::FailScenario::setup();
    if let Some(ctl) = std::env::var_os("STRESS_CTL").map(PathBuf::from) {
        std::fs::create_dir_all(&ctl).unwrap();
        std::thread::spawn(move || control_loop(ctl));
    }
    let mut config = omnigraph_server::load_server_settings(Some(&root), None, true, false)
        .await
        .unwrap();
    config.bind = std::env::var("STRESS_BIND").unwrap_or_else(|_| "127.0.0.1:0".into());
    omnigraph_server::serve(config).await.unwrap();
}

/// Cluster administration for roots the CLI reaches only through a registered
/// target (`az://`): `STRESS_ADMIN=bootstrap STRESS_CONFIG=<dir>` applies the
/// bundle (its `storage:` names the root) and releases the lock the direct
/// owner keeps; `STRESS_ADMIN=unlock STRESS_ADMIN_ROOT=<root>` force-unlocks
/// the retained lock. Run under the Azure admission wrapper (`--mode job`).
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "hunt: stress/ cluster administration for az:// roots, launched by the stress harnesses"]
async fn stress_cluster_admin() {
    let Ok(mode) = std::env::var("STRESS_ADMIN") else {
        return;
    };
    let caller = omnigraph_cluster::DeploymentCaller::storage_owner(None);
    let root = match mode.as_str() {
        "bootstrap" => {
            let config = std::env::var("STRESS_CONFIG").unwrap();
            let applied = omnigraph_cluster::apply_deployment(&config, None, &caller, |_, _, _| {})
                .await
                .unwrap();
            let omnigraph_cluster::DeploymentLookup::Complete { result } = applied else {
                panic!("bootstrap did not complete: {applied:?}")
            };
            assert!(result.converged, "{result:?}");
            std::env::var("STRESS_ADMIN_ROOT").unwrap()
        }
        "unlock" => std::env::var("STRESS_ADMIN_ROOT").unwrap(),
        other => panic!("unknown STRESS_ADMIN {other}"),
    };
    let status = omnigraph_cluster::deployment_status(&root, None, &caller)
        .await
        .unwrap();
    match status.lock_id {
        Some(lock) => {
            omnigraph_cluster::force_unlock_storage_root(&root, &lock)
                .await
                .unwrap();
            println!("STRESS_UNLOCKED {lock}");
        }
        None => println!("STRESS_UNLOCKED none"),
    }
}
