"""Node registration and listing (operator)."""

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from stozher_core.control import ack_covers, ack_reach, contiguous_run, summarize

from ..audit import audit
from ..auth import Principal, require_role
from ..models import AckRecord, Event, Node, NodeKey, TransportId
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


class Delivery(BaseModel):
    """What the cloud holds of this node's records, and whether the node has been told (docs/control.md)."""

    key_id: int
    records: int
    highest_seq: int
    contiguous_seq: int = Field(description="every sequence number up to here is held")
    missing: int = Field(description="gaps below highest_seq: the node may still retransmit these")
    last_ack_at: datetime | None = Field(
        description="when the newest acknowledgement for this key was signed"
    )
    ack_current: bool | None = Field(
        description="the newest signed ack covers what is held now; null if none was ever signed. False means records arrived that the node has not been told about"
    )
    acked_through: int | None = Field(
        default=None,
        description="the highest sequence number the newest signed ack covers; null if none was ever signed. "
        "Gaps 17 or more behind the newest record are given up, so a current ack reaches the highest record held",
    )
    unacked: int = Field(
        default=0,
        description="records held that the newest signed ack does not cover (all of them if none was signed). "
        "Above 0 with ack_current true only for an ack signed before gaps were given up (pinned behind a gap)",
    )


async def _delivery(session, node_id: int) -> Delivery | None:  # type: ignore[no-untyped-def]
    latest = (
        await session.execute(
            select(Event.key_id)
            .where(Event.node_id == node_id)
            .order_by(Event.observed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return None
    seqs = set(
        (
            await session.execute(
                select(Event.seq).where(Event.node_id == node_id, Event.key_id == latest)
            )
        )
        .scalars()
        .all()
    )
    contiguous = contiguous_run(seqs)
    highest = max(seqs)
    ack = (
        await session.execute(
            select(AckRecord)
            .where(AckRecord.node_id == node_id, AckRecord.key_id == latest)
            .order_by(AckRecord.signed_at.desc(), AckRecord.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return Delivery(
        key_id=latest,
        records=len(seqs),
        highest_seq=highest,
        contiguous_seq=contiguous,
        missing=max(0, highest - len([s for s in seqs if s >= 1])),
        last_ack_at=ack.signed_at if ack else None,
        ack_current=None if ack is None else (ack.max_seq, ack.mask) == summarize(seqs),
        acked_through=None if ack is None else ack_reach(ack.max_seq, ack.mask),
        unacked=len(
            [
                s
                for s in seqs
                if s >= 1 and (ack is None or not ack_covers(ack.max_seq, ack.mask, s))
            ]
        ),
    )


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
    delivery: Delivery | None = None


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
        delivery=await _delivery(session, node.id),
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


class NodePatch(BaseModel):
    """Change what a node is called, whether it is in service, and where a fixed node was surveyed. Never its kind or keys."""

    model_config = ConfigDict(
        extra="forbid"
    )  # kind, keys and transport IDs have their own, stricter routes
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "retired"] | None = None
    mode: Literal["fixed", "mobile"] | None = None
    surveyed_lat: float | None = Field(default=None, ge=-90, le=90)
    surveyed_lon: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _position_is_a_pair(self) -> "NodePatch":
        sent = self.model_fields_set
        if ("surveyed_lat" in sent) != ("surveyed_lon" in sent):
            raise ValueError(
                "send surveyed_lat and surveyed_lon together (both null clears the position)"
            )
        if ("surveyed_lat" in sent) and (
            (self.surveyed_lat is None) != (self.surveyed_lon is None)
        ):
            raise ValueError("surveyed_lat and surveyed_lon must both be numbers or both be null")
        for field in ("name", "status", "mode"):
            if field in sent and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


@router.patch("/{node_id}", response_model=NodeOut)
async def patch_node(
    node_id: int, body: NodePatch, request: Request, p: Principal = Depends(operator)
) -> NodeOut:
    """A retired node is rejected at ingest (`node_inactive`); its events stay. Explicit nulls clear the surveyed position."""
    sent = body.model_fields_set
    if not sent:
        raise ApiError(422, "empty-patch", "Nothing to change")
    async with request.app.state.sessionmaker() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise ApiError(404, "not-found", "Node not found")
        changed: list[str] = []
        for field in ("name", "status", "mode"):
            if field in sent and getattr(body, field) != getattr(node, field):
                setattr(node, field, getattr(body, field))
                changed.append(field)
        if "surveyed_lat" in sent:
            node.surveyed_position = (
                None
                if body.surveyed_lat is None or body.surveyed_lon is None
                else func.ST_SetSRID(func.ST_MakePoint(body.surveyed_lon, body.surveyed_lat), 4326)
            )
            changed.append("surveyed_position")
        if changed:
            session.add(audit(p, "node.update", f"{node_id} {','.join(changed)}"[:64]))
        await session.commit()
        await session.refresh(node)
        return await _out(session, node)


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(node_id: int, request: Request, _: Principal = Depends(operator)) -> NodeOut:
    async with request.app.state.sessionmaker() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise ApiError(404, "not-found", "Node not found")
        return await _out(session, node)
