"""Deterministic alert rules. Pure: compares the previous hotspots with the new ones.

Rules (no assistant, no randomness):
  hotspot_new        a hotspot appears while active              info/warning/critical by severity
  hotspot_escalated  severity crosses 0.7 upwards                critical
  hotspot_cleared    a previously active hotspot is now closed   info
Each (kind, hotspot id) fires once; the database enforces it with a unique dedupe key.
"""

import json
import uuid
from dataclasses import dataclass

from shapely import wkt
from shapely.geometry import mapping

from .hotspots import Hotspot

CRITICAL_SEVERITY = 0.7


@dataclass(frozen=True)
class Previous:
    state: str
    severity: float


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


def derive_alerts(previous: dict[uuid.UUID, Previous], hotspots: list[Hotspot]) -> list[NewAlert]:
    out: list[NewAlert] = []
    for h in hotspots:
        area = json.loads(json.dumps(mapping(wkt.loads(h.ellipse_wkt))))
        before = previous.get(h.id)

        def make(
            kind: str, level: str, verb: str, h: Hotspot = h, area: dict[str, object] = area
        ) -> None:
            out.append(NewAlert(level, kind, _text(verb, h), h.id, area, h.sim, f"{kind}:{h.id}"))

        if before is None:
            if h.state == "active":
                make("hotspot_new", _level(h.severity), "detected")
        else:
            if before.state == "active" and h.state == "closed":
                make("hotspot_cleared", "info", "has ended")
            elif h.state == "active" and before.severity < CRITICAL_SEVERITY <= h.severity:
                make("hotspot_escalated", "critical", "has intensified")
    return out
