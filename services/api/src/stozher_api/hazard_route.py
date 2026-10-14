"""Hazards near a route: one query for the API (`along-route`) and the worker (watched-route alerts)."""

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .hazard_logic import TIER_RANK
from .models import RoadHazard


async def hazards_along(
    session: AsyncSession,
    coords: list[list[float]],
    buffer_m: float,
    min_tier: str,
    limit: int = 200,
) -> list[tuple[Any, ...]]:
    """Rows of (hazard, geojson, at_m, distance_m, route_length_m) for active hazards in the corridor, in route order."""
    line = func.ST_SetSRID(
        func.ST_GeomFromGeoJSON(json.dumps({"type": "LineString", "coordinates": coords})), 4326
    )
    geog = func.Geography(RoadHazard.geometry)
    length = func.ST_Length(func.Geography(line))
    frac = func.ST_LineLocatePoint(line, func.ST_ClosestPoint(RoadHazard.geometry, line))
    wanted = [t for t, r in TIER_RANK.items() if r >= TIER_RANK[min_tier]]
    q = (
        select(
            RoadHazard,
            func.ST_AsGeoJSON(RoadHazard.geometry),
            (frac * length).label("at_m"),
            func.ST_Distance(geog, func.Geography(line)).label("dist_m"),
            length.label("len_m"),
        )
        .where(
            RoadHazard.status == "active",
            RoadHazard.tier.in_(wanted),
            func.ST_DWithin(geog, func.Geography(line), buffer_m),
        )
        .order_by("at_m")
        .limit(limit)
    )
    return [tuple(r) for r in (await session.execute(q)).all()]
