"""Signed control traffic: GET /v1/control/public-key and GET /v1/acks (docs/control.md)."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stozher_core.control import Ack, ack_to_text, sign_ack, summarize

from ..auth import Principal, require_role
from ..models import AckRecord, Event, Node, TransportId
from ..problems import ApiError
from ..signing import Signer
from .geo import age_s

router = APIRouter(prefix="/v1")
system_only = require_role("system")


def _signer(request: Request) -> Signer:
    signer: Signer | None = request.app.state.signer
    if signer is None:
        raise ApiError(
            503,
            "signing-not-configured",
            "Acknowledgement signing is not configured",
            "set STOZHER_ED25519_SEED (python -m stozher_api.cli gen-signing-key)",
        )
    return signer


@router.get("/control/public-key")
async def public_key(request: Request) -> dict[str, Any]:
    """The cloud's public key. Open: it is public. Nodes must still be provisioned with it, not fetch it."""
    s = _signer(request)
    return {
        "algorithm": "Ed25519",
        "cloud_key_id": s.cloud_key_id,
        "public_key_hex": s.public_key_hex,
    }


async def _resolve_node(
    session: AsyncSession, node_id: int | None, transport: str | None, external_id: str | None
) -> Node:
    if node_id is not None:
        node = await session.get(Node, node_id)
    elif transport and external_id:
        node = (
            await session.execute(
                select(Node)
                .join(TransportId, TransportId.node_id == Node.id)
                .where(TransportId.transport == transport, TransportId.external_id == external_id)
            )
        ).scalar_one_or_none()
    else:
        raise ApiError(
            422,
            "validation-error",
            "Identify the node",
            "give node_id, or transport and external_id",
        )
    if node is None:
        raise ApiError(404, "not-found", "Node not found")
    return node


async def current_ack(
    session: AsyncSession, signer: Signer, node: Node, key_id: int, now: datetime
) -> AckRecord:
    """The signed ack for the node's present state; reuses the stored one while nothing changed."""
    seqs = (
        (
            await session.execute(
                select(Event.seq).where(Event.node_id == node.id, Event.key_id == key_id)
            )
        )
        .scalars()
        .all()
    )
    max_seq, mask = summarize(set(seqs))
    stored = (
        await session.execute(
            select(AckRecord).where(
                AckRecord.node_id == node.id,
                AckRecord.key_id == key_id,
                AckRecord.cloud_key_id == signer.cloud_key_id,
                AckRecord.max_seq == max_seq,
                AckRecord.mask == mask,
            )
        )
    ).scalar_one_or_none()
    if stored is not None:
        return stored
    ack = Ack(signer.cloud_key_id, node.id, key_id, max_seq, mask, int(now.timestamp()))
    message = sign_ack(ack, signer.key)
    row = AckRecord(
        node_id=node.id,
        key_id=key_id,
        cloud_key_id=signer.cloud_key_id,
        max_seq=max_seq,
        mask=mask,
        signed_at=now,
        signature=message[len(message) - 64 :],
    )
    session.add(row)
    await session.commit()
    return row


@router.get("/acks")
async def acks(
    request: Request,
    node_id: int | None = Query(default=None, ge=1, le=0xFFFFFFFF),
    transport: str | None = Query(default=None, pattern="^(kineis|mesh|direct|sim)$"),
    external_id: str | None = Query(default=None, max_length=64),
    key_id: int | None = Query(default=None, ge=0, le=15),
    _: Principal = Depends(system_only),
) -> dict[str, Any]:
    """Latest signed acknowledgement per key id of one node, for gateways to relay (system only)."""
    signer = _signer(request)
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as session:
        node = await _resolve_node(session, node_id, transport, external_id)
        q = select(Event.key_id).where(Event.node_id == node.id).distinct()
        if key_id is not None:
            q = q.where(Event.key_id == key_id)
        key_ids = sorted((await session.execute(q)).scalars().all())
        newest = (
            await session.execute(
                select(Event.observed_at)
                .where(Event.node_id == node.id)
                .order_by(Event.observed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        items = []
        for k in key_ids:
            row = await current_ack(session, signer, node, k, now)
            ack = Ack(
                row.cloud_key_id,
                row.node_id,
                row.key_id,
                row.max_seq,
                row.mask,
                int(row.signed_at.timestamp()),
            )
            message = ack.payload() + row.signature
            items.append(
                {
                    "node_id": node.id,
                    "key_id": row.key_id,
                    "max_seq": row.max_seq,
                    "mask": row.mask,
                    "signed_at": row.signed_at.isoformat(),
                    "cloud_key_id": row.cloud_key_id,
                    "message_hex": message.hex(),
                    "text": ack_to_text(message),
                    "sim": node.kind == "sim",
                }
            )
    return {
        "items": items,
        "count": len(items),
        "sources": ["witness_nodes"],
        "data_age_s": age_s(newest),
    }
