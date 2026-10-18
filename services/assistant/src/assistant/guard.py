"""Rules enforced by code, because a prompt is not a guarantee (docs/hazards.md, CLAUDE.md rules 5 and 9)."""

import re

from .tools import ToolResult

REFUSAL = (
    "STOZHER describes places and GNSS conditions, not people, vehicles or units, so I cannot help with that. "
    "I can tell you about interference, GNSS trust on a route or area, and reported road conditions."
)

# Questions about individuals, vehicles, units or weapons. A deliberately blunt filter: a false refusal costs a rephrase,
# a missed one would make the assistant a tracking tool. The system prompt repeats the rule for what slips through.
_PEOPLE = r"(person|people|man|woman|him|her|them|driver|journalist|officer|soldier|troops?|unit|convoy|vehicle|car|truck|tank|patrol|checkpoint|individual|colleague|someone)"
_SEEK = r"(where\s+is|where\s+are|track|follow|locate|find|trace|identify|who\s+is|who\s+are|spy|monitor|watch)"
_WEAPON = r"(weapon|explosive|ammunition|missile|drone\s+strike|target(ing)?|attack\s+plan)"
_PATTERNS = (
    re.compile(rf"\b{_SEEK}\b.{{0,60}}\b{_PEOPLE}\b", re.I),
    re.compile(rf"\b{_PEOPLE}\b.{{0,40}}\b{_SEEK}\b", re.I),
    re.compile(rf"\b{_WEAPON}\b", re.I),
)


def refusal(question: str) -> str | None:
    return REFUSAL if any(p.search(question) for p in _PATTERNS) else None


# Tools whose result says something about GNSS conditions now. Alerts and hazards are separate subjects.
LIVE_EVIDENCE_TOOLS = {
    "situation",
    "trust_point",
    "route_trust",
    "recent_events",
    "hotspots",
    "hotspot_detail",
}

NO_EVIDENCE = (
    "No recent evidence was found for this. That is not an all-clear: it means nobody has reported."
)
EXPIRED = "Expired history was used: it shows what was seen, not the current state."
UNVERIFIED = "Hazard reports marked unverified are unverified; only a human confirms them."
SIM_LABEL = "SIMULATED DATA: part of this comes from the simulator."
REMOVED = "A statement the data does not support was removed."
THIN_CONFIDENCE = 0.3  # one witness at full weight tops out at 0.3 (stozher_core.trust)


def thin_evidence(r: ToolResult) -> bool:
    """Live evidence that rests on one source or has low confidence. Unknown values do not count as thin."""
    if r.no_recent_evidence:
        return False  # that case has its own caveat
    low = r.confidence is not None and r.confidence < THIN_CONFIDENCE
    single = r.sources_count is not None and r.sources_count <= 1
    return low or single


def thin_text(r: ToolResult) -> str:
    conf = "unknown" if r.confidence is None else f"{r.confidence:.2f}"
    n = "unknown" if r.sources_count is None else str(r.sources_count)
    return (
        f"Based on thin evidence (confidence {conf}, {n} independent source(s)). "
        "Treat as indicative and confirm with another source of position."
    )


# Wording the data can never support when evidence is absent or thin (CLAUDE.md: nothing is clear by default or by absence).
_OVERCLAIM = re.compile(
    r"\b(all[- ]clear|safe\s+to|is\s+safe|are\s+safe|no\s+(immediate\s+)?action\s+(is\s+)?(required|needed)|"
    r"nothing\s+to\s+worry|no\s+risk|can\s+be\s+trusted\s+fully)\b",
    re.I,
)


def overclaims(text: str) -> bool:
    return _OVERCLAIM.search(text) is not None


def strip_overclaims(text: str) -> tuple[str, bool]:
    """Drop the sentences (and bullet lines) that overclaim. Returns the text and whether anything was removed."""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    kept = [p for p in parts if p.strip() and not overclaims(p)]
    return "\n".join(kept), len(kept) != len([p for p in parts if p.strip()])


def caveats(used: list[tuple[str, ToolResult]]) -> list[str]:
    """Statements added to every answer by code, whatever the model wrote."""
    out: list[str] = []
    live = [r for n, r in used if n in LIVE_EVIDENCE_TOOLS]
    if not any(not r.no_recent_evidence for r in live):
        out.append(NO_EVIDENCE)
    thin = next((r for n, r in used if n in LIVE_EVIDENCE_TOOLS and thin_evidence(r)), None)
    if thin is not None:
        out.append(thin_text(thin))
    if any(n == "trust_history" and not r.error for n, r in used):
        out.append(EXPIRED)
    if any(n == "hazards" and not r.error and r.data.get("count", 0) > 0 for n, r in used):
        out.append(UNVERIFIED)
    if any(r.sim for _, r in used):
        out.append(SIM_LABEL)
    errors = [n for n, r in used if r.error]
    if errors:
        out.append(
            f"Some data could not be read ({', '.join(sorted(set(errors)))}), so this answer is incomplete."
        )
    return out


def freshest_live_age(used: list[tuple[str, ToolResult]]) -> int | None:
    ages = [
        r.data_age_s
        for n, r in used
        if n in LIVE_EVIDENCE_TOOLS and not r.error and not r.expired and r.data_age_s is not None
    ]
    return min(ages) if ages else None
