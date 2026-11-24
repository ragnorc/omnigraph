# Summary

| part | cases | seeds | status | case-hours | truncated | invocations | builder |
|---|---|---|---|---|---|---|---|
| Part 1+2 (random configurations) | 1001 | 1000–2000 | {'ok': 1001} | 37.67 | 23 | {'rel': 180179, 'new': 1043015, 'main': 257106} | [('rel', 743), ('main', 258)] |
| Part 1+2 stress (100,000 rows) | 24 | 9000–9023 | {'ok': 24} | 1.3 | 0 | {'main': 3589, 'new': 11852, 'rel': 2155} | [('rel', 14), ('main', 10)] |
| Part 3 alternating writers | 130 | 5000–5428 | {'ok': 130} | 7.16 | 30 | {'rel': 54323, 'new': 257197, 'main': 75079} | [('rel', 96), ('main', 34)] |

Part 3 concurrent runs: 11; kill -9 runs: 87.
