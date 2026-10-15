"""Verify, decode and store one witness record arriving over any transport."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from stozher_core.codec import CORE_LEN, EXT_LEN, decode
from stozher_core.crypto import derive_keys
from stozher_core.errors import PacketError
from stozher_core.record import MsgType, WitnessRecord

from .jobs import enqueue
from .models import Event, Node, NodeKey, RawMessage, TransportId
from .secrets_box import unseal

MESH_PREFIX = "STZ1:"


@dataclass
class IngestResult:
    status: str  # accepted | duplicate | rejected
    reason: str | None = None
    event_id: uuid.UUID | None = None


def _flags(r: WitnessRecord) -> dict[str, Any]:
    return {
        "osnma": int(r.osnma),
        "stationary": r.stationary,
        "rx_jamming": r.rx_jamming,
        "rx_spoofing": r.rx_spoofing,
        "tamper": r.tamper,
        "mesh": r.mesh,
    }


def _features(r: WitnessRecord) -> dict[str, Any]:
    f: dict[str, Any] = {
        "cn0_mean": r.cn0_mean,
        "cn0_drop": r.cn0_drop,
        "cn0_spread": r.cn0_spread,
        "agc": r.agc,
    }
    if r.extended:
        f.update(r.extended.model_dump())
    return f


async def ingest(
    session: AsyncSession,
    *,
    transport: str,
    external_id: str,
    payload: str,
    kek: str,
    replay_window_s: int,
    meta: dict[str, Any] | None = None,
    now: datetime | None = None,
    node_id: int | None = None,
    origin: str = "cloud",
    via: str | None = None,
    check_window: bool = True,
) -> IngestResult:
    """`node_id`, `origin`, `via` and `check_window=False` are for sync (docs/api.md section 8): the record is old, it
    names its node directly, and it keeps the transport it first arrived by. The tag is verified either way."""
    now = now or datetime.now(UTC)
    text = payload.strip()
    if text.upper().startswith(MESH_PREFIX):
        text = text[len(MESH_PREFIX) :]
    text = text.lower()

    async def finish(
        status: str, reason: str | None, node_id: int | None, event_id: uuid.UUID | None = None
    ) -> IngestResult:
        session.add(
            RawMessage(
                transport=transport,
                external_id=external_id,
                payload_hex=payload[:200],
                received_at=now,
                transport_meta=meta,
                node_id=node_id,
                verify_status=status,
                reject_reason=reason,
            )
        )
        await session.commit()
        return IngestResult(status, reason, event_id)

    if node_id is not None:
        node = await session.get(Node, node_id)
    else:
        node = (
            await session.execute(
                select(Node)
                .join(TransportId, TransportId.node_id == Node.id)
                .where(TransportId.transport == transport, TransportId.external_id == external_id)
            )
        ).scalar_one_or_none()
    if node is None:
        return await finish("rejected", "unknown_transport_id", None)
    if (
        (node.kind == "sim")
        if transport == "sync"
        else ((transport == "sim") != (node.kind == "sim"))
    ):
        return await finish("rejected", "sim_mismatch", node.id)
    if node.status != "active":
        return await finish("rejected", "node_inactive", node.id)

    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return await finish("rejected", "bad_hex", node.id)
    if len(raw) not in (CORE_LEN, EXT_LEN):
        return await finish("rejected", "bad_length", node.id)

    key_id = raw[3] >> 4
    nk = (
        await session.execute(
            select(NodeKey).where(NodeKey.node_id == node.id, NodeKey.key_id == key_id)
        )
    ).scalar_one_or_none()
    if nk is None or nk.valid_from > now or (nk.valid_to is not None and nk.valid_to <= now):
        return await finish("rejected", "unknown_key", node.id)

    master = unseal(kek, nk.master_key_enc, f"{node.id}:{key_id}".encode())
    try:
        rec = decode(raw, node.id, derive_keys(master, node.id, key_id))
    except PacketError as exc:
        return await finish("rejected", exc.reason.value, node.id)

    observed = datetime.fromtimestamp(rec.timestamp, UTC)
    age = (now - observed).total_seconds()
    if (abs(age) if check_window else -age) > replay_window_s:
        return await finish("rejected", "outside_time_window", node.id)

    values = {
        "id": uuid.uuid4(),
        "node_id": node.id,
        "key_id": rec.key_id,
        "seq": rec.seq,
        "msg_type": int(rec.type),
        "event_class": int(rec.event_class),
        "confidence": rec.confidence,
        "flags": _flags(rec),
        "position": func.ST_SetSRID(func.ST_MakePoint(rec.lon, rec.lat), 4326),
        "observed_at": observed,
        "duration_s": rec.duration_s,
        "features": _features(rec),
        "origin": origin,
        "received_via": via or transport,
        "ingested_at": now,
        "payload_hex": text,
        "sim": node.kind == "sim",
    }
    inserted = (
        await session.execute(
            pg_insert(Event)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["node_id", "key_id", "seq"])
            .returning(Event.id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        await session.execute(update(Node).where(Node.id == node.id).values(last_seen=now))
        if rec.type != MsgType.TEST:
            await enqueue(session, "recompute", now, commit=False)
        return await finish("accepted", None, node.id, inserted)

    existing = (
        await session.execute(
            select(Event.id, Event.payload_hex).where(
                Event.node_id == node.id, Event.key_id == key_id, Event.seq == rec.seq
            )
        )
    ).one()
    if existing.payload_hex != text:
        return await finish("rejected", "seq_reuse", node.id)
    return await finish("duplicate", None, node.id, existing.id)


def meta_json(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    return json.loads(json.dumps(meta)) if meta else None
