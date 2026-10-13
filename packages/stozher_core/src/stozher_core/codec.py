"""Encode and decode witness records (docs/packet.md). Pure functions, no I/O."""

import struct

from pydantic import ValidationError

from .crypto import KeySet, crypt, make_tag, tag_ok
from .errors import PacketError, Reason
from .record import EventClass, Extended, MsgType, Osnma, WitnessRecord

VERSION = 1
CORE_LEN = 28
EXT_LEN = 32
_VALID_CLASSES = {c.value for c in EventClass}
_VALID_TYPES = {t.value for t in MsgType}


def _header(r: WitnessRecord) -> bytes:
    ext = 0x08 if r.extended else 0
    return bytes([(VERSION << 4) | r.type, *r.seq.to_bytes(2, "big"), (r.key_id << 4) | ext])


def _body(r: WitnessRecord) -> bytes:
    flags = (
        (r.osnma << 6)
        | (r.stationary << 5)
        | (r.rx_jamming << 4)
        | (r.rx_spoofing << 3)
        | (r.tamper << 2)
        | (r.mesh << 1)
    )
    body = bytes([(r.event_class << 5) | r.confidence, flags])
    body += struct.pack(">iiIH", r.lat_e7, r.lon_e7, r.timestamp, r.duration_s)
    body += bytes([r.cn0_mean, r.cn0_drop, r.cn0_spread, r.agc])
    if r.extended:
        e = r.extended
        body += bytes([e.satellites, e.pos_jump_code, e.clock_jump_code, e.battery])
    return body


def encode(record: WitnessRecord, node_id: int, keys: KeySet) -> bytes:
    header = _header(record)
    ciphertext = crypt(_body(record), keys.enc, node_id, record.seq)
    return header + ciphertext + make_tag(keys.mac, node_id, header, ciphertext)


def encode_hex(record: WitnessRecord, node_id: int, keys: KeySet) -> str:
    return encode(record, node_id, keys).hex()


def decode(raw: bytes, node_id: int, keys: KeySet) -> WitnessRecord:
    """Verify and decode. Raises PacketError with a stable reason."""
    if len(raw) not in (CORE_LEN, EXT_LEN):
        raise PacketError(Reason.BAD_LENGTH)
    header, ciphertext, tag = raw[:4], raw[4:-4], raw[-4:]
    if header[0] >> 4 != VERSION:
        raise PacketError(Reason.BAD_VERSION)
    if header[0] & 0x0F not in _VALID_TYPES:
        raise PacketError(Reason.BAD_TYPE)
    if header[3] & 0x07:
        raise PacketError(Reason.BAD_RESERVED)
    extended = bool(header[3] & 0x08)
    if extended != (len(raw) == EXT_LEN):
        raise PacketError(Reason.LENGTH_MISMATCH)
    if not tag_ok(keys.mac, node_id, header, ciphertext, tag):
        raise PacketError(Reason.BAD_TAG)

    seq = int.from_bytes(header[1:3], "big")
    body = crypt(ciphertext, keys.enc, node_id, seq)
    class_conf, flags = body[0], body[1]
    if (class_conf >> 5) not in _VALID_CLASSES:
        raise PacketError(Reason.BAD_CLASS)
    if flags & 0x01:
        raise PacketError(Reason.BAD_RESERVED)
    lat, lon, ts, dur = struct.unpack(">iiIH", body[2:16])
    if abs(lat) > 900_000_000 or abs(lon) > 1_800_000_000:
        raise PacketError(Reason.BAD_COORDINATES)
    ext = (
        Extended(
            satellites=body[20], pos_jump_code=body[21], clock_jump_code=body[22], battery=body[23]
        )
        if extended
        else None
    )
    try:
        return WitnessRecord(
            type=MsgType(header[0] & 0x0F),
            seq=seq,
            key_id=header[3] >> 4,
            event_class=EventClass(class_conf >> 5),
            confidence=class_conf & 0x1F,
            osnma=Osnma(flags >> 6),
            stationary=bool(flags & 0x20),
            rx_jamming=bool(flags & 0x10),
            rx_spoofing=bool(flags & 0x08),
            tamper=bool(flags & 0x04),
            mesh=bool(flags & 0x02),
            lat_e7=lat,
            lon_e7=lon,
            timestamp=ts,
            duration_s=dur,
            cn0_mean=body[16],
            cn0_drop=body[17],
            cn0_spread=body[18],
            agc=body[19],
            extended=ext,
        )
    except ValidationError as exc:  # unreachable if the checks above hold
        raise PacketError(Reason.BAD_COORDINATES) from exc


def decode_hex(text: str, node_id: int, keys: KeySet) -> WitnessRecord:
    try:
        raw = bytes.fromhex(text.strip())
    except ValueError as exc:
        raise PacketError(Reason.BAD_HEX) from exc
    return decode(raw, node_id, keys)
