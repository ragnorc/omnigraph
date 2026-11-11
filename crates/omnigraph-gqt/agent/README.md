# The GQ agent instrument

**Audience:** anyone changing GQ's surface, its diagnostics or the agent skill,
and anyone deciding the GQ 3 gate
**Authority:** what this directory measures and how to rerun it; the gate it
serves is stage M0 of the GQ 3 RFC (`docs/rfcs/2026-11-07-gq-3.md`,
section 11 and `blocked_on` item 2)

An agent is GQ's main author, and what it costs is round trips. This
instrument asks a headless agent, given one reference card per arm, to write
the query for each of 75 tasks, and judges every answer through the
compiler's own front door and the engine's execution. It is rerunnable and
pinned: the agent model and each card are fixed by `manifest.json`, the tasks
and their ground truths are committed, and the judge is the compiler and the
engine at the commit being measured. Its numbers are evidence for a language
decision, not a CI gate.

## Arms

An arm is a language the agent writes and the judge reads.

| Arm | Card | Grammar |
|---|---|---|
| `gq3` | `cards/gq3.md` | GQ 3 as the product parses it (#1316) |

Edition 2 is deleted on #1316, so it is no arm; its numbers of the
2026-11-09 run (`results/2026-11-09/`) are the gate's baseline.

## What is here

| Path | What it holds |
|---|---|
| `tasks/` | the 45-task competence instrument: 39 kinds of query from the RFC's before-and-after corpus and the six M0 composition tasks (`m1` to `m6`), over the RFC's section 8 schema |
| `corpus/` | the 30-task agent corpus: realistic requests over three GQT datasets, `s*` (social), `a*` (attribution) and `r*` (reading); 10 of them needed several edition-2 queries |
| `datasets/` | each dataset's `schema.pg` and `seed.jsonl` (GQT's seed format) |
| `seeds/` | seeded answers per arm: a ground truth with one injected error per refusal class |
| `cards/` | the reference card each arm's agent reads |
| `manifest.json` | the pinned agent model and each card's SHA-256 |
| `run.py`, `summary.py` | the runner: calls the agent, asks `omnigraph-gqt-agent` to judge, writes results |
| `results/` | one dated directory per run of the runner, when committed |

The judge is the `omnigraph-gqt-agent` binary of this crate
(`src/bin/omnigraph-gqt-agent.rs`, `src/agent/`). The runner never parses a
task or a query itself.

### Datasets

`section8` is the schema of the RFC's section 8, with a seed written for the
45 tasks: null cities and ages, a company with no employees, a `knows`
cycle, an uncited author, documents without a vector and distances with no
ties. The other three are copies of GQT fixtures, kept as copies so that the
instrument's data stays fixed while the GQT corpus changes:

| Dataset | Copied from `crates/omnigraph-gqt/cases/` | What it exercises |
|---|---|---|
| `social` | `issue_954_count_distinct.gqt` | aggregation, nulls, distinct counts, writes over likes |
| `attribution` | `v2/planner/anti_join_key_set_from_a_bounded_binding.gqt` | traversal, sums, anti-joins, an edge property, writes fed by reads |
| `reading` | `v2/planner/issue_789_nearest_and_rrf_rank_a_reached_binding.gqt` | BM25 and vector search, fusion, citations, writes fed by reads |

### A task file

A `.task` file is sections in the shape of a `.gqt` case:

| Section | Holds |
|---|---|
| `--- task` | `id` (the file's name), `dataset`, `kind`; `answer: one` or `fewest` (the reply the prompt asks for); `composition` (why the task composes); `combine: concat` when edition 2's reads together formed the answer |
| `--- intent` | the request in one or two sentences, parameters named in prose as `$name` |
| `--- params` | the parameter values the ground truths and the answers run with |
| `--- edition2` | the edition-2 ground truth of the 2026-11-09 run, kept as the record of what edition 2 needed (one section per query, `bind`/`each` the client glue); it no longer parses. A task with exactly one is a shared item |
| `--- edition3` | a GQ 3 ground truth; the first is canonical and the others are spellings the RFC makes equally canonical |
| `--- check` | for a write: the read whose rows, after the write, judge it |
| `--- expect ordered\|unordered` | the expected rows: the edition-2 ground truth's on the seed, as recorded on 2026-11-09; `--- expect … edition3` marks rows recorded from the first edition-3 truth on #1316's engine, for a task with no edition-2 rows (`record --write`); `live` instead of rows for rows the engine owns (its catalogue) |
| `--- note` | why the task is written the way it is |

A task with no expected rows is one whose first edition-3 truth does not run
on the engine (a planner gap, `P006`); its answers are accepted or refused
but not judged by rows.

## What each number means

`run.py` writes `records.jsonl` (one record per agent call, with the prompt's
digest, the raw reply, the extracted answer and the judge's verdict, each
declaration's core IR by its SHA-256), `meta.json` (model, card digests,
`claude` version, git commit), and `summary.md` and `summary.json`
(`summary.py`), per arm, run and set:

| Number | Meaning |
|---|---|
| First try accepted | the first answer passes the arm's front door: the parser, then the type checker of every declaration to the core IR, as the engine compiles a source |
| First try correct (rows) | the accepted answer's rows on the seed equal the expected rows, by position (column names may differ), in order when the task orders; for a write, the check's rows after it on a fresh copy of the seed. An answer of several declarations is run by its last when that one calls the others, and is unjudged otherwise |
| Planner gaps (P006) | accepted answers the engine refuses at run time with `P006`, a core node the planner does not lower: accepted, not correct |
| Unjudged | accepted answers with no rows to compare: the task has no expected rows, or the answer is several queries glued in prose |
| IR-equal | the answer's core IR equals a GQ 3 ground truth's, after renaming every variable, alias and declaration name by order of first appearance; a secondary measure |
| Shared | the 45 items edition 2 expressed in one query: the gate's items |
| Repair with card accepted / correct | of the refused first answers, those accepted (correct) after one repair turn that carries the card, the schema, the task, the answer and the diagnostic |
| Repair without card | the same with the task, the answer and the diagnostic only: the diagnostics measured alone |
| Correct after repair | the first answer's correctness, or its repair with the card's when it was refused |
| Seeded repairs | of the seeded answers (`seeds/`), those accepted, and those correct, after one repair turn from the diagnostic alone |
| Tokens | mean input and output tokens of the first call |

The RFC's gate reads: an arm's first-try acceptance and its correctness on
the shared items at or above edition 2's (accepted [43, 43, 43] and correct
by rows [39, 39, 40] on 2026-11-09), with the seeded-repair rate reported,
on the pinned model and cards.

The diagnostic an agent sees in a repair turn is the compiler's serialized
four-field diagnostic, as `omnigraph lint --json` and an HTTP refusal carry
it.

## Running it

```bash
cargo test -p omnigraph-gqt --no-run   # builds target/debug/omnigraph-gqt-agent
python3 crates/omnigraph-gqt/agent/run.py run --arms gq3 --runs 3
python3 crates/omnigraph-gqt/agent/run.py run --arms gq3 --runs 1 --only t01,s04 --label smoke
python3 crates/omnigraph-gqt/agent/run.py summarize crates/omnigraph-gqt/agent/results/<dir>
```

The runner needs a logged-in `claude` (Claude Code) on `PATH`. Every call is a
fresh `claude -p` in an empty directory with `--tools ""`, `--safe-mode` and
`--no-session-persistence`, so the card is the only GQ reference the agent
sees and no session carries over. The model is `manifest.json`'s; a reply
that reports any other model stops the run. Before calling the agent, the
runner checks the cards against their pins. Each run of an arm makes one
agent call per task, two more for each refused first answer and one per
seed; `--resume` continues an interrupted results directory.

The judge alone:

```bash
B=target/debug/omnigraph-gqt-agent
$B tasks                                  # every task as JSON
echo '{"id":1,"task":"t14","text":"query staff($slug: String) { … }"}' | $B judge --arm gq3
$B truths                                 # every edition-3 truth against the expected rows
$B record --check                         # the edition3-recorded rows still hold
$B record --write                         # record rows for tasks with none
$B cards --check                          # the cards match their pins
$B cards --write                          # re-pin the cards after an intended change
```

## How it stays valid

The `agent::tests` module of this crate's library checks that every
edition-3 ground truth is accepted, that every truth that runs returns the
expected rows (a planner gap is printed, not failed), that every seed of each
arm is refused with its code, and that the cards match their pins.

## Writing a task

Keep the intent in the words an agent's user would write, name every
parameter and every output column the answer must produce, and choose
parameters whose rows separate the right answer from the near misses (a
window cut before or after a traversal, a group that is empty, a tie). Add
the GQ 3 ground truth, run `record --write`, and read the recorded rows
against the intent before committing them.
