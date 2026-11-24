# Findings by signature

Every signature a fuzz case reported, with the finding it belongs to (see the README) and its class. Cases are seeds; `where` is the step of the first example.

| finding | class | part | oracle | kind | signature | cases | occurrences | seeds | where | reading |
|---|---|---|---|---|---|---|---|---|---|---|
| F5 | c (doc, minor) | Part 1+2 (random configurations) | 4 | one_way_listing_noop | a plan with no step lists none of the one-way constructs the schema holds | 113 | 124 | 1003, 1009, 1036, 1047, 1054, 1060, 1072, 1106 … | schema d | a no-step schema plan lists none of the stored one-way constructs |
| F3 | c (pre-existing) | Part 3 alternating writers | 5 | integrity | main:uncoded:plain-text | 46 | 142 | 5002, 5005, 5008, 5012, 5014, 5101, 5104, 5109 … | index build | maintenance commands refuse in plain text under --json (here GQ 2's index build, optimize, cleanup) |
| F3 | c (pre-existing) | Part 1+2 (random configurations) | 5 | integrity | main:uncoded:plain-text | 43 | 69 | 1011, 1030, 1038, 1042, 1066, 1097, 1127, 1163 … | main index build (probe) | maintenance commands refuse in plain text under --json (here GQ 2's index build, optimize, cleanup) |
| F3 | c (pre-existing) | Part 3 alternating writers | 5 | integrity | rel:uncoded:plain-text | 35 | 99 | 5005, 5008, 5016, 5019, 5023, 5101, 5104, 5111 … | rel index build (probe) | maintenance commands refuse in plain text under --json (here GQ 2's index build, optimize, cleanup) |
| F3 | c (pre-existing) | Part 1+2 (random configurations) | 5 | integrity | rel:uncoded:plain-text | 20 | 27 | 1011, 1030, 1270, 1286, 1350, 1385, 1391, 1400 … | rel index build (probe) | maintenance commands refuse in plain text under --json (here GQ 2's index build, optimize, cleanup) |
| F5 | c (doc, minor) | Part 3 alternating writers | 4 | one_way_listing_noop | a plan with no step lists none of the one-way constructs the schema holds | 17 | 19 | 5017, 5019, 5109, 5114, 5122, 5127, 5201, 5301 … | schema c_set | a no-step schema plan lists none of the stored one-way constructs |
| F1 | c (pre-existing) | Part 1+2 (random configurations) | 1 | fresh_graph_differs | near fresh | 3 | 3 | 1168, 1813, 1828 | final | the same, against a graph rebuilt from the export |
| F1 | c (pre-existing) | Part 1+2 (random configurations) | 1 | index_state_changed_answer | near after main index build+rebuild | 3 | 3 | 1168, 1828, 1919 | 7:optimize:+main-rebuilt | nearest returns fewer than k rows after a vector index build, a delete and optimize |
| F1 | c (pre-existing) | Part 1+2 (random configurations) | 1 | index_state_changed_answer | near after rel index build+rebuild | 3 | 3 | 1168, 1828, 1919 | 7:optimize:+rel-rebuilt | nearest returns fewer than k rows after a vector index build, a delete and optimize |
| F1 | c (pre-existing) | Part 1+2 (random configurations) | 1 | index_state_changed_answer | near state | 3 | 3 | 1168, 1828, 1919 | 7:optimize: | nearest returns fewer than k rows after a vector index build, a delete and optimize |
| F1 | c (pre-existing) | Part 1+2 (random configurations) | 1 | fresh_graph_differs | rrf fresh | 1 | 1 | 1813 | final | the same, against a graph rebuilt from the export |
| — | a | Part 1+2 (random configurations) | 3 | gq2_new_difference | nearest rows different-identities [lagging] | 1 | 4 | 1855 | 0:start: | results.index-state: GQ 2's nearest returned fewer than k rows (an exact prefix of NEW's) |
| — | harness | Part 1+2 (random configurations) | 4 | gq2_write_refused | main reads but cannot write | 1 | 2 | 1453 | 0:start: | the probe drew an empty insert (fixed in the harness at seed ~1460) |
| — | harness | Part 1+2 (random configurations) | 4 | gq2_write_refused | rel reads but cannot write | 1 | 2 | 1453 | 0:start: | the probe drew an empty insert (fixed in the harness at seed ~1460) |
| F6 | c (pre-existing, minor) | Part 1+2 stress (100,000 rows) | 1 | fresh_graph_differs | rng fresh | 1 | 1 | 9011 | final | near query_memory_bytes, an index build or optimize turns an answer into a resource refusal |
| — | a | Part 3 alternating writers | 3 | gq2_new_difference | nearest rows same-identities-same-order [full] | 1 | 18 | 5321 | 10:gq2:fts_rebuild | results.limit-tie-rule: a distance tie at the limit boundary |
| — | a | Part 3 alternating writers | 3 | gq2_new_difference | nearest rows same-identities-same-order [lagging] | 1 | 20 | 5321 | 0:start: | results.limit-tie-rule: a distance tie at the limit boundary |
| — | a | Part 3 alternating writers | 3 | gq2_new_difference | nearest rows same-identities-same-order [rebuilt_by_new] | 1 | 4 | 5321 | 19:new:fts_rebuild | results.limit-tie-rule: a distance tie at the limit boundary |
