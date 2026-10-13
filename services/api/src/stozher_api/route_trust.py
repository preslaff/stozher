"""Trust along a route (docs/api.md, trust/route and watched routes). Pure: no database, no I/O.

A route is sampled every `step_m`; each sample takes the trust of its H3 res-8 cell (or, with a
corridor `buffer_m`, the worst cell within the corridor). Consecutive samples with the same state
merge into one segment. The overall result is the worst segment.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import h3
from pyproj import Geod

GEOD = Geod(ellps="WGS84")
H3_RES = 8
CELL_SPACING_M = 920.0  # distance between neighbouring res-8 cell centres (edge about 530 m), about
MAX_SAMPLES = 6000

# Worse states rank higher. UNKNOWN is worse than TRUSTED: no evidence cannot vouch for a route.
# Spoofing outranks jamming: a receiver can notice jamming, spoofing makes it believe a wrong position.
RANK = {"TRUSTED": 1, "UNKNOWN": 2, "DEGRADED": 3, "JAMMED": 4, "SPOOFED": 5}


@dataclass(frozen=True)
class CellInfo:
    state: str
    score: int | None
    confidence: float
    independent_sources: int
    newest_observed_at: datetime
    sim: bool


@dataclass(frozen=True)
class Segment:
    index: int
    start_m: float
    end_m: float
    coords: list[tuple[float, float]]  # (lon, lat)
    state: str
    score: int | None
    confidence: float
    independent_sources: int
    newest_observed_at: datetime | None
    sim: bool


@dataclass(frozen=True)
class RouteTrust:
    length_m: float
    segments: list[Segment]
    worst: Segment  # the overall result is the worst segment's

    @property
    def sim(self) -> bool:
        return any(s.sim for s in self.segments)


def sample_route(
    coords: Sequence[Sequence[float]], step_m: float = 150.0
) -> list[tuple[float, float, float]]:
    """(lon, lat, distance along the route in metres) at about `step_m` spacing, vertices included."""
    out: list[tuple[float, float, float]] = []
    travelled = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:], strict=False):
        d = float(GEOD.inv(lon1, lat1, lon2, lat2)[2])
        n = max(1, math.ceil(d / step_m))
        inner = GEOD.npts(lon1, lat1, lon2, lat2, n - 1) if n > 1 else []
        pts = [(lon1, lat1), *inner]
        for i, (lo, la) in enumerate(pts):
            out.append((lo, la, travelled + d * i / n))
        travelled += d
    last = coords[-1]
    out.append((last[0], last[1], travelled))
    return out


def corridor_k(buffer_m: float) -> int:
    return max(0, round(buffer_m / CELL_SPACING_M))


def needed_cells(
    coords: Sequence[Sequence[float]], buffer_m: float = 0.0, step_m: float = 150.0
) -> set[str]:
    """Every H3 cell whose trust the route's evaluation can look at (to fetch from storage)."""
    k = corridor_k(buffer_m)
    cells: set[str] = set()
    for lon, lat, _ in sample_route(coords, step_m):
        centre = h3.latlng_to_cell(lat, lon, H3_RES)
        cells.update(h3.grid_disk(centre, k) if k else [centre])
    return cells


def _effective(centre: str, k: int, info: Mapping[str, CellInfo]) -> CellInfo | None:
    """Worst known cell in the corridor around a sample (lowest score breaks ties)."""
    ring = h3.grid_disk(centre, k) if k else [centre]
    known = [info[c] for c in ring if c in info]
    if not known:
        return None
    return max(known, key=lambda c: (RANK[c.state], -(c.score if c.score is not None else 100)))


def evaluate_route(
    coords: Sequence[Sequence[float]],
    info: Mapping[str, CellInfo],
    buffer_m: float = 0.0,
    step_m: float = 150.0,
) -> RouteTrust:
    samples = sample_route(coords, step_m)
    if len(samples) > MAX_SAMPLES:
        raise ValueError(f"route too long for a {step_m:.0f} m step ({len(samples)} samples)")
    k = corridor_k(buffer_m)
    per_sample = [
        _effective(h3.latlng_to_cell(lat, lon, H3_RES), k, info) for lon, lat, _ in samples
    ]
    states = [c.state if c else "UNKNOWN" for c in per_sample]

    groups: list[tuple[int, int]] = []  # [start, end] sample indices, inclusive
    start = 0
    for i in range(1, len(samples)):
        if states[i] != states[start]:
            groups.append((start, i - 1))
            start = i
    groups.append((start, len(samples) - 1))

    segments: list[Segment] = []
    for n, (a, b) in enumerate(groups):
        end = min(b + 1, len(samples) - 1)  # run to the first sample of the next segment: no gaps
        cells = [c for c in per_sample[a : b + 1] if c is not None]
        scores = [c.score for c in cells if c.score is not None]
        segments.append(
            Segment(
                index=n,
                start_m=round(samples[a][2], 1),
                end_m=round(samples[end][2], 1),
                coords=[(s[0], s[1]) for s in samples[a : end + 1]],
                state=states[a],
                score=min(scores) if scores else None,
                confidence=round(min((c.confidence for c in cells), default=0.0), 2),
                independent_sources=max((c.independent_sources for c in cells), default=0),
                newest_observed_at=max((c.newest_observed_at for c in cells), default=None),
                sim=any(c.sim for c in cells),
            )
        )
    worst = max(segments, key=lambda s: (RANK[s.state], -(s.score if s.score is not None else 100)))
    return RouteTrust(length_m=round(samples[-1][2], 1), segments=segments, worst=worst)
