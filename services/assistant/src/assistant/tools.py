"""The assistant's tools: read-only calls to the public /v1 API, made with the asking user's own key.

Roles and accountability are therefore the API's, not ours: a tool the key may not call returns an error text, nothing more.
Results are compacted before the model sees them (no node IDs, no raw records, no geometry), and each carries the facts
the answer must be labelled with (data age, SIM, sources), which code reads, never the model.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .providers import ToolSchema

LIVE_WINDOW_S = 90 * 60  # the live trust grid only holds this much evidence (docs/trust.md)

BBOX_PARAM = {
    "type": "string",
    "description": "minLon,minLat,maxLon,maxLat; optional, defaults to the user's current map view",
}

TOOLS: list[ToolSchema] = [
    {
        "name": "situation",
        "description": "The current picture for an area: active interference hotspots, trust cell counts by state, data age. Start here.",
        "parameters": {"type": "object", "properties": {"bbox": BBOX_PARAM}},
    },
    {
        "name": "trust_point",
        "description": "GNSS trust (state, score, confidence, data age) at one point.",
        "parameters": {
            "type": "object",
            "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "trust_history",
        "description": "EXPIRED last-known evidence for an area over a window (6h, 24h or 7d). Never a current state; use it only to say what was seen and how long ago.",
        "parameters": {
            "type": "object",
            "properties": {
                "bbox": BBOX_PARAM,
                "window": {"type": "string", "enum": ["6h", "24h", "7d"]},
            },
        },
    },
    {
        "name": "hotspots",
        "description": "Interference hotspots (active and recently closed) with kind, severity, confidence, when first and last seen.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
        },
    },
    {
        "name": "hotspot_detail",
        "description": "One hotspot with its timeline.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "recent_events",
        "description": "Summary of witness readings in the last N hours: counts by class, how many had no GNSS position, newest age. No node identities.",
        "parameters": {
            "type": "object",
            "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 168}},
        },
    },
    {
        "name": "alerts",
        "description": "Recent alerts (level, message, age).",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
        },
    },
    {
        "name": "hazards",
        "description": "Road hazards reported by the community, with tier (unverified, corroborated, confirmed) and age. Unverified stays unverified.",
        "parameters": {"type": "object", "properties": {"bbox": BBOX_PARAM}},
    },
    {
        "name": "route_trust",
        "description": "GNSS trust along a route given as [[lon,lat],...] (2 to 50 positions): per segment state and the worst segment.",
        "parameters": {
            "type": "object",
            "properties": {
                "coordinates": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}},
                },
                "buffer_m": {"type": "number", "minimum": 0, "maximum": 2000},
            },
            "required": ["coordinates"],
        },
    },
]


@dataclass
class ToolResult:
    data: Any
    data_age_s: int | None = None
    sim: bool = False
    sources: list[str] = field(default_factory=list)
    expired: bool = False  # history: old by definition
    error: bool = False

    @property
    def no_recent_evidence(self) -> bool:
        """True when this result cannot support a statement about the present."""
        return (
            self.error or self.expired or self.data_age_s is None or self.data_age_s > LIVE_WINDOW_S
        )


def _drop_geometry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in item.items() if k not in ("source_ellipse", "geometry", "area", "polygon")
    }


class Tools:
    def __init__(self, http: httpx.AsyncClient, key: str, default_bbox: str | None = None) -> None:
        self.http, self.key, self.default_bbox = http, key, default_bbox

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, str | None]:
        try:
            r = await self.http.get(path, params=params, headers={"X-API-Key": self.key})
        except httpx.HTTPError as exc:
            return None, f"the STOZHER API is unreachable ({exc.__class__.__name__})"
        if r.status_code in (401, 403):
            return None, "this key's role may not read that"
        if r.status_code >= 400:
            return None, f"the API refused the request (HTTP {r.status_code})"
        return r.json(), None

    def _bbox(self, args: dict[str, Any]) -> str | None:
        return str(args["bbox"]) if args.get("bbox") else self.default_bbox

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolResult({"error": f"unknown tool {name}"}, error=True)
        try:
            return await handler(args)
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as exc:  # a model that sent bad arguments gets told, nothing else happens
            return ToolResult({"error": f"bad arguments: {exc.__class__.__name__}"}, error=True)

    @staticmethod
    def _fail(msg: str) -> ToolResult:
        return ToolResult({"error": msg}, error=True)

    @staticmethod
    def _base(body: dict[str, Any], data: Any, **kw: Any) -> ToolResult:
        return ToolResult(
            data,
            data_age_s=body.get("data_age_s"),
            sim=bool(body.get("sim")),
            sources=list(body.get("sources") or []),
            **kw,
        )

    async def _t_situation(self, args: dict[str, Any]) -> ToolResult:
        bbox = self._bbox(args)
        if not bbox:
            return self._fail("no area: ask the user for one")
        body, err = await self._get("/v1/situation", {"bbox": bbox})
        if err:
            return self._fail(err)
        hs = body.get("hotspots", {})
        data = {
            "hotspots": {
                "active": hs.get("active", 0),
                "by_kind": hs.get("by_kind", {}),
                "top": [_drop_geometry(i) for i in hs.get("items", [])[:5]],
            },
            "trust": body.get("trust"),
            "confidence": body.get("confidence"),
            "data_age_s": body.get("data_age_s"),
            "sim": body.get("sim"),
        }
        return self._base(body, data)

    async def _t_trust_point(self, args: dict[str, Any]) -> ToolResult:
        body, err = await self._get(
            "/v1/trust", {"lat": float(args["lat"]), "lon": float(args["lon"])}
        )
        if err:
            return self._fail(err)
        keep = ("state", "score", "confidence", "data_age_s", "independent_sources", "sim")
        data = {k: body.get(k) for k in keep}
        if body.get("state") == "UNKNOWN":
            data["note"] = "no recent evidence here; this is not an all-clear"
        return self._base(body, data)

    async def _t_trust_history(self, args: dict[str, Any]) -> ToolResult:
        bbox = self._bbox(args)
        if not bbox:
            return self._fail("no area: ask the user for one")
        window = args.get("window", "24h")
        body, err = await self._get("/v1/trust/history", {"bbox": bbox, "window": window})
        if err:
            return self._fail(err)
        counts: dict[str, int] = {}
        for f in body.get("features", []):
            s = f["properties"]["last_state"]
            counts[s] = counts.get(s, 0) + 1
        data = {
            "expired": True,
            "window": window,
            "cells_by_last_state": counts,
            "newest_evidence_age_s": body.get("data_age_s"),
            "note": "expired evidence, not the current state",
        }
        return self._base(body, data, expired=True)

    async def _t_hotspots(self, args: dict[str, Any]) -> ToolResult:
        body, err = await self._get("/v1/hotspots", {"limit": min(int(args.get("limit", 5)), 10)})
        if err:
            return self._fail(err)
        items = [_drop_geometry(i) for i in body.get("items", [])]
        return self._base(body, {"count": body.get("count", len(items)), "items": items})

    async def _t_hotspot_detail(self, args: dict[str, Any]) -> ToolResult:
        body, err = await self._get(f"/v1/hotspots/{args['id']}")
        if err:
            return self._fail(err)
        body = _drop_geometry(body)
        body["timeline"] = body.get("timeline", [])[:20]
        return self._base(body, body)

    async def _t_recent_events(self, args: dict[str, Any]) -> ToolResult:
        hours = min(int(args.get("hours", 24)), 168)
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        body, err = await self._get("/v1/events", {"from": since, "limit": 500})
        if err:
            return self._fail(err)
        by_class: dict[str, int] = {}
        no_fix = 0
        newest_placed: int | None = None
        now = datetime.now(UTC)
        for e in body.get("items", []):
            by_class[str(e["class"])] = by_class.get(str(e["class"]), 0) + 1
            pos = (e.get("position") or {}).get("coordinates") or [None, None]
            if pos == [0.0, 0.0] or pos == [0, 0]:
                no_fix += 1
            else:
                age = int((now - datetime.fromisoformat(e["observed_at"])).total_seconds())
                newest_placed = age if newest_placed is None else min(newest_placed, age)
        data = {
            "hours": hours,
            "count": body.get("count", 0),
            "by_class": by_class,
            "without_gnss_position": no_fix,
            "newest_placed_age_s": newest_placed,
            "note": "class 0 normal, 1 jamming, 2 spoofing, 3 benign, 4 tamper, 5 time anomaly, 7 unknown",
        }
        return self._base(body, data)

    async def _t_alerts(self, args: dict[str, Any]) -> ToolResult:
        body, err = await self._get("/v1/alerts", {"limit": min(int(args.get("limit", 10)), 20)})
        if err:
            return self._fail(err)
        items = [
            {k: a[k] for k in ("level", "kind", "message", "created_at", "sim")}
            for a in body.get("items", [])
        ]
        return self._base(body, {"count": len(items), "items": items})

    async def _t_hazards(self, args: dict[str, Any]) -> ToolResult:
        params = {"bbox": b} if (b := self._bbox(args)) else {}
        body, err = await self._get("/v1/hazards", params)
        if err:
            return self._fail(err)
        keep = ("kind", "tier", "status", "passability", "first_seen", "last_confirmed", "sim")
        items = [{k: h.get(k) for k in keep if k in h} for h in body.get("items", [])[:20]]
        return self._base(
            body,
            {
                "count": body.get("count", len(items)),
                "items": items,
                "note": "unverified stays unverified",
            },
        )

    async def _t_route_trust(self, args: dict[str, Any]) -> ToolResult:
        coords = args["coordinates"]
        if not 2 <= len(coords) <= 50:
            return self._fail("a route needs 2 to 50 positions")
        try:
            r = await self.http.post(
                "/v1/trust/route",
                params={"buffer_m": float(args.get("buffer_m", 0))},
                json={"type": "LineString", "coordinates": coords},
                headers={"X-API-Key": self.key},
            )
        except httpx.HTTPError as exc:
            return self._fail(f"the STOZHER API is unreachable ({exc.__class__.__name__})")
        if r.status_code >= 400:
            return self._fail(f"the API refused the route (HTTP {r.status_code})")
        body = r.json()
        data = {
            "overall": body.get("overall"),
            "length_m": body.get("length_m"),
            "segments": [
                {k: s[k] for k in ("start_m", "end_m", "state", "score", "data_age_s")}
                for s in body.get("segments", [])[:30]
            ],
        }
        return self._base(body, data)
