# Stress and fuzz harness

These scripts run Omnigraph user journeys through the built `omnigraph` CLI and server. They compare each result with a small Python model of the expected answer. The reports of the runs that wrote them are [docs/stress/20261106.md](../../docs/stress/20261106.md) and [docs/stress/20261108.md](../../docs/stress/20261108.md) and [docs/stress/20261111.md](../../docs/stress/20261111.md) and [docs/stress/20261116.md](../../docs/stress/20261116.md) and [docs/stress/20261117.md](../../docs/stress/20261117.md) (two runs).

The scripts are not part of CI. They write scratch graphs under `/tmp/og/`. Set `OG` to the CLI binary under test. The default is `omnigraph` on `PATH`.

| Script | What it checks |
|---|---|
| `qfuzz.py` | Filters with three-valued null logic, `in`, `contains`, `starts_with`, Date comparisons, order and limit, aggregates, correlated blocks, and mutation `where` predicates, against a Python oracle |
| `qfuzz2.py` | I32, U64, F32 and DateTime comparisons, bounded and undirected traversal with shortest-path semantics, `count(distinct)`, and order by two keys |
| `nullfuzz.py` | Many predicate combinations on one nullable column, to find optimizer rewrites that break null logic |
| `mfuzz.py` | Random writes on two branches with merges in both directions, against a three-way merge model. `MFUZZ_OPT=1` runs `optimize` before half of the merges |
| `dfuzz.py` | `omnigraph diff --from A --to B` against the net change between two model states |
| `cfuzz.py` | A change-feed replica, built from `changes baseline` and `changes poll`, against the branch state |
| `kfuzz.py` | `@unique`, `@range`, `@check` and `@card` under loads, mutations and merges |
| `vfuzz.py` | `nearest` with filters and traversal against exact squared L2, before and after `index build` and `optimize` |
| `lintfuzz.py` | Token mutations of the GQT corpus through `omnigraph lint`, to find panics and hangs |
| `conc.py`, `branchstress.py`, `window.py` | Concurrent served writers, merges and readers, through targets named `alice`, `bob` and `carol` |
| `ufuzz.py` | Partial `update`s of scalar, list, Blob and vector properties, merge loads, deletes, `optimize` and `index build`, on main and on merged branches, against a full `export` |
| `pfuzz.py` | Making a property required and adding `@unique` after a random history: the apply must refuse with `OG-MF-104` or `OG-MF-108` exactly when a live row breaks the rule |
| `hscale.py` | Deep history and many branches: historical reads, `commit list`, `diff`, branches from old commits, merges and `cleanup` |
| `cpatch.py` | Concurrent direct writers, `optimize`, `index build` and readers on one graph: no lost update, index results equal to a scan |
| `sserve.py` | Read-your-writes, branch merges and index checks against one long-lived server over HTTP |
| `mcpprobe.py` | The documented MCP contracts of a local server |
| `killtest.py` | `SIGKILL` of a CLI load, then an atomicity check |
| `lfuzz.py` | An unfiltered `limit K` on main, on branches and at older commits, after deletes, updates, merge loads, `optimize`, merges and an added property: each answer holds min(K, live) distinct live rows |
| `s3fault.py` | Served writes and recorded `optimize` on an S3 cluster root (RustFS) behind toxiproxy, with an optional restart (`--restart`) and long outages (`HARSH=1`). A supervisor restarts a server that stops itself. No acknowledged write may be lost, no row may appear that no writer attempted |
| `upj.py` | A graph written by the previous release (`OLD`), then read, written, maintained and upgraded by the candidate (`NEW`), with reads compared across the two binaries |
| `nopolicy.py` | Every route of `openapi.json` and the MCP tools on a graph without a policy, next to a graph with one |
| `tfuzz.py` | Traversal answers on one long-lived server that keeps its cached CSR topology across edge writes, node deletes, branch delete and recreate, merges and recorded `optimize` (#1363). `TF_READERS=N` adds concurrent readers, each judged against the model at the commit the server read |
| `ifuzz.py` | Keyed writes through the id index (#1318): merge loads that are all new, all present, mixed or bring back deleted ids, keyed inserts, updates by key, deletes, `index build` and `optimize`, against a full `export` and point reads |
| `rfuzz.py` | Deep `and`/`or`/`not` filters (#1346) on one table of up to 600 rows, across a traversal and as cross-join residuals, against qfuzz's three-valued oracle |
| `hfuzz.py` | Reads of commits by id on one long-lived server (#1184): snapshot queries, commit changes and diff for commits on branches that are merged, deleted and recreated |
| `optloop.py` | A recorded `optimize` every few seconds beside `sserve.py` |
| `tlsproxy.py` | A TLS proxy for a local server, with fault modes in `/tmp/og/proxy_mode` (`pass`, `drop_post`, `drop_response`, `marker:<text>`, `delay:<seconds>`) |
| `s3merge.py` | On an S3 cluster root (RustFS) behind toxiproxy: writers on main and on short-lived branches, merges while the branch writers run, branch deletes, and served `cluster apply` through the CLI that adds a nullable property and a stored query, whenever only main is live. Each merge answered 200 must carry every row acknowledged on its branch before it was sent; no acknowledged row or applied property may be lost, and nothing may appear that nobody attempted. `HARSH=1` adds long outages; `KILL_APPLY=1` SIGKILLs the server during half of the applies and observes each by its deployment id after the lease takeover |
| `optresume.py` | Bounded recorded `optimize` over four node and three edge tables with a small run byte budget, writes between and during runs, and `--kill` to SIGKILL the server mid-run: every operation reaches a terminal state, a run starts at the recorded `resume_at`, every table is reached, and no row is lost |
| `cread.py` | `cleanup` beside reader processes in the supported topology (write bursts and cleanups alternate): a count, a full export and Blob reads of main's head during which no write happened must succeed with the exact answer |
| `mvfuzz.py` | Branch merges of large deltas (up to about 9,000 rows a side, some 20 to 60 KB wide) with conflicts planted at random positions: one new `@unique` value on both sides, `@card(0..2)` and `@card(1..1)` overflow from edges added on both sides, orphan edges, divergent inserts and delete against update. The dry run and the merge must succeed exactly when the three-way result is valid, and main must then equal the model in a full `export`. `MV_LINEAGE` sets `OMNIGRAPH_MERGE_LINEAGE` (`verify` by default) |
| `keylimit.py` | The merge validation key limit of 32 MiB (#1433): a branch past it is refused with `branch-merge retained validation key bytes` and `effects: none`, then merges in two stages through `branch create --from-commit`. Case B plants a `@unique` clash late in the branch, which stage 2 must refuse. `KL_PLAIN=1` uses a type with no constraint, whose fast-forward needs no check |
| `crace.py` | Conditional writes that race (#1426): writers that read one commit insert different edges under `@card(0..1)`, or nodes with one `@unique` value, with `--if-commit`. Exactly one may win. Every loser must exit 4 with `precondition_failure`. Runs on main and on a fresh branch that was never written |
| `polmatrix.py` | Direct writes to a cluster graph under its applied policy (#1423, #1434): loads, mutations, branch create, merge and delete, optimize, index build and cleanup for three actors and no actor, through the root with `--graph` and through the graph's own store, against `omnigraph access`. A refused command must change nothing. It makes its cluster under `/tmp/og/pol` on first use |
| `ufuzz2.py` | `ufuzz.py` with `@index` on two scalar properties, where a failed `optimize` or `index build` is a finding. The known failure `og-stress-vector-index-after-column-patch-blocks-index-build` is counted, not reported |
| `vminimize.py` | Records the CLI calls of one `ufuzz.py` seed up to the first failing `index build` and removes calls while the replay still fails |

Each fuzzer takes a first seed and a count, for example `OG=target/debug/omnigraph python3 tools/stress/qfuzz.py 100 400`. A failing seed writes its case to the directory that the script names.

The served scripts need an operator configuration in each `OMNIGRAPH_HOME` they use (`/tmp/og/h<N>`), with three token targets for one cluster. Bearer tokens need HTTPS, so put `tlsproxy.py` in front of `omnigraph-server` and add its CA as `transport.tls.ca`.

`repros/` holds the GQT cases of the bugs this run filed. They fail on the base commit. They are outside `crates/omnigraph-gqt/cases/`, so CI does not discover them. Run one with `target/debug/omnigraph-gqt tools/stress/repros/<case>.gqt`.

`repros/vector_partition_count.sh` reproduces the finding of the second 2026-11-17 run: after an update patches a vector column, `index build` fails on every run. `repros/server_boot_refusal_keeps_lock.sh` and `repros/csv_null_and_string_null.sh` reproduce two findings of the 2026-11-16 run. `repros/optimize_table_over_run_budget.py` reproduces the 2026-11-17 documentation finding: a table larger than the optimize run byte budget is deferred on every served run. `repros/export_blocks_branch_control.py` and `repros/optimize_state_read_409.py` reproduce #1355 and #1356 against a running build. The second needs RustFS on `127.0.0.1:9000` and `toxiproxy-server` on `127.0.0.1:8474`. Without Docker, download both as release binaries.
