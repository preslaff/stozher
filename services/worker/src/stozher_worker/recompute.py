"""Recompute the derived tables (hotspots, trust cells) from stored events."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stozher_api.models import Event, Hotspot, HotspotEvent, Node, TrustCell

from .cells import build_cells
from .hotspots import HotspotParams, build_hotspots
from .points import EventPoint

HOTSPOT_LOOKBACK = timedelta(hours=6)
TRUST_LOOKBACK = timedelta(minutes=90)  # evidence older than this is below 12% weight
ANALYSED_TYPES = (0, 1, 2)  # heartbeat, event, summary; test records (3) are ignored


@dataclass(frozen=True)
class Summary:
    events: int
    hotspots: int
    cells: int


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
        )
        for r in rows
    ]


async def recompute(
    session: AsyncSession, now: datetime, params: HotspotParams | None = None
) -> Summary:
    points = await load_points(session, now - HOTSPOT_LOOKBACK)
    hotspots = build_hotspots(points, now, params)
    cells = build_cells([p for p in points if now - p.observed_at <= TRUST_LOOKBACK], now)

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
    await session.commit()
    return Summary(len(points), len(hotspots), len(cells))
