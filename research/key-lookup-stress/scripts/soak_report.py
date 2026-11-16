#!/usr/bin/env python3
"""Per-minute soak series and the RFC soak gates from a served soak directory."""
import json, sys, statistics
d = sys.argv[1]; out = sys.argv[2]
rows = []
for line in open(f"{d}/../../results/{sys.argv[3]}.out"):
    if line.startswith("[metrics]"):
        rows.append(json.loads(line.split(" ", 1)[1]))
stats = []
for line in open(f"{d}/server-0.log"):
    if "[gc-stats]" in line:
        stats.append(json.loads(line.split("[gc-stats] ", 1)[1]))
opt = [json.loads(l) for l in open(f"{d}/events.jsonl") if '"kind": "optimize"' in l]
rtt = [l.split() for l in open(f"{d}/../../results/{sys.argv[3]}-proxy-rtt.txt") if len(l.split()) == 2]
with open(out, "w") as f:
    f.write("| minute | writes/s | p50 ms | p99 ms | max ms | server RSS MB | server CPU % | writes/batch | queue | known-key files |\n")
    f.write("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for i, r in enumerate(rows):
        s = stats[i + 1] if i + 1 < len(stats) else {}
        wpb = round(s.get("entries", 0) / max(1, s.get("batches", 1)), 1) if s else ""
        f.write(f"| {int(r['t_s']//60)} | {r['ok_per_s']} | {r['p50_ms']} | {r['p99_ms']} | {r['max_ms']} | "
                f"{round(r['server_rss_mb'] or 0)} | {r['server_cpu_pct']} | {wpb} | {s.get('queue','')} | {s.get('known_key_files','')} |\n")
first5 = statistics.mean(r["ok_per_s"] for r in rows[:5])
last5 = statistics.mean(r["ok_per_s"] for r in rows[-5:])
rss = {int(r["t_s"] // 60): r["server_rss_mb"] for r in rows}
ok_opt = sum(1 for o in opt if o.get("status") == 200)
print(json.dumps({
    "minutes": len(rows), "first5_writes_per_s": round(first5, 1), "last5_writes_per_s": round(last5, 1),
    "last5_over_first5": round(last5 / first5, 3),
    "rss_10min_mb": round(rss.get(10, 0)), "rss_30min_mb": round(rss.get(30, 0)), "rss_end_mb": round(rows[-1]["server_rss_mb"]),
    "rss_30_over_10": round(rss.get(30, 0) / max(1, rss.get(10, 1)), 3),
    "optimize_runs": len(opt), "optimize_published": ok_opt,
    "optimize_secs_median": statistics.median(o["secs"] for o in opt if o.get("secs") is not None),
    "refusals_total": sum(s.get("refused", 0) for s in stats), "failed_batches": sum(s.get("failed_batches", 0) for s in stats),
    "proxy_rtt_ms_min_max": [min(float(x[1]) for x in rtt), max(float(x[1]) for x in rtt)] if rtt else None,
}))
