"""Recompute the derived tables (hotspots, trust cells) from stored events."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stozher_api.models import (
    Alert,
    Event,
    Hotspot,
    HotspotEvent,
    Node,
    TrustCell,
    WatchedRoute,
)
from stozher_api.route_trust import CellInfo, evaluate_route

from .alerts import Previous, derive_alerts
from .cells import Cell, build_cells
from .hotspots import HotspotParams, build_hotspots, reuse_ids
from .points import EventPoint
from .route_alerts import derive

HOTSPOT_LOOKBACK = timedelta(hours=6)
TRUST_LOOKBACK = timedelta(minutes=90)  # evidence older than this is below 12% weight
ANALYSED_TYPES = (0, 1, 2)  # heartbeat, event, summary; test records (3) are ignored


@dataclass(frozen=True)
class Summary:
    events: int
    hotspots: int
    cells: int
    alerts: int = 0
    route_alerts: int = 0


async def load_points(session: AsyncSession, since: datetime) -> list[EventPoint]:
    rows = await session.execute(
        select(
            Event.id,
            Event.node_id,
            Node.kind,
            func.ST_Y(Event.position),
            func.ST_X(Event.position),
            Event.observed_at,
            Event.event_class,
            Event.confidence,
            Event.features["cn0_drop"].astext,
            Event.sim,
            Event.seq,
        )
        .join(Node, Node.id == Event.node_id)
        .where(Event.observed_at >= since, Event.msg_type.in_(ANALYSED_TYPES))
        .order_by(Event.observed_at, Event.id)
    )
    return [
        EventPoint(
            id=r[0],
            node_id=r[1],
            node_kind=r[2],
            lat=r[3],
            lon=r[4],
            observed_at=r[5],
            event_class=r[6],
            confidence=r[7],
            cn0_drop=int(r[8] or 0),
            sim=r[9],
            seq=r[10],
        )
        for r in rows
    ]


async def recompute(
    session: AsyncSession, now: datetime, params: HotspotParams | None = None
) -> Summary:
    points = await load_points(session, now - HOTSPOT_LOOKBACK)
    old_events: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for hid, eid in (
        await session.execute(select(HotspotEvent.hotspot_id, HotspotEvent.event_id))
    ).all():
        old_events[hid].add(eid)
    old_kind = dict((await session.execute(select(Hotspot.id, Hotspot.kind))).all())
    hotspots = reuse_ids(
        build_hotspots(points, now, params),
        {hid: (old_kind[hid], frozenset(ev)) for hid, ev in old_events.items() if hid in old_kind},
    )
    cells = build_cells([p for p in points if now - p.observed_at <= TRUST_LOOKBACK], now)

    previous = {
        r.id: Previous(r.state, r.severity)
        for r in (await session.execute(select(Hotspot.id, Hotspot.state, Hotspot.severity))).all()
    }
    await session.execute(delete(HotspotEvent))
    await session.execute(delete(Hotspot))
    await session.execute(delete(TrustCell))
    for h in hotspots:
        session.add(
            Hotspot(
                id=h.id,
                kind=h.kind,
                state=h.state,
                centroid=func.ST_SetSRID(func.ST_MakePoint(h.centroid_lon, h.centroid_lat), 4326),
                source_ellipse=func.ST_GeomFromText(h.ellipse_wkt, 4326),
                ellipse={
                    "semi_major_m": h.semi_major_m,
                    "semi_minor_m": h.semi_minor_m,
                    "bearing_deg": h.bearing_deg,
                },
                severity=h.severity,
                confidence=h.confidence,
                first_seen=h.first_seen,
                last_seen=h.last_seen,
                event_count=len(h.event_ids),
                node_count=h.node_count,
                sim=h.sim,
            )
        )
    await session.flush()
    for h in hotspots:
        session.add_all(HotspotEvent(hotspot_id=h.id, event_id=eid) for eid in h.event_ids)
    for c in cells:
        session.add(
            TrustCell(
                h3_index=c.h3_index,
                center=func.ST_SetSRID(func.ST_MakePoint(c.lon, c.lat), 4326),
                state=c.state.value,
                score=c.score,
                confidence=c.confidence,
                independent_sources=c.independent_sources,
                source_kinds=list(c.source_kinds),
                newest_observed_at=c.newest_observed_at,
                sim=c.sim,
            )
        )
    new_alerts = derive_alerts(previous, hotspots)
    for a in new_alerts:  # unique dedupe key: each (kind, hotspot) alerts once
        await session.execute(
            pg_insert(Alert)
            .values(
                level=a.level,
                kind=a.kind,
                message=a.message,
                hotspot_id=a.hotspot_id,
                area=a.area,
                sim=a.sim,
                dedupe_key=a.dedupe_key,
                delivered={"stream": True},
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
    routes = await _watched_routes(session, cells, now)
    await session.commit()
    return Summary(len(points), len(hotspots), len(cells), len(new_alerts), routes)


async def _watched_routes(session: AsyncSession, cells: list[Cell], now: datetime) -> int:
    """Drop expired watches and evaluate the rest against the fresh grid; returns alerts raised."""
    await session.execute(delete(WatchedRoute).where(WatchedRoute.expires_at <= now))
    info = {
        c.h3_index: CellInfo(
            c.state.value, c.score, c.confidence, c.independent_sources, c.newest_observed_at, c.sim
        )
        for c in cells
    }
    raised = 0
    for w in (await session.execute(select(WatchedRoute))).scalars():
        rt = evaluate_route(w.coordinates, info, float(w.buffer_m))
        alert = derive(w.id, w.name, w.last_state, w.transitions, w.min_level, rt, now)
        if alert is not None:
            inserted = await session.execute(
                pg_insert(Alert)
                .values(
                    level=alert.level,
                    kind=alert.kind,
                    message=alert.message,
                    area=alert.area,
                    sim=alert.sim,
                    dedupe_key=alert.dedupe_key,
                    route_id=w.id,
                    delivered={"stream": True},
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(Alert.id)
            )
            if inserted.scalar_one_or_none() is not None:
                w.transitions += 1
                raised += 1
        w.last_state, w.last_evaluated_at = rt.worst.state, now
    return raised
