"""Watched routes (docs/api.md section 7): watch a route, get alerts when its trust worsens.

STOZHER never receives a live position. A watch holds only the geometry the caller submitted.
Evaluation and alerting are deterministic and happen in the worker; this module is the CRUD side
plus the current status.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import RANK, Principal, require_role
from ..models import AuditLog, WatchedRoute
from ..problems import ApiError
from ..route_service import evaluate, route_json
from .trust import LineStringIn

router = APIRouter(prefix="/v1/watched-routes")
responder = require_role(minimum="responder")
MAX_ACTIVE_PER_KEY = 20
DEFAULT_TTL = timedelta(hours=24)
MAX_TTL = timedelta(days=7)


class Notify(BaseModel):
    """Only the alert stream exists today. Webhook ids and mesh delivery are refused until built."""

    model_config = ConfigDict(extra="forbid")
    sse: Literal[True] = True


class WatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    geometry: LineStringIn
    buffer_m: int = Field(default=200, ge=50, le=2000)
    min_level: Literal["degraded", "jammed", "spoofed"] = "degraded"
    notify: Notify = Notify()
    expires_at: datetime | None = None
    asset_type: str | None = Field(default=None, max_length=32)

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("expires_at needs a time zone, e.g. 2026-11-29T18:00:00Z")
        return v


class WatchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")  # the geometry cannot change: create a new watch
    name: str | None = Field(default=None, min_length=1, max_length=120)
    buffer_m: int | None = Field(default=None, ge=50, le=2000)
    min_level: Literal["degraded", "jammed", "spoofed"] | None = None
    notify: Notify | None = None
    expires_at: datetime | None = None


def _expiry(requested: datetime | None, now: datetime) -> datetime:
    when = requested or now + DEFAULT_TTL
    if when <= now:
        raise ApiError(
            422, "validation-error", "Invalid expiry", "expires_at must be in the future"
        )
    if when > now + MAX_TTL:
        raise ApiError(
            422, "validation-error", "Invalid expiry", "expires_at is at most 7 days ahead"
        )
    return when


async def _status(session: AsyncSession, w: WatchedRoute) -> dict[str, Any]:
    now = datetime.now(UTC)
    rt = await evaluate(session, w.coordinates, float(w.buffer_m))
    return {
        "id": str(w.id),
        "name": w.name,
        "buffer_m": w.buffer_m,
        "min_level": w.min_level,
        "notify": w.notify,
        "asset_type": w.asset_type,
        "created_at": w.created_at,
        "expires_at": w.expires_at,
        "evaluated_at": now,
        **route_json(rt, now),
    }


async def _owned(session: AsyncSession, watch_id: uuid.UUID, p: Principal) -> WatchedRoute:
    """Own watches only; operators and admins may see any. Others get 404, not 403."""
    w = await session.get(WatchedRoute, watch_id)
    if (
        w is None
        or w.expires_at <= datetime.now(UTC)
        or (w.owner_prefix != p.prefix and RANK.get(p.role, 0) < RANK["operator"])
    ):
        raise ApiError(404, "not-found", "Watched route not found")
    return w


@router.post("", status_code=201)
async def create(
    body: WatchIn, request: Request, p: Principal = Depends(responder)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    expires = _expiry(body.expires_at, now)
    async with request.app.state.sessionmaker() as session:
        active = (
            await session.execute(
                select(func.count())
                .select_from(WatchedRoute)
                .where(WatchedRoute.owner_prefix == p.prefix, WatchedRoute.expires_at > now)
            )
        ).scalar_one()
        if active >= MAX_ACTIVE_PER_KEY:
            raise ApiError(
                409,
                "limit-reached",
                "Too many watched routes",
                f"at most {MAX_ACTIVE_PER_KEY} active watches per API key; delete one first",
            )
        w = WatchedRoute(
            owner_prefix=p.prefix,
            name=body.name,
            coordinates=body.geometry.coordinates,
            buffer_m=body.buffer_m,
            min_level=body.min_level,
            notify=body.notify.model_dump(),
            asset_type=body.asset_type,
            expires_at=expires,
            transitions=0,
        )
        session.add(w)
        await session.flush()
        status = await _status(session, w)
        w.last_state = status["overall"]["state"]  # baseline: only changes from here raise alerts
        w.last_evaluated_at = now
        session.add(AuditLog(actor=p.prefix, action="route.watch", target=str(w.id)))
        await session.commit()
        status["created_at"] = w.created_at
    return status


@router.get("")
async def list_watches(
    request: Request,
    owner: str | None = Query(default=None, max_length=16),
    p: Principal = Depends(responder),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    q = select(WatchedRoute).where(WatchedRoute.expires_at > now).order_by(WatchedRoute.created_at)
    if RANK.get(p.role, 0) < RANK["operator"]:
        q = q.where(WatchedRoute.owner_prefix == p.prefix)
    elif owner:
        q = q.where(WatchedRoute.owner_prefix == owner)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    items = [
        {
            "id": str(w.id),
            "name": w.name,
            "buffer_m": w.buffer_m,
            "min_level": w.min_level,
            "state": w.last_state,
            "created_at": w.created_at,
            "expires_at": w.expires_at,
            "asset_type": w.asset_type,
            "geometry": {"type": "LineString", "coordinates": w.coordinates},
        }
        for w in rows
    ]
    return {"items": items, "count": len(items)}


@router.get("/{watch_id}")
async def status(
    watch_id: uuid.UUID, request: Request, p: Principal = Depends(responder)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        return await _status(session, await _owned(session, watch_id, p))


@router.patch("/{watch_id}")
async def update(
    watch_id: uuid.UUID, body: WatchPatch, request: Request, p: Principal = Depends(responder)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as session:
        w = await _owned(session, watch_id, p)
        if body.name is not None:
            w.name = body.name
        if body.buffer_m is not None:
            w.buffer_m = body.buffer_m
        if body.min_level is not None:
            w.min_level = body.min_level
        if body.expires_at is not None:
            w.expires_at = _expiry(body.expires_at, now)
        await session.commit()
        return await _status(session, w)


@router.delete("/{watch_id}", status_code=204)
async def delete(watch_id: uuid.UUID, request: Request, p: Principal = Depends(responder)) -> None:
    async with request.app.state.sessionmaker() as session:
        w = await _owned(session, watch_id, p)
        session.add(AuditLog(actor=p.prefix, action="route.unwatch", target=str(w.id)))
        await session.delete(w)
        await session.commit()
