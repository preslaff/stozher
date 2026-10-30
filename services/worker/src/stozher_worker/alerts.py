"""Deterministic alert rules. Pure: compares the previous hotspots with the new ones.

Rules (no assistant, no randomness):
  hotspot_new        a hotspot appears while active              info/warning/critical by severity
  hotspot_escalated  severity crosses 0.7 upwards                critical
  hotspot_cleared    a previously active hotspot is now closed   info
  hotspot_resumed    a closed hotspot becomes active again        by severity
Each (kind, hotspot id) fires once; the database enforces it with a unique dedupe key.

A hotspot is announced (hotspot_new) only once it is confirmed: two or more independent witnesses,
or one witness that did not report normal again within SINGLE_WITNESS_HOLD of its latest report.
A short blip from one node (a receiver warning for a minute) stays on the map and in the API as a
hotspot but raises no alert; escalated, cleared and resumed follow only announced hotspots.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from shapely import wkt
from shapely.geometry import mapping

from .hotspots import Hotspot

CRITICAL_SEVERITY = 0.7
SINGLE_WITNESS_HOLD = timedelta(minutes=5)


@dataclass(frozen=True)
class Previous:
    state: str
    severity: float
    announced: bool = True  # a hotspot_new or hotspot_resumed was raised for it


@dataclass(frozen=True)
class NewAlert:
    level: str
    kind: str
    message: str
    hotspot_id: uuid.UUID
    area: dict[str, object]
    sim: bool
    dedupe_key: str


def _level(severity: float) -> str:
    return "critical" if severity >= CRITICAL_SEVERITY else "warning" if severity >= 0.4 else "info"


def _text(verb: str, h: Hotspot) -> str:
    kind = "Jamming" if h.kind == "jamming" else "Spoofing"
    return (
        f"{kind} interference {verb} near {h.centroid_lat:.3f}, {h.centroid_lon:.3f}: "
        f"{h.node_count} witness{'es' if h.node_count != 1 else ''}, {len(h.event_ids)} reports, "
        f"uncertainty about {h.semi_major_m / 1000:.1f} km"
    )


def confirmed(h: Hotspot, now: datetime, normal_at: Mapping[int, Sequence[datetime]]) -> bool:
    """Two or more witnesses, or one that has not reported normal within the hold of its latest report.

    `normal_at`: per node, the observed times of its normal records (with or without a fix).
    """
    if h.node_count >= 2:
        return True
    if now - h.last_seen < SINGLE_WITNESS_HOLD:
        return False
    end = h.last_seen + SINGLE_WITNESS_HOLD
    return not any(h.last_seen < t <= end for node in h.node_ids for t in normal_at.get(node, ()))


def derive_alerts(
    previous: dict[uuid.UUID, Previous],
    hotspots: list[Hotspot],
    now: datetime | None = None,
    normal_at: Mapping[int, Sequence[datetime]] | None = None,
) -> list[NewAlert]:
    """`now` and `normal_at` gate single-witness hotspots (see `confirmed`); without `now` every hotspot counts."""
    out: list[NewAlert] = []
    for h in hotspots:
        area = json.loads(json.dumps(mapping(wkt.loads(h.ellipse_wkt))))
        before = previous.get(h.id)

        def make(
            kind: str, level: str, verb: str, h: Hotspot = h, area: dict[str, object] = area
        ) -> None:
            out.append(NewAlert(level, kind, _text(verb, h), h.id, area, h.sim, f"{kind}:{h.id}"))

        if before is None or not before.announced:
            if h.state == "active" and (now is None or confirmed(h, now, normal_at or {})):
                make("hotspot_new", _level(h.severity), "detected")
        else:
            if before.state == "closed" and h.state == "active":
                make("hotspot_resumed", _level(h.severity), "has resumed")
            elif before.state == "active" and h.state == "closed":
                make("hotspot_cleared", "info", "has ended")
            elif h.state == "active" and before.severity < CRITICAL_SEVERITY <= h.severity:
                make("hotspot_escalated", "critical", "has intensified")
    return out
