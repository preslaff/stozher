"""Last-known evidence per place (docs/trust.md, "History is not trust").

The live trust grid only holds the last 90 minutes. This builds a separate, clearly expired view from older events so that a
quiet network does not look like a broken one. Every cell is the state as it stood at its newest evidence, never as it is now.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import h3
from stozher_core.trust import (
    Evidence,
    TrustState,
    adverse_state,
    event_weight,
    is_support,
    score_evidence,
)

H3_RES = 8
DISK_K = 4  # the same spread as the live grid: a report covers its own cell plus 4 rings, which also blurs where a node was
WINDOWS = {"6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7)}


@dataclass(frozen=True)
class HistoryPoint:
    node_id: int
    lat: float
    lon: float
    observed_at: datetime
    event_class: int
    confidence: int
    sim: bool


@dataclass(frozen=True)
class HistoryCell:
    h3_index: str
    state: TrustState  # as of `newest_observed_at`, not now
    score: int | None
    newest_observed_at: datetime
    event_count: int
    independent_sources: int
    sim: bool


def history_cells(points: list[HistoryPoint], k: int = DISK_K) -> list[HistoryCell]:
    """One cell per place with evidence in `points`. Cells whose evidence says nothing (tamper, unknown) are left out."""
    evidence: dict[str, list[tuple[HistoryPoint, TrustState | None]]] = defaultdict(list)
    for p in points:
        state = adverse_state(p.event_class)
        if state is None and not is_support(p.event_class):
            continue  # tamper / unknown say nothing about GNSS trust
        for cell in h3.grid_disk(h3.latlng_to_cell(p.lat, p.lon, H3_RES), k):
            evidence[cell].append((p, state))
    cells: list[HistoryCell] = []
    for idx in sorted(evidence):
        items = evidence[idx]
        newest = max(p.observed_at for p, _ in items)
        # weights are taken at the newest evidence of the place, so the state is what the place looked like then
        result = score_evidence(
            [
                Evidence(
                    p.node_id,
                    s,
                    event_weight(p.confidence, (newest - p.observed_at).total_seconds()),
                )
                for p, s in items
            ]
        )
        if result.state is TrustState.UNKNOWN:
            continue
        cells.append(
            HistoryCell(
                h3_index=idx,
                state=result.state,
                score=result.score,
                newest_observed_at=newest,
                event_count=len(items),
                independent_sources=result.independent_sources,
                sim=any(p.sim for p, _ in items),
            )
        )
    return cells
