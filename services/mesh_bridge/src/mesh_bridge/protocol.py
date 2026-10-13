"""Pure helpers: what a witness record looks like on the mesh, and how alerts are phrased for it."""

import re
from dataclasses import dataclass

PREFIX = "STZ1:"
RECORD_HEX_LENGTHS = (56, 64)  # 28-byte core and 32-byte extended record, hex encoded
_HEX = re.compile(r"[0-9a-fA-F]+")
MAX_TEXT_BYTES = 200  # Meshtastic text payloads are limited to roughly 230 bytes


@dataclass(frozen=True)
class MeshText:
    """One received text message, reduced to what the bridge needs."""

    from_id: str  # Meshtastic node ID, e.g. "!a1b2c3d4"; the transport ID of the witness
    text: str
    hops: int | None = None
    snr: float | None = None
    rssi: int | None = None
    channel: int | None = None


def extract_record(text: str) -> str | None:
    """The record hex if `text` is a witness record message (`STZ1:` + hex), else None.

    Anything else on the channel (people chatting, other apps) is ignored, never forwarded.
    """
    t = text.strip()
    if not t[: len(PREFIX)].upper() == PREFIX:
        return None
    body = t[len(PREFIX) :]
    if len(body) not in RECORD_HEX_LENGTHS or not _HEX.fullmatch(body):
        return None
    return body.lower()


def node_id(packet: dict) -> str | None:
    """Meshtastic node ID of the sender, from `fromId` or the numeric `from`."""
    fid = packet.get("fromId")
    if isinstance(fid, str) and fid:
        return fid.lower()
    num = packet.get("from")
    return f"!{num:08x}" if isinstance(num, int) else None


def hops_used(packet: dict) -> int | None:
    start, limit = packet.get("hopStart"), packet.get("hopLimit")
    if isinstance(start, int) and isinstance(limit, int) and start >= limit:
        return start - limit
    return None


def to_mesh_text(packet: dict) -> MeshText | None:
    """Convert a Meshtastic receive packet into a MeshText; None if it is not a text from a known node."""
    text = (packet.get("decoded") or {}).get("text")
    sender = node_id(packet)
    if not isinstance(text, str) or sender is None:
        return None
    rssi = packet.get("rxRssi")
    snr = packet.get("rxSnr")
    return MeshText(
        from_id=sender,
        text=text,
        hops=hops_used(packet),
        snr=float(snr) if isinstance(snr, (int, float)) else None,
        rssi=int(rssi) if isinstance(rssi, (int, float)) else None,
        channel=packet.get("channel") if isinstance(packet.get("channel"), int) else None,
    )


LEVELS = {"info": 0, "warning": 1, "critical": 2}


def alert_text(alert: dict, *, relay_sim: bool = False, min_level: str = "warning") -> str | None:
    """Short plain-language text for the mesh, or None if this alert should not be relayed.

    Simulated alerts are never sent over a real radio unless explicitly asked for, and are then
    prefixed [SIM] so nobody mistakes them for live ones.
    """
    if alert.get("sim") and not relay_sim:
        return None
    if LEVELS.get(str(alert.get("level")), 0) < LEVELS[min_level]:
        return None
    msg = " ".join(str(alert.get("message", "")).split())
    if not msg:
        return None
    head = f"{'[SIM] ' if alert.get('sim') else ''}STOZHER {str(alert.get('level')).upper()}: "
    text = head + msg
    raw = text.encode()
    if len(raw) > MAX_TEXT_BYTES:
        text = raw[: MAX_TEXT_BYTES - 3].decode(errors="ignore") + "..."
    return text
