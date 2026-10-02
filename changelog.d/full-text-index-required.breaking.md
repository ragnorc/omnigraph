- A full-text call (`search`, `fuzzy`, `match_text` or `bm25`) on a property
  that declares no full-text index is now refused at type checking (`T27`).
  It used to scan the property with a case-sensitive tokenizer and answer
  differently from an indexed search. Stored queries are recompiled when a
  server starts, so run `omnigraph queries validate` and `omnigraph cluster
  plan` before upgrading, and declare `@index` on each property a refused
  query names. A call on a declared index that no build has reached is a `409`
  conflict carrying `full_text_index_required` until `omnigraph build-indexes`
  builds it on that branch.
  See the [full-text search guide][full-text-index-required-search].

[full-text-index-required-search]: ../docs/user/search/index.md#full-text-search
