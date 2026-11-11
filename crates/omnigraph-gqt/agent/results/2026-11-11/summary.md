# Agent instrument results: 2026-11-11

Model `claude-sonnet-5-5`, 2.1.316 (Claude Code), git `d18bb217d1c0ec80dec883f697072104c729e4cd`, started 2026-11-11T07:44:44+00:00.
Card digests: `gq3.md` `9aa2fd3cea11`, `where.md` `2637d4a75c66`.
README.md beside the runner defines every column. Not a CI gate.

Means over the runs, the range in parentheses. `shared` is the 45 items edition 2 expresses in one query; `all` is the 75.

| Arm | Set | Items | First try accepted | First try correct (rows) | Planner gaps (P006) | Unjudged (no expected rows) | IR-equal | Refused | Repair with card accepted / correct | Repair without card accepted / correct | Correct after repair | Tokens in / out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gq3 | shared | 45 | 45.0 (45) | 30.7 (30–31) | 10.3 (10–11) | 0.0 (0) (0.0 (0)) | 24.7 (24–25) | 0.0 (0) | 0.0 (0) / 0.0 (0) | 0.0 (0) / 0.0 (0) | 30.7 (30–31) | 7550.0 (7550) / 147.0 (140–151) |
| gq3 | tasks | 45 | 45.0 (45) | 19.3 (19–20) | 5.7 (5–6) | 17.0 (17) (17.0 (17)) | 28.0 (27–29) | 0.0 (0) | 0.0 (0) / 0.0 (0) | 0.0 (0) / 0.0 (0) | 19.3 (19–20) | 7618.0 (7618) / 168.7 (165–173) |
| gq3 | corpus | 30 | 30.0 (30) | 13.3 (13–14) | 14.7 (14–15) | 1.0 (1) (1.0 (1)) | 13.7 (13–14) | 0.0 (0) | 0.0 (0) / 0.0 (0) | 0.0 (0) / 0.0 (0) | 13.3 (13–14) | 7469.0 (7469) / 233.3 (230–238) |
| gq3 | all | 75 | 75.0 (75) | 32.7 (32–33) | 20.3 (20–21) | 18.0 (18) (18.0 (18)) | 41.7 (41–42) | 0.0 (0) | 0.0 (0) / 0.0 (0) | 0.0 (0) / 0.0 (0) | 32.7 (32–33) | 7558.0 (7558) / 194.7 (192–196) |
| where | shared | 45 | 43.3 (42–44) | 31.0 (30–32) | 11.0 (11) | 0.0 (0) (0.0 (0)) | 21.3 (21–22) | 1.7 (1–3) | 1.7 (1–3) / 0.0 (0) | 1.0 (0–2) / 0.0 (0) | 31.0 (30–32) | 7674.0 (7674) / 162.0 (155–173) |
| where | tasks | 45 | 44.3 (44–45) | 20.0 (19–21) | 6.3 (6–7) | 17.0 (17) (17.0 (17)) | 29.0 (29) | 0.7 (0–1) | 0.7 (0–1) / 0.0 (0) | 0.0 (0) / 0.0 (0) | 20.0 (19–21) | 7742.0 (7742) / 180.0 (178–183) |
| where | corpus | 30 | 28.7 (28–30) | 13.0 (13) | 14.0 (13–15) | 1.0 (1) (1.0 (1)) | 10.7 (10–11) | 1.3 (0–2) | 1.3 (0–2) / 0.0 (0) | 1.0 (0–2) / 0.0 (0) | 13.0 (13) | 7593.0 (7593) / 251.7 (243–267) |
| where | all | 75 | 73.0 (72–74) | 33.0 (32–34) | 20.3 (19–21) | 18.0 (18) (18.0 (18)) | 39.7 (39–40) | 2.0 (1–3) | 2.0 (1–3) / 0.0 (0) | 1.0 (0–2) / 0.0 (0) | 33.0 (32–34) | 7682.0 (7682) / 208.7 (204–214) |

| Arm | Seeds | Seeded repairs accepted | Seeded repairs correct |
|---|---|---|---|
| gq3 | 29 | 25.0 (25) | 18.0 (18) |
| where | 29 | 25.0 (25) | 17.7 (17–18) |

## The gate

Edition 2 on 2026-11-09, 45 shared items, per run: accepted [43, 43, 43], correct by rows [39, 39, 40].

- `gq3`: accepted [45, 45, 45], correct by rows [31, 30, 31] (planner gaps [10, 11, 10]). Acceptance at or above edition 2's: yes; correctness at or above: no. Verdict: **fail**.
- `where`: accepted [44, 44, 42], correct by rows [31, 32, 30] (planner gaps [11, 11, 11]). Acceptance at or above edition 2's: yes; correctness at or above: no. Verdict: **fail**.

## By task

First answers over the runs per arm: `c` correct, `g` planner gap, `w` wrong rows, `e` engine error, `r` refused, `u` unjudged. Rows where the arms differ or either is not correct every run.

| Task | Set | Shared | Kind | gq3 | where |
|---|---|---|---|---|---|
| a02 | corpus | yes | two hops from a keyed node, counted | ggg | ggg |
| a05 | corpus |  | delete nodes selected by an anti-join | ggg | ggg |
| a06 | corpus |  | an aggregate of an aggregate | uuu | uuu |
| a07 | corpus | yes | filter on a sum | ggg | ggg |
| a08 | corpus |  | a top-1 read feeding a traversal | ggg | ggg |
| a09 | corpus |  | update nodes selected through a neighbour | ggg | grg |
| r01 | corpus | yes | lexical ranking with the score | ccg | ggg |
| r02 | corpus |  | a vector window, then a traversal from the hits | ggg | ggg |
| r03 | corpus | yes | a vector window over a traversal's population | cgc | ccc |
| r04 | corpus |  | a lexical cut across the corpus, then a traversal | ggg | gwg |
| r05 | corpus | yes | hybrid search by fusion | www | wrr |
| r07 | corpus |  | insert edges fed by a traversal | ggg | ggg |
| r10 | corpus |  | count with zero for an empty group | ggg | ggg |
| r11 | corpus |  | delete nodes selected by an anti-join | ggg | ggg |
| s03 | corpus | yes | two aggregates over one traversal | ggg | ggr |
| s04 | corpus |  | count with zero for an empty group | ggg | ggg |
| s06 | corpus |  | delete edges selected through a neighbour's property | ggg | ggg |
| s08 | corpus | yes | filter on a count | ggg | ggg |
| m1 | tasks |  | block as a value (count plus projection) | uuu | uuu |
| m2 | tasks |  | count block compared with another binding's value | uuu | uuu |
| m3 | tasks | yes | filter on an aggregate | www | ggg |
| m4 | tasks |  | reuse of a declared read with arguments | uuu | uuu |
| m5 | tasks |  | a window over documents, then a traversal from the hits | uuu | uuu |
| m6 | tasks |  | window versus population | uuu | uuu |
| t02 | tasks | yes | comparison filter | www | www |
| t07 | tasks |  | interface binding | uuu | uuu |
| t12 | tasks |  | unbounded reachability | uuu | uuu |
| t17 | tasks |  | aggregate of an aggregate | uuu | uuu |
| t18 | tasks | yes | count block with a parameter bound | ggc | cgg |
| t20 | tasks |  | outer join | uuu | uuu |
| t21 | tasks |  | union of two reads | uuu | uuu |
| t22 | tasks |  | correlated subquery per group | uuu | uuu |
| t23 | tasks |  | parameterised limit with a default | uuu | uuu |
| t24 | tasks | yes | nearest window | ggg | ggg |
| t25 | tasks | yes | nearest with the distance returned | ggg | ggg |
| t26 | tasks |  | every document within a distance | uuu | uuu |
| t27 | tasks | yes | lexical ranking | ggg | gcg |
| t29 | tasks | yes | hybrid, fused | www | rcr |
| t30 | tasks |  | hybrid, weighted | uuu | uuu |
| t31 | tasks |  | declared recall | uuu | uuu |
| t33 | tasks | yes | delete by predicate | ggg | ggg |
| t34 | tasks | yes | write whose targets come from a traversal | ggg | ggg |
| t35 | tasks |  | write with a computed value | uuu | uuu |
| t36 | tasks |  | write whose targets come from a ranked read | uuu | uuu |

## Every first answer that is not correct

| Arm | Run | Task | Outcome | Code or reason |
|---|---|---|---|---|
| gq3 | 1 | a02 | gap | P006: `channel_source_touchpoints` failed: plan error: P006: `channel_source_touchpoints`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | a05 | gap | P006: `delete_unwatched_touchpoints` failed: plan error: P006: `delete_unwatched_touchpoints`: the planner does not lower a write group whose clauses are more t |
| gq3 | 1 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | a07 | gap | P006: `heavy_identities` failed: plan error: P006: `heavy_identities`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | a08 | gap | P006: `sources_of_heaviest_identity` failed: plan error: P006: `sources_of_heaviest_identity`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | a09 | gap | P006: `set_touchpoint_weight` failed: plan error: P006: `set_touchpoint_weight`: the planner does not lower a write group whose clauses are more than a filtered |
| gq3 | 1 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m3 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 1 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | r02 | gap | P006: `nearest_cites` failed: plan error: P006: `nearest_cites`: the planner does not lower a `ApproxTopK` node in a pattern |
| gq3 | 1 | r04 | gap | P006: `top_docs_and_readers` failed: plan error: P006: `top_docs_and_readers`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | r05 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 1 | r07 | gap | P006: `know_cited` failed: plan error: P006: `know_cited`: the planner does not lower a write group whose clauses are more than a filtered scan of the target |
| gq3 | 1 | r10 | gap | P006: `docs_known_per_person` failed: plan error: P006: `docs_known_per_person`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | r11 | gap | P006: `delete_unknown_docs` failed: plan error: P006: `delete_unknown_docs`: the planner does not lower a write group whose clauses are more than a filtered sca |
| gq3 | 1 | s03 | gap | P006: `comment_author_likes` failed: plan error: P006: `comment_author_likes`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | s04 | gap | P006: `liked_counts` failed: plan error: P006: `liked_counts`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | s06 | gap | P006: `remove_long_comment_likes` failed: plan error: P006: `remove_long_comment_likes`: the planner does not lower a write group whose clauses are more than a  |
| gq3 | 1 | s08 | gap | P006: `popular_comments` failed: plan error: P006: `popular_comments`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 1 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t18 | gap | P006: `knows_at_least` failed: plan error: P006: `knows_at_least`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t24 | gap | P006: `recent_similar` failed: plan error: P006: `recent_similar`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| gq3 | 1 | t25 | gap | P006: `similar_docs` failed: plan error: P006: `similar_docs`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| gq3 | 1 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t27 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| gq3 | 1 | t29 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 1 | t30 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t33 | gap | P006: `remove_employment` failed: plan error: P006: `remove_employment`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| gq3 | 1 | t34 | gap | P006: `close_company` failed: plan error: P006: `close_company`: the planner does not lower a write group whose clauses are more than a filtered scan of the tar |
| gq3 | 1 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 1 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | a02 | gap | P006: `watched_source_touchpoints` failed: plan error: P006: `watched_source_touchpoints`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | a05 | gap | P006: `delete_touchpoints_on_unwatched_sources` failed: plan error: P006: `delete_touchpoints_on_unwatched_sources`: the planner does not lower a write group wh |
| gq3 | 2 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | a07 | gap | P006: `heavy_identities` failed: plan error: P006: `heavy_identities`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | a08 | gap | P006: `identity_sources` failed: plan error: P006: `identity_sources`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | a09 | gap | P006: `set_touchpoint_weight` failed: plan error: P006: `set_touchpoint_weight`: the planner does not lower a write group whose clauses are more than a filtered |
| gq3 | 2 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m3 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | r02 | gap | P006: `nearest_cites` failed: plan error: P006: `nearest_cites`: the planner does not lower a `ApproxTopK` node in a pattern |
| gq3 | 2 | r03 | gap | P006: `nearest_known` failed: plan error: P006: `nearest_known`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| gq3 | 2 | r04 | gap | P006: `top_docs_known` failed: plan error: P006: `top_docs_known`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | r05 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | r07 | gap | P006: `know_cited` failed: plan error: P006: `know_cited`: the planner does not lower a write group whose clauses are more than a filtered scan of the target |
| gq3 | 2 | r10 | gap | P006: `docs_known_per_person` failed: plan error: P006: `docs_known_per_person`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | r11 | gap | P006: `delete_unknown_docs` failed: plan error: P006: `delete_unknown_docs`: the planner does not lower a write group whose clauses are more than a filtered sca |
| gq3 | 2 | s03 | gap | P006: `author_like_stats` failed: plan error: P006: `author_like_stats`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | s04 | gap | P006: `liked_counts` failed: plan error: P006: `liked_counts`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | s06 | gap | P006: `remove_long_likes` failed: plan error: P006: `remove_long_likes`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| gq3 | 2 | s08 | gap | P006: `popular_comments` failed: plan error: P006: `popular_comments`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t18 | gap | P006: `knows_at_least` failed: plan error: P006: `knows_at_least`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t24 | gap | P006: `similar_recent_docs` failed: plan error: P006: `similar_recent_docs`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| gq3 | 2 | t25 | gap | P006: `similar_docs` failed: plan error: P006: `similar_docs`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| gq3 | 2 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t27 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| gq3 | 2 | t29 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 2 | t30 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t33 | gap | P006: `remove_employment` failed: plan error: P006: `remove_employment`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| gq3 | 2 | t34 | gap | P006: `close_company` failed: plan error: P006: `close_company`: the planner does not lower a write group whose clauses are more than a filtered scan of the tar |
| gq3 | 2 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 2 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | a02 | gap | P006: `source_touchpoints` failed: plan error: P006: `source_touchpoints`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | a05 | gap | P006: `purge_unwatched_touchpoints` failed: plan error: P006: `purge_unwatched_touchpoints`: the planner does not lower a write group whose clauses are more tha |
| gq3 | 3 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | a07 | gap | P006: `heavy_identities` failed: plan error: P006: `heavy_identities`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | a08 | gap | P006: `sources_of_heaviest_identity` failed: plan error: P006: `sources_of_heaviest_identity`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | a09 | gap | P006: `set_touchpoint_weight` failed: plan error: P006: `set_touchpoint_weight`: the planner does not lower a write group whose clauses are more than a filtered |
| gq3 | 3 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m3 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 3 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | r01 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | r02 | gap | P006: `nearest_cites` failed: plan error: P006: `nearest_cites`: the planner does not lower a `ApproxTopK` node in a pattern |
| gq3 | 3 | r04 | gap | P006: `top_docs_known` failed: plan error: P006: `top_docs_known`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | r05 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 3 | r07 | gap | P006: `know_cited` failed: plan error: P006: `know_cited`: the planner does not lower a write group whose clauses are more than a filtered scan of the target |
| gq3 | 3 | r10 | gap | P006: `docs_known` failed: plan error: P006: `docs_known`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | r11 | gap | P006: `delete_unknown_docs` failed: plan error: P006: `delete_unknown_docs`: the planner does not lower a write group whose clauses are more than a filtered sca |
| gq3 | 3 | s03 | gap | P006: `author_likes` failed: plan error: P006: `author_likes`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | s04 | gap | P006: `liked_counts` failed: plan error: P006: `liked_counts`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | s06 | gap | P006: `remove_long_comment_likes` failed: plan error: P006: `remove_long_comment_likes`: the planner does not lower a write group whose clauses are more than a  |
| gq3 | 3 | s08 | gap | P006: `popular_comments` failed: plan error: P006: `popular_comments`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 3 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t24 | gap | P006: `similar_recent_docs` failed: plan error: P006: `similar_recent_docs`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| gq3 | 3 | t25 | gap | P006: `similar_docs` failed: plan error: P006: `similar_docs`: the planner does not lower an `order` over a ranking that does not lead with its score |
| gq3 | 3 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t27 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| gq3 | 3 | t29 | wrong-rows | rows differ from the expected rows on the seed |
| gq3 | 3 | t30 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t33 | gap | P006: `remove_employment` failed: plan error: P006: `remove_employment`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| gq3 | 3 | t34 | gap | P006: `close_company` failed: plan error: P006: `close_company`: the planner does not lower a write group whose clauses are more than a filtered scan of the tar |
| gq3 | 3 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| gq3 | 3 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | a02 | gap | P006: `channel_sources` failed: plan error: P006: `channel_sources`: the planner does not lower a `Project` node in a pattern |
| where | 1 | a05 | gap | P006: `delete_touchpoints_on_unwatched_sources` failed: plan error: P006: `delete_touchpoints_on_unwatched_sources`: the planner does not lower a write group wh |
| where | 1 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | a07 | gap | P006: `heavy_identities` failed: plan error: P006: `heavy_identities`: the planner does not lower a `Project` node in a pattern |
| where | 1 | a08 | gap | P006: `heaviest_identity_sources` failed: plan error: P006: `heaviest_identity_sources`: the planner does not lower a `Project` node in a pattern |
| where | 1 | a09 | gap | P006: `set_touchpoint_weight` failed: plan error: P006: `set_touchpoint_weight`: the planner does not lower a write group whose clauses are more than a filtered |
| where | 1 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | m3 | gap | P006: `big_companies` failed: plan error: P006: `big_companies`: the planner does not lower a `Project` node in a pattern |
| where | 1 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | r01 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| where | 1 | r02 | gap | P006: `nearest_cited` failed: plan error: P006: `nearest_cited`: the planner does not lower a `ApproxTopK` node in a pattern |
| where | 1 | r04 | gap | P006: `top_docs_known_by` failed: plan error: P006: `top_docs_known_by`: the planner does not lower a `Project` node in a pattern |
| where | 1 | r05 | wrong-rows | rows differ from the expected rows on the seed |
| where | 1 | r07 | gap | P006: `know_cited` failed: plan error: P006: `know_cited`: the planner does not lower a write group whose clauses are more than a filtered scan of the target |
| where | 1 | r10 | gap | P006: `docs_known_per_person` failed: plan error: P006: `docs_known_per_person`: the planner does not lower a `Project` node in a pattern |
| where | 1 | r11 | gap | P006: `delete_unknown_docs` failed: plan error: P006: `delete_unknown_docs`: the planner does not lower a write group whose clauses are more than a filtered sca |
| where | 1 | s03 | gap | P006: `author_likes` failed: plan error: P006: `author_likes`: the planner does not lower a `Project` node in a pattern |
| where | 1 | s04 | gap | P006: `liked_counts` failed: plan error: P006: `liked_counts`: the planner does not lower a `Project` node in a pattern |
| where | 1 | s06 | gap | P006: `remove_long_likes` failed: plan error: P006: `remove_long_likes`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| where | 1 | s08 | gap | P006: `popular_comments` failed: plan error: P006: `popular_comments`: the planner does not lower a `Project` node in a pattern |
| where | 1 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| where | 1 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t24 | gap | P006: `recent_similar` failed: plan error: P006: `recent_similar`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| where | 1 | t25 | gap | P006: `nearest_docs` failed: plan error: P006: `nearest_docs`: the planner does not lower an `order` over a ranking that does not lead with its score |
| where | 1 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t27 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| where | 1 | t29 | refused | Q001 |
| where | 1 | t30 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t33 | gap | P006: `remove_employment` failed: plan error: P006: `remove_employment`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| where | 1 | t34 | gap | P006: `close_company` failed: plan error: P006: `close_company`: the planner does not lower a write group whose clauses are more than a filtered scan of the tar |
| where | 1 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 1 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | a02 | gap | P006: `watched_sources` failed: plan error: P006: `watched_sources`: the planner does not lower a `Project` node in a pattern |
| where | 2 | a05 | gap | P006: `delete_touchpoints_on_unwatched_sources` failed: plan error: P006: `delete_touchpoints_on_unwatched_sources`: the planner does not lower a write group wh |
| where | 2 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | a07 | gap | P006: `heavy_identities` failed: plan error: P006: `heavy_identities`: the planner does not lower a `Project` node in a pattern |
| where | 2 | a08 | gap | P006: `sources_of_heaviest_identity` failed: plan error: P006: `sources_of_heaviest_identity`: the planner does not lower a `Project` node in a pattern |
| where | 2 | a09 | refused | Q001 |
| where | 2 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | m3 | gap | P006: `big_companies` failed: plan error: P006: `big_companies`: the planner does not lower a `Project` node in a pattern |
| where | 2 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | r01 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| where | 2 | r02 | gap | P006: `nearest_cited` failed: plan error: P006: `nearest_cited`: the planner does not lower a `ApproxTopK` node in a pattern |
| where | 2 | r04 | wrong-rows | rows differ from the expected rows on the seed |
| where | 2 | r05 | refused | T6 |
| where | 2 | r07 | gap | P006: `know_cited` failed: plan error: P006: `know_cited`: the planner does not lower a write group whose clauses are more than a filtered scan of the target |
| where | 2 | r10 | gap | P006: `docs_known` failed: plan error: P006: `docs_known`: the planner does not lower a `Project` node in a pattern |
| where | 2 | r11 | gap | P006: `delete_unknown_docs` failed: plan error: P006: `delete_unknown_docs`: the planner does not lower a write group whose clauses are more than a filtered sca |
| where | 2 | s03 | gap | P006: `author_likes` failed: plan error: P006: `author_likes`: the planner does not lower a `Project` node in a pattern |
| where | 2 | s04 | gap | P006: `liked_counts` failed: plan error: P006: `liked_counts`: the planner does not lower a `Project` node in a pattern |
| where | 2 | s06 | gap | P006: `remove_long_likes` failed: plan error: P006: `remove_long_likes`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| where | 2 | s08 | gap | P006: `popular_comments` failed: plan error: P006: `popular_comments`: the planner does not lower a `Project` node in a pattern |
| where | 2 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| where | 2 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t18 | gap | P006: `knows_at_least` failed: plan error: P006: `knows_at_least`: the planner does not lower a `Project` node in a pattern |
| where | 2 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t24 | gap | P006: `recent_similar` failed: plan error: P006: `recent_similar`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| where | 2 | t25 | gap | P006: `nearest_docs` failed: plan error: P006: `nearest_docs`: the planner does not lower an `order` over a ranking that does not lead with its score |
| where | 2 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t30 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t33 | gap | P006: `remove_employment` failed: plan error: P006: `remove_employment`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| where | 2 | t34 | gap | P006: `close_company` failed: plan error: P006: `close_company`: the planner does not lower a write group whose clauses are more than a filtered scan of the tar |
| where | 2 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 2 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | a02 | gap | P006: `watched_source_touchpoints` failed: plan error: P006: `watched_source_touchpoints`: the planner does not lower a `Project` node in a pattern |
| where | 3 | a05 | gap | P006: `delete_unwatched_touchpoints` failed: plan error: P006: `delete_unwatched_touchpoints`: the planner does not lower a write group whose clauses are more t |
| where | 3 | a06 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | a07 | gap | P006: `heavy_identities` failed: plan error: P006: `heavy_identities`: the planner does not lower a `Project` node in a pattern |
| where | 3 | a08 | gap | P006: `sources_of_heaviest_identity` failed: plan error: P006: `sources_of_heaviest_identity`: the planner does not lower a `Project` node in a pattern |
| where | 3 | a09 | gap | P006: `set_touchpoint_weight` failed: plan error: P006: `set_touchpoint_weight`: the planner does not lower a write group whose clauses are more than a filtered |
| where | 3 | m1 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | m2 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | m3 | gap | P006: `big_companies` failed: plan error: P006: `big_companies`: the planner does not lower a `Project` node in a pattern |
| where | 3 | m4 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | m5 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | m6 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | r01 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower an `order` over a ranking that does not lead with its score |
| where | 3 | r02 | gap | P006: `nearest_cited` failed: plan error: P006: `nearest_cited`: the planner does not lower a `ApproxTopK` node in a pattern |
| where | 3 | r04 | gap | P006: `top_docs_known_by` failed: plan error: P006: `top_docs_known_by`: the planner does not lower a `Project` node in a pattern |
| where | 3 | r05 | refused | T6 |
| where | 3 | r07 | gap | P006: `know_cited` failed: plan error: P006: `know_cited`: the planner does not lower a write group whose clauses are more than a filtered scan of the target |
| where | 3 | r10 | gap | P006: `docs_known_per_person` failed: plan error: P006: `docs_known_per_person`: the planner does not lower a `Project` node in a pattern |
| where | 3 | r11 | gap | P006: `delete_unknown_docs` failed: plan error: P006: `delete_unknown_docs`: the planner does not lower a write group whose clauses are more than a filtered sca |
| where | 3 | s03 | refused | Q001 |
| where | 3 | s04 | gap | P006: `liked_counts` failed: plan error: P006: `liked_counts`: the planner does not lower a `Project` node in a pattern |
| where | 3 | s06 | gap | P006: `remove_long_likes` failed: plan error: P006: `remove_long_likes`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| where | 3 | s08 | gap | P006: `popular_comments` failed: plan error: P006: `popular_comments`: the planner does not lower a `Project` node in a pattern |
| where | 3 | t02 | wrong-rows | rows differ from the expected rows on the seed |
| where | 3 | t07 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t12 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t17 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t18 | gap | P006: `knows_at_least` failed: plan error: P006: `knows_at_least`: the planner does not lower a `Project` node in a pattern |
| where | 3 | t20 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t21 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t22 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t23 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t24 | gap | P006: `recent_similar` failed: plan error: P006: `recent_similar`: the planner does not lower a `nearest` window `k` other than the read's `limit` |
| where | 3 | t25 | gap | P006: `nearest_docs` failed: plan error: P006: `nearest_docs`: the planner does not lower an `order` over a ranking that does not lead with its score |
| where | 3 | t26 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t27 | gap | P006: `top_docs` failed: plan error: P006: `top_docs`: the planner does not lower a `Project` node in a pattern |
| where | 3 | t29 | refused | Q001 |
| where | 3 | t30 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t31 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t33 | gap | P006: `remove_employment` failed: plan error: P006: `remove_employment`: the planner does not lower a write group whose clauses are more than a filtered scan of |
| where | 3 | t34 | gap | P006: `close_company` failed: plan error: P006: `close_company`: the planner does not lower a write group whose clauses are more than a filtered scan of the tar |
| where | 3 | t35 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |
| where | 3 | t36 | unjudged | no expected rows: the task's first edition-3 truth does not run on this engine |

## Seeds by code

| Arm | Seed | Code | Class | Accepted | Correct |
|---|---|---|---|---|---|
| gq3 | e3-01 | T7 | infix `contains` and `starts_with` (edition 2's spelling) | 3/3 | 3/3 |
| gq3 | e3-02 | T7 | `contains` over a list property (edition 2's spelling) | 3/3 | 3/3 |
| gq3 | e3-03 | T71 | a positional option (edition 2's `fuzzy` budget) | 3/3 | 3/3 |
| gq3 | e3-04 | T65 | a ranking in `order` (edition 2's spelling) | 3/3 | 3/3 |
| gq3 | e3-05 | T65 | a ranking in `return` and `order` (edition 2's spelling) | 3/3 | 3/3 |
| gq3 | e3-06 | Q008 | `delete T where` (edition 2's spelling) | 3/3 | 3/3 |
| gq3 | e3-07 | Q008 | `update T set … where` (edition 2's spelling) | 3/3 | 0/3 |
| gq3 | e3-08 | Q014 | `having` (SQL) | 3/3 | 0/3 |
| gq3 | e3-09 | Q014 | `optional` without braces (Cypher) | 3/3 | 0/3 |
| gq3 | e3-10 | Q012 | `CASE … END` (SQL) | 3/3 | 3/3 |
| gq3 | e3-11 | Q016 | a minus spaced on one side only | 3/3 | 3/3 |
| gq3 | e3-12 | Q010 | a comparison inside binding braces | 3/3 | 3/3 |
| gq3 | e3-13 | Q002 | a declaration without its parameter list | 3/3 | 3/3 |
| gq3 | e3-14 | Q009 | the `mutation` keyword (GraphQL) | 3/3 | 3/3 |
| gq3 | e3-15 | Q005 | a write body wrapped in `match { }` | 3/3 | 0/3 |
| gq3 | e3-16 | T7 | a reserved edge name written bare | 3/3 | 3/3 |
| gq3 | e3-17 | Q014 | `where` (SQL, Cypher) | 3/3 | 3/3 |
| gq3 | e3-18 | Q007 | an arrow traversal (Cypher) | 3/3 | 3/3 |
| gq3 | e3-19 | Q012 | `let … in` (a row-binding `let`) | 3/3 | 0/3 |
| gq3 | e3-20 | Q014 | `count(*)` (SQL) | 0/3 | 0/3 |
| gq3 | e3-21 | Q014 | a `null` literal in a read | 3/3 | 3/3 |
| gq3 | e3-22 | Q014 | an uppercase keyword | 3/3 | 3/3 |
| gq3 | e3-23 | T8 | an aggregate of an aggregate in one `return` | 0/3 | 0/3 |
| gq3 | e3-24 | T68 | two rankings drawn into one scope | 0/3 | 0/3 |
| gq3 | e3-25 | T17 | `nearest` without `k` | 3/3 | 3/3 |
| gq3 | e3-26 | Q014 | `union` between two reads (SQL) | 0/3 | 0/3 |
| gq3 | e3-27 | Q014 | single-quoted strings and `==` | 3/3 | 3/3 |
| gq3 | e3-28 | Q019 | a space before a call's parenthesis | 3/3 | 0/3 |
| gq3 | e3-29 | T74 | a drawn column that is neither a variable nor aliased | 3/3 | 0/3 |
| where | w-01 | T7 | infix `contains` and `starts_with` (edition 2's spelling) | 3/3 | 3/3 |
| where | w-02 | T7 | `contains` over a list property (edition 2's spelling) | 3/3 | 3/3 |
| where | w-03 | T71 | a positional option (edition 2's `fuzzy` budget) | 3/3 | 3/3 |
| where | w-04 | T65 | a ranking in `order` (edition 2's spelling) | 3/3 | 2/3 |
| where | w-05 | T65 | a ranking in `return` and `order` (edition 2's spelling) | 3/3 | 3/3 |
| where | w-06 | Q008 | `delete T where` (edition 2's spelling) | 3/3 | 3/3 |
| where | w-07 | Q008 | `update T set … where` (edition 2's spelling) | 3/3 | 0/3 |
| where | w-08 | Q014 | `having` (SQL) | 3/3 | 0/3 |
| where | w-09 | Q014 | `optional` without braces (Cypher) | 3/3 | 0/3 |
| where | w-10 | Q012 | `CASE … END` (SQL) | 3/3 | 3/3 |
| where | w-11 | Q016 | a minus spaced on one side only | 3/3 | 3/3 |
| where | w-12 | Q010 | a comparison inside binding braces | 3/3 | 3/3 |
| where | w-13 | Q002 | a declaration without its parameter list | 3/3 | 3/3 |
| where | w-14 | Q009 | the `mutation` keyword (GraphQL) | 3/3 | 3/3 |
| where | w-15 | Q005 | a write's clauses written bare (the inverse of GQ 3's `match { }` around a write body) | 3/3 | 0/3 |
| where | w-16 | T7 | a reserved edge name written bare | 3/3 | 3/3 |
| where | w-17 | Q020 | `where` inside the `match` braces (SQL, Cypher) | 3/3 | 3/3 |
| where | w-18 | Q007 | an arrow traversal (Cypher) | 3/3 | 3/3 |
| where | w-19 | Q012 | `let … in` (a row-binding `let`) | 3/3 | 0/3 |
| where | w-20 | Q014 | `count(*)` (SQL) | 0/3 | 0/3 |
| where | w-21 | Q014 | a `null` literal in a read | 3/3 | 3/3 |
| where | w-22 | Q014 | an uppercase keyword | 3/3 | 3/3 |
| where | w-23 | T8 | an aggregate of an aggregate in one `return` | 0/3 | 0/3 |
| where | w-24 | T68 | two rankings drawn into one scope | 0/3 | 0/3 |
| where | w-25 | T17 | `nearest` without `k` | 3/3 | 3/3 |
| where | w-26 | Q014 | `union` between two reads (SQL) | 0/3 | 0/3 |
| where | w-27 | Q014 | single-quoted strings and `==` | 3/3 | 3/3 |
| where | w-28 | Q019 | a space before a call's parenthesis | 3/3 | 0/3 |
| where | w-29 | T74 | a drawn column that is neither a variable nor aliased | 3/3 | 0/3 |
