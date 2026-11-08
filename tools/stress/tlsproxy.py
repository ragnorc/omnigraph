#!/usr/bin/env python3
"""TLS-terminating TCP proxy with optional fault injection.

usage: tlsproxy.py LISTEN_PORT UPSTREAM_PORT CERT KEY
Faults are read per connection from /tmp/og/proxy_mode (one word):
  pass            forward normally (default)
  drop_response   forward the request, then close the client socket before
                  any response byte is sent back (lost answer)
  delay:<sec>     delay the response by <sec> seconds
"""
import asyncio, ssl, sys, os

LP, UP, CERT, KEY = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
MODE_FILE = "/tmp/og/proxy_mode"


def mode():
    try:
        return open(MODE_FILE).read().strip() or "pass"
    except FileNotFoundError:
        return "pass"


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(cr, cw):
    m = mode()
    try:
        ur, uw = await asyncio.open_connection("127.0.0.1", UP)
    except Exception:
        cw.close()
        return
    if m == "drop_post":
        state = {"post": False}

        async def c2u():
            try:
                while True:
                    data = await cr.read(65536)
                    if not data:
                        break
                    if data.startswith(b"POST "):
                        state["post"] = True
                    uw.write(data)
                    await uw.drain()
            except Exception:
                pass

        async def u2c():
            try:
                while True:
                    data = await ur.read(65536)
                    if not data:
                        break
                    if state["post"]:
                        # upstream answered the POST: drop the answer and cut the client
                        cw.close()
                        uw.close()
                        return
                    cw.write(data)
                    await cw.drain()
            except Exception:
                pass
            finally:
                try:
                    cw.close()
                except Exception:
                    pass
        await asyncio.gather(c2u(), u2c())
        return
    if m == "drop_response":
        # forward requests, swallow responses, then close the client after the upstream answered
        async def swallow():
            try:
                data = await ur.read(65536)
            except Exception:
                data = b""
            cw.close()
            uw.close()
        await asyncio.gather(pipe(cr, uw), swallow())
        return
    if m.startswith("delay:"):
        d = float(m.split(":", 1)[1])

        async def delayed(reader, writer):
            first = True
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    if first:
                        await asyncio.sleep(d)
                        first = False
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()
        await asyncio.gather(pipe(cr, uw), delayed(ur, cw))
        return
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    srv = await asyncio.start_server(handle, "127.0.0.1", LP, ssl=ctx)
    async with srv:
        await srv.serve_forever()


asyncio.run(main())
