#!/usr/bin/env python3
"""Round 2, campaign 3: the CLI's `--timeout` on a served optimize, and
`GET /graphs/{g}/access` listing `maintain`.

`--timeout` must bound wall time from the start of the command (submission,
every lookup and every sleep), exit 2 with "stopped waiting" and an
`--operation` hint, and a later `--operation <id>` must observe the real
outcome. Cases run against the seam-armed child through the TLS proxy.
Usage: c10_cli_timeout.py OUTDIR [case ...]
"""
import json, re, shutil, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm
from c4_deploy import operator_config, start_proxy
from c6_cli import cli, posts, set_fault

GRAPH = "kg"


def main():
    out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    wanted = sys.argv[2:]
    port = [14800]
    results = []

    def case(name, setup, timeout_arg, after=None, server_env=None):
        if wanted and name not in wanted:
            return
        port[0] += 1
        p = port[0]
        d = out / name; d.mkdir()
        root = og.make_root(d, name); og.apply_cluster(root)
        ctl = d / "ctl"; ctl.mkdir()
        env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "30", "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "60"}
        env.update(server_env or {})
        srv = Child(root, p, str(d / "child.log"), ctl, env).start()
        pctl = d / "proxy"; proxy = start_proxy(p, pctl)
        cfg = d / "operator.yaml"; operator_config(cfg, p)
        res = {"case": name, "timeout": timeout_arg}
        try:
            for i in range(3):
                srv.http.load(GRAPH, json.dumps({"type": "Person", "data": {"name": f"s{i}", "bio": "b"}}))
            ctx = {"srv": srv, "ctl": ctl, "pctl": pctl, "cfg": cfg}
            if setup:
                setup(ctx)
            args = ["--json"] + (["--timeout", timeout_arg] if timeout_arg else [])
            rc, so, se, took = cli(cfg, args, timeout=120)
            m = re.search(r"operation id: (\w{26})", se)
            oid = m.group(1) if m else None
            res.update({"exit": rc, "wall_s": took, "id": oid, "posts": len(posts(pctl)),
                        "stopped_waiting": "stopped waiting" in se,
                        "operation_hint": bool(oid and f"--operation {oid}" in se),
                        "stderr": se.strip()[-400:]})
            for f in pctl.glob("*"):
                if f.name != "log":
                    f.unlink()
            if after:
                after(ctx)
            if oid:
                # observe the real outcome once the server settles
                end = time.time() + 60
                while True:
                    rc2, so2, se2, _ = cli(cfg, ["--operation", oid, "--json"], timeout=60)
                    state = None
                    try:
                        state = (json.loads(so2).get("operation") or {}).get("state") if so2.strip() else None
                    except ValueError:
                        pass
                    if rc2 != 2 or time.time() > end or "holds no record" in se2:
                        break
                    time.sleep(1)
                s, b, _ = srv.http.lookup(GRAPH, oid)
                res["later_operation"] = {"exit": rc2, "state": state, "server_lookup": (s, ((b or {}).get("operation") or {}).get("state"), (b or {}).get("lookup")),
                                          "stderr": se2.strip()[-200:]}
        except Exception as e:  # noqa: BLE001
            res["error"] = repr(e)[:500]
        finally:
            srv.kill(signal.SIGKILL); proxy.kill()
        print(json.dumps(res)[:1500], flush=True)
        results.append(res)

    def park(ctx):
        arm(ctx["ctl"], "optimize.before_compact", "hold")
        for i in range(3):
            ctx["srv"].http.load(GRAPH, json.dumps({"type": "Person", "data": {"name": f"q{i}", "bio": "b"}}))

    def release(ctx):
        (ctx["ctl"] / "optimize.before_compact.release").write_text("")

    # 1. The submission's answer stalls 20 s (the server accepted and ran it).
    case("stalled_submission_answer", lambda c: set_fault(c["pctl"], delay=20000, match="POST /graphs/kg/operations/optimize", count=1),
         "3s", after=lambda c: time.sleep(1))
    # 2. The submission is never forwarded and its connection stays open.
    case("stalled_submission_blackhole", lambda c: set_fault(c["pctl"], "stall_request", match="POST /graphs/kg/operations/optimize", count=1),
         "3s")
    # 3. Lookups stall (the run is parked, every lookup's answer is held 30 s).
    case("stalled_lookups", lambda c: (park(c), set_fault(c["pctl"], delay=30000, match="GET /graphs/kg/operations/")),
         "3s", after=release)
    # 4. Long poll: the run is parked 20 s, the CLI polls normally.
    case("long_poll", park, "2s", after=release)
    # 5. A deadline shorter than one poll interval.
    case("timeout_below_poll", park, "200ms", after=release)
    # 6. Zero.
    case("timeout_zero", park, "0s", after=release)
    # 7. Lookups blackholed after a normal submission.
    case("blackhole_lookups", lambda c: (park(c), set_fault(c["pctl"], "stall_request", match="GET /graphs/kg/operations/")),
         "3s", after=release)

    # /access: maintain is listed exactly for the principals the policy lets optimize.
    def access(name="access", fixture=og.FIXTURE):
        if wanted and name not in wanted:
            return
        port[0] += 1
        d = out / name; d.mkdir()
        root = og.make_root(d, name); og.apply_cluster(root, config=fixture)
        srv = og.Server(root, port[0], str(d / "server.log")).start()
        res = {"case": name}
        try:
            for who, tok in og.TOKENS.items():
                s, b, _ = srv.http.call("GET", f"/graphs/{GRAPH}/access", token=tok)
                listed = {"graph_wide_maintain": "maintain" in (b or {}).get("graph_wide", [])}
                for br in ("main", "feature"):
                    sb, bb, _ = srv.http.call("GET", f"/graphs/{GRAPH}/access?branch={br}", token=tok)
                    listed[f"{br}_maintain"] = "maintain" in (bb or {}).get("actions", []) if sb == 200 else sb
                s2, b2, _ = srv.http.submit(GRAPH, og.ulid(), token=tok)
                res[who] = {"access_status": s, "access": listed, "submit": s2,
                            "submit_error": (b2 or {}).get("error") if s2 not in (200, 202) else None}
                if s2 in (200, 202):
                    srv.http.wait_terminal(GRAPH, b2["operation"]["operation_id"], 60)
            s, b, _ = srv.http.call("GET", f"/graphs/{GRAPH}/access", token=None)
            res["anonymous"] = s
        finally:
            srv.kill(signal.SIGTERM)
        print(json.dumps(res)[:3000], flush=True)
        results.append(res)
    access()
    access("access_branch_scoped", og.REPO / "stress" / "fixture_access")
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
