| NEW operation | NEW outcome (graphs) | graph changed / unchanged | OLD probes after (graphs per outcome) | first OLD refusal |
|---|---|---|---|---|
| branch:create_from_deleted_commit | refused:branch create×14 — refused:branch create: [branch_start_refusal] cannot start a branch at commit 'hb1.01M7SCSMC0J1ZWNNQ6MZ4YW3NZ.2.01M7SCSMC0J1ZWNNQ6MZ4YW3NZ': branch 'gone', which wrote it, was deleted; start the branch at a commit of a live branch | 0 / 14 | — |  |
| branch:create_write_merge_delete | ok×112 | 112 / 0 | all probes ok |  |
| branch:delete_old[…] | ok×98 | 98 / 0 | all probes ok |  |
| branch:merge_old[…] | ok×98 | 98 / 0 | all probes ok |  |
| commit:drop_old_merge_proposal | ok×14 | 14 / 0 | all probes ok |  |
| commit:publish_old_merge_proposal | ok×14 | 14 / 0 | all probes ok |  |
| commit:publish_old_proposal | ok×14 | 14 / 0 | all probes ok |  |
| load:existing:append | refused:load --data×112 — refused:load --data: error: invalid value 'append' for '--mode <MODE>': the load mode `append` was removed; write `insert`, which means what `append` meant (`upsert` sets only the properties a record names) For more information, try '… | 0 / 112 | — |  |
| load:existing:insert | refused:load --data×112 — refused:load --data: [conflict] edge type 'WorksAt' already has id '["p000@example.com","c0"]'; `upsert` sets the named properties of an existing entity and `replace` writes its whole row | 112 / 0 | all probes ok |  |
| load:existing:merge | refused:load --data×112 — refused:load --data: error: invalid value 'merge' for '--mode <MODE>': the load mode `merge` was removed; write `replace`, which means what `merge` meant (`upsert` sets only the properties a record names) For more information, try '--… | 0 / 112 | — |  |
| load:existing:overwrite | refused:load --data×112 — refused:load --data: [bad_request] __src 'p000@example.com' not found in Person | 0 / 112 | — |  |
| load:existing:replace | ok×112 | 112 / 0 | all probes ok |  |
| load:existing:upsert | ok×112 | 112 / 0 | all probes ok |  |
| load:new:append | refused:load --data×112 — refused:load --data: error: invalid value 'append' for '--mode <MODE>': the load mode `append` was removed; write `insert`, which means what `append` meant (`upsert` sets only the properties a record names) For more information, try '… | 0 / 112 | — |  |
| load:new:insert | ok×112 | 112 / 0 | all probes ok |  |
| load:new:merge | refused:load --data×112 — refused:load --data: error: invalid value 'merge' for '--mode <MODE>': the load mode `merge` was removed; write `replace`, which means what `merge` meant (`upsert` sets only the properties a record names) For more information, try '--… | 0 / 112 | — |  |
| load:new:overwrite | refused:load --data×112 — refused:load --data: [bad_request] __src 'Graph-00000' not found in Doc | 0 / 112 | — |  |
| load:new:replace | ok×112 | 112 / 0 | all probes ok |  |
| load:new:replace_from_main | ok×112 | 112 / 0 | all probes ok |  |
| load:new:upsert | ok×112 | 112 / 0 | all probes ok |  |
| load:overwrite:append | refused:load --data×112 — refused:load --data: error: invalid value 'append' for '--mode <MODE>': the load mode `append` was removed; write `insert`, which means what `append` meant (`upsert` sets only the properties a record names) For more information, try '… | 0 / 112 | — |  |
| load:overwrite:insert | ok×112 | 112 / 0 | all probes ok |  |
| load:overwrite:merge | refused:load --data×112 — refused:load --data: error: invalid value 'merge' for '--mode <MODE>': the load mode `merge` was removed; write `replace`, which means what `merge` meant (`upsert` sets only the properties a record names) For more information, try '--… | 0 / 112 | — |  |
| load:overwrite:overwrite | ok×112 | 112 / 0 | all probes ok |  |
| load:overwrite:replace | ok×112 | 112 / 0 | all probes ok |  |
| load:overwrite:upsert | ok×112 | 112 / 0 | all probes ok |  |
| maint:cleanup_keep1 | ok×112 | 98 / 14 | all probes ok |  |
| maint:index_build | ok×112 | 112 / 0 | all probes ok |  |
| maint:index_rebuild_fulltext | ok×112 | 112 / 0 | read:s_bm25_title: refused×112; read:s_rrf: refused×112; read:s_search_title: refused×112 | read:s_bm25_title: full-text index 'title_idx' requires rebuild: analyzer certificate payload is malformed (index UUID bd84acc6-b0c5-42da-b335-132d8f354201); run omnigraph index rebuild --kind full-text --target <target> --branch <branch> on the live branch (historical snapshots… (base__complete__br… |
| maint:optimize | ok×112 | 112 / 0 | all probes ok |  |
| maint:upgrade | ok×112 | 0 / 112 | — |  |
| open_rw_noop | ok×112 | 0 / 112 | — |  |
| schema:add_type | ok×112 | 112 / 0 | all probes ok |  |
| schema:cosine | ok×112 | 112 / 0 | export: refused×112; index_build: refused×112; optimize: refused×112; read:q_scalars_all: refused×112; read:q_traverse: refused×112; read:s_bm25_title: refused×112; read:s_nearest: refused×112; read:s_rrf: refused×112; read:s_search_title: refused×112; recover:fts_rebuild: refused×112; recover:read:s_bm25_title: refused×112; recover:read:s_search_title: refused×112; snapshot: refused×112; write: refused×112 | export: schema evolution is locked down in phase 1: schema feature 'vector-distance' in the schema_contract row is unknown to this build; upgrade omnigraph before opening this graph; open will not recover or migrate this schema; manual coordination is required disposi… (base__complete__branches); in… |
| schema:evolve | ok×56 | 56 / 0 | all probes ok |  |
| schema:fulltext | ok×112 | 112 / 0 | export: refused×112; index_build: refused×112; optimize: refused×112; read:q_scalars_all: refused×112; read:q_traverse: refused×112; read:s_bm25_title: refused×112; read:s_nearest: refused×112; read:s_rrf: refused×112; read:s_search_title: refused×112; recover:fts_rebuild: refused×112; recover:read:s_bm25_title: refused×112; recover:read:s_search_title: refused×112; snapshot: refused×112; write: refused×112 | export: schema evolution is locked down in phase 1: schema feature 'full-text-analysis' in the schema_contract row is unknown to this build; upgrade omnigraph before opening this graph; open will not recover or migrate this schema; manual coordination is required disp… (base__complete__branches); in… |
| schema:fulltext_analyzer | ok×112 | 112 / 0 | export: refused×112; index_build: refused×112; optimize: refused×112; read:q_scalars_all: refused×112; read:q_traverse: refused×112; read:s_bm25_title: refused×112; read:s_nearest: refused×112; read:s_rrf: refused×112; read:s_search_title: refused×112; recover:fts_rebuild: refused×112; recover:read:s_bm25_title: refused×112; recover:read:s_search_title: refused×112; snapshot: refused×112; write: refused×112 | export: schema evolution is locked down in phase 1: schema feature 'full-text-analysis' in the schema_contract row is unknown to this build; upgrade omnigraph before opening this graph; open will not recover or migrate this schema; manual coordination is required disp… (base__complete__branches); in… |
| schema:l2_explicit | ok×112 | 112 / 0 | export: refused×112; index_build: refused×112; optimize: refused×112; read:q_scalars_all: refused×112; read:q_traverse: refused×112; read:s_bm25_title: refused×112; read:s_nearest: refused×112; read:s_rrf: refused×112; read:s_search_title: refused×112; recover:fts_rebuild: refused×112; recover:read:s_bm25_title: refused×112; recover:read:s_search_title: refused×112; snapshot: refused×112; write: refused×112 | export: schema evolution is locked down in phase 1: schema-contract source is not a valid accepted schema definition: parse error: --> 39:10 \\| 39 \\| emb: Vector(8, distance = "l2") @index \\| ^--- \\| = expected core_type; manual coordination is required disposition: {… (base__complete__branches)… |
| schema:list_index | ok×112 | 112 / 0 | export: refused×112; index_build: refused×112; optimize: refused×112; read:q_scalars_all: refused×112; read:q_traverse: refused×112; read:s_bm25_title: refused×112; read:s_nearest: refused×112; read:s_rrf: refused×112; read:s_search_title: refused×112; recover:fts_rebuild: refused×112; recover:read:s_bm25_title: refused×112; recover:read:s_search_title: refused×112; snapshot: refused×112; write: refused×112 | export: schema evolution is locked down in phase 1: schema-contract source is not a valid accepted schema definition: parse error: parse error: @index is not supported on list property Scalars.tags; manual coordination is required disposition: {"effects":"none","actio… (base__complete__branches); in… |
| schema:reapply_same | ok×112 | 112 / 0 | all probes ok |  |
| write:w_blob_insert:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_blob_insert:fixed | refused:mutate w_blob_insert×112 — refused:mutate w_blob_insert: [conflict] node type 'Asset' already has id 'a000'; `upsert` sets the named properties of an existing entity and `replace` writes its whole row | 0 / 112 | — |  |
| write:w_blob_insert:upsert | ok×112 | 112 / 0 | all probes ok |  |
| write:w_card_violation:e3 | refused:mutate w_card_violation×112 — refused:mutate w_card_violation: [bad_request] @card violation on edge WorksAt: source 'p000@example.com' has 2 edges (max 1) | 0 / 112 | — |  |
| write:w_card_violation:fixed | refused:mutate w_card_violation×112 — refused:mutate w_card_violation: [bad_request] @card violation on edge WorksAt: source 'p000@example.com' has 2 edges (max 1) | 0 / 112 | — |  |
| write:w_delete_edge:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_edge:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_edge_from:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_edge_from:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_mixed:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_mixed:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_node:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_delete_node:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_compound_existing:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_compound_existing:fixed | refused:mutate w_insert_compound_existing×112 — refused:mutate w_insert_compound_existing: [conflict] node type 'Pair' already has id '["y","4"]'; `upsert` sets the named properties of an existing entity and `replace` writes its whole row | 0 / 112 | — |  |
| write:w_insert_compound_existing:insert | refused:mutate w_insert_compound_existing×112 — refused:mutate w_insert_compound_existing: [conflict] node type 'Pair' already has id '["y","4"]'; `upsert` sets the named properties of an existing entity and `replace` writes its whole row | 0 / 112 | — |  |
| write:w_insert_edge:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_edge:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_edge:from_to | refused:mutate w_insert_edge×112 — refused:mutate w_insert_edge: [bad_request] type error: T11: an edge's endpoints are assigned as `@src` and `@dst`; `from:` is edition 2's spelling of `@src:` | 0 / 112 | — |  |
| write:w_insert_existing_key:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_existing_key:fixed | refused:mutate w_insert_existing_key×112 — refused:mutate w_insert_existing_key: [conflict] node type 'Person' already has id 'p001@example.com'; `upsert` sets the named properties of an existing entity and `replace` writes its whole row | 0 / 112 | — |  |
| write:w_insert_existing_key:insert | refused:mutate w_insert_existing_key×112 — refused:mutate w_insert_existing_key: [conflict] node type 'Person' already has id 'p001@example.com'; `upsert` sets the named properties of an existing entity and `replace` writes its whole row | 0 / 112 | — |  |
| write:w_insert_existing_key:upsert | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_keyed_edge_existing:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_keyed_edge_existing:fixed | refused:mutate w_insert_keyed_edge_existing×112 — refused:mutate w_insert_keyed_edge_existing: [conflict] edge type 'WorksAt' already has id '["p000@example.com","c0"]'; `upsert` sets the named properties of an existing entity and `replace` writes its whole ro… | 0 / 112 | — |  |
| write:w_insert_keyed_edge_existing:insert | refused:mutate w_insert_keyed_edge_existing×112 — refused:mutate w_insert_keyed_edge_existing: [conflict] edge type 'WorksAt' already has id '["p000@example.com","c0"]'; `upsert` sets the named properties of an existing entity and `replace` writes its whole ro… | 0 / 112 | — |  |
| write:w_insert_new_key:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_new_key:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_new_key:replace | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_unkeyed:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_insert_unkeyed:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_multi_statement:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_multi_statement:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_range_violation:e3 | refused:mutate w_range_violation×112 — refused:mutate w_range_violation: [bad_request] @range violation on Scalars.i32: value 5000 > max 1000 | 0 / 112 | — |  |
| write:w_range_violation:fixed | refused:mutate w_range_violation×112 — refused:mutate w_range_violation: [bad_request] @range violation on Scalars.i32: value 5000 > max 1000 | 0 / 112 | — |  |
| write:w_scalar_extremes:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_scalar_extremes:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_unique_violation:e3 | refused:mutate w_unique_violation×112 — refused:mutate w_unique_violation: [bad_request] @unique violation on Scalars.u32: value '3999999999' held by 's00001' and 's00005' | 0 / 112 | — |  |
| write:w_unique_violation:fixed | refused:mutate w_unique_violation×112 — refused:mutate w_unique_violation: [bad_request] @unique violation on Scalars.u32: value '3999999999' held by 's00001' and 's00005' | 0 / 112 | — |  |
| write:w_update_edge:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_edge:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_node:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_node:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_scalars:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_scalars:fixed | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_vector:e3 | ok×112 | 112 / 0 | all probes ok |  |
| write:w_update_vector:fixed | ok×112 | 112 / 0 | all probes ok |  |
