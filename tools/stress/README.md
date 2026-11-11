# Stress and fuzz harness

These scripts run Omnigraph user journeys through the built `omnigraph` CLI and server. They compare each result with a small Python model of the expected answer. The reports of the runs that wrote them are [docs/stress/20261106.md](../../docs/stress/20261106.md) and [docs/stress/20261108.md](../../docs/stress/20261108.md) and [docs/stress/20261111.md](../../docs/stress/20261111.md).

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
| `tlsproxy.py` | A TLS proxy for a local server, with fault modes in `/tmp/og/proxy_mode` (`pass`, `drop_post`, `drop_response`, `marker:<text>`, `delay:<seconds>`) |

Each fuzzer takes a first seed and a count, for example `OG=target/debug/omnigraph python3 tools/stress/qfuzz.py 100 400`. A failing seed writes its case to the directory that the script names.

The served scripts need an operator configuration in each `OMNIGRAPH_HOME` they use (`/tmp/og/h<N>`), with three token targets for one cluster. Bearer tokens need HTTPS, so put `tlsproxy.py` in front of `omnigraph-server` and add its CA as `transport.tls.ca`.

`repros/` holds the GQT cases of the bugs this run filed. They fail on the base commit. They are outside `crates/omnigraph-gqt/cases/`, so CI does not discover them. Run one with `target/debug/omnigraph-gqt tools/stress/repros/<case>.gqt`.

`repros/export_blocks_branch_control.py` and `repros/optimize_state_read_409.py` reproduce #1355 and #1356 against a running build. The second needs RustFS on `127.0.0.1:9000` and `toxiproxy-server` on `127.0.0.1:8474`. Without Docker, download both as release binaries.
