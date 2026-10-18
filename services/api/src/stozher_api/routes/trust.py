"""GET /v1/trust, /v1/trust/grid (public). Coarse data only: no node identities."""

from datetime import UTC, datetime
from typing import Any, Literal

import h3
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from ..auth import Principal, require_role
from ..history_service import WINDOWS, HistoryPoint, history_cells
from ..models import Event, TrustCell
from ..problems import ApiError
from ..route_service import evaluate, route_json
from .geo import age_s, parse_bbox

router = APIRouter(prefix="/v1/trust")
public = require_role(minimum="public")
responder = require_role(minimum="responder")
MAX_CELLS = 5000
MAX_HISTORY_EVENTS = 20000
BBOX_MARGIN_DEG = (
    0.06  # a report spreads over 4 rings of cells (about 3 km), so look a little outside the box
)
ANALYSED_TYPES = (0, 1, 2)  # heartbeat, event, summary
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
    async with request.app.state.sessionmaker() as session:
        no_fix = (func.ST_X(Event.position) == 0) & (func.ST_Y(Event.position) == 0)
        latest, latest_unplaced = (
            await session.execute(
                select(
                    func.max(Event.observed_at).filter(~no_fix),
                    func.max(Event.observed_at).filter(no_fix),
                ).where(Event.msg_type.in_(ANALYSED_TYPES))
            )
        ).one()
    now = datetime.now(UTC)
    return {
        "type": "FeatureCollection",
        "features": features,
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest, now),
        # when the grid is empty: how old the newest evidence anywhere is (age only, no place), so a quiet network is not read as a broken one
        "newest_evidence_age_s": age_s(latest, now),
        # readings that arrived without a GNSS position: stored, never placed on the map
        "newest_unplaced_age_s": age_s(latest_unplaced, now),
        "sim": any(c.sim for c in cells),
    }


@router.get("/history")
async def trust_history(
    request: Request,
    bbox: str = Query(description="minLon,minLat,maxLon,maxLat"),
    window: Literal["6h", "24h", "7d"] = Query(default="24h"),
    _: Principal = Depends(responder),
) -> dict[str, Any]:
    """Last-known evidence per place in the window. Expired by definition: never a current trust state."""
    box = parse_bbox(bbox)
    assert box is not None
    now = datetime.now(UTC)
    margin = BBOX_MARGIN_DEG
    q = (
        select(
            Event.node_id,
            func.ST_Y(Event.position),
            func.ST_X(Event.position),
            Event.observed_at,
            Event.event_class,
            Event.confidence,
            Event.sim,
        )
        .where(
            Event.msg_type.in_(ANALYSED_TYPES),
            Event.observed_at >= now - WINDOWS[window],
            Event.position.intersects(
                func.ST_MakeEnvelope(
                    box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin, 4326
                )
            ),
        )
        .limit(MAX_HISTORY_EVENTS + 1)
    )
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).all()
    if len(rows) > MAX_HISTORY_EVENTS:
        raise ApiError(
            422, "bbox-too-large", "Area too large", "too many events; use a smaller bbox or window"
        )
    points = [
        HistoryPoint(r[0], r[1], r[2], r[3], r[4], r[5], r[6])
        for r in rows
        if not (r[1] == 0.0 and r[2] == 0.0)  # no fix: stored, never placed
    ]
    cells = [
        c
        for c in history_cells(points)
        if box[0] <= h3.cell_to_latlng(c.h3_index)[1] <= box[2]
        and box[1] <= h3.cell_to_latlng(c.h3_index)[0] <= box[3]
    ]
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
                "properties": {
                    "h3": c.h3_index,
                    "expired": True,
                    "last_state": c.state.value,
                    "last_score": c.score,
                    "newest_observed_at": c.newest_observed_at.isoformat(),
                    "data_age_s": age_s(c.newest_observed_at, now),
                    "event_count": c.event_count,
                    "independent_sources": c.independent_sources,
                    "sim": c.sim,
                },
            }
        )
    newest = max((c.newest_observed_at for c in cells), default=None)
    return {
        "type": "FeatureCollection",
        "features": features,
        "expired": True,
        "window": window,
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest, now),
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
