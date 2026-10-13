"""The virtual node's detection rules (a stand-in for the firmware's, same shape as the spec)."""

import math
from dataclasses import dataclass

from stozher_core.record import EventClass


@dataclass(frozen=True)
class Observables:
    cn0_mean: int
    cn0_drop: int
    cn0_spread: int
    agc: int
    satellites: int
    pos_jump_m: float
    clock_jump_ms: float


@dataclass(frozen=True)
class Detection:
    event_class: EventClass
    confidence: int
    rx_jamming: bool
    rx_spoofing: bool


def classify(o: Observables) -> Detection:
    if o.pos_jump_m >= 150 or o.clock_jump_ms >= 200:
        if o.cn0_drop < 6:  # spoofing: signal stays strong while position or time moves
            size = max(o.pos_jump_m / 100.0, o.clock_jump_ms / 200.0)
            conf = max(8, min(31, round(10 + 21 * min(1.0, math.log10(max(size, 1.0)) / 1.2))))
            return Detection(EventClass.SPOOFING, conf, False, o.pos_jump_m >= 300)
    if o.cn0_drop >= 8 and o.agc >= 135:
        conf = max(4, min(31, round(31 * min(1.0, (o.cn0_drop - 6) / 24))))
        return Detection(EventClass.JAMMING, conf, o.agc >= 150, False)
    if o.cn0_drop >= 5 and o.agc < 135:
        return Detection(EventClass.BENIGN, 6, False, False)
    return Detection(EventClass.NORMAL, 0, False, False)
