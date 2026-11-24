| class | sanction or cause | oracle | checks | cases | signatures | example (seed, case) |
|---|---|---|---|---|---|---|
| c | `fix.filter-only-block-loops` | 4 | 7083 | 7083 | fix-does-not-converge (7083) | 1000, 7 |
| c | `fix.statements-join-match-group` | 3 | 1184 | 685 | fixed:write:affected:[…] -> […] (643); fixed:write:state:-N +N lines (499); fixed:write:error:ok->conflict (29); fixed:write:error:ok->bad_request (13) | 1010, 2 |
| c | `fix.rrf-explicit-k-no-edit` | 4 | 394 | 394 | fix-stops-unfixed:T65 (370); fix-stops-unfixed:T65,T81 (24) | 1015, 15 |
| c | `fix.rrf-over-a-triple-bound-binding-no-edit` | 4 | 45 | 45 | fix-stops-unfixed:T65 (42); fix-stops-unfixed:T65,T81 (3) | 1024, 2 |
| c | `panic.grouped-order-key` | 4,6 | 21 | 10 | panic:lint (10); fix-status:None (9); panic:new (1); uncoded-error:new (1) | 1415, 14 |
| b | `fix.read-named-after-function-no-edit` | 2,4 | 6737 | 5253 | fix-stops-unfixed:T74 (4804); refusal-without-fix:T74 (1483); fix-stops-unfixed:T65,T74 (217); fix-stops-unfixed:T74,T81 (205) | 1000, 19 |
| b | `open:write-groups-one-snapshot` | 4 | 2678 | 2678 | fix-stops-unfixed:T91 (2678) | 1002, 16 |
| b | `fix.fuzzy-max-edits-above-2-no-edit` | 4 | 563 | 563 | fix-stops-unfixed:T71 (562); fix-stops-unfixed:T71,T81 (1) | 1005, 15 |
| b | `accepted.empty-list-in-assignment` | 5 | 525 | 525 | e2-refused-new-accepts:T3 (525) | 1037, 18 |
| b | `accepted.whole-float-into-integer` | 1 | 467 | 467 | unchanged:write:error:bad_request->ok (467) | 1001, 5 |
| b | `writes.keyed-insert-repeated-in-one-write` | 1,3 | 16 | 16 | unchanged:write:error:ok->conflict (13); fixed:write:error:ok->conflict (3) | 1912, 1 |
| b | `accepted.exponent-float-literal` | 5 | 4 | 4 | e2-refused-new-accepts:Q001 (4) | 3155, 20 |
| a | `writes.keyed-insert` | 1,3 | 3282 | 3282 | unchanged:write:error:ok->conflict (1955); fixed:write:error:ok->conflict (1327) | 1003, 10 |
| a | `language.ranking-under-aggregate-no-edit` | 4 | 3220 | 3220 | fix-stops-unfixed:T65 (3033); fix-stops-unfixed:T65,T81 (187) | 1000, 18 |
| a | `results.bm25-f64` | 3 | 1781 | 1717 | fixed:read:types:FN->FN (1717); fixed:read:rows:values-equal-within-fN (64) | 1006, 0 |
| a | `results.limit-tie-rule` | 1,3 | 911 | 911 | unchanged:read:rows:different-identities N->N (651); fixed:read:rows:different-identities N->N (260) | 1005, 7 |
| a | `results.distance-rename` | 3 | 894 | 894 | fixed:read:columns:rename _distance (894) | 1006, 14 |
| a | `results.bm25-score-rename` | 3 | 856 | 856 | fixed:read:columns:rename _score (856) | 1006, 22 |
| a | `contract.D10-ties` | 1,3 | 790 | 652 | fixed:read:rows:same-identities-other-order (362); unchanged:read:rows:same-identities-other-order (320); fixed:read:rows:different-identities N->N (57); unchanged:read:rows:different-identities N->N (51) | 1007, 14 |
| a | `results.hop-range-self-loop` | 3 | 405 | 405 | fixed:read:rows:different-identities N->N (405) | 1017, 11 |
| a | `language.accepted-spelling` | 5 | 317 | 317 | e2-refused-new-accepts:Q001 (317) | 1003, 6 |
| a | `results.full-text-analysis` | 3 | 111 | 111 | fixed:read:rows:different-identities N->N (111) | 1071, 21 |
| a | `results.integer-float-comparison` | 3 | 105 | 79 | fixed:read:rows:different-identities N->N (48); fixed:write:affected:[…] -> […] (31); fixed:write:state:-N +N lines (26) | 1138, 23 |
| a | `results.rank-column-rename` | 3 | 68 | 68 | fixed:read:columns:rename _rrf (68) | 1064, 12 |
| a | `language.rrf-two-bindings` | 4 | 48 | 48 | fix-stops-unfixed:T65 (26); fix-stops-unfixed:T65,T81 (22) | 1076, 3 |
| a | `language.accepted-item-beside-aggregates` | 5 | 42 | 42 | e2-refused-new-accepts:T9 (42) | 2366, 8 |
| a | `results.fusion-window` | 3 | 34 | 34 | fixed:read:rows:different-identities N->N (34) | 1132, 14 |
| a | `results.avg-exact-integer` | 1,3 | 18 | 16 | unchanged:read:rows:values-equal-within-fN (10); fixed:read:rows:values-equal-within-fN (8) | 2170, 13 |
| - | `e2-refused-too` | 4 | 12733 | 12733 | fix-stops-unfixed (12733) | 1000, 4 |
| ? | `ranking-tie?` | 3 | 7 | 7 | fixed:read:rows:same-identities-other-order (7) | 1287, 10 |
| ? | `UNATTRIBUTED` | 3 | 2 | 2 | fixed:read:rows:same-identities-other-order (2) | 1361, 12 |
| ? | `conflict-without-existing-key?` | 3 | 1 | 1 | fixed:write:error:ok->conflict (1) | 4413, 5 |
