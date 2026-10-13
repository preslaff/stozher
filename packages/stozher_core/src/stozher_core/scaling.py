"""Lossy log scaling for the extended jump codes (docs/packet.md). Not in the auth path."""

import math


def encode_jump(value: float) -> int:
    """Metres (position) or milliseconds (clock) to a uint8 code."""
    return min(255, max(0, round(8 * math.log2(1 + max(value, 0.0)))))


def decode_jump(code: int) -> float:
    return 2 ** (code / 8) - 1
