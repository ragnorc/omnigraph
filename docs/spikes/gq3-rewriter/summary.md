| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| files | 123 | 20 | 449 | 4 | 596 |
| query texts | 1201 | 39 | 2756 | 33 | 4029 |
| already `gq 3;` | 0 | 0 | 0 | 0 | 0 |
| not edition-2 text (parse refused) | 0 | 5 | 0 | 6 | 11 |
| statements (no declaration) | 132 | 6 | 654 | 0 | 792 |
| declarations | 1069 | 29 | 2102 | 31 | 3231 |
|   reads | 954 | 20 | 1487 | 22 | 2483 |
|   writes | 115 | 9 | 615 | 9 | 748 |
|   without a schema | 752 | 26 | 0 | 21 | 799 |
|   refused by today's type checker (expect-error steps) | 0 | 0 | 132 | 7 | 139 |
| declarations rewritten | 112 | 8 | 468 | 12 | 600 |
| declarations untouched | 955 | 21 | 1586 | 19 | 2581 |
| declarations with a refusal | 2 | 0 | 48 | 0 | 50 |
| proof failures | 0 | 0 | 0 | 0 | 0 |
| list `contains` -> `in`: typed IR equal (today's compiler) | 0 | 0 | 18 | 0 | 18 |
| list `contains` -> `in`: typed IR differs | 0 | 0 | 0 | 0 | 0 |

**Edits per row** (constructs rewritten)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| infix contains -> contains() | 0 | 0 | 36 | 0 | 36 |
| infix starts_with -> starts_with() | 14 | 0 | 11 | 0 | 25 |
| list contains -> in | 0 | 0 | 22 | 0 | 22 |
| fuzzy 3rd arg -> max_edits: | 0 | 0 | 4 | 0 | 4 |
| nearest -> from nearest() | 10 | 1 | 96 | 4 | 111 |
| bm25 -> from bm25() | 0 | 3 | 34 | 2 | 39 |
| rrf -> let + from rrf() | 3 | 1 | 22 | 2 | 28 |
| delete T where -> $x: T phi delete $x | 20 | 2 | 82 | 2 | 106 |
| update T set where -> $x: T phi update $x | 65 | 1 | 186 | 2 | 254 |

**Declarations using each spelling** (rewritten or refused)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| infix contains -> contains() | 0 | 0 | 35 | 0 | 35 |
| infix starts_with -> starts_with() | 14 | 0 | 11 | 0 | 25 |
| list contains -> in | 0 | 0 | 18 | 0 | 18 |
| fuzzy 3rd arg -> max_edits: | 0 | 0 | 4 | 0 | 4 |
| nearest -> from nearest() | 10 | 1 | 97 | 4 | 112 |
| bm25 -> from bm25() | 0 | 3 | 35 | 2 | 40 |
| rrf -> let + from rrf() | 3 | 1 | 40 | 2 | 46 |
| delete T where -> $x: T phi delete $x | 20 | 2 | 84 | 2 | 108 |
| update T set where -> $x: T phi update $x | 67 | 1 | 210 | 2 | 280 |

**Of those, in declarations today's type checker refuses** (expect-error steps whose text the rewriter edits)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| infix contains -> contains() | 0 | 0 | 0 | 0 | 0 |
| infix starts_with -> starts_with() | 0 | 0 | 0 | 0 | 0 |
| list contains -> in | 0 | 0 | 0 | 0 | 0 |
| fuzzy 3rd arg -> max_edits: | 0 | 0 | 0 | 0 | 0 |
| nearest -> from nearest() | 0 | 0 | 1 | 2 | 3 |
| bm25 -> from bm25() | 0 | 0 | 1 | 2 | 3 |
| rrf -> let + from rrf() | 0 | 0 | 0 | 1 | 1 |
| delete T where -> $x: T phi delete $x | 0 | 0 | 9 | 0 | 9 |
| update T set where -> $x: T phi update $x | 0 | 0 | 5 | 0 | 5 |

**Refusals**

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| ranking: `asc` on a rank key (edition-2 T54) would become legal | 0 | 0 | 2 | 0 | 2 |
| ranking: a second ranking beside the window | 0 | 0 | 1 | 0 | 1 |
| ranking: nearest/rrf in return with no ranking order key | 0 | 0 | 2 | 0 | 2 |
| rrf: arms over different bindings | 0 | 0 | 2 | 0 | 2 |
| rrf: duplicate arms (edition-3 type error) | 0 | 0 | 15 | 0 | 15 |
| write: several statements in one body | 6 | 0 | 51 | 0 | 57 |

**Notes** (observations a rewrite carries)

| | benchmarks | docs | gqt cases | skill | total |
|---|---:|---:|---:|---:|---:|
| bare-rank-key-desc | 3 | 3 | 47 | 4 | 57 |
| edge-binding | 16 | 0 | 16 | 0 | 32 |
| legacy-endpoint | 0 | 0 | 6 | 0 | 6 |
| population-edge-column | 0 | 0 | 4 | 0 | 4 |
| population-node-columns | 3 | 2 | 61 | 2 | 68 |
| ranked-variable-dropped | 3 | 2 | 51 | 2 | 58 |
| ranking-under-aggregate | 0 | 0 | 7 | 0 | 7 |
| result-column-renamed | 0 | 0 | 2 | 0 | 2 |
| rrf-let-name | 0 | 0 | 3 | 0 | 3 |
