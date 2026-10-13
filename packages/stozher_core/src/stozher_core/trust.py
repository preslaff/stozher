"""The one definition of the GNSS trust score (docs/trust.md). Pure functions, no I/O."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class TrustState(StrEnum):
    TRUSTED = "TRUSTED"
    DEGRADED = "DEGRADED"
    JAMMED = "JAMMED"
    SPOOFED = "SPOOFED"
    UNKNOWN = "UNKNOWN"


# Event classes from docs/packet.md.
_ADVERSE = {
    1: TrustState.JAMMED,
    2: TrustState.SPOOFED,
    3: TrustState.DEGRADED,
    5: TrustState.DEGRADED,
}
_SUPPORT_CLASS = 0

HALF_LIFE_S = 1800.0  # evidence loses half its weight every 30 minutes
SCORE_CAP = {TrustState.JAMMED: 30, TrustState.SPOOFED: 30, TrustState.DEGRADED: 60}
_SEVERITY = {TrustState.JAMMED: 3, TrustState.SPOOFED: 2, TrustState.DEGRADED: 1}


@dataclass(frozen=True)
class Evidence:
    node_id: int
    state: TrustState | None  # adverse state, or None for supporting ("normal") evidence
    weight: float  # 0..1 after age decay


@dataclass(frozen=True)
class TrustResult:
    state: TrustState
    score: int | None  # None when UNKNOWN: there is no honest number
    confidence: float  # 0..1
    independent_sources: int


def event_weight(confidence: int, age_s: float, half_life_s: float = HALF_LIFE_S) -> float:
    """Node confidence (0-31) mapped to 0.25..1, halved every half-life of age."""
    base = 0.25 + 0.75 * max(0, min(31, confidence)) / 31
    return base * 0.5 ** (max(age_s, 0.0) / half_life_s)


def adverse_state(event_class: int) -> TrustState | None:
    """Trust state an event class argues for; None if it says nothing about GNSS trust."""
    return _ADVERSE.get(event_class)


def is_support(event_class: int) -> bool:
    return event_class == _SUPPORT_CLASS


def score_evidence(evidence: Sequence[Evidence]) -> TrustResult:
    """Combine weighted evidence for one place into a trust state, score and confidence.

    Adverse evidence that outweighs supporting evidence sets the state; contested evidence
    is DEGRADED; supporting evidence alone is TRUSTED; nothing is UNKNOWN.
    """
    if not evidence:
        return TrustResult(TrustState.UNKNOWN, None, 0.0, 0)
    adverse: dict[TrustState, float] = defaultdict(float)
    support = 0.0
    nodes: set[int] = set()
    for e in evidence:
        nodes.add(e.node_id)
        if e.state is None:
            support += e.weight
        else:
            adverse[e.state] += e.weight
    total_adverse = sum(adverse.values())
    total = total_adverse + support
    if total <= 0:
        return TrustResult(TrustState.UNKNOWN, None, 0.0, len(nodes))
    confidence = round(min(1.0, 0.3 + 0.25 * (len(nodes) - 1)) * min(1.0, total), 2)
    if total_adverse == 0:
        return TrustResult(
            TrustState.TRUSTED, min(100, round(70 + 30 * min(1.0, support))), confidence, len(nodes)
        )
    ratio = total_adverse / total
    if ratio >= 0.5:
        dominant = max(adverse, key=lambda s: (adverse[s], _SEVERITY[s]))
        score = round(SCORE_CAP[dominant] * 2 * (1 - ratio))
        return TrustResult(dominant, score, confidence, len(nodes))
    return TrustResult(TrustState.DEGRADED, round(70 * (1 - ratio)), confidence, len(nodes))
