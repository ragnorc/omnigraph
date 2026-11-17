"""ann-benchmarks HDF5 -> Omnigraph schema, JSONL, query params and exact ground truth.
Usage: python -I convert.py <file.hdf5> <outprefix> <n_train or 0> <n_queries> [normalize]
Writes <outprefix>.pg, .jsonl, .params.json, .gt.npz (ids and squared-L2 distances of the
exact 100 nearest in the loaded subset, recomputed when the subset is not the full train set)."""
import sys, json, numpy as np, h5py
src, pre, n, nq = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
norm = len(sys.argv) > 5 and sys.argv[5] == "normalize"
f = h5py.File(src, "r")
X = np.asarray(f["train"], dtype=np.float32); Q = np.asarray(f["test"], dtype=np.float32)
full = n == 0 or n >= len(X)
if not full: X = X[:n]
Q = Q[:nq]
if norm:
    X = X / np.linalg.norm(X, axis=1, keepdims=True).clip(1e-12); Q = Q / np.linalg.norm(Q, axis=1, keepdims=True).clip(1e-12)
dim = X.shape[1]
open(pre + ".pg", "w").write(f"node Item {{\n    id: I64 @key\n    v: Vector({dim}) @index\n}}\n")
with open(pre + ".jsonl", "w") as o:
    for i, x in enumerate(X):
        o.write('{"type":"Item","data":{"id":%d,"v":[%s]}}\n' % (i, ",".join(repr(float(v)) for v in x)))
# exact ground truth by brute force (squared L2, ties by smaller id)
gt_i = np.zeros((len(Q), 100), dtype=np.int64); gt_d = np.zeros((len(Q), 100), dtype=np.float64)
Xd = X.astype(np.float64); xx = (Xd * Xd).sum(1)
for j, q in enumerate(Q.astype(np.float64)):
    d = xx - 2 * Xd @ q + (q * q).sum()
    idx = np.lexsort((np.arange(len(d)), d))[:100]
    gt_i[j] = idx; gt_d[j] = ((Xd[idx] - q) ** 2).sum(1)
np.savez(pre + ".gt.npz", ids=gt_i, d2=gt_d, Q=Q, X=X)
json.dump({"knn": [{"q": [float(v) for v in q]} for q in Q], "exact": [{"q": [float(v) for v in q]} for q in Q],
           "knn_full": [{"q": [float(v) for v in q]} for q in Q]}, open(pre + ".params.json", "w"))
print(pre, X.shape, Q.shape, "normalized" if norm else "")
