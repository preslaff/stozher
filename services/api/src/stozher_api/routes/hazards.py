"""Hazards (docs/hazards.md sections 5, 7, 8): read for responders and above; decisions by operators.

No response here contains a reporter's key prefix or any reporter text. Decisions are the only way a
hazard becomes `confirmed`, `closed` by a person, or `dismissed`; each is audited with a reason code.
The `public` and `reporter` views (confirmed only, blurred) and the along-route check come in the next step.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..auth import Principal, require_role
from ..hazard_logic import TIER_RANK
from ..hazard_service import reconcile
from ..models import HazardHistory, RoadHazard
from ..problems import ApiError
from .geo import BBOX, age_s, parse_bbox

router = APIRouter(prefix="/v1/hazards")
responder = require_role(minimum="responder")
operator = require_role(minimum="operator")

# Reason codes are a closed list, like everything else in this module: no free text.
ConfirmReason = Literal["verified_on_site", "official_source", "multiple_sources"]
CloseReason = Literal["cleared_on_site", "official_source", "no_longer_applicable"]
DismissReason = Literal["false_report", "mistaken_location", "duplicate", "test_data"]


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmIn(_Decision):
    reason: ConfirmReason


class CloseIn(_Decision):
    reason: CloseReason


class DismissIn(_Decision):
    reason: DismissReason


def _view(h: RoadHazard, geo: str | None, now: datetime) -> dict[str, Any]:
    return {
        "id": str(h.id),
        "kind": h.kind,
        "passability": h.passability,
        "tier": h.tier,
        "status": h.status,
        "geometry": json.loads(geo) if geo else None,
        "sources": h.sources,
        "evidence": h.evidence,
        "first_seen": h.first_seen,
        "last_confirmed": h.last_confirmed,
        "valid_until": h.valid_until,
        "ended_at": h.ended_at,
        "decision": h.decision,
        "decision_reason": h.decision_reason,
        "decided_at": h.decided_at,
        "sim": h.sim,
        "data_age_s": age_s(h.last_confirmed, now),
    }


@router.get("")
async def list_hazards(
    request: Request,
    bbox: str | None = BBOX,
    kind: str | None = Query(default=None, max_length=24),
    min_tier: Literal["unverified", "corroborated", "confirmed"] = "unverified",
    status: Literal["active", "closed", "expired", "dismissed", "all"] = "active",
    limit: int = Query(default=200, ge=1, le=1000),
    _: Principal = Depends(responder),
) -> dict[str, Any]:
    q = (
        select(RoadHazard, func.ST_AsGeoJSON(RoadHazard.geometry))
        .order_by(RoadHazard.first_seen.desc())
        .limit(limit)
    )
    if status != "all":
        q = q.where(RoadHazard.status == status)
    if kind:
        q = q.where(RoadHazard.kind == kind)
    wanted = [t for t, r in TIER_RANK.items() if r >= TIER_RANK[min_tier]]
    q = q.where(RoadHazard.tier.in_(wanted))
    if (box := parse_bbox(bbox)) is not None:
        q = q.where(RoadHazard.geometry.intersects(func.ST_MakeEnvelope(*box, 4326)))
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).all()
    items = [_view(h, geo, now) for h, geo in rows]
    newest = max((h.last_confirmed for h, _ in rows if h.last_confirmed), default=None)
    return {
        "items": items,
        "count": len(items),
        "sources": ["community", "operator"],
        "data_age_s": age_s(newest, now),
    }


@router.get("/{hazard_id}")
async def get_hazard(
    hazard_id: uuid.UUID, request: Request, _: Principal = Depends(responder)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(RoadHazard, func.ST_AsGeoJSON(RoadHazard.geometry)).where(
                    RoadHazard.id == hazard_id
                )
            )
        ).first()
        if row is None:
            raise ApiError(404, "not-found", "Hazard not found")
        history = (
            (
                await session.execute(
                    select(HazardHistory)
                    .where(HazardHistory.hazard_id == hazard_id)
                    .order_by(HazardHistory.id)
                )
            )
            .scalars()
            .all()
        )
    # the timeline names the kind of source (community, operator, system), never a key prefix
    timeline = [{"at": e.at, "event": e.event, "value": e.value, "by": e.source} for e in history]
    return {**_view(row[0], row[1], now), "timeline": timeline}


async def _decide(
    session: AsyncSession,
    request: Request,
    p: Principal,
    hazard_id: uuid.UUID,
    decision: str,
    reason: str,
    allowed: tuple[str, ...],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    h = await session.get(RoadHazard, hazard_id)
    if h is None:
        raise ApiError(404, "not-found", "Hazard not found")
    if h.status not in allowed:
        raise ApiError(
            409,
            "not-open",
            f"A {h.status} hazard cannot be {decision}",
            f"allowed from: {', '.join(allowed)}",
        )
    h.decision, h.decided_by, h.decision_reason, h.decided_at = decision, p.prefix, reason, now
    if (
        h.status == "expired"
    ):  # a human decision on an expired hazard is applied by the same reconcile run
        h.status, h.ended_at = "active", None
    session.add(
        HazardHistory(hazard_id=h.id, at=now, event="decision", value=decision, source="operator")
    )
    session.add(audit(p, f"hazard.{decision}", str(h.id)))
    await session.flush()
    await reconcile(session, now)  # applies the decision and commits
    refreshed = (
        await session.execute(
            select(RoadHazard, func.ST_AsGeoJSON(RoadHazard.geometry)).where(
                RoadHazard.id == hazard_id
            )
        )
    ).one()
    return _view(refreshed[0], refreshed[1], now)


@router.post("/{hazard_id}/confirm")
async def confirm(
    hazard_id: uuid.UUID, body: ConfirmIn, request: Request, p: Principal = Depends(operator)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        return await _decide(
            session, request, p, hazard_id, "confirmed", body.reason, ("active", "expired")
        )


@router.post("/{hazard_id}/close")
async def close(
    hazard_id: uuid.UUID, body: CloseIn, request: Request, p: Principal = Depends(operator)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        return await _decide(
            session, request, p, hazard_id, "closed", body.reason, ("active", "expired")
        )


@router.post("/{hazard_id}/dismiss")
async def dismiss(
    hazard_id: uuid.UUID, body: DismissIn, request: Request, p: Principal = Depends(operator)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        return await _decide(
            session, request, p, hazard_id, "dismissed", body.reason, ("active", "expired")
        )
