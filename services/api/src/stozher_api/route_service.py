"""Evaluate a route against the stored trust grid, and serialise the result."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TrustCell
from .problems import ApiError
from .route_trust import CellInfo, RouteTrust, Segment, evaluate_route, needed_cells
from .routes.geo import age_s


async def load_cells(session: AsyncSession, ids: set[str]) -> dict[str, CellInfo]:
    if not ids:
        return {}
    rows = (await session.execute(select(TrustCell).where(TrustCell.h3_index.in_(ids)))).scalars()
    return {
        c.h3_index: CellInfo(
            c.state, c.score, c.confidence, c.independent_sources, c.newest_observed_at, c.sim
        )
        for c in rows
    }


async def evaluate(session: AsyncSession, coords: list[list[float]], buffer_m: float) -> RouteTrust:
    try:
        cells = await load_cells(session, needed_cells(coords, buffer_m))
        return evaluate_route(coords, cells, buffer_m)
    except ValueError as exc:
        raise ApiError(422, "route-too-long", "Route too long", str(exc)) from exc


def _result(s: Segment, now: datetime) -> dict[str, Any]:
    return {
        "state": s.state,
        "score": s.score,
        "confidence": s.confidence,
        "data_age_s": age_s(s.newest_observed_at, now),
        "independent_sources": s.independent_sources,
    }


def route_json(rt: RouteTrust, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    return {
        "length_m": rt.length_m,
        "overall": {**_result(rt.worst, now), "worst_segment": rt.worst.index},
        "segments": [
            {
                "index": s.index,
                "start_m": s.start_m,
                "end_m": s.end_m,
                "geometry": {"type": "LineString", "coordinates": [list(c) for c in s.coords]},
                **_result(s, now),
                "sim": s.sim,
            }
            for s in rt.segments
        ],
        "alternatives": [],  # filled only when a routing engine is configured (optional module)
        "sources": ["witness_nodes"],
        "data_age_s": age_s(rt.worst.newest_observed_at, now),
        "sim": rt.sim,
    }
