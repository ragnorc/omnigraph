"""Print the notable records of one graph's results (a debugging aid)."""
import json
import sys

for path in sys.argv[1:]:
    recs = [json.loads(l) for l in open(path)]
    print("#####", path)
    for r in recs:
        k = r["kind"]
        if k == "harness_error":
            print("HARNESS", r.get("op"), r["error"][-800:])
        elif k == "compare":
            for tag, res in r["results"].items():
                if res["diffs"] or res["status"] != "ok" or r["old_status"] != "ok":
                    print("Q", r["op"], tag, "old", r["old_status"], "new", res["status"], res["diffs"][:2])
        elif k == "side" and r["status"] != "ok":
            print("SIDE", r["op"], r["side"], r["status"], (r.get("msg") or "")[:200])
        elif k == "write_compare" and (not r["same_state"] or r["old_status"] != r["new_status"]):
            print("W", r["op"], "old", r["old_status"], "new", r["new_status"], "same", r["same_state"],
                  (r.get("new_msg") or "")[:160].replace("\n", " "), "|", r["diff"][:2])
        elif k == "load_compare" and (not r.get("same_state") or r.get("old_status") != r["new_status"]):
            print("L", r["op"], "old", r.get("old_status"), "new", r["new_status"], "same", r.get("same_state"),
                  (r.get("new_msg") or "")[:160].replace("\n", " "), "|", r.get("diff", [])[:2])
        elif k == "rollback" and r.get("changed") and r["regressions"]:
            print("RB", r["op"], {a: (b["status"], (b.get("msg") or "")[:160].replace("\n", " ")) for a, b in r["regressions"].items()},
                  {a: b["status"] for a, b in r["probes"].items() if a.startswith("recover")})
        elif k == "new_op":
            bad = [x for x in r["runs"] if isinstance(x, dict) and x.get("status") != "ok"]
            if bad and not r["op"].startswith(("write:", "load:")):
                print("OP", r["op"], [(x["args"][:3], x["status"], (x.get("msg") or "")[:220].replace("\n", " ")) for x in bad])
        elif k in ("exports", "digest", "roundtrip", "merge_compare"):
            ok = r.get("equal", r.get("unchanged", r.get("old_eq_new", r.get("same_state"))))
            if not ok or (k == "roundtrip" and not r["old_eq_back"]):
                print(k.upper(), {a: b for a, b in r.items() if a not in ("graph", "phase", "kind")})
        elif k == "schema_old_side" and r["old_status"] != "ok":
            print("SCHEMA_OLD", r["op"], (r.get("old_msg") or "")[:160])
