"""Proof that a device holds a node's key, from one packet it sent (docs/mesh.md, "After reflashing a device").

The tag of a witness record is an HMAC under the node's own key, over the node ID, the header and the ciphertext. A packet
from a radio ID we do not know whose tag verifies for node X therefore came from a device that holds X's key. No database here:
the caller supplies the key and the facts about the arrival.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from stozher_core.codec import CORE_LEN, EXT_LEN, decode
from stozher_core.crypto import derive_keys
from stozher_core.errors import PacketError
from stozher_core.record import WitnessRecord

MESH_PREFIX = "STZ1:"


@dataclass(frozen=True)
class SenderProof:
    ok: bool
    reason: str | None = None
    record: WitnessRecord | None = None


def packet_bytes(payload: str) -> bytes | None:
    """The raw record from a stored payload (the optional `STZ1:` prefix is allowed), or None if it is not hex."""
    text = payload.strip()
    if text.upper().startswith(MESH_PREFIX):
        text = text[len(MESH_PREFIX) :]
    try:
        raw = bytes.fromhex(text.lower())
    except ValueError:
        return None
    return raw if len(raw) in (CORE_LEN, EXT_LEN) else None


def verify_sender_proof(
    payload: str,
    node_id: int,
    master_for_key: Callable[[int], bytes | None],
    received_at: datetime,
    replay_window_s: int,
) -> SenderProof:
    """Does this packet prove the sender holds `node_id`'s key, and was it fresh when it arrived?"""
    raw = packet_bytes(payload)
    if raw is None:
        return SenderProof(False, "bad_packet")
    key_id = raw[3] >> 4
    master = master_for_key(key_id)
    if master is None:
        return SenderProof(False, "unknown_key")
    try:
        rec = decode(raw, node_id, derive_keys(master, node_id, key_id))
    except PacketError as exc:
        return SenderProof(False, exc.reason.value)
    observed = datetime.fromtimestamp(rec.timestamp, UTC)
    if abs((received_at - observed).total_seconds()) > replay_window_s:
        return SenderProof(False, "outside_time_window", rec)
    return SenderProof(True, None, rec)
