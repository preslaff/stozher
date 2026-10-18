"""Tell the API the assistant is alive and which engines it has (the dashboard's "assistant" chip)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger("stozher.assistant")


async def beat(http: httpx.AsyncClient, key: str, info: dict[str, Any]) -> bool:
    try:
        r = await http.post(
            "/v1/system/heartbeat",
            headers={"X-API-Key": key},
            json={"module": "assistant", "info": info},
        )
        return r.status_code == 204
    except httpx.HTTPError:  # the API may be down: the assistant carries on
        return False


async def run(
    http: httpx.AsyncClient,
    key: str,
    info: Callable[[], Awaitable[dict[str, Any]]],
    interval_s: float = 30.0,
) -> None:
    while True:
        await beat(http, key, await info())
        await asyncio.sleep(interval_s)
