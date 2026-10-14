"""The road-hazard scenario (docs/hazards.md section 12): synthetic community reports, scored against what must happen.

Data only. Every report goes through `POST /v1/sim/reports`, is stored with `sim = true`, and is removed by
`POST /v1/sim/reset`. Places are on the Bulgarian coast near the coastal GNSS scenario; nothing here is real.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

LON0, LAT0 = 27.9100, 43.2000
M_PER_DEG_LON = 81_000.0
ROUTE = [[LON0 - 0.005, LAT0], [LON0 + 0.035, LAT0]]  # about 3.2 km of coast road


def lon_at(east_m: float) -> float:
    return round(LON0 + east_m / M_PER_DEG_LON, 5)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class HazardReportCard:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)


class HazardScenario:
    def __init__(
        self,
        http: httpx.AsyncClient,
        operator_key: str,
        *,
        tick: Callable[[], Awaitable[None]] | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.http, self.op = http, {"X-API-Key": operator_key}
        self.tick, self.timeout_s = tick, timeout_s
        self.card = HazardReportCard()
        self.watch_id: str | None = None

    # ---- plumbing ------------------------------------------------------------------------------------------------

    async def report(
        self,
        team: str,
        reporter: str,
        kind: str,
        passability: str,
        east: float,
        minutes_ago: float = 5,
    ) -> httpx.Response:
        when = (
            (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
        )
        return await self.http.post(
            "/v1/sim/reports",
            headers=self.op,
            json={
                "team": team,
                "reporter": reporter,
                "kind": kind,
                "passability": passability,
                "observed_at": when,
                "geometry": {"type": "Point", "coordinates": [lon_at(east), LAT0]},
            },
        )

    async def hazard(self, kind: str, east: float) -> dict[str, Any] | None:
        r = await self.http.get(
            "/v1/hazards", headers=self.op, params={"status": "all", "limit": 500}
        )
        for h in r.json()["items"]:
            c = h["geometry"]["coordinates"] if h["geometry"]["type"] == "Point" else None
            if c is None:  # a hull of several reports: use its first vertex
                ring = h["geometry"]["coordinates"]
                c = ring[0][0] if h["geometry"]["type"] == "Polygon" else ring[0]
            if h["kind"] == kind and abs(c[0] - lon_at(east)) < 0.004:
                return h  # type: ignore[no-any-return]
        return None

    async def alerts(self) -> list[dict[str, Any]]:
        r = await self.http.get(
            "/v1/alerts", headers=self.op, params={"route_id": self.watch_id, "limit": 200}
        )
        return [a for a in r.json()["items"] if a["kind"].startswith("route_hazard")]

    async def until(self, cond: Callable[[], Awaitable[bool]]) -> bool:
        """Let the worker run (in process, or wait for a running one) until `cond` holds."""
        waited = 0.0
        while waited <= self.timeout_s:
            if self.tick:
                await self.tick()
            if await cond():
                return True
            await asyncio.sleep(0.2 if self.tick else 1.0)
            waited += 1.0
        return False

    async def quiet(self) -> None:
        """Give the worker time to do something wrong before a check that nothing happened."""
        if self.tick:
            await self.tick()
        else:
            await asyncio.sleep(3)

    # ---- the story -------------------------------------------------------------------------------------------------

    async def run(self, keep: bool = False) -> HazardReportCard:
        c = self.card
        if not keep:
            await self.http.post("/v1/sim/reset", headers=self.op)
        w = await self.http.post(
            "/v1/watched-routes",
            headers=self.op,
            json={
                "name": "SIM coast road",
                "geometry": {"type": "LineString", "coordinates": ROUTE},
                "buffer_m": 150,
            },
        )
        self.watch_id = w.json()["id"]
        try:
            await self._story(c)
        finally:
            await self.http.delete(f"/v1/watched-routes/{self.watch_id}", headers=self.op)
        return c

    async def _story(self, c: HazardReportCard) -> None:
        # 1 a lone report is unverified and does not alert a default watch
        assert (await self.report("Alpha", "1", "flood", "blocked", 600)).status_code == 202

        async def lone() -> bool:
            return (await self.hazard("flood", 600)) is not None

        c.add("1 single report becomes a hazard", await self.until(lone))
        h = await self.hazard("flood", 600)
        c.add(
            "1 it is unverified", h is not None and h["tier"] == "unverified", str(h and h["tier"])
        )
        await self.quiet()
        c.add("1 a default watch is not alerted by it", await self.alerts() == [])

        # 2 the same key again changes nothing
        await self.report("Alpha", "1", "flood", "blocked", 610, minutes_ago=3)
        await self.quiet()
        h = await self.hazard("flood", 600)
        c.add(
            "2 a duplicate from the same key stays unverified",
            h is not None and h["tier"] == "unverified",
        )

        # 3 an independent team corroborates, and the watch alerts once with tier and age
        await self.report("Bravo", "1", "flood", "blocked", 650, minutes_ago=2)

        async def flood_alert() -> bool:
            return any(a["kind"] == "route_hazard" for a in await self.alerts())

        c.add(
            "3 two independent teams corroborate and the watch alerts",
            await self.until(flood_alert),
        )
        h = await self.hazard("flood", 600)
        c.add("3 tier is corroborated", h is not None and h["tier"] == "corroborated")
        msgs = [a["message"] for a in await self.alerts() if a["kind"] == "route_hazard"]
        c.add(
            "3 the alert gives tier and age, not a bare fact",
            bool(msgs) and "corroborated" in msgs[0] and "old" in msgs[0],
            msgs[0] if msgs else "",
        )

        # 4 a coordinated fake: two keys of one team
        await self.report("Charlie", "1", "landslide", "blocked", 1500)
        await self.report("Charlie", "2", "landslide", "blocked", 1510)
        await self.until(lambda: self._exists("landslide", 1500))
        await self.quiet()
        h = await self.hazard("landslide", 1500)
        c.add(
            "4 two keys of one team do not corroborate", h is not None and h["tier"] == "unverified"
        )

        # 5 a lone doubtful report is dismissed by an operator; forbidden fields are refused
        await self.report("Delta", "1", "debris", "blocked", 3000)
        await self.until(lambda: self._exists("debris", 3000))
        d = await self.hazard("debris", 3000)
        if d is not None:
            r = await self.http.post(
                f"/v1/hazards/{d['id']}/dismiss", headers=self.op, json={"reason": "false_report"}
            )
            c.add(
                "5 an operator can dismiss a false report",
                r.status_code == 200 and r.json()["status"] == "dismissed",
            )
        else:
            c.add("5 the doubtful report became a hazard", False)
        refused = 0
        for extra in (
            {"note": "text"},
            {"photo": "x"},
            {"device_location": [0, 0]},
            {"name": "someone"},
        ):
            r = await self.http.post(
                "/v1/sim/reports",
                headers=self.op,
                json={
                    "team": "Delta",
                    "kind": "debris",
                    "passability": "blocked",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "geometry": {"type": "Point", "coordinates": [LON0, LAT0]},
                    **extra,
                },
            )
            refused += r.status_code == 422
        c.add(
            "5 free text, photos, device location and names are refused",
            refused == 4,
            f"{refused}/4",
        )

        # 6 only an operator confirms
        f = await self.hazard("flood", 600)
        if f is not None:
            r = await self.http.post(
                f"/v1/hazards/{f['id']}/confirm",
                headers=self.op,
                json={"reason": "verified_on_site"},
            )
            c.add(
                "6 an operator confirms",
                r.status_code == 200
                and r.json()["tier"] == "confirmed"
                and r.json()["sim"] is True,
            )

        # 7 two independent clear reports close an unconfirmed hazard, and the watch says so
        await self.report("Alpha", "1", "debris", "blocked", 2200, minutes_ago=40)
        await self.report("Bravo", "1", "debris", "blocked", 2210, minutes_ago=35)

        async def debris_alert() -> bool:
            return any(
                a["kind"] == "route_hazard" and "debris" in a["message"]
                for a in await self.alerts()
            )

        c.add("7 debris is corroborated and alerts", await self.until(debris_alert))
        await self.report("Alpha", "1", "debris", "reported_clear", 2200, minutes_ago=6)
        await self.report("Bravo", "1", "debris", "reported_clear", 2210, minutes_ago=5)

        async def cleared() -> bool:
            return any(a["kind"] == "route_hazard_cleared" for a in await self.alerts())

        c.add(
            "7 two independent clear reports close it and the watch is told",
            await self.until(cleared),
        )
        h = await self.hazard("debris", 2200)
        c.add(
            "7 the hazard is closed",
            h is not None and h["status"] == "closed",
            str(h and h["status"]),
        )

        # 8 expiry is not clear
        await self.report("Alpha", "1", "debris", "blocked", 2600, minutes_ago=13 * 60)
        await self.report("Bravo", "1", "debris", "blocked", 2610, minutes_ago=13 * 60)
        await self.until(lambda: self._exists("debris", 2600))
        await self.quiet()
        h = await self.hazard("debris", 2600)
        c.add(
            "8 an old report expires and is not marked clear",
            h is not None and h["status"] == "expired",
            str(h and h["status"]),
        )
        kinds = [a["kind"] for a in await self.alerts()]
        c.add(
            "8 expiry raised no alert",
            kinds.count("route_hazard") == 2 and kinds.count("route_hazard_cleared") == 1,
            str(kinds),
        )

        # 9 everything here is labelled simulated
        allh = (
            await self.http.get(
                "/v1/hazards", headers=self.op, params={"status": "all", "limit": 500}
            )
        ).json()["items"]
        c.add(
            "9 every hazard and alert is labelled simulated",
            bool(allh)
            and all(x["sim"] for x in allh)
            and all(a["sim"] for a in await self.alerts()),
        )

    async def _exists(self, kind: str, east: float) -> bool:
        return (await self.hazard(kind, east)) is not None
