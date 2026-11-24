| form | cases | EDITION-2 accepted |
|---|---|---|
| filter inside match | 70942 | 56665 |
| filter in property braces | 19727 | 15706 |
| whitespace-separated list | 45546 | 35142 |
| not { } over a pattern | 6554 | 5200 |
| not { } over filters only | 6922 | 5610 |
| count block | 11796 | 9386 |
| exists block | 6695 | 5316 |
| aggregate block | 1706 | 1333 |
| contains over String | 6496 | 4894 |
| contains over a list | 11048 | 8948 |
| starts_with | 6535 | 4952 |
| search | 4888 | 3352 |
| match_text | 4903 | 3360 |
| fuzzy, 2 arguments | 4933 | 3000 |
| fuzzy, 3 arguments | 4791 | 2960 |
| nearest in order | 10502 | 6526 |
| bm25 in order | 8880 | 6865 |
| rrf | 3498 | 1262 |
| ranking in return, aliased | 4523 | 2438 |
| ranking in return, unaliased | 4550 | 2400 |
| ranking under an aggregate | 5774 | 2443 |
| ranking under distinct | 1712 | 1208 |
| limit | 55810 | 43734 |
| limit without order | 16048 | 14419 |
| multi-hop | 22115 | 17053 |
| hop range | 8179 | 6078 |
| reversed triple | 5239 | 4068 |
| disconnected components | 7600 | 6066 |
| triple naming a new variable | 28403 | 21982 |
| triple naming a new variable inside a block | 8260 | 6467 |
| order keys over grouped rows | 16745 | 13373 |
| grouped order key on a meta-field | 1917 | 181 |
| read named after a function | 7167 | 5960 |
| mixed keyword case | 2290 | 0 |
| unused parameter declaration | 9688 | 7710 |
| integer/float comparison | 5587 | 2854 |
| avg over integers | 4924 | 3674 |
| integers beyond 2^53 | 2517 | 1782 |
| aggregate in return | 30122 | 22457 |
| return distinct | 8876 | 7664 |
| edge binding | 8839 | 6908 |
| undirected edge | 4077 | 3181 |
| edge alternation | 3836 | 2921 |
| is null | 8776 | 6980 |
| in over a literal list | 35129 | 28291 |
| write: several statements | 18924 | 12516 |
| write: keyed insert | 9116 | 5926 |
| write: keyed insert of an existing key | 4291 | 2772 |
| write: unkeyed insert | 15099 | 10106 |
| write: update … set … where | 8911 | 6345 |
| write: delete … where | 4747 | 3991 |
| write: edge insert with from:/to: | 5477 | 3416 |
| write: Blob assignment | 3332 | 2019 |
| write: `mutation` keyword (edition 2 refuses it) | 2421 | 0 |
| `optional` block (edition 2 refuses it) | 366 | 0 |
| parameter String | 31661 | 22637 |
| parameter Bool | 6257 | 4835 |
| parameter I32 | 12677 | 9528 |
| parameter I64 | 14044 | 10484 |
| parameter U32 | 2023 | 1457 |
| parameter U64 | 1966 | 1373 |
| parameter F32 | 3951 | 3062 |
| parameter F64 | 6333 | 4782 |
| parameter Date | 6090 | 4626 |
| parameter DateTime | 2883 | 2220 |
| parameter Vector | 22870 | 13849 |
| parameter Blob | 3049 | 2019 |

Pairs of forms never drawn together in a case EDITION-2 accepted (58 of 946):

- aggregate block + count block
- aggregate block + exists block
- aggregate block + grouped order key on a meta-field
- aggregate block + not { } over a pattern
- aggregate block + not { } over filters only
- aggregate in return + ranking in return, aliased
- aggregate in return + ranking in return, unaliased
- aggregate in return + ranking under distinct
- aggregate in return + return distinct
- avg over integers + ranking in return, aliased
- avg over integers + ranking in return, unaliased
- avg over integers + ranking under distinct
- avg over integers + return distinct
- bm25 in order + grouped order key on a meta-field
- bm25 in order + limit without order
- bm25 in order + nearest in order
- bm25 in order + order keys over grouped rows
- bm25 in order + rrf
- count block + exists block
- count block + not { } over a pattern
- count block + not { } over filters only
- exists block + not { } over a pattern
- exists block + not { } over filters only
- fuzzy, 2 arguments + fuzzy, 3 arguments
- fuzzy, 2 arguments + match_text
- fuzzy, 2 arguments + search
- fuzzy, 3 arguments + match_text
- fuzzy, 3 arguments + search
- grouped order key on a meta-field + limit without order
- grouped order key on a meta-field + nearest in order
- grouped order key on a meta-field + ranking in return, aliased
- grouped order key on a meta-field + ranking in return, unaliased
- grouped order key on a meta-field + ranking under an aggregate
- grouped order key on a meta-field + ranking under distinct
- grouped order key on a meta-field + return distinct
- grouped order key on a meta-field + rrf
- limit without order + nearest in order
- limit without order + order keys over grouped rows
- limit without order + ranking in return, aliased
- limit without order + ranking in return, unaliased
- limit without order + ranking under an aggregate
- limit without order + ranking under distinct
- limit without order + rrf
- match_text + search
- nearest in order + order keys over grouped rows
- nearest in order + rrf
- not { } over a pattern + not { } over filters only
- not { } over filters only + triple naming a new variable inside a block
- order keys over grouped rows + ranking in return, aliased
- order keys over grouped rows + ranking in return, unaliased
- order keys over grouped rows + ranking under an aggregate
- order keys over grouped rows + ranking under distinct
- order keys over grouped rows + rrf
- ranking in return, aliased + ranking in return, unaliased
- ranking in return, aliased + ranking under an aggregate
- ranking in return, unaliased + ranking under an aggregate
- ranking under an aggregate + ranking under distinct
- ranking under an aggregate + return distinct
