# Rollback per NEW step kind and GQ 2 binary

After every NEW step each GQ 2 binary opened the graph (`snapshot`), ran the edition-2 read set, exported it, wrote to a copy, and on another copy ran its own `index build` and `index rebuild --kind full-text`. Status after the step: `ok`; `fts_refused` (lexical reads refused with `full_text_index_rebuild_required`, everything else answers); `whole_refused` with its cause (`one_way`: the stored schema holds a construct `schema plan` listed; `stamp`: the graph is at a storage format the binary does not serve). Transitions count the status change from the previous observation of the same case. `restored` counts GQ 2's own full-text rebuild bringing its lexical reads back.

| NEW step | GQ 2 | observations | status after | transitions | GQ 2's rebuild |
|---|---|---|---|---|---|
| branch | main | 375 | ok: 262, fts_refused: 68, whole_refused (one_way): 45 | — | restored: 68 |
| branch | rel | 280 | ok: 183, fts_refused: 48, whole_refused (one_way): 33, whole_refused (stamp): 16 | — | restored: 48 |
| cleanup | main | 229 | ok: 149, fts_refused: 44, whole_refused (one_way): 36 | — | restored: 44 |
| cleanup | rel | 171 | ok: 102, fts_refused: 29, whole_refused (one_way): 26, whole_refused (stamp): 14 | — | restored: 29 |
| fts_rebuild | main | 452 | fts_refused: 336, ok: 59, whole_refused (one_way): 57 | ok -> fts_refused: 266 | restored: 336 |
| fts_rebuild | rel | 336 | fts_refused: 248, whole_refused (one_way): 36, ok: 28, whole_refused (stamp): 24 | ok -> fts_refused: 193 | restored: 248 |
| gq2:branch_create | main | 89 | ok: 55, whole_refused (one_way): 21, fts_refused: 13 | — | restored: 13 |
| gq2:branch_create | rel | 65 | ok: 36, whole_refused (one_way): 17, fts_refused: 12 | — | restored: 12 |
| gq2:branch_delete | main | 35 | whole_refused (one_way): 15, ok: 15, fts_refused: 5 | — | restored: 5 |
| gq2:branch_delete | rel | 24 | whole_refused (one_way): 12, ok: 7, fts_refused: 5 | — | restored: 5 |
| gq2:branch_merge | main | 3 | ok: 2, whole_refused (one_way): 1 | — | — |
| gq2:branch_merge | rel | 2 | ok: 1, whole_refused (one_way): 1 | — | — |
| gq2:cleanup | main | 37 | ok: 20, whole_refused (one_way): 12, fts_refused: 5 | — | restored: 5 |
| gq2:cleanup | rel | 24 | ok: 13, whole_refused (one_way): 9, fts_refused: 2 | — | restored: 2 |
| gq2:delete | main | 60 | ok: 36, whole_refused (one_way): 18, fts_refused: 6 | — | restored: 6 |
| gq2:delete | rel | 39 | ok: 20, whole_refused (one_way): 13, fts_refused: 6 | — | restored: 6 |
| gq2:evolve | main | 105 | ok: 63, whole_refused (one_way): 32, fts_refused: 10 | — | restored: 10 |
| gq2:evolve | rel | 80 | ok: 42, whole_refused (one_way): 30, fts_refused: 8 | — | restored: 8 |
| gq2:fts_rebuild | main | 67 | ok: 53, whole_refused (one_way): 14 | fts_refused -> ok: 5 | — |
| gq2:fts_rebuild | rel | 49 | ok: 38, whole_refused (one_way): 11 | fts_refused -> ok: 4 | — |
| gq2:index_build | main | 202 | ok: 113, whole_refused (one_way): 62, fts_refused: 27 | — | restored: 27 |
| gq2:index_build | rel | 151 | ok: 76, whole_refused (one_way): 49, fts_refused: 26 | — | restored: 26 |
| gq2:insert | main | 140 | ok: 73, whole_refused (one_way): 46, fts_refused: 21 | — | restored: 21 |
| gq2:insert | rel | 103 | ok: 44, whole_refused (one_way): 39, fts_refused: 20 | — | restored: 20 |
| gq2:insert_edge | main | 18 | ok: 12, whole_refused (one_way): 3, fts_refused: 3 | — | restored: 3 |
| gq2:insert_edge | rel | 13 | ok: 8, whole_refused (one_way): 3, fts_refused: 2 | — | restored: 2 |
| gq2:load | main | 152 | ok: 92, whole_refused (one_way): 40, fts_refused: 20 | fts_refused -> ok: 1 | restored: 20 |
| gq2:load | rel | 109 | ok: 61, whole_refused (one_way): 34, fts_refused: 14 | — | restored: 14 |
| gq2:optimize | main | 91 | ok: 43, whole_refused (one_way): 29, fts_refused: 19 | — | restored: 19 |
| gq2:optimize | rel | 64 | ok: 28, whole_refused (one_way): 24, fts_refused: 12 | — | restored: 12 |
| gq2:update | main | 101 | ok: 61, whole_refused (one_way): 30, fts_refused: 10 | — | restored: 10 |
| gq2:update | rel | 76 | ok: 44, whole_refused (one_way): 25, fts_refused: 7 | — | restored: 7 |
| index_build | main | 783 | ok: 457, whole_refused (one_way): 217, fts_refused: 109 | — | restored: 109 |
| index_build | rel | 580 | ok: 299, whole_refused (one_way): 151, fts_refused: 87, whole_refused (stamp): 43 | — | restored: 87 |
| load | main | 458 | ok: 338, fts_refused: 62, whole_refused (one_way): 58 | fts_refused -> ok: 3 | restored: 62 |
| load | rel | 337 | ok: 223, fts_refused: 43, whole_refused (one_way): 43, whole_refused (stamp): 28 | fts_refused -> ok: 2 | restored: 43 |
| merge_gq2 | main | 100 | ok: 65, fts_refused: 25, whole_refused (one_way): 10 | — | restored: 25 |
| merge_gq2 | rel | 69 | ok: 41, fts_refused: 16, whole_refused (stamp): 7, whole_refused (one_way): 5 | — | restored: 16 |
| optimize | main | 376 | ok: 249, fts_refused: 73, whole_refused (one_way): 54 | — | restored: 73 |
| optimize | rel | 281 | ok: 173, fts_refused: 55, whole_refused (one_way): 39, whole_refused (stamp): 14 | — | restored: 55 |
| ro_open | main | 198 | ok: 149, fts_refused: 25, whole_refused (one_way): 24 | — | restored: 25 |
| ro_open | rel | 147 | ok: 103, whole_refused (one_way): 18, fts_refused: 18, whole_refused (stamp): 8 | — | restored: 18 |
| rw_open | main | 212 | ok: 151, fts_refused: 34, whole_refused (one_way): 27 | — | restored: 34 |
| rw_open | rel | 160 | ok: 105, fts_refused: 23, whole_refused (one_way): 17, whole_refused (stamp): 15 | — | restored: 23 |
| schema:a | main | 136 | whole_refused (one_way): 136 | ok -> whole_refused: 47, fts_refused -> whole_refused: 27 | — |
| schema:a | rel | 102 | whole_refused (one_way): 93, whole_refused (stamp): 9 | ok -> whole_refused: 31, fts_refused -> whole_refused: 18 | — |
| schema:b | main | 176 | whole_refused (one_way): 176 | ok -> whole_refused: 66, fts_refused -> whole_refused: 27 | — |
| schema:b | rel | 136 | whole_refused (one_way): 116, whole_refused (stamp): 20 | ok -> whole_refused: 46, fts_refused -> whole_refused: 17 | — |
| schema:c_back | main | 199 | whole_refused (one_way): 199 | — | — |
| schema:c_back | rel | 147 | whole_refused (one_way): 130, whole_refused (stamp): 17 | — | — |
| schema:c_change | main | 200 | whole_refused (one_way): 200 | ok -> whole_refused: 14, fts_refused -> whole_refused: 12 | — |
| schema:c_change | rel | 148 | whole_refused (one_way): 131, whole_refused (stamp): 17 | fts_refused -> whole_refused: 10, ok -> whole_refused: 8 | — |
| schema:c_set | main | 175 | whole_refused (one_way): 175 | ok -> whole_refused: 58, fts_refused -> whole_refused: 27 | — |
| schema:c_set | rel | 129 | whole_refused (one_way): 112, whole_refused (stamp): 17 | ok -> whole_refused: 40, fts_refused -> whole_refused: 18 | — |
| schema:d | main | 113 | whole_refused (one_way): 113 | ok -> whole_refused: 42, fts_refused -> whole_refused: 19 | — |
| schema:d | rel | 89 | whole_refused (one_way): 79, whole_refused (stamp): 10 | ok -> whole_refused: 29, fts_refused -> whole_refused: 12 | — |
| schema:e_explicit | main | 122 | whole_refused (one_way): 122 | ok -> whole_refused: 51, fts_refused -> whole_refused: 14 | — |
| schema:e_explicit | rel | 98 | whole_refused (one_way): 84, whole_refused (stamp): 14 | ok -> whole_refused: 32, fts_refused -> whole_refused: 10 | — |
| schema:e_remove | main | 140 | whole_refused (one_way): 140 | ok -> whole_refused: 13, fts_refused -> whole_refused: 6 | — |
| schema:e_remove | rel | 112 | whole_refused (one_way): 98, whole_refused (stamp): 14 | ok -> whole_refused: 9, fts_refused -> whole_refused: 6 | — |
| schema:evolve_add_index | main | 19 | ok: 11, fts_refused: 4, whole_refused (one_way): 4 | — | restored: 4 |
| schema:evolve_add_index | rel | 18 | ok: 9, fts_refused: 4, whole_refused (one_way): 3, whole_refused (stamp): 2 | — | restored: 4 |
| schema:evolve_add_prop | main | 28 | ok: 24, fts_refused: 2, whole_refused (one_way): 2 | — | restored: 2 |
| schema:evolve_add_prop | rel | 21 | ok: 17, whole_refused (one_way): 2, whole_refused (stamp): 1, fts_refused: 1 | — | restored: 1 |
| schema:evolve_add_type | main | 21 | ok: 18, fts_refused: 2, whole_refused (one_way): 1 | — | restored: 2 |
| schema:evolve_add_type | rel | 16 | ok: 10, whole_refused (stamp): 3, fts_refused: 2, whole_refused (one_way): 1 | — | restored: 2 |
| schema:evolve_drop_index | main | 18 | ok: 15, whole_refused (one_way): 2, fts_refused: 1 | — | restored: 1 |
| schema:evolve_drop_index | rel | 15 | ok: 10, whole_refused (stamp): 3, whole_refused (one_way): 1, fts_refused: 1 | — | restored: 1 |
| schema:evolve_drop_prop | main | 27 | ok: 19, whole_refused (one_way): 4, fts_refused: 4 | — | restored: 4 |
| schema:evolve_drop_prop | rel | 20 | ok: 13, whole_refused (one_way): 4, whole_refused (stamp): 2, fts_refused: 1 | — | restored: 1 |
| schema:evolve_rename_prop | main | 32 | ok: 22, whole_refused (one_way): 6, fts_refused: 4 | — | restored: 4 |
| schema:evolve_rename_prop | rel | 19 | ok: 12, whole_refused (one_way): 5, fts_refused: 2 | — | restored: 2 |
| schema:f_to_fulltext | main | 104 | whole_refused (one_way): 104 | ok -> whole_refused: 37, fts_refused -> whole_refused: 11 | — |
| schema:f_to_fulltext | rel | 79 | whole_refused (one_way): 68, whole_refused (stamp): 11 | ok -> whole_refused: 24, fts_refused -> whole_refused: 8 | — |
| schema:f_to_plain | main | 119 | whole_refused (one_way): 48, ok: 46, fts_refused: 25 | whole_refused -> ok: 38, whole_refused -> fts_refused: 23 | restored: 25 |
| schema:f_to_plain | rel | 92 | ok: 32, whole_refused (one_way): 31, fts_refused: 18, whole_refused (stamp): 11 | whole_refused -> ok: 26, whole_refused -> fts_refused: 16 | restored: 18 |
| schema:g_drop | main | 68 | ok: 39, fts_refused: 15, whole_refused (one_way): 14 | whole_refused -> ok: 32, whole_refused -> fts_refused: 12 | restored: 15 |
| schema:g_drop | rel | 50 | ok: 25, fts_refused: 10, whole_refused (one_way): 9, whole_refused (stamp): 6 | whole_refused -> ok: 19, whole_refused -> fts_refused: 9 | restored: 10 |
| schema:g_fulltext | main | 126 | whole_refused (one_way): 125, fts_refused: 1 | ok -> whole_refused: 38, fts_refused -> whole_refused: 25 | restored: 1 |
| schema:g_fulltext | rel | 95 | whole_refused (one_way): 79, whole_refused (stamp): 15, fts_refused: 1 | ok -> whole_refused: 23, fts_refused -> whole_refused: 16 | restored: 1 |
| schema:g_readd | main | 68 | whole_refused (one_way): 68 | ok -> whole_refused: 39, fts_refused -> whole_refused: 15 | — |
| schema:g_readd | rel | 50 | whole_refused (one_way): 44, whole_refused (stamp): 6 | ok -> whole_refused: 25, fts_refused -> whole_refused: 10 | — |
| schema:g_rename | main | 76 | whole_refused (one_way): 69, ok: 5, fts_refused: 2 | — | restored: 2 |
| schema:g_rename | rel | 59 | whole_refused (one_way): 45, whole_refused (stamp): 9, ok: 3, fts_refused: 2 | — | restored: 2 |
| schema:h | main | 93 | whole_refused (one_way): 74, ok: 15, fts_refused: 4 | ok -> whole_refused: 27, fts_refused -> whole_refused: 7 | restored: 4 |
| schema:h | rel | 66 | whole_refused (one_way): 48, whole_refused (stamp): 9, ok: 6, fts_refused: 3 | ok -> whole_refused: 16, fts_refused -> whole_refused: 5 | restored: 3 |
| schema:i_add_list | main | 32 | ok: 19, whole_refused (one_way): 10, fts_refused: 3 | — | restored: 3 |
| schema:i_add_list | rel | 20 | ok: 9, whole_refused (one_way): 7, fts_refused: 3, whole_refused (stamp): 1 | — | restored: 3 |
| schema:i_body | main | 85 | ok: 61, whole_refused (one_way): 13, fts_refused: 11 | — | restored: 11 |
| schema:i_body | rel | 56 | ok: 33, fts_refused: 10, whole_refused (one_way): 9, whole_refused (stamp): 4 | — | restored: 10 |
| schema:i_prop | main | 56 | whole_refused (one_way): 51, ok: 4, fts_refused: 1 | ok -> whole_refused: 26, fts_refused -> whole_refused: 6 | restored: 1 |
| schema:i_prop | rel | 37 | whole_refused (one_way): 26, whole_refused (stamp): 8, ok: 2, fts_refused: 1 | ok -> whole_refused: 15, fts_refused -> whole_refused: 3 | restored: 1 |
| start | main | 785 | ok: 785 | — | — |
| start | rel | 590 | ok: 590 | — | — |
| upgrade | main | 99 | ok: 68, whole_refused (one_way): 16, fts_refused: 15 | — | restored: 15 |
| upgrade | rel | 99 | whole_refused (stamp): 99 | ok -> whole_refused: 60, fts_refused -> whole_refused: 14 | — |
| write | main | 688 | ok: 471, fts_refused: 121, whole_refused (one_way): 96 | — | restored: 121 |
| write | rel | 513 | ok: 305, fts_refused: 93, whole_refused (one_way): 74, whole_refused (stamp): 41 | — | restored: 93 |
