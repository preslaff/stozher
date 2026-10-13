"""Build the H3 trust grid from recent events. Scoring itself is stozher_core.trust."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import h3
from stozher_core.trust import (
    Evidence,
    TrustState,
    adverse_state,
    event_weight,
    is_support,
    score_evidence,
)

from .points import EventPoint

H3_RES = 8
DISK_K = 4  # a witness's report covers its own cell plus 4 rings (about 3 km)


@dataclass(frozen=True)
class Cell:
    h3_index: str
    lat: float
    lon: float
    state: TrustState
    score: int | None
    confidence: float
    independent_sources: int
    source_kinds: tuple[str, ...]
    newest_observed_at: datetime
    sim: bool


def build_cells(events: list[EventPoint], now: datetime, k: int = DISK_K) -> list[Cell]:
    evidence: dict[str, list[Evidence]] = defaultdict(list)
    meta: dict[str, list[EventPoint]] = defaultdict(list)
    for e in events:
        state = adverse_state(e.event_class)
        if state is None and not is_support(e.event_class):
            continue  # tamper / unknown say nothing about GNSS trust
        w = event_weight(e.confidence, (now - e.observed_at).total_seconds())
        centre = h3.latlng_to_cell(e.lat, e.lon, H3_RES)
        for cell in h3.grid_disk(centre, k):
            evidence[cell].append(Evidence(e.node_id, state, w))
            meta[cell].append(e)
    cells: list[Cell] = []
    for idx in sorted(evidence):
        result = score_evidence(evidence[idx])
        if result.state is TrustState.UNKNOWN:
            continue
        lat, lon = h3.cell_to_latlng(idx)
        src = meta[idx]
        cells.append(
            Cell(
                h3_index=idx,
                lat=lat,
                lon=lon,
                state=result.state,
                score=result.score,
                confidence=result.confidence,
                independent_sources=result.independent_sources,
                source_kinds=tuple(sorted({e.node_kind for e in src})),
                newest_observed_at=max(e.observed_at for e in src),
                sim=any(e.sim for e in src),
            )
        )
    return cells
