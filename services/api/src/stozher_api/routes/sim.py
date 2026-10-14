"""POST /v1/sim/reset (operator): delete simulated data only; live data is never touched."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import delete, func, select

from ..audit import audit
from ..auth import Principal, require_role
from ..jobs import enqueue
from ..models import (
    AckRecord,
    Alert,
    Event,
    HazardHistory,
    HazardReport,
    HotspotEvent,
    Node,
    NodeKey,
    RawMessage,
    RoadHazard,
    TransportId,
)
from .reports import ReportIn, _wkt, check_report_time

router = APIRouter(prefix="/v1/sim")
operator = require_role(minimum="operator")


@router.post("/reset")
async def reset(request: Request, p: Principal = Depends(operator)) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        sim_nodes = select(Node.id).where(Node.kind == "sim")
        events = select(Event.id).where(Event.node_id.in_(sim_nodes))
        await session.execute(delete(HotspotEvent).where(HotspotEvent.event_id.in_(events)))
        n_events = (
            await session.execute(delete(Event).where(Event.node_id.in_(sim_nodes)))
        ).rowcount
        await session.execute(delete(Alert).where(Alert.sim.is_(True)))
        await session.execute(delete(AckRecord).where(AckRecord.node_id.in_(sim_nodes)))
        await session.execute(delete(RawMessage).where(RawMessage.transport == "sim"))
        await session.execute(delete(NodeKey).where(NodeKey.node_id.in_(sim_nodes)))
        await session.execute(delete(TransportId).where(TransportId.node_id.in_(sim_nodes)))
        sim_hazards = select(RoadHazard.id).where(RoadHazard.sim.is_(True))
        await session.execute(delete(HazardHistory).where(HazardHistory.hazard_id.in_(sim_hazards)))
        n_reports = (
            await session.execute(delete(HazardReport).where(HazardReport.sim.is_(True)))
        ).rowcount
        await session.execute(delete(RoadHazard).where(RoadHazard.sim.is_(True)))
        n_nodes = (await session.execute(delete(Node).where(Node.kind == "sim"))).rowcount
        session.add(audit(p, "sim.reset", f"{n_nodes} nodes"))
        await enqueue(session, "recompute", datetime.now(UTC), commit=False)
        await session.commit()
    return {"deleted_nodes": n_nodes, "deleted_events": n_events, "deleted_reports": n_reports}


class SimReportIn(ReportIn):
    team: str = Field(
        min_length=1, max_length=40
    )  # the issuing team the report pretends to come from
    reporter: str = Field(default="1", min_length=1, max_length=16)  # which of the team's keys


@router.post("/reports", status_code=202)
async def sim_report(
    body: SimReportIn, request: Request, p: Principal = Depends(operator)
) -> dict[str, Any]:
    """A simulated community report (data only). Stored with `sim = true`; it never needs, and never uses, a reporter key."""
    now = datetime.now(UTC)
    check_report_time(body.observed_at, now, request.app.state.settings.report_max_age_h)
    async with request.app.state.sessionmaker() as session:
        report = HazardReport(
            reporter_prefix=f"sim-{body.team}-{body.reporter}"[:16],
            sim_team=body.team,
            kind=body.kind,
            passability=body.passability,
            geometry=func.ST_GeomFromText(_wkt(body.geometry), 4326),
            observed_at=body.observed_at,
            sim=True,
            status="accepted",
        )
        session.add(report)
        await enqueue(session, "hazards", now, commit=False)
        await session.commit()
    return {"id": str(report.id), "status": "accepted", "sim": True}
