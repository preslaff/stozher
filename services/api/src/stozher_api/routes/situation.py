"""GET /v1/situation (responder): the current picture in one call. Derived data only, never raw witness records."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from ..auth import Principal, require_role
from ..models import Hotspot, TrustCell
from .geo import BBOX, age_s, geojson, parse_bbox

router = APIRouter(prefix="/v1/situation")
responder = require_role(minimum="responder")
TOP = 10  # hotspots listed; the rest are in the count


@router.get("")
async def situation(
    request: Request, bbox: str | None = BBOX, _: Principal = Depends(responder)
) -> dict[str, Any]:
    """Active hotspots, the area they affect, and how many trust cells are in each state.

    No evidence is not an all-clear: `trust.cells` counts only cells that have evidence, and `data_age_s` is null when
    there is none. `confidence` is the mean confidence of the active hotspots (or of the evidence cells if none).
    """
    box = parse_bbox(bbox)
    now = datetime.now(UTC)
    hot = select(Hotspot).where(Hotspot.state == "active")
    cells = select(TrustCell)
    area = select(func.ST_AsGeoJSON(func.ST_Union(Hotspot.source_ellipse))).where(
        Hotspot.state == "active"
    )
    if box is not None:
        env = func.ST_MakeEnvelope(*box, 4326)
        hot = hot.where(Hotspot.source_ellipse.intersects(env))
        area = area.where(Hotspot.source_ellipse.intersects(env))
        cells = cells.where(TrustCell.center.intersects(env))
    async with request.app.state.sessionmaker() as session:
        hotspots = (await session.execute(hot.order_by(Hotspot.severity.desc()))).scalars().all()
        union = (await session.execute(area)).scalar_one()
        cell_rows = (await session.execute(cells)).scalars().all()
        centroids = {
            h.id: geojson(c)
            for h, c in (
                await session.execute(
                    select(Hotspot, func.ST_AsGeoJSON(Hotspot.centroid)).where(
                        Hotspot.id.in_([h.id for h in hotspots[:TOP]])
                    )
                )
            ).all()
        }
    by_kind: dict[str, int] = {}
    for h in hotspots:
        by_kind[h.kind] = by_kind.get(h.kind, 0) + 1
    by_state: dict[str, int] = {}
    for c in cell_rows:
        by_state[c.state] = by_state.get(c.state, 0) + 1
    newest = max(
        [h.last_seen for h in hotspots] + [c.newest_observed_at for c in cell_rows], default=None
    )
    pool = [h.confidence for h in hotspots] or [c.confidence for c in cell_rows]
    return {
        "bbox": list(box) if box else None,
        "hotspots": {
            "active": len(hotspots),
            "by_kind": by_kind,
            "items": [
                {
                    "id": str(h.id),
                    "kind": h.kind,
                    "severity": h.severity,
                    "confidence": h.confidence,
                    "independent_sources": h.node_count,
                    "last_seen": h.last_seen,
                    "centroid": centroids.get(h.id),
                    "sim": h.sim,
                }
                for h in hotspots[:TOP]
            ],
        },
        "affected_area": geojson(union),
        "trust": {"cells": len(cell_rows), "by_state": by_state},
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest, now),
        "confidence": round(sum(pool) / len(pool), 2) if pool else 0.0,
        "sim": any(h.sim for h in hotspots) or any(c.sim for c in cell_rows),
    }
