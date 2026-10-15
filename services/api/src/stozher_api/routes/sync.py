"""GET /v1/sync/events and POST /v1/sync/upload (system): a field kit catches up with the cloud and uploads what it holds."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ..auth import Principal, require_role
from ..sync_service import accept_records, event_item, events_since

router = APIRouter(prefix="/v1/sync")
system_only = require_role("system")
MAX_RECORDS = 200


class EventsOut(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: int
    has_more: bool


class UploadRecord(BaseModel):
    node_id: int
    payload_hex: str = Field(min_length=1, max_length=210)
    received_via: str | None = Field(default=None, max_length=16)


class UploadBody(BaseModel):
    records: list[UploadRecord] = Field(max_length=MAX_RECORDS)


class UploadOut(BaseModel):
    results: list[dict[str, Any]]


@router.get("/events", response_model=EventsOut)
async def events(
    request: Request,
    since: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=MAX_RECORDS),
    _: Principal = Depends(system_only),
) -> EventsOut:
    async with request.app.state.sessionmaker() as session:
        rows, more = await events_since(session, since, limit, datetime.now(UTC))
    return EventsOut(
        items=[event_item(e) for e in rows],
        next_cursor=int(rows[-1].sync_seq or since) if rows else since,
        has_more=more,
    )


@router.post("/upload", response_model=UploadOut)
async def upload(
    body: UploadBody, request: Request, _: Principal = Depends(system_only)
) -> UploadOut:
    s = request.app.state.settings
    async with request.app.state.sessionmaker() as session:
        results = await accept_records(
            session,
            [r.model_dump() for r in body.records],
            kek=s.stozher_kek,
            replay_window_s=s.replay_window_s,
        )
    return UploadOut(results=results)
