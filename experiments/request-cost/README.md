# Request-aware plan cost: experiment

This experiment tests the RFC "Request-aware plan cost" (2026-10-13,
branch `rfc/request-aware-plan-cost`): does pricing request waves let the v2
planner pick faster plans on object storage without changing any local plan?
It is not a pull request. The answer is in `FINDINGS.md`, the generated
tables are in `results/RESULTS.md`, and the calibration is in
`CALIBRATION.md`.

## Contents

| Path | What it is |
|---|---|
| `gen.py` | Writes the GQT cases (`two_hop_200k`, `two_hop_20k`, `hub_200k`, `small_control`). `PROFILE=...` adds a `# storage_profile:` pin. |
| `bless.sh`, `expected.json` | The expected rows, blessed on a direct-engine copy. |
| `measure.sh` | Runs the cases under `--measure` for the `unit` and `s3-like` models, in DST. |
| `summarize.py`, `compare.py` | One row per query step, and before/after tables. |
| `anchor.py` | Step 5: model predictions for each written order against the measured rankings. |
| `realstore/` | RustFS plus a per-request latency proxy, timing the shapes on a real S3 API. |
| `results/baseline` | The rows-only planner (no pin). |
| `results/os<L>` | The object-store pin at `latency_units = L`. `os20000` is the default. |
| `results/local-cpu` | Release-build warm wall time of each plan on a local graph, plus the calibration runs. |
| `results/realstore` | Release-build warm wall time on RustFS with 17 ms injected per request. |

## Reproduce

```bash
cargo build -p omnigraph-gqt --bin omnigraph-gqt --locked
experiments/request-cost/measure.sh baseline two_hop_200k two_hop_20k hub_200k small_control
PROFILE="object_store" experiments/request-cost/measure.sh os20000 two_hop_200k two_hop_20k hub_200k small_control
PROFILE="object_store latency_units=48000" experiments/request-cost/measure.sh os48000 two_hop_200k
experiments/request-cost/compare.py experiments/request-cost/results/baseline experiments/request-cost/results/os20000

cargo build --release -p omnigraph-engine --example request_cost_calibrate --locked
target/release/examples/request_cost_calibrate build /tmp/og-calib 200000
target/release/examples/request_cost_calibrate time /tmp/og-calib 25
target/release/examples/request_cost_calibrate shapes /tmp/og-calib 9 12000 48000
RUSTFS_BIN=/path/to/rustfs experiments/request-cost/realstore/realstore.sh 20000 5 17
```

Every step runs three times: after the seed, after a `--- restart` (cold),
and again (warm). Every case asserts its rows, so a plan change that moved a
row would fail the run.
