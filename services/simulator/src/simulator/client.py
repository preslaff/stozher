"""Thin async client for the STOZHER API as the simulator uses it."""

import asyncio
from typing import Any

import httpx

from .engine import master_key
from .scenario import Scenario


class ApiError(RuntimeError):
    pass


class SimClient:
    def __init__(self, http: httpx.AsyncClient, operator_key: str, system_key: str) -> None:
        self.http = http
        self.op = {"X-API-Key": operator_key}
        self.sys = {"X-API-Key": system_key}

    async def _ok(self, r: httpx.Response) -> Any:
        if r.status_code >= 400:
            raise ApiError(
                f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:200]}"
            )
        return r.json()

    async def reset(self) -> dict[str, Any]:
        return await self._ok(await self.http.post("/v1/sim/reset", headers=self.op))  # type: ignore[no-any-return]

    async def register(self, sc: Scenario) -> dict[str, int]:
        ids: dict[str, int] = {}
        for n in sc.nodes:
            body = {
                "name": n.name,
                "kind": "sim",
                "master_key_hex": master_key(sc.seed, n.name).hex(),
                "surveyed_lat": n.lat,
                "surveyed_lon": n.lon,
                "transport_ids": [{"transport": "sim", "external_id": f"sim-{sc.name}-{n.name}"}],
            }
            ids[n.name] = int(
                (await self._ok(await self.http.post("/v1/nodes", headers=self.op, json=body)))[
                    "id"
                ]
            )
        return ids

    async def ingest(self, external_id: str, payload_hex: str) -> dict[str, Any]:
        r = await self.http.post(
            "/v1/ingest/sim",
            headers=self.sys,
            json={"external_id": external_id, "payload_hex": payload_hex},
        )
        return await self._ok(r)  # type: ignore[no-any-return]

    async def hotspots(self) -> list[dict[str, Any]]:
        r = await self._ok(
            await self.http.get("/v1/hotspots", headers=self.op, params={"limit": 500})
        )
        return r["items"]  # type: ignore[no-any-return]

    async def wait_for_hotspots(
        self, timeout_s: float = 30.0, quiet_polls: int = 3
    ) -> list[dict[str, Any]]:
        """Poll until the hotspot list stops changing (the worker runs recompute asynchronously)."""
        last, same = None, 0
        for _ in range(int(timeout_s)):
            cur = [(h["id"], h["event_count"]) for h in await self.hotspots()]
            same = same + 1 if cur == last and cur else 0
            if same >= quiet_polls:
                break
            last = cur
            await asyncio.sleep(1)
        return await self.hotspots()
