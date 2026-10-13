"""Community hazard reports (docs/hazards.md sections 3, 5, 6, 8): the submit side.

A report is a place, a kind, a passability and a time. Nothing else is accepted: no free text, photo,
device location, contact or identity. The platform stores only the issuing key's public prefix, and no
response here ever contains a reporter prefix. Merging reports into hazards (and tiers) is a later step,
so for now a report is `unverified` and `hazard_id` is null.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pyproj import Geod
from sqlalchemy import func, select

from ..auth import Principal, require_role
from ..jobs import enqueue
from ..models import HazardReport, RoadHazard
from ..problems import ApiError

router = APIRouter(prefix="/v1/reports")
reporter = require_role(minimum="reporter")
GEOD = Geod(ellps="WGS84")
MAX_LINE_M = 500.0
MAX_VERTICES = 50
FUTURE_SKEW = timedelta(minutes=10)

# `closed_by_authority` exists as a hazard kind but only official sources may assert it.
ReportableKind = Literal[
    "flood", "landslide", "debris", "road_collapse", "bridge_damage", "fire_near_road", "snow_ice"
]
Passability = Literal["blocked", "restricted", "passable_with_care", "reported_clear"]


def _position(p: list[float]) -> list[float]:
    if len(p) != 2 or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
        raise ValueError("each position must be exactly [lon, lat] within -180..180 and -90..90")
    return [round(p[0], 5), round(p[1], 5)]  # about 1 m: no false precision


class PointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["Point"]
    coordinates: list[float]

    _p = field_validator("coordinates")(_position)


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["LineString"]
    coordinates: list[list[float]] = Field(min_length=2, max_length=MAX_VERTICES)

    @field_validator("coordinates")
    @classmethod
    def _line(cls, v: list[list[float]]) -> list[list[float]]:
        pts = [_position(p) for p in v]
        length = GEOD.line_length([p[0] for p in pts], [p[1] for p in pts])
        if length > MAX_LINE_M:
            raise ValueError(f"a reported stretch is at most {MAX_LINE_M:.0f} m long")
        return pts


class ReportIn(BaseModel):
    """Exactly these four fields. Any other field is refused (422), by design."""

    model_config = ConfigDict(extra="forbid")
    kind: ReportableKind
    passability: Passability
    geometry: Annotated[PointIn | LineIn, Field(discriminator="type")]
    observed_at: datetime

    @field_validator("kind", mode="before")
    @classmethod
    def _official_only(cls, v: Any) -> Any:
        if v == "closed_by_authority":
            raise ValueError("closed_by_authority comes from official sources only")
        return v

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("needs a time zone, e.g. 2026-11-28T10:00:00Z")
        return v


def _wkt(g: PointIn | LineIn) -> str:
    if isinstance(g, PointIn):
        return f"POINT({g.coordinates[0]} {g.coordinates[1]})"
    return "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in g.coordinates) + ")"


def _retry_after(oldest: datetime, window: timedelta, now: datetime) -> str:
    return str(max(1, int((oldest + window - now).total_seconds())))


@router.post("", status_code=202)
async def submit(
    body: ReportIn, request: Request, p: Principal = Depends(reporter)
) -> dict[str, Any]:
    cfg = request.app.state.settings
    now = datetime.now(UTC)
    if body.observed_at > now + FUTURE_SKEW:
        raise ApiError(422, "validation-error", "Invalid time", "observed_at is in the future")
    if body.observed_at < now - timedelta(hours=cfg.report_max_age_h):
        raise ApiError(
            422,
            "validation-error",
            "Report too old",
            f"observed_at must be within the last {cfg.report_max_age_h} hours",
        )
    async with request.app.state.sessionmaker() as session:
        for window, limit in (
            (timedelta(hours=1), cfg.report_limit_per_hour),
            (timedelta(days=1), cfg.report_limit_per_day),
        ):
            times = (
                (
                    await session.execute(  # withdrawn reports count: withdrawing is not a way round the limit
                        select(HazardReport.received_at)
                        .where(
                            HazardReport.reporter_prefix == p.prefix,
                            HazardReport.received_at > now - window,
                        )
                        .order_by(HazardReport.received_at)
                    )
                )
                .scalars()
                .all()
            )
            if len(times) >= limit:
                raise ApiError(
                    429,
                    "report-rate-limited",
                    "Too many reports",
                    f"at most {limit} reports per {'hour' if window.days == 0 else 'day'} per key",
                    headers={"Retry-After": _retry_after(times[0], window, now)},
                )
        report = HazardReport(
            reporter_prefix=p.prefix,
            kind=body.kind,
            passability=body.passability,
            geometry=func.ST_GeomFromText(_wkt(body.geometry), 4326),
            observed_at=body.observed_at,
            sim=False,
            status="accepted",
        )
        session.add(report)
        await enqueue(session, "hazards", now, commit=False)  # the worker merges it into a hazard
        await session.commit()
    return {"id": str(report.id), "hazard_id": None, "tier": "unverified", "status": "accepted"}


@router.post("/{report_id}/withdraw")
async def withdraw(
    report_id: uuid.UUID, request: Request, p: Principal = Depends(reporter)
) -> dict[str, Any]:
    """Withdraw one's own report. Any other key gets a 404, so reports of others are not even confirmed to exist."""
    async with request.app.state.sessionmaker() as session:
        r = await session.get(HazardReport, report_id)
        if r is None or r.reporter_prefix != p.prefix:
            raise ApiError(404, "not-found", "Report not found")
        r.status = "withdrawn"
        await enqueue(session, "hazards", datetime.now(UTC), commit=False)
        await session.commit()
    return {"id": str(report_id), "status": "withdrawn"}


@router.get("")
async def my_reports(
    request: Request,
    mine: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    p: Principal = Depends(reporter),
) -> dict[str, Any]:
    """Only one's own reports. There is deliberately no way to list reports by other reporters."""
    if not mine:
        raise ApiError(
            422, "validation-error", "Only your own reports can be listed", "use mine=true"
        )
    q = (
        select(HazardReport, func.ST_AsGeoJSON(HazardReport.geometry), RoadHazard.tier)
        .outerjoin(RoadHazard, RoadHazard.id == HazardReport.hazard_id)
        .where(HazardReport.reporter_prefix == p.prefix)
        .order_by(HazardReport.received_at.desc())
        .limit(limit)
    )
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).all()
    items = [
        {
            "id": str(r.id),
            "kind": r.kind,
            "passability": r.passability,
            "geometry": json.loads(geo),
            "observed_at": r.observed_at,
            "received_at": r.received_at,
            "status": r.status,
            "hazard_id": str(r.hazard_id) if r.hazard_id else None,
            "tier": tier or "unverified",
        }
        for r, geo, tier in rows
    ]
    return {"items": items, "count": len(items)}
