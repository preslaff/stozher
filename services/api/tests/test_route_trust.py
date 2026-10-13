from datetime import UTC, datetime

import h3
import pytest
from stozher_api.route_trust import (
    H3_RES,
    CellInfo,
    corridor_k,
    evaluate_route,
    needed_cells,
    sample_route,
)

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
# A straight line of about 8.3 km along a parallel (lat 43.2, lon 27.90 to 28.00).
LINE = [[27.90, 43.2], [28.00, 43.2]]


def info(
    state: str, score: int | None = 50, conf: float = 0.8, sources: int = 2, sim: bool = False
) -> CellInfo:
    return CellInfo(state, score, conf, sources, NOW, sim)


def cells_at(coords: list[list[float]], lo: float, hi: float) -> list[str]:
    """H3 cells of the route samples whose longitude lies in [lo, hi]."""
    return sorted(
        {
            h3.latlng_to_cell(la, lo_, H3_RES)
            for lo_, la, _ in sample_route(coords)
            if lo <= lo_ <= hi
        }
    )


def test_sampling_is_dense_geodesic_and_includes_the_ends() -> None:
    pts = sample_route(LINE, step_m=150)
    assert pts[0][:2] == (27.90, 43.2) and pts[-1][:2] == (28.00, 43.2)
    assert pts[-1][2] == pytest.approx(8_050, rel=0.02)  # 0.1 degrees of longitude at 43.2 N
    steps = [b[2] - a[2] for a, b in zip(pts, pts[1:], strict=False)]
    assert max(steps) <= 151 and all(s > 0 for s in steps)
    assert sample_route([[10, 10], [10, 10]])[-1][2] == 0.0  # a degenerate route does not crash


def test_no_evidence_is_one_unknown_segment_with_no_score() -> None:
    r = evaluate_route(LINE, {})
    assert len(r.segments) == 1 and r.worst.state == "UNKNOWN" and r.worst.score is None
    assert r.worst.confidence == 0.0 and r.worst.newest_observed_at is None
    assert r.length_m == pytest.approx(8_050, rel=0.02)


def test_a_jammed_stretch_splits_the_route_into_segments() -> None:
    table = {c: info("TRUSTED", 95) for c in needed_cells(LINE)}
    for c in cells_at(LINE, 27.94, 27.96):
        table[c] = info("JAMMED", 8, conf=0.9, sources=3)
    r = evaluate_route(LINE, table)
    assert [s.state for s in r.segments] == ["TRUSTED", "JAMMED", "TRUSTED"]
    assert r.worst.state == "JAMMED" and r.worst.index == 1 and r.worst.score == 8
    assert r.segments[1].independent_sources == 3 and r.segments[1].confidence == 0.9
    # segments tile the route with no gaps, and each starts where the last ended
    assert r.segments[0].start_m == 0 and r.segments[-1].end_m == r.length_m
    assert all(a.end_m == b.start_m for a, b in zip(r.segments, r.segments[1:], strict=False))
    assert r.segments[0].coords[-1] == r.segments[1].coords[0]


def test_overall_ranks_adverse_above_unknown_above_trusted() -> None:
    cells = sorted(needed_cells(LINE))
    only_trusted = evaluate_route(LINE, {c: info("TRUSTED", 99) for c in cells})
    assert only_trusted.worst.state == "TRUSTED" and len(only_trusted.segments) == 1
    mixed = {c: info("TRUSTED") for c in cells[:-3]}  # the last few cells have no evidence
    assert evaluate_route(LINE, mixed).worst.state == "UNKNOWN"
    mixed[cells[5]] = info("DEGRADED", 40)
    assert evaluate_route(LINE, mixed).worst.state == "DEGRADED"
    mixed[cells[6]] = info("JAMMED", 5)
    assert evaluate_route(LINE, mixed).worst.state == "JAMMED"
    mixed[cells[7]] = info("SPOOFED", 20)  # spoofing is the worst: a receiver cannot notice it
    assert evaluate_route(LINE, mixed).worst.state == "SPOOFED"


def test_a_corridor_catches_trouble_next_to_the_route() -> None:
    near = h3.latlng_to_cell(43.2 + 0.012, 27.95, H3_RES)  # about 1.3 km north of the line
    table = {near: info("JAMMED", 5)}
    assert (
        evaluate_route(LINE, table, buffer_m=0).worst.state == "UNKNOWN"
    )  # not on the route itself
    assert (
        evaluate_route(LINE, table, buffer_m=2000).worst.state == "JAMMED"
    )  # inside a 2 km corridor
    assert (
        corridor_k(0) == 0
        and corridor_k(2000) == 2
        and corridor_k(50) == 0
        and corridor_k(2760) == 3
    )
    assert needed_cells(LINE, 2000) > needed_cells(LINE, 0)


def test_segment_summary_uses_the_cautious_values() -> None:
    cells = cells_at(LINE, 27.90, 28.0)
    table = {
        c: info("DEGRADED", 60 - i, conf=0.9 - i * 0.01, sources=1 + i % 3)
        for i, c in enumerate(cells)
    }
    seg = evaluate_route(LINE, table).segments[0]
    assert seg.state == "DEGRADED" and seg.score == 60 - (len(cells) - 1)  # lowest score
    assert seg.confidence == pytest.approx(
        0.9 - (len(cells) - 1) * 0.01, abs=0.01
    )  # lowest confidence
    assert seg.independent_sources == 3  # most sources seen


def test_simulated_cells_flag_the_result_and_long_routes_are_refused() -> None:
    c = h3.latlng_to_cell(43.2, 27.95, H3_RES)
    assert evaluate_route(LINE, {c: info("TRUSTED", sim=True)}).sim is True
    with pytest.raises(ValueError):
        evaluate_route([[0.0, 0.0], [10.0, 0.0]], {})  # about 1,100 km: over 7,000 samples at 150 m
