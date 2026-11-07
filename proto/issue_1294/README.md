# Issue #1294 measurement cases (prototype branch, not for merge)

Store requests per step on DST in-memory storage:

    cargo run -p omnigraph-gqt --bin omnigraph-gqt -- proto/issue_1294/skewed_hub.gqt --measure
    cargo run -p omnigraph-gqt --bin omnigraph-gqt -- proto/issue_1294/estimated_frontier.gqt --measure

`skewed_hub.gqt`: 12,000 people with no `__id` BTREE; hub h0 knows 256, h1 knows 3,072.
The planner estimates the frontier at 1 row (average fanout 0.28).
`estimated_frontier.gqt`: 40,000 people, 12 hubs of 256 each through a `Hub` type,
so the planner's estimate (3,072) is exact.
