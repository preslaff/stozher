"""Reconcile reports into hazards in the database (the worker job, and after an operator's decision).

Loads what the pure logic in `hazard_logic` needs, runs it, and writes the result. One advisory lock
serialises runs, so the worker and an operator's decision cannot interleave.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .hazard_logic import Derived, Haz, Rep, assign, derive
from .models import ApiKey, HazardHistory, HazardReport, RoadHazard

LOCK_KEY = 7_101_001


@dataclass(frozen=True)
class Summary:
    created: int = 0
    changed: int = 0
    purged: int = 0


def _points(geojson: str) -> tuple[tuple[float, float], ...]:
    g = json.loads(geojson)
    coords = [g["coordinates"]] if g["type"] == "Point" else g["coordinates"]
    return tuple((float(x), float(y)) for x, y in coords)


def _wkt(points: tuple[tuple[float, float], ...]) -> str:
    if len(points) == 1:
        return f"POINT({points[0][0]} {points[0][1]})"
    return "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in points) + ")"


async def _reports(session: AsyncSession, where: Any) -> list[tuple[HazardReport, Rep]]:
    rows = (
        await session.execute(
            select(HazardReport, ApiKey.label, func.ST_AsGeoJSON(HazardReport.geometry))
            .outerjoin(ApiKey, ApiKey.prefix == HazardReport.reporter_prefix)
            .where(HazardReport.status == "accepted", where)
        )
    ).all()
    return [
        (
            r,
            Rep(
                r.id,
                r.sim_team or label or r.reporter_prefix,
                r.kind,
                r.passability,
                _points(geo),
                r.observed_at,
                r.received_at,
                r.sim,
            ),
        )
        for r, label, geo in rows
    ]


def _sources(h: RoadHazard) -> list[str]:
    return ["community", "operator"] if h.decision == "confirmed" else ["community"]


async def reconcile(session: AsyncSession, now: datetime) -> Summary:
    await session.execute(text("select pg_advisory_xact_lock(:k)").bindparams(k=LOCK_KEY))
    rows = (
        (await session.execute(select(RoadHazard).where(RoadHazard.status == "active")))
        .scalars()
        .all()
    )
    by_id = {h.id: h for h in rows}
    members: dict[uuid.UUID, list[Rep]] = {h.id: [] for h in rows}
    if by_id:
        for r, rep in await _reports(session, HazardReport.hazard_id.in_(list(by_id))):
            members[r.hazard_id].append(rep)  # type: ignore[index]
    hazards = [Haz(h.id, h.kind, h.status, h.decision, h.decided_at, members[h.id]) for h in rows]

    fresh = await _reports(session, HazardReport.hazard_id.is_(None))
    by_report = {rep.id: row for row, rep in fresh}
    mapping, created = assign(hazards, [rep for _, rep in fresh])
    for fresh_haz in created:
        first = fresh_haz.members[0]
        session.add(
            RoadHazard(
                id=fresh_haz.id,
                kind=fresh_haz.kind,
                passability=first.passability,
                geometry=func.ST_GeomFromText(_wkt(first.points), 4326),
                tier="unverified",
                sources=["community"],
                first_seen=first.observed_at,
                valid_until=first.observed_at,
                status="active",
                sim=first.sim,
            )
        )
    await session.flush()
    for rid, hid in mapping.items():
        if hid is not None:
            by_report[rid].hazard_id = hid
    await session.flush()
    hazards.extend(created)

    created_ids = {h.id for h in created}
    changed = 0
    touched: list[uuid.UUID] = []
    for h in hazards:
        d: Derived = derive(h, now)
        row = by_id.get(h.id) or await session.get(RoadHazard, h.id)
        assert row is not None
        old = (row.tier, row.status, row.passability)
        asserts = [m for m in h.members if not m.is_clear]
        row.tier, row.status, row.passability = d.tier, d.status, d.passability
        row.first_seen, row.last_confirmed, row.valid_until = (
            d.first_seen,
            d.last_support,
            d.valid_until,
        )
        if d.status != "active" and row.ended_at is None:
            row.ended_at = d.ended_at or now
        row.sources = _sources(row)
        if (
            h.members
        ):  # simulated only if every report is: live reports never hide behind a SIM badge
            row.sim = all(m.sim for m in h.members)
        row.evidence = {
            "reports": len(asserts),
            "clear_reports": len(h.members) - len(asserts),
            "independent_teams": d.independent_teams,
        }
        row.updated_at = now
        if h.id in created_ids:
            session.add(
                HazardHistory(
                    hazard_id=h.id, at=now, event="created", value=d.tier, source="community"
                )
            )
            touched.append(h.id)
        new = (d.tier, d.status, d.passability)
        if old != new or h.id in created_ids:
            changed += 1
            for ev, o, n in (
                ("tier", old[0], new[0]),
                ("status", old[1], new[1]),
                ("passability", old[2], new[2]),
            ):
                if o != n and h.id not in created_ids:
                    session.add(
                        HazardHistory(
                            hazard_id=h.id,
                            at=now,
                            event=ev,
                            value=n,
                            source="operator" if h.decision else "community",
                        )
                    )
        if any(m.id in mapping for m in h.members):
            touched.append(h.id)
    if touched:  # the hazard's shape is the convex hull of its reports
        await session.execute(
            update(RoadHazard)
            .where(RoadHazard.id.in_(touched))
            .values(
                geometry=select(func.ST_ConvexHull(func.ST_Collect(HazardReport.geometry)))
                .where(HazardReport.hazard_id == RoadHazard.id, HazardReport.status == "accepted")
                .scalar_subquery()
            )
            .execution_options(synchronize_session=False)
        )
    purged = await _purge(session, now)
    await session.commit()
    return Summary(len(created), changed, purged)


async def _purge(session: AsyncSession, now: datetime) -> int:
    """Delete hazards that ended long ago, with their reports and history (retention, docs/hazards.md section 9)."""
    cutoff = now - timedelta(days=get_settings().hazard_retention_days)
    old = select(RoadHazard.id).where(RoadHazard.status != "active", RoadHazard.ended_at < cutoff)
    ids = (await session.execute(old)).scalars().all()
    if ids:
        await session.execute(delete(HazardHistory).where(HazardHistory.hazard_id.in_(ids)))
        await session.execute(delete(HazardReport).where(HazardReport.hazard_id.in_(ids)))
        await session.execute(delete(RoadHazard).where(RoadHazard.id.in_(ids)))
    stray = await session.execute(
        delete(HazardReport)
        .where(HazardReport.hazard_id.is_(None), HazardReport.received_at < cutoff)
        .returning(HazardReport.id)
    )
    return len(ids) + len(stray.scalars().all())
