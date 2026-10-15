"""Input record for the pure analytics functions (no database types)."""

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EventPoint:
    id: uuid.UUID
    node_id: int
    node_kind: str  # expert, tbeam, phone, sim
    lat: float
    lon: float
    observed_at: datetime
    event_class: int
    confidence: int
    cn0_drop: int
    sim: bool
    seq: int = 0  # tie-break for events in the same second: (node, seq) is stable, uuids are not


def has_fix(lat: float, lon: float) -> bool:
    """False for a record with no position. A node without a GNSS fix sends latitude and longitude 0 (docs/packet.md).

    Such a record is stored and shown as an event, but it says nothing about where, so it is never localised: no hotspot,
    no trust cell, no alert text with coordinates. (0, 0) is open sea in the Gulf of Guinea, far outside any area we cover.
    """
    return not (lat == 0.0 and lon == 0.0)
