"""A TCP proxy that delays the start of every client request by DELAY ms.

HTTP/1.1 requests on one connection are sequential, so a client-to-server
read that follows an idle gap is a new request; it is held DELAY ms before
it is forwarded, which models a round trip to a remote store. Responses
pass through untouched.
usage: latency_proxy.py <listen_port> <target_port> <delay_ms>
"""
import asyncio
import sys
import time

LISTEN, TARGET, DELAY = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]) / 1000.0
GAP = 0.002

async def pipe(reader, writer, delay):
    last = 0.0
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            now = time.monotonic()
            if delay and now - last > GAP:
                await asyncio.sleep(delay)
            last = time.monotonic()
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def handle(client_reader, client_writer):
    server_reader, server_writer = await asyncio.open_connection("127.0.0.1", TARGET)
    await asyncio.gather(
        pipe(client_reader, server_writer, DELAY),
        pipe(server_reader, client_writer, 0.0),
    )

async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN)
    async with server:
        await server.serve_forever()

asyncio.run(main())
