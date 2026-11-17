| suite | query | ed | n | err | wall p50 ms | wall p95 ms | engine p50 ms | peak RSS p50 MiB | Lance calls p50 | rows equal (ordered/unordered/differ) |
|---|---|---|---|---|---|---|---|---|---|---|
| ann-fmnist | knn784 | e2 | 200 | 0 | 272 | 278 | 247 | 662 | 50 |  |
| ann-fmnist | knn784 | gq3 | 200 | 0 | 272 | 277 | 246 | 702 | 50 | 200/0/0 |
| ann-fmnist | full784 | e2 | 100 | 0 | 271 | 277 | 246 | 658 | 50 |  |
| ann-fmnist | full784 | gq3 | 100 | 0 | 271 | 278 | 246 | 702 | 50 | 100/0/0 |
| ann-fmnist | exact784 | e2 | 0 | 0 | nan | nan | nan | nan | nan |  |
| ann-fmnist | exact784 | gq3 | 100 | 0 | 273 | 285 | 247 | 618 | 29 | 0/0/0 |
| ann-sift200k | knn128 | e2 | 200 | 0 | 178 | 191 | 153 | 497 | 44 |  |
| ann-sift200k | knn128 | gq3 | 200 | 0 | 178 | 191 | 153 | 540 | 44 | 200/0/0 |
| ann-sift200k | full128 | e2 | 100 | 0 | 177 | 184 | 152 | 499 | 44 |  |
| ann-sift200k | full128 | gq3 | 100 | 0 | 177 | 184 | 152 | 540 | 44 | 100/0/0 |
| ann-sift200k | exact128 | e2 | 0 | 0 | nan | nan | nan | nan | nan |  |
| ann-sift200k | exact128 | gq3 | 100 | 0 | 237 | 246 | 211 | 495 | 24 | 0/0/0 |
