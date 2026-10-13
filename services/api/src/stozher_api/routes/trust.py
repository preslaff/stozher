"""GET /v1/trust, /v1/trust/grid (public). Coarse data only: no node identities."""

from datetime import UTC, datetime
from typing import Any, Literal

import h3
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ..auth import Principal, require_role
from ..models import TrustCell
from ..problems import ApiError
from ..route_service import evaluate, route_json
from .geo import age_s, parse_bbox

router = APIRouter(prefix="/v1/trust")
public = require_role(minimum="public")
MAX_CELLS = 5000
H3_RES = 8


def _result(
    c: TrustCell | None, lat: float | None = None, lon: float | None = None
) -> dict[str, Any]:
    if c is None:
        return {
            "state": "UNKNOWN",
            "score": None,
            "confidence": 0.0,
            "data_age_s": None,
            "independent_sources": 0,
            "sources": [],
            "sim": False,
        }
    return {
        "state": c.state,
        "score": c.score,
        "confidence": c.confidence,
        "data_age_s": age_s(c.newest_observed_at),
        "independent_sources": c.independent_sources,
        "sources": c.source_kinds,
        "sim": c.sim,
    }


@router.get("")
async def trust_at(
    request: Request,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    _: Principal = Depends(public),
) -> dict[str, Any]:
    idx = h3.latlng_to_cell(lat, lon, H3_RES)
    async with request.app.state.sessionmaker() as session:
        cell = await session.get(TrustCell, idx)
    return {**_result(cell), "location": {"type": "Point", "coordinates": [lon, lat]}, "h3": idx}


@router.get("/grid")
async def trust_grid(
    request: Request,
    bbox: str = Query(description="minLon,minLat,maxLon,maxLat"),
    _: Principal = Depends(public),
) -> dict[str, Any]:
    box = parse_bbox(bbox)
    assert box is not None
    q = (
        select(TrustCell)
        .where(TrustCell.center.intersects(func.ST_MakeEnvelope(*box, 4326)))
        .limit(MAX_CELLS + 1)
    )
    async with request.app.state.sessionmaker() as session:
        cells = (await session.execute(q)).scalars().all()
    if len(cells) > MAX_CELLS:
        raise ApiError(
            422,
            "bbox-too-large",
            "Area too large",
            f"more than {MAX_CELLS} cells; use a smaller bbox",
        )
    features = []
    for c in cells:
        ring = [[lng, la] for la, lng in h3.cell_to_boundary(c.h3_index)]
        ring.append(ring[0])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"h3": c.h3_index, **_result(c)},
            }
        )
    newest = max((c.newest_observed_at for c in cells), default=None)
    return {
        "type": "FeatureCollection",
        "features": features,
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest, datetime.now(UTC)),
        "sim": any(c.sim for c in cells),
    }


class LineStringIn(BaseModel):
    """A GeoJSON LineString: [lon, lat] positions, at most 500."""

    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(min_length=2, max_length=500)

    @field_validator("coordinates")
    @classmethod
    def _positions(cls, v: list[list[float]]) -> list[list[float]]:
        for p in v:
            if len(p) < 2 or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
                raise ValueError("each position must be [lon, lat] within -180..180 and -90..90")
        return [p[:2] for p in v]


@router.post("/route")
async def trust_route(
    body: LineStringIn,
    request: Request,
    buffer_m: float = Query(default=0, ge=0, le=2000, description="corridor half-width in metres"),
    _: Principal = Depends(public),
) -> dict[str, Any]:
    """Trust along a route: segments with their own trust, and the worst segment as the overall result."""
    async with request.app.state.sessionmaker() as session:
        rt = await evaluate(session, body.coordinates, buffer_m)
    return route_json(rt)
