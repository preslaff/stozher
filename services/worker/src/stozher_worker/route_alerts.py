"""Alerts for watched routes. Pure: previous state + new evaluation -> at most one alert.

Rules (docs/api.md section 7), deterministic and with no randomness:
  route_affected  the worst state first reaches the watch's `min_level`, or gets worse while at it
  route_cleared   the worst state falls back below `min_level`
Anything else (including repeated recomputes with no change) raises nothing.
UNKNOWN never triggers an alert: no evidence is not evidence of trouble.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from shapely.geometry import LineString, mapping
from stozher_api.route_trust import RANK, RouteTrust

# The ladder of the API: degraded < jammed < spoofed. A watch alerts from its level upwards.
LADDER = {
    "degraded": ("DEGRADED", "JAMMED", "SPOOFED"),
    "jammed": ("JAMMED", "SPOOFED"),
    "spoofed": ("SPOOFED",),
}


def meets(state: str | None, min_level: str) -> bool:
    return state in LADDER[min_level]


def decide(previous: str | None, new: str, min_level: str) -> str | None:
    now_hot, was_hot = meets(new, min_level), meets(previous, min_level)
    if now_hot and not was_hot:
        return "route_affected"
    if now_hot and was_hot and RANK[new] > RANK[previous or "TRUSTED"]:
        return "route_affected"
    if was_hot and not now_hot:
        return "route_cleared"
    return None


@dataclass(frozen=True)
class RouteAlert:
    level: str
    kind: str
    message: str
    area: dict[str, object] | None
    sim: bool
    dedupe_key: str


def _age(newest: datetime | None, now: datetime) -> str:
    if newest is None:
        return "no data"
    s = max(0, int((now - newest).total_seconds()))
    return f"{s} s old" if s < 90 else f"{round(s / 60)} min old"


def describe(
    kind: str, name: str, rt: RouteTrust, now: datetime
) -> tuple[str, str, dict[str, object] | None]:
    """(level, message, area) for an alert. The area is the worst segment so a map can show it."""
    w = rt.worst
    if kind == "route_cleared":
        return "info", f"Route '{name}': back below the alert level; worst is now {w.state}", None
    span = f"km {w.start_m / 1000:.1f}-{w.end_m / 1000:.1f}"
    n = w.independent_sources
    msg = (
        f"Route '{name}': segment {w.index + 1} ({span}) likely {w.state} "
        f"({n} source{'s' if n != 1 else ''}, data {_age(w.newest_observed_at, now)})"
    )
    area = json.loads(json.dumps(mapping(LineString(w.coords))))
    return ("critical" if w.state in ("JAMMED", "SPOOFED") else "warning"), msg, area


def derive(
    route_id: uuid.UUID,
    name: str,
    previous: str | None,
    transitions: int,
    min_level: str,
    rt: RouteTrust,
    now: datetime,
) -> RouteAlert | None:
    kind = decide(previous, rt.worst.state, min_level)
    if kind is None:
        return None
    level, message, area = describe(kind, name, rt, now)
    return RouteAlert(level, kind, message, area, rt.sim, f"route:{route_id}:{transitions + 1}")
