"""GET /v1/events (responder)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from ..auth import Principal, require_role
from ..models import Event
from ..problems import ApiError
from .geo import BBOX, age_s, geojson, parse_bbox

router = APIRouter(prefix="/v1/events")
responder = require_role(minimum="responder")
CLASSES = {
    0: "normal",
    1: "jamming",
    2: "spoofing",
    3: "benign",
    4: "tamper",
    5: "time_anomaly",
    7: "unknown",
}
TYPES = {0: "heartbeat", 1: "event", 2: "summary", 3: "test"}


def _item(e: Event, pos: str | None) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "node_id": e.node_id,
        "seq": e.seq,
        "key_id": e.key_id,
        "type": TYPES.get(e.msg_type),
        "class": CLASSES.get(e.event_class),
        "confidence": e.confidence,
        "flags": e.flags,
        "position": geojson(pos),
        "observed_at": e.observed_at,
        "ingested_at": e.ingested_at,
        "duration_s": e.duration_s,
        "features": e.features,
        "received_via": e.received_via,
        "sim": e.sim,
    }


@router.get("")
async def list_events(
    request: Request,
    bbox: str | None = BBOX,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    class_: str | None = Query(default=None, alias="class"),
    min_confidence: int = Query(default=0, ge=0, le=31),
    limit: int = Query(default=100, ge=1, le=1000),
    _: Principal = Depends(responder),
) -> dict[str, Any]:
    q = select(Event, func.ST_AsGeoJSON(Event.position)).where(Event.confidence >= min_confidence)
    if (box := parse_bbox(bbox)) is not None:
        q = q.where(Event.position.intersects(func.ST_MakeEnvelope(*box, 4326)))
    if from_:
        q = q.where(Event.observed_at >= from_)
    if to:
        q = q.where(Event.observed_at <= to)
    if class_ is not None:
        names = {v: k for k, v in CLASSES.items()}
        if class_ not in names:
            raise ApiError(422, "validation-error", "Unknown class", f"one of {sorted(names)}")
        q = q.where(Event.event_class == names[class_])
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q.order_by(Event.observed_at.desc()).limit(limit))).all()
    items = [_item(e, pos) for e, pos in rows]
    newest = max((e.observed_at for e, _ in rows), default=None)
    return {
        "items": items,
        "count": len(items),
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest, datetime.now(UTC)),
    }


@router.get("/{event_id}")
async def get_event(
    event_id: uuid.UUID, request: Request, _: Principal = Depends(responder)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(Event, func.ST_AsGeoJSON(Event.position)).where(Event.id == event_id)
            )
        ).first()
    if row is None:
        raise ApiError(404, "not-found", "Event not found")
    return {**_item(*row), "sources": ["witness_nodes"], "data_age_s": age_s(row[0].observed_at)}
