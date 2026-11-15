# Calibration record: the object-store profile's row-unit constants

The planner's cost unit is one indexed-probe row. The object-store profile
prices a request wave at `latency_units` row units, which is 20 ms divided by
the CPU cost of one indexed-probe row on a reference host.

## Host

- Modal sandbox under gVisor (kernel string `4.19.0-gvisor`), AMD CPU (the
  sandbox hides the model name), 32 vCPUs visible, `nproc` 2, 64 GiB memory
- Debian GNU/Linux 13 (trixie), root on an overlay filesystem (the graph
  lives under `/tmp` on it)
- Rust 1.97.1 (`rust-toolchain.toml`), release profile, the workspace
  rustflags (`--cfg tokio_unstable`)
- Lance 13.0.0 (crates.io), Omnigraph at the branch's head

## Method

`crates/omnigraph/examples/request_cost_calibrate.rs`, release build:

```bash
cargo build --release -p omnigraph-engine --example request_cost_calibrate --locked
target/release/examples/request_cost_calibrate build /tmp/og-calib 200000
target/release/examples/request_cost_calibrate time /tmp/og-calib 25
```

The graph has 200,000 Person rows in 50 fragments, 200 Company rows, 20 City
rows, one WorksAt edge per person (uniform), and indexes built. Every probe
is warm: three warm-up runs, then the median of `reps` runs in one process.
The frontier is `k` companies (`k × 1,000` edges), for k in 1, 2, 4, 8, 16, 24.
Each row unit is a least-squares slope over k:

- indexed-probe row: (pinned-indexed hop) minus (the same hop on a warm
  pinned CSR), per edge. Both plans read the destination by `id_lookup`
  (the planner always scans it), so the difference is the indexed hop less
  a CSR neighbour read.
- `id_lookup` row: the warm-CSR query's own slope per destination row.
- scan row: an aggregate over Person minus the same over Company, per row.
- CSR build edge: a fresh handle's first CSR-pinned hop minus its first
  indexed hop, per edge of the 200,000-edge build.

## Measurements

Five runs (`results/local-cpu/calibration_200k.txt`,
`calibration_repeats.txt`, and the first run in the experiment log):

| Run | reps | indexed-probe row | id_lookup row (count / 3 columns) | scan row | CSR build edge | 20 ms in row units |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 15 | 1.628 µs | 16.17 / 21.67 µs | 0.489 µs | 0.414 µs | 12,285 |
| 2 | 15 | 0.760 µs | 16.40 / 21.57 µs | 0.472 µs | 0.404 µs | 26,304 |
| 3 | 25 | 0.980 µs | 16.65 / 21.65 µs | 0.514 µs | 0.406 µs | 20,401 |
| 4 | 25 | 0.456 µs | 16.97 / 22.22 µs | 0.510 µs | 0.420 µs | 43,899 |
| 5 | 25 | 1.050 µs | 16.54 / 21.45 µs | 0.491 µs | 0.355 µs | 19,042 |

The `id_lookup`, scan and CSR weights are stable within 5%. The
indexed-probe row is a difference of two medians of similar size, so it
varies 3.5× between runs. Its median is 0.98 µs, which puts 20 ms at about
20,000 row units (12,000 to 44,000 across the runs).

## Derived constants

- `OBJECT_STORE_LATENCY_UNITS = 20_000`: the median.
- `OBJECT_STORE_BYTES_PER_UNIT = 3_300`: 64 requests in flight at the GQT
  `s3-like` model's 50 MiB/s each move about 3,300 bytes in 0.98 µs.
- `OBJECT_STORE_PARALLELISM = 64`: Lance's cloud I/O parallelism.

## What the weights say about today's row constants

In indexed-probe rows, at the median 0.98 µs:

| Row | Today's constant | Measured |
|---|---:|---:|
| `id_lookup` row | 8 (`HASH_JOIN_RATIO`) | 17 (count), 22 (3 columns) |
| scan / hash-join build row | 1 | 0.5 |
| CSR build edge | 1.5 (`CSR_BUILD_FACTOR`) | 0.4 |

So on this host the rows-only model underprices `id_lookup` by 2–3× and
overprices a scan by 2× and a CSR build by about 4×. The local profile keeps
today's constants by design, so local plans do not change.

## Caveats

- One host with a hidden CPU model, under gVisor, with 2 effective cores. The
  slopes include local filesystem I/O through gVisor, which a server reading
  object storage does not pay. Its time there is network time, which the GQT
  `s3-like` model already counts.
- The indexed-probe slope is the least stable number, and `latency_units`
  depends on it inversely.
- The in-region request latency (about 20 ms) comes from the RFC's Railway
  measurement, not from this host.
