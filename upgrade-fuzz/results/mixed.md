# Part 3: mixed binaries

## Concurrent processes on a 20,000-row graph

| pair | GQ 2 | secs | writes | reads | errors | panics | uncoded | count went down | acked missing / acked per binary | exports equal |
|---|---|---|---|---|---|---|---|---|---|---|
| gq2w-newr | rel | 60.0 | {'rel:ok': 451} | {'new:ok': 129} | — | 0 | 0 | 0 | {'new': (0, 451), 'rel': (0, 451)} | True |
| neww-gq2r | rel | 150.0 | {'new:ok': 950} | {'rel:ok': 648} | — | 0 | 0 | 0 | {'new': (0, 950), 'rel': (0, 950)} | True |
| both-write | rel | 150.0 | {'new:ok': 670, 'rel:ok': 552} | {'rel:ok': 861, 'new:ok': 328} | — | 0 | 0 | 0 | {'new': (0, 1222), 'rel': (0, 1222)} | True |
| neww-gq2r + maintenance | main | 180.0 | {'new:ok': 868, 'new:index-build:ok': 24, 'new:index-rebuild:ok': 24, 'new:optimize:ok': 24, 'new:cleanup:ok': 24} | {'main:ok': 1486} | {"main:search:[conflict] full-text index 'title_idx' requires rebuild: analyzer certificate payload is m": 1466} | 0 | 0 | 0 | {'new': (0, 868), 'main': (0, 868)} | True |
| both-write + maintenance | main | 180.0 | {'main:ok': 828, 'new:ok': 561, 'main:index-build:ok': 18, 'main:index-rebuild:ok': 15, 'new:index-build:refused': 15, 'main:optimize:ok': 10, 'main:cleanup:ok': 2, 'new:index-rebuild:refused': 8, 'main:index-build:refused': 5, 'new:optimize:refused': 14, 'main:optimize:refused': 13, 'new:cleanup:refused': 14, 'main:cleanup:refused': 21, 'main:index-rebuild:refused': 8, 'new:index-rebuild:ok': 8, 'new:index-build:ok': 1, 'new:optimize:ok': 1, 'new:cleanup:ok': 1} | {'main:ok': 1027, 'new:ok': 379} | {"main:search:[conflict] full-text index 'title_idx' requires rebuild: analyzer certificate payload is m": 208, "main:cleanup --keep:Error: \n   0: \x1b[91mcollector branch 'main' changed during capture; retry cleanup\x1b[0m\n\n  ━━": 21, "new:optimize:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 14, "main:optimize:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 13, "new:cleanup --keep:Error: \n   0: \x1b[91mcollector branch 'main' changed during capture; retry cleanup\x1b[0m\n\n  ━━": 12, "new:index build:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 10, "new:index rebuild:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 8, "main:index rebuild:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 8, "main:index build:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 5, 'new:index build:Error: \n   0: \x1b[91mstage index batch on node:Doc ([Scalar { column: "body", family: NGram ': 5, "new:cleanup --keep:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 2} | 0 | 5 | 0 | {'new': (0, 1389), 'main': (0, 1389)} | True |
| gq2w-newr + maintenance | main | 180.0 | {'main:ok': 1441, 'main:index-build:ok': 40, 'main:index-rebuild:ok': 40, 'main:optimize:ok': 40, 'main:cleanup:ok': 40} | {'new:ok': 385} | — | 0 | 0 | 0 | {'new': (0, 1441), 'main': (0, 1441)} | True |
| both-write + maintenance | main | 100 | {'new:ok': 432, 'main:ok': 720, 'main:index-build:ok': 15, 'main:index-rebuild:ok': 10, 'new:index-build:refused': 12, 'main:optimize:ok': 11, 'main:cleanup:ok': 4, 'new:index-rebuild:refused': 9, 'main:index-build:refused': 5, 'new:optimize:refused': 12, 'main:optimize:refused': 9, 'new:cleanup:refused': 12, 'main:cleanup:refused': 16, 'main:index-rebuild:refused': 10, 'new:index-rebuild:ok': 3} | {'main:ok': 645, 'new:ok': 225} | {"main:search:[conflict] full-text index 'title_idx' requires rebuild: analyzer certificate payload is m": 61, "main:cleanup --keep:Error: \n   0: \x1b[91mcollector branch 'main' changed during capture; retry cleanup\x1b[0m\n\n  ━━": 16, "new:index build:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 12, "new:optimize:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 12, "new:cleanup --keep:Error: \n   0: \x1b[91mcollector branch 'main' changed during capture; retry cleanup\x1b[0m\n\n  ━━": 12, "main:index rebuild:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 10, "new:index rebuild:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 9, "main:optimize:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 9, "main:index build:Error: \n   0: \x1b[91mwrite authority 'graph_head:main' changed during preparation (expected ": 5} | 0 | 5 | 0 | {'new': (0, 1152), 'main': (0, 1152)} | True |
| both-write + maintenance | rel | 300.0 | {'rel:ok': 1116, 'new:ok': 804, 'rel:index-build:ok': 18, 'rel:index-rebuild:ok': 17, 'new:index-build:refused': 23, 'rel:optimize:refused': 15, 'new:index-rebuild:refused': 15, 'rel:cleanup:refused': 27, 'new:optimize:refused': 18, 'new:cleanup:refused': 22, 'rel:index-rebuild:refused': 14, 'rel:optimize:ok': 16, 'rel:cleanup:ok': 4, 'rel:index-build:refused': 13, 'new:index-rebuild:ok': 7, 'new:optimize:ok': 4} | {'rel:ok': 1678, 'new:ok': 617} | {"rel:search:[conflict] full-text index 'title_idx' requires rebuild: analyzer certificate payload is m": 634, "rel:cleanup --keep:Error: \n   0: collector branch 'main' changed during capture; retry cleanup\n\n  ━━━━━━━━━━━": 27, "new:cleanup --keep:Error: \n   0: collector branch 'main' changed during capture; retry cleanup\n\n  ━━━━━━━━━━━": 22, "new:optimize:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 18, "new:index build:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 15, "rel:optimize:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 15, "new:index rebuild:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 15, "rel:index rebuild:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 14, "rel:index build:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 13, 'new:index build:Error: \n   0: stage index batch on node:Doc ([Scalar { column: "body", family: NGram }, Sc': 8} | 0 | 5 | 0 | {'new': (0, 1920), 'rel': (0, 1920)} | True |
| both-write + maintenance | main | 300.0 | {'new:ok': 837, 'main:ok': 1188, 'main:index-build:ok': 24, 'main:index-rebuild:ok': 15, 'new:index-build:refused': 24, 'main:optimize:refused': 15, 'new:index-rebuild:refused': 16, 'main:cleanup:refused': 27, 'new:optimize:refused': 19, 'new:cleanup:refused': 21, 'main:index-rebuild:refused': 18, 'main:cleanup:ok': 6, 'main:index-build:refused': 9, 'main:optimize:ok': 18, 'new:cleanup:ok': 2, 'new:index-rebuild:ok': 7, 'new:optimize:ok': 4} | {'new:ok': 614, 'main:ok': 1620} | {"main:search:[conflict] full-text index 'title_idx' requires rebuild: analyzer certificate payload is m": 523, "main:cleanup --keep:Error: \n   0: collector branch 'main' changed during capture; retry cleanup\n\n  ━━━━━━━━━━━": 26, "new:optimize:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 19, "new:cleanup --keep:Error: \n   0: collector branch 'main' changed during capture; retry cleanup\n\n  ━━━━━━━━━━━": 19, "main:index rebuild:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 18, "new:index rebuild:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 16, "main:optimize:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 15, "new:index build:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 14, 'new:index build:Error: \n   0: stage index batch on node:Doc ([Scalar { column: "body", family: NGram }, Sc': 10, "main:index build:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 9, "new:cleanup --keep:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 2, "main:cleanup --keep:Error: \n   0: write authority 'graph_head:main' changed during preparation (expected hb1.0": 1} | 0 | 5 | 0 | {'new': (0, 2025), 'main': (0, 2025)} | True |
| gq2w-newr + maintenance | rel | 240.0 | {'rel:ok': 2045, 'rel:index-build:ok': 57, 'rel:index-rebuild:ok': 57, 'rel:optimize:ok': 57, 'rel:cleanup:ok': 56} | {'new:ok': 540} | — | 0 | 0 | 0 | {'new': (0, 2045), 'rel': (0, 2045)} | True |
| neww-gq2r + maintenance | rel | 240.0 | {'new:ok': 1269, 'new:index-build:ok': 36, 'new:index-rebuild:ok': 35, 'new:optimize:ok': 35, 'new:cleanup:ok': 35} | {'rel:ok': 2198} | {"rel:search:[conflict] full-text index 'title_idx' requires rebuild: analyzer certificate payload is m": 2176} | 0 | 0 | 0 | {'new': (0, 1269), 'rel': (0, 1269)} | True |

## kill -9 of NEW mid-operation

| operation | seed | built by | kill | delay / duration s | after the kill | NEW's answers | rerun ok | answers after rerun | content kept | cleanup ok |
|---|---|---|---|---|---|---|---|---|---|---|
| index_build | 1 | rel | killed | 0.353 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| index_build | 2 | rel | finished first | 2.391 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| fts_rebuild | 10 | rel | finished first | 1.437 / ? | new:open rel:open,lex=refused main:open,lex=refused | True | True | True | True | True |
| fts_rebuild | 11 | rel | finished first | 1.142 / ? | new:open rel:open,lex=refused main:open,lex=refused | True | True | True | True | True |
| fts_rebuild | 12 | rel | finished first | 1.197 / ? | new:open rel:open,lex=refused main:open,lex=refused | True | True | True | True | True |
| optimize | 10 | rel | killed | 1.437 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| optimize | 11 | rel | killed | 1.142 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| optimize | 12 | rel | killed | 1.197 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| schema_apply | 10 | rel | finished first | 1.437 / ? | new:open rel:refused(content?),lex=refused main:refused(content?),lex=refused | False | True | False | True | True |
| schema_apply | 11 | rel | finished first | 1.142 / ? | new:open rel:refused(content?),lex=refused main:refused(content?),lex=refused | False | True | False | True | True |
| schema_apply | 12 | rel | finished first | 1.197 / ? | new:open rel:refused(content?),lex=refused main:refused(content?),lex=refused | False | True | False | True | True |
| schema_apply_plain | 10 | rel | finished first | 1.437 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| schema_apply_plain | 11 | rel | finished first | 1.142 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| schema_apply_plain | 12 | rel | finished first | 1.197 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| index_build | 10 | rel | killed | 1.437 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| index_build | 11 | rel | killed | 1.142 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| index_build | 12 | rel | killed | 1.197 / ? | new:open rel:open,lex=ok main:open,lex=ok | True | True | True | True | True |
| fts_rebuild | 20 | rel | killed | 0.261 / 0.294 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 20 | rel | killed | 0.043 / 0.049 | new:open rel:refused(content?),lex=refused main:refused(content?),lex=refused | after | True | True | True | True |
| optimize | 20 | rel | killed | 1.381 / 1.552 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 20 | rel | killed | 1.343 / 1.509 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 20 | rel | killed | 0.046 / 0.052 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 21 | rel | killed | 0.053 / 0.3 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 21 | rel | killed | 0.008 / 0.047 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 21 | rel | killed | 0.275 / 1.544 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 21 | rel | killed | 0.277 / 1.553 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 21 | rel | killed | 0.01 / 0.053 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 22 | rel | killed | 0.285 / 0.303 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 22 | rel | killed | 0.048 / 0.051 | new:open rel:refused(content?),lex=refused main:refused(content?),lex=refused | after | True | True | True | True |
| optimize | 22 | rel | killed | 1.426 / 1.517 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 22 | rel | killed | 1.469 / 1.563 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 22 | rel | killed | 0.051 / 0.054 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 23 | rel | killed | 0.273 / 0.3 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 23 | rel | killed | 0.046 / 0.051 | new:open rel:refused(content?),lex=refused main:refused(content?),lex=refused | after | True | True | True | True |
| optimize | 23 | rel | killed | 1.306 / 1.439 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 23 | rel | killed | 1.402 / 1.544 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 23 | rel | killed | 0.048 / 0.052 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 24 | rel | killed | 0.206 / 0.293 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 24 | rel | killed | 0.033 / 0.047 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 24 | rel | killed | 1.076 / 1.529 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 24 | rel | killed | 1.026 / 1.458 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 24 | rel | killed | 0.039 / 0.056 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 25 | rel | killed | 0.114 / 0.297 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 25 | rel | killed | 0.019 / 0.049 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 25 | rel | killed | 0.588 / 1.541 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 25 | rel | killed | 0.588 / 1.539 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 25 | rel | killed | 0.022 / 0.056 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 26 | rel | killed | 0.222 / 0.301 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 26 | rel | killed | 0.038 / 0.051 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 26 | rel | killed | 1.172 / 1.589 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 26 | rel | killed | 1.142 / 1.549 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 26 | rel | killed | 0.041 / 0.056 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 27 | rel | killed | 0.191 / 0.298 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 27 | rel | killed | 0.033 / 0.051 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 27 | rel | killed | 0.988 / 1.538 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 27 | rel | killed | 0.958 / 1.491 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 27 | rel | killed | 0.035 / 0.054 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 28 | rel | killed | 0.039 / 0.3 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 28 | rel | killed | 0.006 / 0.048 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 28 | rel | killed | 0.201 / 1.561 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 28 | rel | killed | 0.19 / 1.48 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 28 | rel | killed | 0.007 / 0.053 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 29 | rel | killed | 0.167 / 0.306 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply | 29 | rel | killed | 0.028 / 0.051 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| optimize | 29 | rel | killed | 0.862 / 1.579 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| index_build | 29 | rel | killed | 0.837 / 1.532 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| schema_apply_plain | 29 | rel | killed | 0.029 / 0.054 | new:open rel:open,lex=ok main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 40 | main | killed | 0.139 / 0.303 | new:open main:open,lex=ok | before | True | True | True | True |
| schema_apply | 40 | main | killed | 0.024 / 0.052 | new:open main:open,lex=ok | before | True | True | True | True |
| optimize | 40 | main | killed | 0.705 / 1.533 | new:open main:open,lex=ok | before | True | True | True | True |
| index_build | 40 | main | killed | 0.663 / 1.44 | new:open main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 41 | main | killed | 0.125 / 0.323 | new:open main:open,lex=ok | before | True | True | True | True |
| schema_apply | 41 | main | killed | 0.021 / 0.053 | new:open main:open,lex=ok | before | True | True | True | True |
| optimize | 41 | main | killed | 0.607 / 1.575 | new:open main:open,lex=ok | before | True | True | True | True |
| index_build | 41 | main | killed | 0.59 / 1.528 | new:open main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 42 | main | killed | 0.195 / 0.307 | new:open main:open,lex=ok | before | True | True | True | True |
| schema_apply | 42 | main | killed | 0.032 / 0.051 | new:open main:open,lex=ok | before | True | True | True | True |
| optimize | 42 | main | killed | 0.883 / 1.393 | new:open main:open,lex=ok | before | True | True | True | True |
| index_build | 42 | main | killed | 0.991 / 1.563 | new:open main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 43 | main | killed | 0.017 / 0.305 | new:open main:open,lex=ok | before | True | True | True | True |
| schema_apply | 43 | main | killed | 0.003 / 0.052 | new:open main:open,lex=ok | before | True | True | True | True |
| optimize | 43 | main | killed | 0.085 / 1.498 | new:open main:open,lex=ok | before | True | True | True | True |
| index_build | 43 | main | killed | 0.088 / 1.536 | new:open main:open,lex=ok | before | True | True | True | True |
| fts_rebuild | 44 | main | killed | 0.121 / 0.294 | new:open main:open,lex=ok | before | True | True | True | True |
| schema_apply | 44 | main | killed | 0.02 / 0.049 | new:open main:open,lex=ok | before | True | True | True | True |
| optimize | 44 | main | killed | 0.608 / 1.475 | new:open main:open,lex=ok | before | True | True | True | True |
| index_build | 44 | main | killed | 0.642 / 1.558 | new:open main:open,lex=ok | before | True | True | True | True |

