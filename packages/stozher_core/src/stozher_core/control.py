"""Cloud-signed control messages (docs/control.md). Pure functions, no I/O.

Only acknowledgements exist so far. Anything that does not verify raises `ControlError` with a
stable reason, and callers must ignore it.
"""

import struct
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ACK_TYPE = 0xA1  # type (acknowledgement) and version (1)
ACK_CONTEXT = b"stozher/v1/ack"
ACK_PAYLOAD_LEN = 15
SIGNATURE_LEN = 64
ACK_LEN = ACK_PAYLOAD_LEN + SIGNATURE_LEN
ACK_TEXT_PREFIX = "STZA1:"
MASK_BITS = 16


class ControlReason(StrEnum):
    BAD_TEXT = "bad_text"
    BAD_HEX = "bad_hex"
    BAD_LENGTH = "bad_length"
    BAD_TYPE = "bad_type"
    UNKNOWN_CLOUD_KEY = "unknown_cloud_key"
    BAD_SIGNATURE = "bad_signature"


class ControlError(ValueError):
    def __init__(self, reason: ControlReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class Ack:
    cloud_key_id: int
    node_id: int
    key_id: int
    max_seq: int  # every sequence 1..max_seq was received
    mask: int  # bit i: sequence max_seq + 2 + i was received
    signed_at: int

    def payload(self) -> bytes:
        return struct.pack(
            ">BBIBHHI",
            ACK_TYPE,
            self.cloud_key_id,
            self.node_id,
            self.key_id,
            self.max_seq,
            self.mask,
            self.signed_at,
        )

    def covers(self, seq: int) -> bool:
        """True if the cloud holds this sequence number according to this ack."""
        return ack_covers(self.max_seq, self.mask, seq)


def ack_covers(max_seq: int, mask: int, seq: int) -> bool:
    """True if an ack with this (max_seq, mask) tells the node the cloud holds `seq`."""
    if 1 <= seq <= max_seq:
        return True
    i = seq - max_seq - 2
    return 0 <= i < MASK_BITS and bool(mask >> i & 1)


def ack_reach(max_seq: int, mask: int) -> int:
    """The highest sequence number an ack covers: max_seq, or the highest one set in the mask.

    An ack reaches at most max_seq + 1 + MASK_BITS, so one record that never arrives keeps every later record
    beyond that window unacknowledged, however many the cloud holds.
    """
    return max_seq + 1 + mask.bit_length() if mask else max_seq


def summarize(received: Collection[int]) -> tuple[int, int]:
    """(max_seq, mask) for a set of received sequence numbers; sequence 0 is not part of the run."""
    have = {s for s in received if s >= 1}
    n = 0
    while n + 1 in have:
        n += 1
    mask = 0
    for i in range(MASK_BITS):
        if n + 2 + i in have:
            mask |= 1 << i
    return n, mask


def sign_ack(ack: Ack, key: Ed25519PrivateKey) -> bytes:
    payload = ack.payload()
    return payload + key.sign(ACK_CONTEXT + payload)


def verify_ack(message: bytes, trusted: Mapping[int, Ed25519PublicKey]) -> Ack:
    """Parse and verify an acknowledgement; raises ControlError. `trusted` maps cloud key id to key."""
    if len(message) != ACK_LEN:
        raise ControlError(ControlReason.BAD_LENGTH)
    if message[0] != ACK_TYPE:
        raise ControlError(ControlReason.BAD_TYPE)
    payload, signature = message[:ACK_PAYLOAD_LEN], message[ACK_PAYLOAD_LEN:]
    _, cloud_key_id, node_id, key_id, max_seq, mask, signed_at = struct.unpack(">BBIBHHI", payload)
    public = trusted.get(cloud_key_id)
    if public is None:
        raise ControlError(ControlReason.UNKNOWN_CLOUD_KEY)
    try:
        public.verify(signature, ACK_CONTEXT + payload)
    except InvalidSignature as exc:
        raise ControlError(ControlReason.BAD_SIGNATURE) from exc
    return Ack(cloud_key_id, node_id, key_id, max_seq, mask, signed_at)


def ack_to_text(message: bytes) -> str:
    return ACK_TEXT_PREFIX + message.hex()


def ack_from_text(text: str) -> bytes:
    t = text.strip()
    if t[: len(ACK_TEXT_PREFIX)].upper() != ACK_TEXT_PREFIX:
        raise ControlError(ControlReason.BAD_TEXT)
    try:
        return bytes.fromhex(t[len(ACK_TEXT_PREFIX) :])
    except ValueError as exc:
        raise ControlError(ControlReason.BAD_HEX) from exc


def private_key_from_seed(seed: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_from_bytes(raw: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw)
