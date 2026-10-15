"""Re-bind a node to a new transport ID after its device was reflashed (docs/mesh.md, "After reflashing a device").

A reflash can change the Meshtastic ID. The API only attaches a new ID to a node when a packet from that ID has proved,
by its tag, that the sender holds the node's key, and that packet is fresh and not a replay. No proof, no re-bind.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from stozher_core.record import WitnessRecord

from .audit import audit
from .auth import Principal
from .ingest_service import ingest
from .models import Event, Node, NodeKey, RawMessage, TransportId
from .problems import ApiError
from .secrets_box import unseal
from .sender_proof import verify_sender_proof

REASON = "unknown_transport_id"
LOOKBACK = timedelta(days=7)  # unknown senders are listed this long
PROOF_MAX_AGE = timedelta(hours=24)  # a proof packet must have arrived this recently
MAX_SENDERS = 50  # the listing does HMAC checks: bounded
MAX_PACKETS = 5  # newest packets per sender that are tried as proof when listing
RECOVER_PACKETS = 50  # newest packets re-ingested after a re-bind
TRANSPORTS = ("kineis", "mesh", "direct")  # `sim` nodes are never re-bound


@dataclass
class SenderRow:
    transport: str
    external_id: str
    first_seen: datetime
    last_seen: datetime
    count: int
    candidate_node_id: int | None = None
    candidate_name: str | None = None


@dataclass
class Proof:
    message: RawMessage
    record: WitnessRecord


async def _masters(
    session: AsyncSession, kek: str, node_id: int, now: datetime
) -> Callable[[int], bytes | None]:
    keys: dict[int, bytes] = {}
    rows = (await session.execute(select(NodeKey).where(NodeKey.node_id == node_id))).scalars()
    for nk in rows:
        if nk.valid_from <= now and (nk.valid_to is None or nk.valid_to > now):
            keys[nk.key_id] = unseal(kek, nk.master_key_enc, f"{node_id}:{nk.key_id}".encode())
    return keys.get


async def _proofs(
    session: AsyncSession,
    *,
    node_id: int,
    kek: str,
    transport: str,
    external_id: str,
    now: datetime,
    replay_window_s: int,
    limit: int = MAX_PACKETS,
) -> list[Proof]:
    """Packets from this unknown sender that prove it holds the node's key: fresh, and not already stored as an event."""
    masters = await _masters(session, kek, node_id, now)
    msgs = (
        (
            await session.execute(
                select(RawMessage)
                .where(
                    RawMessage.transport == transport,
                    RawMessage.external_id == external_id,
                    RawMessage.reject_reason == REASON,
                    RawMessage.received_at >= now - PROOF_MAX_AGE,
                )
                .order_by(RawMessage.received_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    proofs: list[Proof] = []
    seen: set[str] = set()
    for m in msgs:
        if m.payload_hex in seen:
            continue
        seen.add(m.payload_hex)
        res = verify_sender_proof(m.payload_hex, node_id, masters, m.received_at, replay_window_s)
        if not res.ok or res.record is None:
            continue
        stored = (
            await session.execute(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.node_id == node_id,
                    Event.key_id == res.record.key_id,
                    Event.seq == res.record.seq,
                )
            )
        ).scalar_one()
        if stored == 0:  # a sequence number the node already delivered is a replay, not a proof
            proofs.append(Proof(m, res.record))
    return proofs


async def unrecognised_senders(
    session: AsyncSession, *, kek: str, transport: str, now: datetime, replay_window_s: int
) -> list[SenderRow]:
    """Radio IDs that sent something we could not place, and the node each one proves to be (if exactly one)."""
    agg = (
        await session.execute(
            select(
                RawMessage.external_id,
                func.count(),
                func.min(RawMessage.received_at),
                func.max(RawMessage.received_at),
            )
            .where(
                RawMessage.transport == transport,
                RawMessage.reject_reason == REASON,
                RawMessage.received_at >= now - LOOKBACK,
                RawMessage.external_id.not_in(  # bound since: no longer unrecognised
                    select(TransportId.external_id).where(TransportId.transport == transport)
                ),
            )
            .group_by(RawMessage.external_id)
            .order_by(func.max(RawMessage.received_at).desc())
            .limit(MAX_SENDERS)
        )
    ).all()
    nodes = (
        (
            await session.execute(
                select(Node)
                .where(Node.status == "active", Node.kind != "sim")
                .join(NodeKey, NodeKey.node_id == Node.id)
                .distinct()
                .order_by(Node.id)
            )
        )
        .scalars()
        .all()
    )
    rows: list[SenderRow] = []
    for ext, count, first, last in agg:
        row = SenderRow(transport, ext, first, last, count)
        found = [
            n
            for n in nodes
            if await _proofs(
                session,
                node_id=n.id,
                kek=kek,
                transport=transport,
                external_id=ext,
                now=now,
                replay_window_s=replay_window_s,
                limit=1,
            )
        ]
        if len(found) == 1:
            row.candidate_node_id, row.candidate_name = found[0].id, found[0].name
        rows.append(row)
    return rows


async def rebind(
    session: AsyncSession,
    p: Principal,
    *,
    node_id: int,
    transport: str,
    external_id: str,
    kek: str,
    now: datetime,
    replay_window_s: int,
) -> tuple[Node, list[tuple[str, str | None]]]:
    """Attach `external_id` to the node, then re-ingest the packets that proved it. Returns the node and each packet's result."""
    if transport not in TRANSPORTS:
        raise ApiError(422, "bad-transport", "Transport cannot be re-bound", transport)
    node = await session.get(Node, node_id)
    if node is None:
        raise ApiError(404, "not-found", "Node not found")
    if node.kind == "sim":
        raise ApiError(409, "sim-node", "Simulated nodes are not re-bound")
    owner = (
        await session.execute(
            select(TransportId.node_id).where(
                TransportId.transport == transport, TransportId.external_id == external_id
            )
        )
    ).scalar_one_or_none()
    if owner is not None and owner != node_id:
        raise ApiError(409, "id-in-use", "That ID already belongs to another node")
    if owner == node_id:
        return node, []
    proofs = await _proofs(
        session,
        node_id=node_id,
        kek=kek,
        transport=transport,
        external_id=external_id,
        now=now,
        replay_window_s=replay_window_s,
        limit=RECOVER_PACKETS,
    )
    if not proofs:
        raise ApiError(
            409,
            "no-proof",
            "No fresh packet from that sender proves it holds this node's key",
            "The device must send a packet (a self-test, event or heartbeat) that reaches the API first; "
            "a packet that was already delivered, or one that is old, does not count.",
        )
    old = (
        (
            await session.execute(
                select(TransportId.external_id).where(
                    TransportId.node_id == node_id, TransportId.transport == transport
                )
            )
        )
        .scalars()
        .all()
    )
    await session.execute(
        delete(TransportId).where(
            TransportId.node_id == node_id, TransportId.transport == transport
        )
    )
    session.add(TransportId(node_id=node_id, transport=transport, external_id=external_id))
    session.add(
        audit(
            p,
            "node.rebind_transport",
            f"{node_id} {','.join(old) or '-'}>{external_id}"[:64],
        )
    )
    await session.commit()
    results: list[tuple[str, str | None]] = []
    for proof in reversed(proofs):  # oldest first, with the arrival time they really had
        res = await ingest(
            session,
            transport=transport,
            external_id=external_id,
            payload=proof.message.payload_hex,
            kek=kek,
            replay_window_s=replay_window_s,
            meta=proof.message.transport_meta,
            now=proof.message.received_at,
        )
        results.append((res.status, res.reason))
    return node, results
