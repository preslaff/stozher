"""Tell the API this bridge is alive and whether its radio is connected (the dashboard's "mesh bridge" chip)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger("stozher.bridge")


class HeartbeatPusher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        key: str,
        info: Callable[[], dict[str, Any]],
        *,
        interval_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        instance: str | None = None,
    ) -> None:
        self.instance = instance  # which bridge, when the kit runs one per LoRa band
        self.http, self.key, self.info, self.interval_s, self.sleep = (
            http,
            key,
            info,
            interval_s,
            sleep,
        )
        self.sent = self.errors = 0
        self.last_error: str | None = None

    async def beat(self) -> bool:
        """One heartbeat. False means the API did not take it; the bridge goes on regardless."""
        try:
            r = await self.http.post(
                "/v1/system/heartbeat",
                headers={"X-API-Key": self.key},
                json={
                    "module": "mesh_bridge",
                    "info": self.info(),
                    **({"instance": self.instance} if self.instance else {}),
                },
            )
            ok = r.status_code == 204
            self.last_error = None if ok else f"HTTP {r.status_code}"
        except (
            httpx.HTTPError
        ) as exc:  # the API may be down or the network away: never stops the bridge
            ok, self.last_error = False, exc.__class__.__name__
        self.sent += ok
        self.errors += not ok
        return ok

    async def run(self) -> None:
        while True:
            await self.beat()
            await self.sleep(self.interval_s)
