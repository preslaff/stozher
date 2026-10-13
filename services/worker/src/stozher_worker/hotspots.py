"""Hotspot clustering and source localization (weighted centroid + uncertainty ellipse).

Pure functions. A hotspot is a group of adverse events of one kind that are close in space
and time. The source position is an estimate from the reporting witnesses' positions, so the
ellipse expresses uncertainty, not detection range. Moving sources are reported as a time
window; road-segment tracks come later.
"""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from pyproj import Geod, Proj
from shapely.geometry import Polygon
from sklearn.cluster import DBSCAN
from stozher_core.trust import TrustState, adverse_state, event_weight

from .points import EventPoint

EARTH_RADIUS_M = 6_371_000.0
HOTSPOT_NS = uuid.UUID("5a1f3c52-9d0e-4c6b-8f43-2f6f9b7a0c11")
KIND_BY_STATE = {TrustState.JAMMED: "jamming", TrustState.SPOOFED: "spoofing"}
MIN_RADIUS_M = 1500.0
GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class HotspotParams:
    eps_m: float = 5000.0  # events closer than this belong together
    gap_s: float = 3600.0  # a quiet gap longer than this starts a new hotspot
    active_s: float = 1800.0  # active while the last event is younger than this
    core_confidence: int = 22  # reports at or above this define hotspots; weaker ones attach


@dataclass(frozen=True)
class Hotspot:
    id: uuid.UUID
    kind: str
    state: str  # active, closed
    centroid_lon: float
    centroid_lat: float
    semi_major_m: float
    semi_minor_m: float
    bearing_deg: float  # of the major axis, clockwise from north
    ellipse_wkt: str
    severity: float
    confidence: float
    first_seen: datetime
    last_seen: datetime
    event_ids: tuple[uuid.UUID, ...]
    node_count: int
    sim: bool


def _kind(e: EventPoint) -> str | None:
    state = adverse_state(e.event_class)
    return KIND_BY_STATE.get(state) if state else None


def _spatial_time_groups(pts: list[EventPoint], p: HotspotParams) -> list[list[EventPoint]]:
    """DBSCAN on great-circle distance (min 1 event), then split each group at quiet gaps."""
    coords = np.radians([[e.lat, e.lon] for e in pts])
    labels = (
        DBSCAN(eps=p.eps_m / EARTH_RADIUS_M, min_samples=1, metric="haversine").fit(coords).labels_
    )
    spatial: dict[int, list[EventPoint]] = defaultdict(list)
    for e, lab in zip(pts, labels, strict=True):
        spatial[int(lab)].append(e)
    groups: list[list[EventPoint]] = []
    for members in spatial.values():
        members.sort(key=lambda e: (e.observed_at, str(e.id)))
        current = [members[0]]
        for e in members[1:]:
            if (e.observed_at - current[-1].observed_at).total_seconds() > p.gap_s:
                groups.append(current)
                current = []
            current.append(e)
        groups.append(current)
    return groups


def cluster(events: list[EventPoint], p: HotspotParams) -> list[list[EventPoint]]:
    """Group adverse events of one kind into hotspots.

    Strong reports (confidence >= core_confidence) define the hotspots. Weak reports, which
    come from witnesses far from a source, join the nearest strong group but never link two
    groups together; weak reports with no strong group nearby form their own hotspots. This
    keeps two sources apart when one witness hears both faintly.
    """
    by_kind: dict[str, list[EventPoint]] = defaultdict(list)
    for e in events:
        k = _kind(e)
        if k:
            by_kind[k].append(e)
    out: list[list[EventPoint]] = []
    for k in sorted(by_kind):
        strong = [e for e in by_kind[k] if e.confidence >= p.core_confidence]
        weak = [e for e in by_kind[k] if e.confidence < p.core_confidence]
        groups = _spatial_time_groups(strong, p) if strong else []
        leftovers: list[EventPoint] = []
        for e in weak:
            best, best_d = None, p.eps_m * 1.5
            for g in groups:
                if (e.observed_at - g[-1].observed_at).total_seconds() > p.gap_s or (
                    g[0].observed_at - e.observed_at
                ).total_seconds() > p.gap_s:
                    continue
                d = min(GEOD.inv(e.lon, e.lat, x.lon, x.lat)[2] for x in g)
                if d < best_d:
                    best, best_d = g, d
            if best is None:
                leftovers.append(e)
            else:
                best.append(e)
        if leftovers:
            groups += _spatial_time_groups(leftovers, p)
        out += groups
    return out


def _weights(group: list[EventPoint], now: datetime) -> np.ndarray:
    return np.array(
        [
            event_weight(e.confidence, (now - e.observed_at).total_seconds())
            * (1 + e.cn0_drop / 10)
            for e in group
        ]
    )


def localize(group: list[EventPoint], now: datetime) -> tuple[float, float, float, float, float]:
    """Weighted centroid and 2-sigma ellipse: (lon, lat, semi_major_m, semi_minor_m, bearing_deg)."""
    w = _weights(group, now)
    lat0 = float(np.average([e.lat for e in group], weights=w))
    lon0 = float(np.average([e.lon for e in group], weights=w))
    proj = Proj(proj="aeqd", lat_0=lat0, lon_0=lon0, datum="WGS84")
    xy = np.array([proj(e.lon, e.lat) for e in group])  # metres east, north
    cx, cy = np.average(xy, axis=0, weights=w)
    lon, lat = proj(cx, cy, inverse=True)
    if len(group) < 2:
        return lon, lat, MIN_RADIUS_M, MIN_RADIUS_M, 0.0
    d = xy - np.array([cx, cy])
    cov = (d * w[:, None]).T @ d / w.sum()
    vals, vecs = np.linalg.eigh(cov)  # ascending
    major = max(2 * math.sqrt(max(vals[1], 0.0)), MIN_RADIUS_M)
    minor = max(2 * math.sqrt(max(vals[0], 0.0)), MIN_RADIUS_M)
    vx, vy = vecs[:, 1]
    bearing = math.degrees(math.atan2(vx, vy)) % 180.0
    return lon, lat, major, minor, bearing


def ellipse_polygon(
    lon: float, lat: float, a: float, b: float, bearing_deg: float, n: int = 36
) -> str:
    """WKT polygon of the ellipse, placed with geodesic forward projections."""
    ring: list[tuple[float, float]] = []
    th = math.radians(bearing_deg)
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = a * math.cos(t), b * math.sin(t)  # along major (bearing) and minor axes
        east = x * math.sin(th) + y * math.cos(th)
        north = x * math.cos(th) - y * math.sin(th)
        dist = math.hypot(east, north)
        az = math.degrees(math.atan2(east, north))
        lo, la, _ = GEOD.fwd(lon, lat, az, dist)
        ring.append((lo, la))
    ring.append(ring[0])
    return Polygon(ring).wkt


def hotspot_id(group: list[EventPoint]) -> uuid.UUID:
    """Stable across recomputes while the earliest event stays in the window."""
    first = min(group, key=lambda e: (e.observed_at, e.node_id, e.seq, str(e.id)))
    return uuid.uuid5(HOTSPOT_NS, str(first.id))


def severity_and_confidence(group: list[EventPoint], nodes: int) -> tuple[float, float]:
    top = max(e.confidence for e in group) / 31
    severity = min(1.0, 0.15 * len(group) + 0.2 * nodes + 0.3 * top)
    confidence = min(1.0, 0.3 + 0.25 * (nodes - 1)) * (0.5 + 0.5 * top)
    return round(severity, 2), round(confidence, 2)


def build_hotspots(
    events: list[EventPoint], now: datetime, p: HotspotParams | None = None
) -> list[Hotspot]:
    p = p or HotspotParams()
    out: list[Hotspot] = []
    for group in cluster(events, p):
        lon, lat, a, b, bearing = localize(group, now)
        nodes = len({e.node_id for e in group})
        last = max(e.observed_at for e in group)
        sev, conf = severity_and_confidence(group, nodes)
        kind = _kind(group[0]) or "jamming"
        out.append(
            Hotspot(
                id=hotspot_id(group),
                kind=kind,
                state="active" if now - last <= timedelta(seconds=p.active_s) else "closed",
                centroid_lon=lon,
                centroid_lat=lat,
                semi_major_m=round(a, 1),
                semi_minor_m=round(b, 1),
                bearing_deg=round(bearing, 1),
                ellipse_wkt=ellipse_polygon(lon, lat, a, b, bearing),
                severity=sev,
                confidence=conf,
                first_seen=min(e.observed_at for e in group),
                last_seen=last,
                event_ids=tuple(e.id for e in group),
                node_count=nodes,
                sim=any(e.sim for e in group),
            )
        )
    return sorted(out, key=lambda h: (h.first_seen, str(h.id)))
