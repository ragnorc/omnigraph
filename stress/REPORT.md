# Stress report: remote optimize (PR #1275 engine + PR #1282 served)

**Branch under test:** `feat/optimize-served` at `f6b2443c` (PR #1282, stacked on
#1275 at `5667fddb`). **Stress branch:** `stress/optimize-1282` on
`https://github.com/ragnorc/omnigraph`. **Box:** 2 cores reported (`nproc`),
64 GiB, gVisor, local disk; RustFS 1.0.0-beta.12 (the release CI pins, binary
verified against the release `SHA256SUMS`) run locally as the S3 backend.
**Started** 2026-11-09 00:32 UTC.

This file is the deliverable. Raw notes are in `FINDINGS-DRAFT.md`; every
number below comes from a file under `evidence/` produced by a harness in this
directory.

## Verdict

_Written last; see the end of the file._

## How to reproduce

All harnesses drive the real `omnigraph` and `omnigraph-server` binaries (built
with `cargo dev-build-tests`) or, for exact crash points, a seam-armed copy of
the production server (`served_child.rs`, wired as the `stress_served_child`
test target of `omnigraph-server`). Python harnesses use only the standard
library.

| File | What it is |
|---|---|
| `og.py`, `invariants.py` | HTTP client, cluster apply/unlock, server lifecycle, record reader (local or S3), invariant checks against main's history |
| `fixture/`, `fixture_v2/`, `fixture_other2/`, `fixture_nokg/`, `fixture_big/` | Cluster sources: `kg` (Person/Company/Doc with FTS, vector, Blob; Knows/WorksAt/Authored edges), `other`; variants for deployments; a 1,030-table catalog |
| `served_child.rs` | Production `serve()` with seams armed at runtime from a control directory (`hold`, `fail_once`, `fail_always`, `panic`) |
| `engine_optimize_stress.rs` | Campaign 1 (embedded), wired as the `engine_optimize_stress` test target of `omnigraph-engine` |
| `faultfs.c` | `LD_PRELOAD` shim: fail/delay/lose the answer of renames onto a path; delay opens of a path |
| `tlsproxy.py` | TLS-terminating HTTP fault proxy (drop/truncate/504/delay) for the CLI's server target |
| `s3proxy.py`, `s3util.py` | Plain-HTTP fault proxy between the server and RustFS (`drop_response`, `late_forward`, `drop_request`); stdlib SigV4 client |
| `c2_served_hammer.py` | Campaign 2 |
| `c3_crash.py`, `c3_settle_by_merge.py`, `c3_sigterm_artifact.py`, `c3_lease_loss.py` | Campaign 3 |
| `c4_deploy.py` | Campaign 4 |
| `c5_record_write_faults.py`, `c5_record_bounds.py`, `c5_big_catalog.py`, `c5_s3_lost_answer.py` | Campaign 5 |
| `c6_cli.py` | Campaign 6 |
| `c1_served_early_exit.py`, `c1_reads_during_optimize.py`, `c2_unresolved_cap.py` | Targeted scenarios |

Ports: this box's ephemeral range is 16000–65535, so every server binds below
16000. Set `STRESS_S3_PREFIX=s3://bucket/prefix` (with RustFS on
127.0.0.1:9000, keys `omnigraphci`/`omnigraphci-secret`) to run campaigns 2
and 3 on S3.
