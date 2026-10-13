"""GET /v1/alerts (list) and GET /v1/stream/alerts (server-sent events), responder and above."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse

from ..auth import Principal, require_role
from ..models import Alert, Job
from .geo import age_s

router = APIRouter(prefix="/v1")
responder = require_role(minimum="responder")
POLL_S = 1.0


def alert_json(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "level": a.level,
        "kind": a.kind,
        "area": a.area,
        "message": a.message,
        "hotspot_id": str(a.hotspot_id) if a.hotspot_id else None,
        "route_id": str(a.route_id) if a.route_id else None,
        "created_at": a.created_at.isoformat(),
        "sim": a.sim,
        "sources": ["witness_nodes"],
        "data_age_s": age_s(a.created_at),
    }


@router.get("/alerts")
async def list_alerts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    after: int | None = None,
    route_id: uuid.UUID | None = None,
    _: Principal = Depends(responder),
) -> dict[str, Any]:
    q = select(Alert).order_by(Alert.id.desc()).limit(limit)
    if after is not None:
        q = select(Alert).where(Alert.id > after).order_by(Alert.id).limit(limit)
    if route_id is not None:
        q = q.where(Alert.route_id == route_id)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    return {
        "items": [alert_json(a) for a in rows],
        "count": len(rows),
        "sources": ["witness_nodes"],
    }


@dataclass
class StreamState:
    last_id: int
    last_refresh: datetime | None
    primed: bool = False  # the first poll only records the baseline
    route_id: uuid.UUID | None = None  # only this route's alerts


async def poll(session: AsyncSession, st: StreamState) -> list[dict[str, Any]]:
    """One poll: new alerts after `last_id`, plus a `refresh` when the analytics finished a run."""
    out: list[dict[str, Any]] = []
    q = select(Alert).where(Alert.id > st.last_id).order_by(Alert.id)
    if st.route_id is not None:
        q = q.where(Alert.route_id == st.route_id)
    for a in (await session.execute(q)).scalars():
        out.append({"id": str(a.id), "event": "alert", "data": json.dumps(alert_json(a))})
        st.last_id = a.id
    done = (
        await session.execute(
            select(func.max(Job.finished_at)).where(Job.kind == "recompute", Job.state == "done")
        )
    ).scalar_one()
    if st.primed and done is not None and done != st.last_refresh:
        out.append(
            {"event": "refresh", "data": json.dumps({"what": ["hotspots", "trust", "events"]})}
        )
    st.last_refresh, st.primed = done, True
    return out


async def event_stream(
    request: Request,
    maker: async_sessionmaker[AsyncSession],
    last_id: int | None,
    route_id: uuid.UUID | None = None,
) -> AsyncIterator[dict[str, Any]]:
    async with maker() as session:
        latest = (await session.execute(select(func.coalesce(func.max(Alert.id), 0)))).scalar_one()
    st = StreamState(
        last_id=latest if last_id is None else last_id, last_refresh=None, route_id=route_id
    )
    yield {
        "event": "hello",
        "data": json.dumps({"server_time": datetime.now(UTC).isoformat(), "last_id": st.last_id}),
    }
    while not await request.is_disconnected():
        async with maker() as session:
            for ev in await poll(session, st):
                yield ev
        await asyncio.sleep(POLL_S)


@router.get("/stream/alerts")
async def stream_alerts(
    request: Request,
    last_event_id: str | None = Header(default=None),
    after: int | None = Query(default=None),
    route_id: uuid.UUID | None = Query(default=None),
    _: Principal = Depends(responder),
) -> EventSourceResponse:
    resume = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    return EventSourceResponse(
        event_stream(request, request.app.state.sessionmaker, resume, route_id)
    )
