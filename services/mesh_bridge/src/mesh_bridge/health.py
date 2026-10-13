"""Tiny health endpoint (CLAUDE.md: every module exposes health). JSON over plain HTTP."""

import asyncio
import json
from collections.abc import Callable
from typing import Any


async def serve_health(port: int, snapshot: Callable[[], dict[str, Any]]) -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            body = json.dumps(snapshot()).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", port)
