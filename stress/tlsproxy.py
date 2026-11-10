#!/usr/bin/env python3
"""A TLS-terminating HTTP/1.1 fault proxy in front of a local omnigraph-server.

The CLI refuses plain HTTP for authenticated server targets, so the served-CLI
and served-deployment campaigns go through this proxy. Each connection carries
one request (the proxy forces `Connection: close`). Faults are chosen per
request from files in CTL (checked when a request's headers arrive):

  CTL/drop_response   forward the request, wait for the upstream answer, then
                      close the client connection without a byte (lost answer)
  CTL/drop_request    close the client connection before forwarding (nothing sent)
  CTL/truncate        forward, then send only half the upstream answer
  CTL/gateway_504     forward, discard the answer, send 504
  CTL/delay_ms        N: hold the upstream answer N ms before relaying it
  CTL/stall_request   keep the client connection open, never forward (a
                      server that accepted the connection and stalls)
  CTL/match           only requests whose request line contains this text fault
  CTL/count           N: only the next N matching requests fault (decremented)
  CTL/log             appended: one line per request and the fault applied

Usage: tlsproxy.py LISTEN_PORT UPSTREAM_PORT CTL_DIR CERT KEY
"""

from __future__ import annotations

import asyncio
import ssl
import sys
import time
from pathlib import Path


def ctl_read(ctl: Path, name: str):
    p = ctl / name
    if not p.exists():
        return None
    return p.read_text().strip()


def choose_fault(ctl: Path, request_line: str):
    match = ctl_read(ctl, "match")
    if match and match not in request_line:
        return None, 0
    fault = None
    for name in ("drop_request", "drop_response", "truncate", "gateway_504", "stall_request"):
        if (ctl / name).exists():
            fault = name
            break
    delay = int(ctl_read(ctl, "delay_ms") or 0)
    if fault or delay:
        count = ctl_read(ctl, "count")
        if count is not None:
            n = int(count or 0)
            if n <= 0:
                return None, 0
            (ctl / "count").write_text(f"{n - 1}\n")
    return fault, delay


async def read_http_message(reader: asyncio.StreamReader, is_response: bool, no_body: bool = False):
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    length, chunked = None, False
    for line in lines[1:]:
        k, _, v = line.partition(b":")
        k = k.strip().lower()
        if k == b"content-length":
            length = int(v.strip())
        elif k == b"transfer-encoding" and b"chunked" in v.lower():
            chunked = True
    body = b""
    if no_body:
        return head, body
    if chunked:
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size = int(size_line.strip().split(b";")[0], 16)
            chunk = await reader.readexactly(size + 2)
            body += size_line + chunk
            if size == 0:
                break
    elif length is not None:
        body = await reader.readexactly(length)
    elif is_response:
        body = await reader.read()
    return head, body


def force_close(head: bytes) -> bytes:
    lines = [l for l in head.split(b"\r\n") if l and not l.lower().startswith(b"connection:")]
    return b"\r\n".join(lines) + b"\r\nConnection: close\r\n\r\n"


async def handle(client_r, client_w, upstream_port: int, ctl: Path):
    try:
        head, body = await read_http_message(client_r, False)
    except Exception:  # noqa: BLE001
        client_w.close()
        return
    request_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    fault, delay = choose_fault(ctl, request_line)
    with open(ctl / "log", "a") as f:
        f.write(f"{time.time():.3f} {request_line} fault={fault} delay={delay}\n")
    if fault == "drop_request":
        client_w.close()
        return
    if fault == "stall_request":
        await asyncio.sleep(600)
        client_w.close()
        return
    up_r, up_w = await asyncio.open_connection("127.0.0.1", upstream_port)
    up_w.write(force_close(head) + body)
    await up_w.drain()
    try:
        rhead, rbody = await read_http_message(up_r, True, no_body=request_line.startswith("HEAD "))
    except Exception:  # noqa: BLE001
        client_w.close()
        up_w.close()
        return
    up_w.close()
    if delay:
        await asyncio.sleep(delay / 1000)
    if fault == "drop_response":
        client_w.transport.abort()
        return
    if fault == "gateway_504":
        msg = b'{"error":"gateway timeout"}'
        client_w.write(b"HTTP/1.1 504 Gateway Timeout\r\ncontent-type: application/json\r\ncontent-length: "
                       + str(len(msg)).encode() + b"\r\nconnection: close\r\n\r\n" + msg)
        await client_w.drain()
        client_w.close()
        return
    payload = force_close(rhead) + rbody
    if fault == "truncate":
        payload = payload[: len(force_close(rhead)) + len(rbody) // 2]
        client_w.write(payload)
        await client_w.drain()
        client_w.transport.abort()
        return
    client_w.write(payload)
    await client_w.drain()
    client_w.close()


async def main():
    listen, upstream, ctl, cert, key = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]
    ctl.mkdir(parents=True, exist_ok=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = await asyncio.start_server(lambda r, w: handle(r, w, upstream, ctl), "127.0.0.1", listen, ssl=context)
    print(f"listening {listen}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
