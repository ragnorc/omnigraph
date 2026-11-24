# Coverage

## Schema index mix (Part 1 draws)

| feature | count (types or properties) |
|---|---|
| @index on a Bool | 320 |
| @index on a Date/DateTime | 634 |
| @index on a Vector | 967 |
| @index on a numeric | 1848 |
| @index on an enum | 323 |
| @key String with @index | 830 |
| @key String without @index | 893 |
| @range | 147 |
| @unique | 573 |
| Vector without a distance | 1889 |
| body-level @index(list) | 456 |
| cases with 0 edge types | 351 |
| cases with 1 edge types | 162 |
| cases with 1 node types | 190 |
| cases with 2 edge types | 169 |
| cases with 2 node types | 162 |
| cases with 3 edge types | 176 |
| cases with 3 node types | 173 |
| cases with 4 edge types | 167 |
| cases with 4 node types | 173 |
| cases with 5 node types | 177 |
| cases with 6 node types | 150 |
| edge @card | 350 |
| edge @key(@src, @dst) | 380 |
| edge @unique(@src, @dst) | 232 |
| edge types | 1696 |
| managed Blob | 455 |
| managed Blob? | 432 |
| node types | 3510 |
| plain @index on a String (edition 2 full text) | 3349 |
| property Blob | 455 |
| property Blob? | 432 |
| property Bool | 551 |
| property Bool? | 349 |
| property Date | 555 |
| property Date? | 384 |
| property DateTime | 533 |
| property DateTime? | 360 |
| property F32 | 505 |
| property F32? | 358 |
| property F64 | 529 |
| property F64? | 366 |
| property I32 | 1028 |
| property I32? | 366 |
| property I64 | 957 |
| property I64? | 334 |
| property String | 5074 |
| property String? | 1915 |
| property U32 | 513 |
| property U32? | 350 |
| property U64 | 523 |
| property U64? | 357 |
| property Vector | 1136 |
| property Vector? | 753 |
| property enum | 908 |
| property list | 1087 |
| property list? | 701 |
| type with no index | 269 |
| type with several full-text fields | 879 |
| type: compound @key | 502 |
| type: no @key | 872 |
| type: single @key | 2136 |

## Index states

Per lexical field type, the state of GQ 2's full-text index at the start of Part 2 (`start_state`) and at each observation (`obs_state`): `never_built`, `complete:<side>` (GQ 2's first index pass, nothing written after), `rebuilt:<side>` (GQ 2's `index rebuild --kind full-text`), `uncovered_tail` (rows appended after), `lagging` (rows updated after), `deletions`, `rebuilt_by_new` (NEW's full-text rebuild, which GQ 2 refuses). NEW's own `index build` never refreshes GQ 2's full-text index, so it does not change the label.

| state | count |
|---|---|
| obs_state:complete:main | 1090 |
| obs_state:complete:rel | 3641 |
| obs_state:deletions | 1397 |
| obs_state:lagging | 4540 |
| obs_state:never_built | 7468 |
| obs_state:rebuilt:main | 710 |
| obs_state:rebuilt:rel | 2951 |
| obs_state:rebuilt_by_new | 7407 |
| obs_state:uncovered_tail | 3525 |
| start_state:complete:main | 347 |
| start_state:complete:rel | 1097 |
| start_state:deletions | 270 |
| start_state:lagging | 808 |
| start_state:never_built | 1206 |
| start_state:rebuilt:main | 247 |
| start_state:rebuilt:rel | 914 |
| start_state:uncovered_tail | 454 |

## NEW step kinds

| step | count |
|---|---|
| new_schema:a | 215 |
| new_schema:b | 261 |
| new_schema:c_back | 238 |
| new_schema:c_change | 239 |
| new_schema:c_set | 240 |
| new_schema:d | 173 |
| new_schema:e_explicit | 172 |
| new_schema:e_remove | 171 |
| new_schema:evolve_add_index | 26 |
| new_schema:evolve_add_prop | 34 |
| new_schema:evolve_add_type | 25 |
| new_schema:evolve_drop_index | 35 |
| new_schema:evolve_drop_prop | 34 |
| new_schema:evolve_rename_prop | 36 |
| new_schema:f_to_fulltext | 152 |
| new_schema:f_to_plain | 152 |
| new_schema:g_drop | 72 |
| new_schema:g_fulltext | 165 |
| new_schema:g_readd | 72 |
| new_schema:g_rename | 93 |
| new_schema:h | 167 |
| new_schema:i_add_list | 36 |
| new_schema:i_body | 104 |
| new_schema:i_prop | 83 |
| new_schema_result:a:applied | 215 |
| new_schema_result:b:applied | 254 |
| new_schema_result:b:noop | 7 |
| new_schema_result:c_back:applied | 238 |
| new_schema_result:c_change:applied | 239 |
| new_schema_result:c_set:applied | 231 |
| new_schema_result:c_set:noop | 9 |
| new_schema_result:d:applied | 153 |
| new_schema_result:d:noop | 20 |
| new_schema_result:e_explicit:applied | 168 |
| new_schema_result:e_explicit:noop | 4 |
| new_schema_result:e_remove:applied | 171 |
| new_schema_result:evolve_add_index:applied | 26 |
| new_schema_result:evolve_add_prop:applied | 34 |
| new_schema_result:evolve_add_type:applied | 25 |
| new_schema_result:evolve_drop_index:refused | 35 |
| new_schema_result:evolve_drop_prop:applied | 34 |
| new_schema_result:evolve_rename_prop:applied | 36 |
| new_schema_result:f_to_fulltext:applied | 144 |
| new_schema_result:f_to_fulltext:noop | 8 |
| new_schema_result:f_to_plain:applied | 152 |
| new_schema_result:g_drop:applied | 72 |
| new_schema_result:g_fulltext:applied | 155 |
| new_schema_result:g_fulltext:noop | 10 |
| new_schema_result:g_readd:applied | 72 |
| new_schema_result:g_rename:applied | 93 |
| new_schema_result:h:applied | 117 |
| new_schema_result:h:noop | 49 |
| new_schema_result:i_add_list:applied | 36 |
| new_schema_result:i_body:applied | 60 |
| new_schema_result:i_body:noop | 42 |
| new_schema_result:i_body:refused | 1 |
| new_schema_result:i_prop:applied | 65 |
| new_schema_result:i_prop:noop | 18 |
| new_schema_skipped:h | 1 |
| new_schema_skipped:i_body | 1 |
| new_step:branch | 499 |
| new_step:cleanup | 299 |
| new_step:fts_rebuild | 600 |
| new_step:index_build | 947 |
| new_step:load | 592 |
| new_step:merge_gq2 | 122 |
| new_step:optimize | 490 |
| new_step:ro_open | 268 |
| new_step:rw_open | 287 |
| new_step:schema | 2995 |
| new_step:upgrade | 150 |
| new_step:write | 902 |

## GQ 2 operations in Part 1

| operation:outcome | count |
|---|---|
| gq2_op:branch_create:ok | 825 |
| gq2_op:branch_delete:ok | 338 |
| gq2_op:branch_merge:ok | 57 |
| gq2_op:branch_merge:refused | 1 |
| gq2_op:cleanup:ok | 419 |
| gq2_op:delete:ok | 558 |
| gq2_op:evolve:ok | 668 |
| gq2_op:evolve:refused | 111 |
| gq2_op:fts_rebuild:ok | 399 |
| gq2_op:index_build:ok | 1367 |
| gq2_op:insert:ok | 1396 |
| gq2_op:insert:refused | 2 |
| gq2_op:insert_edge:ok | 158 |
| gq2_op:insert_edge:refused | 23 |
| gq2_op:load:ok | 1202 |
| gq2_op:load:refused | 48 |
| gq2_op:optimize:ok | 521 |
| gq2_op:update:ok | 569 |
| gq2_op:update:refused | 3 |

## Oracles

| check | count |
|---|---|
| index_event:main:fts_rebuild | 321 |
| index_event:main:index_build | 1169 |
| index_event:new:fts_rebuild | 2085 |
| index_event:new:index_build | 3321 |
| index_event:new:schema | 10535 |
| index_event:rel:fts_rebuild | 1066 |
| index_event:rel:index_build | 3527 |
| mirror:load:insert~append:same | 170 |
| mirror:load:overwrite~overwrite:same | 148 |
| mirror:load:replace~merge:same | 88 |
| mirror:write:delete:same | 130 |
| mirror:write:insert:same | 139 |
| mirror:write:insert_edge:same | 85 |
| mirror:write:replace:same | 109 |
| mirror:write:update:same | 86 |
| mirror_skipped_one_way | 216 |
| one_way_unlisted_on_zero_step_plan | 124 |
| oracle1_compared | 588417 |
| oracle1_fresh | 1025 |
| oracle2_reference:bm25:ok | 10258 |
| oracle2_reference:bm25_ties:ok | 10258 |
| oracle2_reference:fuzzy:ok | 10360 |
| oracle2_reference:lex_all:ok | 10355 |
| oracle2_reference:lex_any:ok | 10356 |
| oracle3:main:a | 27479 |
| oracle3:main:new | 2 |
| oracle3:main:same | 185066 |
| oracle3:rel:a | 18924 |
| oracle3:rel:new | 2 |
| oracle3:rel:same | 126664 |
| probe_gq2_fts_rebuild:main:ok | 5631 |
| probe_gq2_fts_rebuild:rel:ok | 2396 |
| probe_gq2_index_build:main:ok | 5562 |
| probe_gq2_index_build:main:refused | 69 |
| probe_gq2_index_build:rel:ok | 2369 |
| probe_gq2_index_build:rel:refused | 27 |
| probe_write:main:ok | 5543 |
| probe_write:main:refused | 2 |
| probe_write:rel:ok | 3761 |
| probe_write:rel:refused | 2 |
| rollback:main:fts_refused | 1360 |
| rollback:main:ok | 4345 |
| rollback:main:whole_refused | 3471 |
| rollback:rel:fts_refused | 940 |
| rollback:rel:ok | 2901 |
| rollback:rel:whole_refused | 2975 |
| rollback_fts_restored:main:yes | 1350 |
| rollback_fts_restored:rel:yes | 936 |

## Part 3 alternating writers

| check | count |
|---|---|
| mixed_gq2_step:branch_create | 92 |
| mixed_gq2_step:branch_delete | 36 |
| mixed_gq2_step:branch_merge | 3 |
| mixed_gq2_step:cleanup | 39 |
| mixed_gq2_step:delete | 67 |
| mixed_gq2_step:evolve | 110 |
| mixed_gq2_step:fts_rebuild | 67 |
| mixed_gq2_step:index_build | 216 |
| mixed_gq2_step:insert | 148 |
| mixed_gq2_step:insert_edge | 19 |
| mixed_gq2_step:load | 158 |
| mixed_gq2_step:optimize | 93 |
| mixed_gq2_step:update | 112 |
| new_step:branch | 61 |
| new_step:cleanup | 37 |
| new_step:fts_rebuild | 77 |
| new_step:index_build | 156 |
| new_step:load | 86 |
| new_step:merge_gq2 | 14 |
| new_step:optimize | 78 |
| new_step:ro_open | 26 |
| new_step:rw_open | 35 |
| new_step:schema | 209 |
| new_step:write | 108 |
| oracle1_compared | 172344 |
| oracle1_fresh | 130 |
| oracle2_reference:bm25:ok | 1751 |
| oracle2_reference:bm25_ties:ok | 1751 |
| oracle2_reference:fuzzy:ok | 1751 |
| oracle2_reference:lex_all:ok | 1751 |
| oracle2_reference:lex_any:ok | 1751 |
| oracle3:main:a | 7166 |
| oracle3:main:new | 21 |
| oracle3:main:same | 54975 |
| oracle3:rel:a | 5430 |
| oracle3:rel:new | 21 |
| oracle3:rel:same | 39580 |
| rollback:main:fts_refused | 321 |
| rollback:main:ok | 1351 |
| rollback:main:whole_refused | 505 |
| rollback:rel:fts_refused | 268 |
| rollback:rel:ok | 899 |
| rollback:rel:whole_refused | 408 |
| rollback_fts_restored:main:yes | 321 |
| rollback_fts_restored:rel:yes | 268 |

## NEW step kind × starting index state (cases)

| step | complete | deletions | lagging | never_built | rebuilt | uncovered_tail |
|---|---|---|---|---|---|---|
| branch | 124 | 52 | 145 | 77 | 88 | 81 |
| cleanup | 85 | 37 | 103 | 54 | 61 | 48 |
| fts_rebuild | 144 | 62 | 154 | 102 | 109 | 86 |
| index_build | 205 | 92 | 217 | 123 | 153 | 124 |
| load | 138 | 62 | 157 | 90 | 106 | 91 |
| merge_gq2 | 24 | 16 | 40 | 21 | 27 | 23 |
| optimize | 129 | 52 | 136 | 85 | 83 | 75 |
| ro_open | 75 | 39 | 89 | 49 | 49 | 39 |
| rw_open | 76 | 29 | 90 | 55 | 66 | 39 |
| schema | 290 | 116 | 303 | 194 | 221 | 177 |
| schema:a | 67 | 22 | 75 | 45 | 43 | 39 |
| schema:b | 82 | 34 | 76 | 58 | 56 | 53 |
| schema:c_back | 70 | 37 | 72 | 39 | 65 | 47 |
| schema:c_change | 71 | 38 | 73 | 39 | 65 | 47 |
| schema:c_set | 71 | 38 | 74 | 39 | 66 | 47 |
| schema:d | 54 | 21 | 53 | 35 | 44 | 26 |
| schema:e_explicit | 49 | 22 | 49 | 45 | 46 | 25 |
| schema:e_remove | 49 | 22 | 49 | 44 | 46 | 25 |
| schema:evolve_add_index | 7 | 2 | 8 | 10 | 3 | 4 |
| schema:evolve_add_prop | 10 | 5 | 16 | 5 | 9 | 5 |
| schema:evolve_add_type | 9 | 3 | 6 | 8 | 3 | 4 |
| schema:evolve_drop_index | 16 | 6 | 15 | 7 | 3 | 8 |
| schema:evolve_drop_prop | 11 | 4 | 15 | 6 | 4 | 10 |
| schema:evolve_rename_prop | 11 | 3 | 10 | 9 | 13 | 9 |
| schema:f_to_fulltext | 56 | 23 | 53 | 30 | 35 | 33 |
| schema:f_to_plain | 56 | 23 | 53 | 30 | 35 | 33 |
| schema:g_drop | 18 | 6 | 29 | 19 | 13 | 12 |
| schema:g_fulltext | 41 | 21 | 56 | 40 | 40 | 30 |
| schema:g_readd | 18 | 6 | 29 | 19 | 13 | 12 |
| schema:g_rename | 26 | 16 | 31 | 23 | 28 | 21 |
| schema:h | 56 | 21 | 52 | 31 | 36 | 27 |
| schema:i_add_list | 12 | 2 | 10 | 7 | 2 | 7 |
| schema:i_body | 37 | 10 | 29 | 25 | 20 | 19 |
| schema:i_prop | 25 | 15 | 28 | 10 | 20 | 26 |
| upgrade | 40 | 14 | 47 | 26 | 42 | 28 |
| write | 185 | 70 | 219 | 126 | 133 | 108 |

Never drawn: none.

