"""Alerts on watched routes for road hazards (docs/hazards.md section 8). Pure decision, then a DB step.

Rules, deterministic:
  route_hazard          a hazard of at least the watch's tier appears in the corridor, or its passability gets worse
  route_hazard_cleared  a hazard we alerted on was CLOSED by a person or by two independent clear reports
A hazard that expires, is dismissed or drops below the tier leaves the watch's memory silently: running out
of time is not "clear", and a dismissal is not a road report. Messages carry tier and age, never a bare
"road blocked".
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stozher_api.hazard_logic import SEVERITY
from stozher_api.hazard_route import hazards_along
from stozher_api.models import Alert, RoadHazard, WatchedRoute


@dataclass(frozen=True)
class OnRoute:
    id: str
    kind: str
    passability: str
    tier: str
    reports: int
    age_s: int | None
    at_m: float
    sim: bool


@dataclass(frozen=True)
class HazardAlert:
    level: str
    kind: str
    message: str
    dedupe_key: str
    sim: bool


def _key(route_id: uuid.UUID, hazard_id: str, n: int) -> str:
    return f"rh:{route_id.hex}:{hazard_id.replace('-', '')}:{n}"  # fits the 80-character column


def _age(s: int | None) -> str:
    if s is None:
        return "no recent report"
    return (
        f"{s} s old"
        if s < 90
        else f"{round(s / 60)} min old"
        if s < 7200
        else f"{round(s / 3600)} h old"
    )


def _what(h: OnRoute) -> str:
    n = f"{h.reports} report{'s' if h.reports != 1 else ''}"
    return f"{h.kind.replace('_', ' ')} ({h.passability.replace('_', ' ')}), {h.tier}, {n}, {_age(h.age_s)}, km {h.at_m / 1000:.1f}"


def derive(
    route_id: uuid.UUID,
    name: str,
    state: dict[str, dict[str, Any]],
    current: list[OnRoute],
    closed: dict[str, OnRoute],
) -> tuple[list[HazardAlert], dict[str, dict[str, Any]]]:
    """(alerts, new state). `closed` holds hazards of the old state that are now closed, as last known."""
    alerts: list[HazardAlert] = []
    new: dict[str, dict[str, Any]] = {}
    for h in current:
        old = state.get(h.id)
        n = old["n"] if old else 0
        worse = old is not None and SEVERITY[h.passability] > SEVERITY[old["p"]]
        if old is None or worse:
            n += 1
            level = "critical" if h.passability == "blocked" else "warning"
            alerts.append(
                HazardAlert(
                    level,
                    "route_hazard",
                    f"Route '{name}': {_what(h)}",
                    _key(route_id, h.id, n),
                    h.sim,
                )
            )
        new[h.id] = {"p": h.passability, "n": n}
    for hid, h in closed.items():
        n = state[hid]["n"] + 1
        alerts.append(
            HazardAlert(
                "info",
                "route_hazard_cleared",
                f"Route '{name}': closed — {_what(h)}",
                _key(route_id, hid, n),
                h.sim,
            )
        )
    return alerts, new


async def watch_hazards(session: AsyncSession, now: datetime) -> int:
    """Evaluate every active watch against the stored hazards; returns alerts raised."""
    raised = 0
    for w in (
        await session.execute(select(WatchedRoute).where(WatchedRoute.expires_at > now))
    ).scalars():
        rows = await hazards_along(session, w.coordinates, float(w.buffer_m), w.hazard_min_tier)
        current = [
            OnRoute(
                str(h.id),
                h.kind,
                h.passability,
                h.tier,
                int((h.evidence or {}).get("reports", 0)),
                int((now - h.last_confirmed).total_seconds()) if h.last_confirmed else None,
                float(at),
                h.sim,
            )
            for h, _geo, at, _d, _len in rows
        ]
        state: dict[str, dict[str, Any]] = dict(w.hazard_state or {})
        gone = [hid for hid in state if hid not in {c.id for c in current}]
        closed: dict[str, OnRoute] = {}
        if gone:
            for h in (
                await session.execute(
                    select(RoadHazard).where(RoadHazard.id.in_([uuid.UUID(g) for g in gone]))
                )
            ).scalars():
                if h.status == "closed":
                    closed[str(h.id)] = OnRoute(
                        str(h.id),
                        h.kind,
                        h.passability,
                        h.tier,
                        int((h.evidence or {}).get("reports", 0)),
                        None,
                        0.0,
                        h.sim,
                    )
        alerts, new = derive(w.id, w.name, state, current, closed)
        for a in alerts:
            inserted = await session.execute(
                pg_insert(Alert)
                .values(
                    level=a.level,
                    kind=a.kind,
                    message=a.message,
                    sim=a.sim,
                    dedupe_key=a.dedupe_key,
                    route_id=w.id,
                    delivered={"stream": True},
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(Alert.id)
            )
            if inserted.scalar_one_or_none() is not None:
                raised += 1
        w.hazard_state = new
    return raised
