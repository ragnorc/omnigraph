#!/usr/bin/env python3
"""A plain-HTTP fault proxy between omnigraph and RustFS (S3).

Requests are relayed one per connection. For a request whose request line
contains CTL/match (and only the next CTL/count of them, when set):

  CTL/drop_response     forward, await the answer, then reset the client
                        (the write applied, its answer is lost)
  CTL/late_forward      reset the client at once and forward the request
                        CTL/delay_ms later (the write lands after the client
                        has given up and read the object back)
  CTL/drop_request      reset the client without forwarding (nothing applied)

Every request is logged to CTL/log with the fault applied.
Usage: s3proxy.py LISTEN_PORT UPSTREAM_PORT CTL_DIR
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tlsproxy import read_http_message, force_close  # noqa: E402


def choose_rule(ctl: Path, request_line: str):
    """Rules in CTL/rules.json: [{"match", "mode", "delay_ms", "count"}]; the
    first matching rule with count left applies (modes as below, plus `slow`:
    wait delay_ms, then forward and answer normally)."""
    p = ctl / "rules.json"
    if not p.exists():
        return None, 0
    import json
    rules = json.loads(p.read_text())
    for rule in rules:
        if rule["match"] in request_line and rule.get("count", 1) != 0:
            if rule.get("count", -1) > 0:
                rule["count"] -= 1
                p.write_text(json.dumps(rules))
            return rule["mode"], int(rule.get("delay_ms", 0))
    return None, 0


def choose(ctl: Path, request_line: str):
    match = (ctl / "match").read_text().strip() if (ctl / "match").exists() else None
    if not match or match not in request_line:
        return None
    for name in ("drop_response", "late_forward", "drop_request"):
        if (ctl / name).exists():
            count = ctl / "count"
            if count.exists():
                n = int(count.read_text().strip() or 0)
                if n <= 0:
                    return None
                count.write_text(f"{n - 1}\n")
            return name
    return None


async def forward(upstream: int, head: bytes, body: bytes, no_body: bool):
    r, w = await asyncio.open_connection("127.0.0.1", upstream)
    w.write(force_close(head) + body)
    await w.drain()
    rhead, rbody = await read_http_message(r, True, no_body=no_body)
    w.close()
    return rhead, rbody


async def handle(cr, cw, upstream: int, ctl: Path):
    try:
        head, body = await read_http_message(cr, False)
    except Exception:  # noqa: BLE001
        cw.close()
        return
    line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    fault, delay = choose_rule(ctl, line)
    if fault is None:
        fault = choose(ctl, line)
        delay = int((ctl / "delay_ms").read_text().strip()) if (ctl / "delay_ms").exists() else 0
    if fault == "slow":
        await asyncio.sleep(delay / 1000)
        fault = None
    with open(ctl / "log", "a") as f:
        f.write(f"{time.time():.3f} {line} fault={fault}\n")
    no_body = line.startswith("HEAD ")
    if fault == "drop_request":
        cw.transport.abort()
        return
    if fault == "late_forward":
        cw.transport.abort()
        await asyncio.sleep(delay / 1000)
        try:
            rhead, _ = await forward(upstream, head, body, no_body)
            with open(ctl / "log", "a") as f:
                f.write(f"{time.time():.3f} LATE {line} -> {rhead.split(b' ', 2)[1].decode()}\n")
        except Exception as e:  # noqa: BLE001
            with open(ctl / "log", "a") as f:
                f.write(f"{time.time():.3f} LATE {line} failed {e}\n")
        return
    try:
        rhead, rbody = await forward(upstream, head, body, no_body)
    except Exception:  # noqa: BLE001
        cw.transport.abort()
        return
    if fault == "drop_response":
        cw.transport.abort()
        return
    cw.write(force_close(rhead) + rbody)
    await cw.drain()
    cw.close()


async def main():
    listen, upstream, ctl = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    ctl.mkdir(parents=True, exist_ok=True)
    server = await asyncio.start_server(lambda r, w: handle(r, w, upstream, ctl), "127.0.0.1", listen)
    print(f"listening {listen}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
