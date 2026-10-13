"""GET /v1/hotspots (responder)."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from ..auth import Principal, require_role
from ..models import Event, Hotspot, HotspotEvent
from ..problems import ApiError
from .geo import BBOX, age_s, geojson, parse_bbox

router = APIRouter(prefix="/v1/hotspots")
responder = require_role(minimum="responder")


def _item(h: Hotspot, centroid: str | None, ellipse: str | None) -> dict[str, Any]:
    return {
        "id": str(h.id),
        "kind": h.kind,
        "state": h.state,
        "centroid": geojson(centroid),
        "source_ellipse": geojson(ellipse),
        "ellipse": h.ellipse,
        "severity": h.severity,
        "confidence": h.confidence,
        "first_seen": h.first_seen,
        "last_seen": h.last_seen,
        "event_count": h.event_count,
        "independent_sources": h.node_count,
        "sim": h.sim,
        "track": h.track,
        "sources": ["witness_nodes"],
        "data_age_s": age_s(h.last_seen),
    }


@router.get("")
async def list_hotspots(
    request: Request,
    state: Literal["active", "closed"] | None = None,
    bbox: str | None = BBOX,
    limit: int = Query(default=100, ge=1, le=500),
    _: Principal = Depends(responder),
) -> dict[str, Any]:
    q = select(
        Hotspot, func.ST_AsGeoJSON(Hotspot.centroid), func.ST_AsGeoJSON(Hotspot.source_ellipse)
    )
    if state:
        q = q.where(Hotspot.state == state)
    if (box := parse_bbox(bbox)) is not None:
        q = q.where(Hotspot.source_ellipse.intersects(func.ST_MakeEnvelope(*box, 4326)))
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q.order_by(Hotspot.last_seen.desc()).limit(limit))).all()
    items = [_item(*r) for r in rows]
    newest = max((r[0].last_seen for r in rows), default=None)
    return {
        "items": items,
        "count": len(items),
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest, datetime.now(UTC)),
    }


@router.get("/{hotspot_id}")
async def get_hotspot(
    hotspot_id: uuid.UUID, request: Request, _: Principal = Depends(responder)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(
                    Hotspot,
                    func.ST_AsGeoJSON(Hotspot.centroid),
                    func.ST_AsGeoJSON(Hotspot.source_ellipse),
                ).where(Hotspot.id == hotspot_id)
            )
        ).first()
        if row is None:
            raise ApiError(404, "not-found", "Hotspot not found")
        timeline = (
            await session.execute(
                select(Event.id, Event.node_id, Event.observed_at, Event.confidence)
                .join(HotspotEvent, HotspotEvent.event_id == Event.id)
                .where(HotspotEvent.hotspot_id == hotspot_id)
                .order_by(Event.observed_at)
            )
        ).all()
    return {
        **_item(*row),
        "timeline": [
            {"event_id": str(i), "node_id": n, "observed_at": t, "confidence": c}
            for i, n, t, c in timeline
        ],
    }
