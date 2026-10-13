"""POST /v1/sim/reset (operator): delete simulated data only; live data is never touched."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select

from ..auth import Principal, require_role
from ..jobs import enqueue
from ..models import (
    AckRecord,
    Alert,
    AuditLog,
    Event,
    HotspotEvent,
    Node,
    NodeKey,
    RawMessage,
    TransportId,
)

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
        n_nodes = (await session.execute(delete(Node).where(Node.kind == "sim"))).rowcount
        session.add(AuditLog(actor=p.prefix, action="sim.reset", target=f"{n_nodes} nodes"))
        await enqueue(session, "recompute", datetime.now(UTC), commit=False)
        await session.commit()
    return {"deleted_nodes": n_nodes, "deleted_events": n_events}
