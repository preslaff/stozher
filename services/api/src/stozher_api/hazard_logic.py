"""Merging reports into hazards, and the verification tiers (docs/hazards.md sections 5 and 7).

Pure functions: no database, no I/O. The service layer loads rows, calls these, and writes the result.

The rules that matter for safety, in one place:
  * Independence means different issuing TEAMS (the key label), not different keys: two keys of one
    team count once. Two independent teams are needed to reach `corroborated`.
  * Only a human decision makes `confirmed`. Nothing here confirms, and a confirmed hazard is closed
    only by a human.
  * `expired` is not `closed`: running out of time never marks a road clear.
  * Two independent clear reports, both newer than the last report of the hazard, close an
    unconfirmed hazard; absence of reports never does.
"""

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pyproj import Geod

GEOD = Geod(ellps="WGS84")
MERGE_M = 300.0
MERGE_WINDOW = timedelta(hours=6)

VALIDITY = {
    "flood": timedelta(hours=24),
    "landslide": timedelta(hours=72),
    "debris": timedelta(hours=12),
    "road_collapse": timedelta(days=7),
    "bridge_damage": timedelta(days=7),
    "fire_near_road": timedelta(hours=12),
    "snow_ice": timedelta(hours=12),
}
SEVERITY = {"blocked": 3, "restricted": 2, "passable_with_care": 1, "reported_clear": 0}
TIER_RANK = {"unverified": 0, "corroborated": 1, "confirmed": 2}

Point = tuple[float, float]  # (lon, lat)


@dataclass(frozen=True)
class Rep:
    """A report as the logic sees it: who (team), what, where, when. Never a person."""

    id: uuid.UUID
    team: str
    kind: str
    passability: str
    points: tuple[Point, ...]
    observed_at: datetime
    received_at: datetime
    sim: bool = False

    @property
    def is_clear(self) -> bool:
        return self.passability == "reported_clear"


@dataclass
class Haz:
    id: uuid.UUID
    kind: str
    status: str = "active"
    decision: str | None = None  # confirmed, closed, dismissed: set by a human only
    decided_at: datetime | None = None
    members: list[Rep] = field(default_factory=list)


@dataclass(frozen=True)
class Derived:
    tier: str
    status: str
    passability: str
    independent_teams: int
    clear_teams: int
    first_seen: datetime
    last_support: datetime
    valid_until: datetime
    ended_at: datetime | None


def distance_m(a: Sequence[Point], b: Sequence[Point]) -> float:
    """Smallest distance between two sets of points, in metres (geodesic)."""
    best = math.inf
    for lon1, lat1 in a:
        for lon2, lat2 in b:
            best = min(best, float(GEOD.inv(lon1, lat1, lon2, lat2)[2]))
    return best


def _asserts(h: Haz) -> list[Rep]:
    return [m for m in h.members if not m.is_clear]


def last_support(h: Haz) -> datetime | None:
    a = _asserts(h)
    return max(m.observed_at for m in a) if a else None


def _points(h: Haz) -> list[Point]:
    return [p for m in _asserts(h) for p in m.points]


def assign(
    hazards: list[Haz], reports: list[Rep], new_id: Callable[[], uuid.UUID] = uuid.uuid4
) -> tuple[dict[uuid.UUID, uuid.UUID | None], list[Haz]]:
    """Attach each new report to an active hazard, or start a new one (asserting reports only).

    A report joins an active hazard of the same kind within 300 m, and (for an asserting report)
    within 6 h of that hazard's latest support. A clear report joins the nearest active hazard of the
    same kind within 300 m regardless of the time gap. A clear that matches nothing stays unattached.
    Hazards that are closed, expired or dismissed take nothing: a new report starts a new hazard.
    Returns (report id -> hazard id or None, hazards created). `hazards` is updated in place.
    """
    created: list[Haz] = []
    out: dict[uuid.UUID, uuid.UUID | None] = {}
    for r in sorted(reports, key=lambda x: (x.received_at, str(x.id))):
        best: Haz | None = None
        best_d = MERGE_M
        for h in [*hazards, *created]:
            if h.status != "active" or h.kind != r.kind or not _asserts(h):
                continue
            if not r.is_clear:
                ls = last_support(h)
                if ls is None or abs(r.observed_at - ls) > MERGE_WINDOW:
                    continue
            d = distance_m(r.points, _points(h))
            if d <= best_d:
                best, best_d = h, d
        if best is None:
            if r.is_clear:
                out[r.id] = None
                continue
            best = Haz(id=new_id(), kind=r.kind)
            created.append(best)
        best.members.append(r)
        out[r.id] = best.id
    return out, created


def derive(h: Haz, now: datetime) -> Derived:
    asserts = _asserts(h)
    if not asserts:  # every supporting report was withdrawn
        base = h.decided_at or now
        return Derived(
            "unverified",
            "dismissed" if h.decision != "confirmed" else "active",
            "reported_clear",
            0,
            0,
            base,
            base,
            base,
            None if h.decision == "confirmed" else now,
        )
    teams = {m.team for m in asserts}
    first, last = min(m.observed_at for m in asserts), max(m.observed_at for m in asserts)
    tier = (
        "confirmed"
        if h.decision == "confirmed"
        else ("corroborated" if len(teams) >= 2 else "unverified")
    )
    valid_until = last + VALIDITY[h.kind]
    if h.decision == "confirmed" and h.decided_at is not None:
        valid_until = max(
            valid_until, h.decided_at + VALIDITY[h.kind]
        )  # a human re-confirming extends it
    clear_teams = {m.team for m in h.members if m.is_clear and m.observed_at > last}
    clear_times = [m.observed_at for m in h.members if m.is_clear and m.observed_at > last]

    status, ended = "active", None
    if h.decision == "dismissed":
        status, ended = "dismissed", h.decided_at
    elif h.decision == "closed":
        status, ended = "closed", h.decided_at
    elif (
        h.decision != "confirmed" and len(clear_teams) >= 2
    ):  # a confirmed hazard needs a human to close it
        status, ended = "closed", max(clear_times)
    elif now > valid_until:
        status, ended = "expired", valid_until  # expired is NOT clear
    passability = max((m.passability for m in asserts), key=lambda p: SEVERITY[p])
    if status == "closed":
        passability = "reported_clear"
    return Derived(
        tier, status, passability, len(teams), len(clear_teams), first, last, valid_until, ended
    )


# What the public and reporter roles may see (docs/hazards.md section 8): confirmed hazards of a few kinds,
# on a coarse grid. Nothing here depends on who reported.
PUBLIC_KINDS = ("closed_by_authority", "flood", "landslide", "snow_ice", "fire_near_road")
BLUR_M = 100.0
_DEG_LAT = BLUR_M / 111_320.0


def blur_geometry(geo: dict[str, Any]) -> dict[str, Any]:
    """Snap a Point or LineString to the centres of a 100 m grid: a Point, or a MultiPoint of the cells touched."""
    raw = geo["coordinates"]
    coords: list[list[float]] = [raw] if geo["type"] == "Point" else raw
    cells: list[tuple[float, float]] = []
    for lon, lat, *_ in coords:
        row = math.floor(lat / _DEG_LAT)
        step = _DEG_LAT / max(0.05, math.cos(math.radians((row + 0.5) * _DEG_LAT)))
        c = (round((math.floor(lon / step) + 0.5) * step, 5), round((row + 0.5) * _DEG_LAT, 5))
        if c not in cells:
            cells.append(c)
    if len(cells) == 1:
        return {"type": "Point", "coordinates": list(cells[0])}
    return {"type": "MultiPoint", "coordinates": [list(c) for c in cells[:200]]}
