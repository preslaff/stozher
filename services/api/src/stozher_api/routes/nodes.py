"""Node registration and listing (operator)."""

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..audit import audit
from ..auth import Principal, require_role
from ..models import Node, NodeKey, TransportId
from ..problems import ApiError
from ..rebind_service import rebind, unrecognised_senders
from ..secrets_box import KekMissingError, seal

router = APIRouter(prefix="/v1/nodes")
operator = require_role(minimum="operator")


class TransportIn(BaseModel):
    transport: Literal["kineis", "mesh", "direct", "sim"]
    external_id: str = Field(min_length=1, max_length=64)


class NodeIn(BaseModel):
    id: int | None = Field(
        default=None, ge=1, le=0xFFFFFFFF, description="uint32 node ID; assigned if omitted"
    )
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["expert", "tbeam", "phone", "sim"]
    mode: Literal["fixed", "mobile"] = "fixed"
    surveyed_lat: float | None = Field(default=None, ge=-90, le=90)
    surveyed_lon: float | None = Field(default=None, ge=-180, le=180)
    key_id: int = Field(default=0, ge=0, le=15)
    master_key_hex: str = Field(
        description="16-byte master key, 32 hex chars; stored encrypted, never returned"
    )
    transport_ids: list[TransportIn] = Field(default_factory=list)

    @field_validator("master_key_hex")
    @classmethod
    def _key(cls, v: str) -> str:
        try:
            if len(bytes.fromhex(v)) != 16:
                raise ValueError
        except ValueError as exc:
            raise ValueError("must be 32 hex characters") from exc
        return v.lower()


class NodeOut(BaseModel):
    id: int
    name: str
    kind: str
    mode: str
    status: str
    last_seen: datetime | None
    sim: bool
    position: dict[str, Any] | None
    transport_ids: list[TransportIn]


async def _out(session, node: Node) -> NodeOut:  # type: ignore[no-untyped-def]
    tids = (
        (await session.execute(select(TransportId).where(TransportId.node_id == node.id)))
        .scalars()
        .all()
    )
    pos = (
        await session.execute(
            select(func.ST_AsGeoJSON(Node.surveyed_position)).where(Node.id == node.id)
        )
    ).scalar_one()
    return NodeOut(
        id=node.id,
        name=node.name,
        kind=node.kind,
        mode=node.mode,
        status=node.status,
        last_seen=node.last_seen,
        sim=node.kind == "sim",
        position=json.loads(pos) if pos else None,
        transport_ids=[TransportIn(transport=t.transport, external_id=t.external_id) for t in tids],
    )  # type: ignore[arg-type]


@router.post("", status_code=201, response_model=NodeOut)
async def register(body: NodeIn, request: Request, p: Principal = Depends(operator)) -> NodeOut:
    kek = request.app.state.settings.stozher_kek
    async with request.app.state.sessionmaker() as session:
        node = Node(name=body.name, kind=body.kind, mode=body.mode)
        if body.id is not None:
            node.id = body.id
        if body.surveyed_lat is not None and body.surveyed_lon is not None:
            node.surveyed_position = func.ST_SetSRID(
                func.ST_MakePoint(body.surveyed_lon, body.surveyed_lat), 4326
            )
        session.add(node)
        try:
            await session.flush()
            sealed = seal(
                kek, bytes.fromhex(body.master_key_hex), f"{node.id}:{body.key_id}".encode()
            )
            session.add(NodeKey(node_id=node.id, key_id=body.key_id, master_key_enc=sealed))
            for t in body.transport_ids:
                session.add(
                    TransportId(node_id=node.id, transport=t.transport, external_id=t.external_id)
                )
            session.add(audit(p, "node.register", str(node.id)))
            await session.commit()
        except KekMissingError as exc:
            await session.rollback()
            raise ApiError(503, "kek-missing", "Server cannot store node keys", str(exc)) from exc
        except IntegrityError as exc:
            await session.rollback()
            raise ApiError(409, "conflict", "Node ID or transport ID already registered") from exc
        return await _out(session, node)


@router.get("", response_model=list[NodeOut])
async def list_nodes(request: Request, _: Principal = Depends(operator)) -> list[NodeOut]:
    async with request.app.state.sessionmaker() as session:
        nodes = (await session.execute(select(Node).order_by(Node.id))).scalars().all()
        return [await _out(session, n) for n in nodes]


class SenderOut(BaseModel):
    transport: str
    external_id: str
    first_seen: datetime
    last_seen: datetime
    count: int
    candidate_node_id: int | None = Field(
        description="The one node whose key this sender has proved to hold, if any"
    )
    candidate_name: str | None


@router.get("/unrecognised-senders", response_model=list[SenderOut])
async def senders(
    request: Request,
    transport: Literal["kineis", "mesh", "direct"] = "mesh",
    _: Principal = Depends(operator),
) -> list[SenderOut]:
    """Radio IDs that sent something we could not place (for example after a reflash), and who they prove to be."""
    s = request.app.state.settings
    async with request.app.state.sessionmaker() as session:
        rows = await unrecognised_senders(
            session,
            kek=s.stozher_kek,
            transport=transport,
            now=datetime.now(UTC),
            replay_window_s=s.replay_window_s,
        )
    return [SenderOut(**r.__dict__) for r in rows]


class RebindIn(BaseModel):
    transport: Literal["kineis", "mesh", "direct"]
    external_id: str = Field(min_length=1, max_length=64)


class RecoveredOut(BaseModel):
    status: str
    reason: str | None


class RebindOut(BaseModel):
    node: NodeOut
    recovered: list[RecoveredOut]


@router.post("/{node_id}/transport-ids/rebind", response_model=RebindOut)
async def rebind_transport(
    node_id: int, body: RebindIn, request: Request, p: Principal = Depends(operator)
) -> RebindOut:
    """Attach a new transport ID to a node, only if a fresh packet from that ID proves it holds the node's key."""
    s = request.app.state.settings
    async with request.app.state.sessionmaker() as session:
        node, results = await rebind(
            session,
            p,
            node_id=node_id,
            transport=body.transport,
            external_id=body.external_id,
            kek=s.stozher_kek,
            now=datetime.now(UTC),
            replay_window_s=s.replay_window_s,
        )
        return RebindOut(
            node=await _out(session, node),
            recovered=[RecoveredOut(status=a, reason=b) for a, b in results],
        )


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(node_id: int, request: Request, _: Principal = Depends(operator)) -> NodeOut:
    async with request.app.state.sessionmaker() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise ApiError(404, "not-found", "Node not found")
        return await _out(session, node)
