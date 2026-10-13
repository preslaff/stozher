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
