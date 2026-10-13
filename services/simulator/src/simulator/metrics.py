"""Score a run against ground truth: detection, time to detect, false alarms, localization."""

from dataclasses import dataclass, field
from typing import Any

from .engine import Plan, TruthSource
from .geo import distance_m

MATCH_RADIUS_M = 8000.0


@dataclass
class SourceResult:
    id: str
    kind: str
    detected: bool
    time_to_report_s: float | None  # first adverse record sent, from source start
    time_to_arrive_s: float | None  # same, including link delay
    localization_error_m: float | None


@dataclass
class Report:
    sources: list[SourceResult] = field(default_factory=list)
    detection_rate: float = 0.0
    false_alarm_hotspots: int = 0
    false_alarm_reports: int = 0
    mean_localization_error_m: float | None = None
    merged_sources: int = 0  # real sources that ended up sharing a hotspot
    forged_total: int = 0
    forged_correct: int = 0
    packets: int = 0
    lost: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)


def _near(h_lat: float, h_lon: float, src: TruthSource, radius: float) -> float | None:
    d = min(distance_m(h_lat, h_lon, la, lo) for _, la, lo in src.track)
    return d if d <= radius else None


def _node_pos(plan: Plan) -> dict[str, tuple[float, float]]:
    return {n.name: (n.lat, n.lon) for n in plan.scenario.nodes}


def evaluate(
    plan: Plan, hotspots: list[dict[str, Any]], results: list[tuple[Any, dict[str, Any]]]
) -> Report:
    rep = Report(packets=len(plan.packets), lost=plan.lost)
    pos = _node_pos(plan)
    for pkt, res in results:
        rep.outcomes[res["status"]] = rep.outcomes.get(res["status"], 0) + 1
        if pkt.expect != "accepted" or pkt.mutation != "none":
            rep.forged_total += 1
            if res["status"] == pkt.expect:
                rep.forged_correct += 1
        if res["status"] != pkt.expect and pkt.expect != "accepted":
            rep.mismatches.append(
                f"{pkt.node} {pkt.mutation}: expected {pkt.expect}, got {res['status']}"
            )
        elif pkt.expect == "accepted" and res["status"] not in ("accepted", "duplicate"):
            rep.mismatches.append(
                f"{pkt.node} seq {pkt.record.seq}: rejected ({res.get('reason')})"
            )

    hs = [
        (h["kind"], h["centroid"]["coordinates"][1], h["centroid"]["coordinates"][0])
        for h in hotspots
    ]
    errors: list[float] = []
    for src in plan.truth:
        dists = [
            d
            for kind, la, lo in hs
            if kind == src.kind and (d := _near(la, lo, src, MATCH_RADIUS_M)) is not None
        ]
        adverse = [
            p
            for p in plan.packets
            if p.mutation == "none"
            and p.expect == "accepted"
            and p.record.event_class.name.lower() == src.kind
            and src.start <= p.sent_t <= src.end + 120
            and min(distance_m(*pos[p.node], la, lo) for _, la, lo in src.track) <= MATCH_RADIUS_M
        ]
        first = min(adverse, key=lambda p: p.sent_t, default=None)
        err = min(dists) if dists else None
        if err is not None:
            errors.append(err)
        rep.sources.append(
            SourceResult(
                src.id,
                src.kind,
                bool(dists),
                None if first is None else first.sent_t - src.start,
                None if first is None else first.arrive_t - src.start,
                err,
            )
        )
    # Merged: more than one real source of a kind answered by the same (nearest) hotspot.
    for kind in {s.kind for s in plan.truth}:
        owners: list[tuple[float, float] | None] = []
        for src in (s for s in plan.truth if s.kind == kind):
            best = min(
                (
                    (min(distance_m(la, lo, a, b) for _, a, b in src.track), (la, lo))
                    for k, la, lo in hs
                    if k == kind
                ),
                default=None,
            )
            owners.append(best[1] if best and best[0] <= MATCH_RADIUS_M else None)
        found = [o for o in owners if o is not None]
        rep.merged_sources += len(found) - len(set(found))
    rep.detection_rate = (
        sum(s.detected for s in rep.sources) / len(rep.sources) if rep.sources else 1.0
    )
    rep.mean_localization_error_m = sum(errors) / len(errors) if errors else None

    for kind, la, lo in hs:
        if not any(
            s.kind == kind and _near(la, lo, s, MATCH_RADIUS_M) is not None for s in plan.truth
        ):
            rep.false_alarm_hotspots += 1
    for p in plan.packets:  # adverse reports from nodes with no source nearby while it was active
        cls = p.record.event_class.name.lower()
        if p.mutation != "none" or cls not in ("jamming", "spoofing") or p.expect != "accepted":
            continue
        if not any(
            s.kind == cls
            and s.start <= p.sent_t <= s.end + 120
            and min(distance_m(*pos[p.node], la, lo) for _, la, lo in s.track) <= 2 * MATCH_RADIUS_M
            for s in plan.truth
        ):
            rep.false_alarm_reports += 1
    return rep
