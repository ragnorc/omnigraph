# Stress and fuzz harness

These scripts run Omnigraph user journeys through the built `omnigraph` CLI and server. They compare each result with a small Python model of the expected answer. The report of the run that wrote them is [docs/stress/20261106.md](../../docs/stress/20261106.md).

The scripts are not part of CI. They write scratch graphs under `/tmp/og/`. Set `OG` to the CLI binary under test. The default is `omnigraph` on `PATH`.

| Script | What it checks |
|---|---|
| `qfuzz.py` | Filters with three-valued null logic, `in`, `contains`, `starts_with`, Date comparisons, order and limit, aggregates, correlated blocks, and mutation `where` predicates, against a Python oracle |
| `qfuzz2.py` | I32, U64, F32 and DateTime comparisons, bounded and undirected traversal with shortest-path semantics, `count(distinct)`, and order by two keys |
| `nullfuzz.py` | Many predicate combinations on one nullable column, to find optimizer rewrites that break null logic |
| `mfuzz.py` | Random writes on two branches with merges in both directions, against a three-way merge model |
| `dfuzz.py` | `omnigraph diff --from A --to B` against the net change between two model states |
| `cfuzz.py` | A change-feed replica, built from `changes baseline` and `changes poll`, against the branch state |
| `kfuzz.py` | `@unique`, `@range`, `@check` and `@card` under loads, mutations and merges |
| `vfuzz.py` | `nearest` with filters and traversal against exact squared L2, before and after `index build` and `optimize` |
| `lintfuzz.py` | Token mutations of the GQT corpus through `omnigraph lint`, to find panics and hangs |
| `conc.py`, `branchstress.py`, `window.py` | Concurrent served writers, merges and readers, through targets named `alice`, `bob` and `carol` |
| `killtest.py` | `SIGKILL` of a CLI load, then an atomicity check |
| `tlsproxy.py` | A TLS proxy for a local server, with fault modes in `/tmp/og/proxy_mode` (`pass`, `drop_post`, `drop_response`, `delay:<seconds>`) |

Each fuzzer takes a first seed and a count, for example `OG=target/debug/omnigraph python3 tools/stress/qfuzz.py 100 400`. A failing seed writes its case to the directory that the script names.

The served scripts need an operator configuration in each `OMNIGRAPH_HOME` they use (`/tmp/og/h<N>`), with three token targets for one cluster. Bearer tokens need HTTPS, so put `tlsproxy.py` in front of `omnigraph-server` and add its CA as `transport.tls.ca`.

`repros/` holds the GQT cases of the bugs this run filed. They fail on the base commit. They are outside `crates/omnigraph-gqt/cases/`, so CI does not discover them. Run one with `target/debug/omnigraph-gqt tools/stress/repros/<case>.gqt`.
