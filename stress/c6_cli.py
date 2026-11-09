#!/usr/bin/env python3
"""Campaign 6: `omnigraph optimize --target <server>` under lost responses.

The CLI talks to the seam-armed child through the TLS fault proxy
(`tlsproxy.py`). Each case states the exit the user docs' table gives
(docs/user/operations/recorded-operations.md) and checks that the CLI never
submitted the work twice (the proxy logs every request).

Usage: c6_cli.py OUTDIR [case ...]
"""
import json, os, re, shutil, signal, subprocess, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import og
from c3_crash import Child, arm, wait_reached
from c4_deploy import operator_config, start_proxy

GRAPH = "kg"


def cli(cfg, args, timeout=180, env_extra=None):
    env = og.env_base("/tmp/oghome")
    env["OG_STRESS_TOKEN"] = "op-secret"
    env.update(env_extra or {})
    t0 = time.time()
    p = subprocess.run([og.OMNIGRAPH, "optimize", "--operator-config", str(cfg), "--target", "srv", "--graph", GRAPH, *args],
                       env=env, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr, round(time.time() - t0, 2)


def posts(proxy_ctl):
    log = proxy_ctl / "log"
    if not log.exists():
        return []
    return [l for l in log.read_text().splitlines() if "POST /graphs/kg/operations/optimize" in l]


def set_fault(ctl, fault=None, match=None, count=None, delay=None):
    for f in ("drop_request", "drop_response", "truncate", "gateway_504", "match", "count", "delay_ms"):
        (ctl / f).unlink(missing_ok=True)
    if match:
        (ctl / "match").write_text(match)
    if count is not None:
        (ctl / "count").write_text(str(count))
    if delay:
        (ctl / "delay_ms").write_text(str(delay))
    if fault:
        (ctl / fault).write_text("")


def main():
    out = Path(sys.argv[1])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    wanted = sys.argv[2:]
    port = 14700
    results = []

    def case(name, expect_exit, setup, args, check=None, server_env=None, cli_timeout=180):
        nonlocal port
        if wanted and name not in wanted:
            return
        port += 1
        d = out / name
        d.mkdir()
        root = og.make_root(d, name)
        og.apply_cluster(root)
        ctl = d / "ctl"
        ctl.mkdir()
        env = {"OMNIGRAPH_OPTIMIZE_GATE_HOLD_SECONDS": "30", "OMNIGRAPH_OPTIMIZE_MAX_GATE_HOLD_SECONDS": "60"}
        env.update(server_env or {})
        srv = Child(root, port, str(d / "child.log"), ctl, env).start()
        pctl = d / "proxy"
        proxy = start_proxy(port, pctl)
        cfg = d / "operator.yaml"
        operator_config(cfg, port)
        res = {"case": name, "expect_exit": expect_exit}
        try:
            for i in range(3):
                srv.http.load(GRAPH, json.dumps({"type": "Person", "data": {"name": f"s{i}", "bio": "b"}}))
            ctx = {"srv": srv, "ctl": ctl, "pctl": pctl, "cfg": cfg, "d": d}
            if setup:
                setup(ctx)
            rc, so, se, took = cli(cfg, args(ctx) if callable(args) else args, timeout=cli_timeout)
            res.update({"exit": rc, "took": took, "stderr": se.strip()[-600:], "stdout": so.strip()[-400:],
                        "posts": len(posts(pctl))})
            m = re.search(r"operation id: (\w{26})", se)
            res["id"] = m.group(1) if m else None
            if check:
                check(ctx, res)
            res["ok"] = rc == expect_exit and res.get("posts", 0) <= 1 and not res.get("violation")
        except Exception as e:  # noqa: BLE001
            res["error"] = repr(e)[:500]
            res["ok"] = False
        finally:
            for f in pctl.glob("*"):
                if f.name != "log":
                    f.unlink()
            srv.kill(signal.SIGKILL)
            proxy.kill()
        print(json.dumps({k: v for k, v in res.items() if k not in ("stdout",)})[:1400], flush=True)
        results.append(res)

    def record_has(ctx, res):
        oid = res.get("id")
        if not oid:
            return
        s, b, _ = ctx["srv"].http.lookup(GRAPH, oid)
        res["server_state"] = (b.get("operation") or {}).get("state") if isinstance(b, dict) else None

    def park(ctx):
        arm(ctx["ctl"], "optimize.before_compact", "hold")
        for i in range(3):
            ctx["srv"].http.load(GRAPH, json.dumps({"type": "Person", "data": {"name": f"q{i}", "bio": "b"}}))

    def park_and_release_after(seconds):
        def f(ctx):
            park(ctx)
            threading.Timer(seconds, lambda: (ctx["ctl"] / "optimize.before_compact.release").write_text("")).start()
        return f

    case("normal", 0, None, ["--json"], record_has)
    case("lost_post_response", 0, lambda c: set_fault(c["pctl"], "drop_response", match="POST /graphs/kg/operations/optimize", count=1), ["--json"], record_has)
    case("lost_post_request", 2, lambda c: set_fault(c["pctl"], "drop_request", match="POST /graphs/kg/operations/optimize", count=1), ["--json"], record_has)
    case("truncated_post", 0, lambda c: set_fault(c["pctl"], "truncate", match="POST /graphs/kg/operations/optimize", count=1), ["--json"], record_has)
    case("gateway_504_post", 0, lambda c: set_fault(c["pctl"], "gateway_504", match="POST /graphs/kg/operations/optimize", count=1), ["--json"], record_has)
    case("lookups_unanswered", 2, lambda c: (park(c), set_fault(c["pctl"], "drop_response", match="GET /graphs/kg/operations/")), ["--json"], record_has)
    case("lookups_504", 2, lambda c: (park(c), set_fault(c["pctl"], "gateway_504", match="GET /graphs/kg/operations/")), ["--json"], record_has)
    case("no_wait", 0, park_and_release_after(3), ["--no-wait", "--json"], record_has)
    case("timeout_while_running", 2, park_and_release_after(8), ["--timeout", "2s", "--json"], record_has)
    case("another_active", 75, lambda c: (park_and_release_after(5)(c), c["srv"].http.submit(GRAPH, og.ulid())), ["--json"])
    case("hold_over_ceiling", 1, None, ["--max-gate-hold", "5m", "--json"])
    case("unknown_tables", 1, None, ["--tables", "node:Nope", "--json"], record_has)
    case("routes_disabled", 1, None, ["--json"], server_env={"OMNIGRAPH_OPERATION_ROUTES": "false"})

    def cancel_during(ctx):
        park(ctx)
        def go():
            # find the CLI's id from the record, cancel it, then release
            end = time.time() + 20
            while time.time() < end:
                rec = og.read_record(ctx["srv"].root)
                act = list((rec or {}).get("active", {}).values())
                if act:
                    ctx["srv"].http.cancel(GRAPH, act[0]["operation_id"])
                    (ctx["ctl"] / "optimize.before_compact.release").write_text("")
                    return
                time.sleep(0.1)
        threading.Thread(target=go, daemon=True).start()
    case("cancelled_while_waiting", 1, cancel_during, ["--json"], record_has)

    def observe_unknown(ctx):
        ctx["unknown"] = og.ulid()
    case("observe_unknown", 2, observe_unknown, lambda c: ["--operation", c["unknown"], "--json"])

    def observe_done(ctx):
        oid = og.ulid()
        ctx["srv"].http.submit(GRAPH, oid)
        ctx["srv"].http.wait_terminal(GRAPH, oid)
        ctx["done"] = oid
    case("observe_succeeded", 0, observe_done, lambda c: ["--operation", c["done"], "--json"])

    def doubt(ctx):
        arm(ctx["ctl"], "publish.read_back", "fail_always")
        arm(ctx["ctl"], "publish.pre_merge", "fail_once")
    case("publication_in_doubt", 2, doubt, ["--timeout", "5s", "--json"], record_has)

    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    bad = [r for r in results if not r.get("ok")]
    print(f"{len(results)} cases, {len(bad)} unexpected")


if __name__ == "__main__":
    main()
