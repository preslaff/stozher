import uuid
from datetime import UTC, datetime, timedelta

import h3
import pytest
from shapely import wkt
from stozher_core.trust import TrustState
from stozher_worker.cells import DISK_K, build_cells
from stozher_worker.hotspots import HotspotParams, build_hotspots, localize, reuse_ids
from stozher_worker.points import EventPoint

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
VARNA = (43.20, 27.91)
JAM, SPOOF, NORMAL, TAMPER = 1, 2, 0, 4


def pt(
    node: int,
    dlat: float = 0.0,
    dlon: float = 0.0,
    cls: int = JAM,
    conf: int = 24,
    age_s: int = 60,
    drop: int = 14,
    sim: bool = False,
    kind: str = "expert",
) -> EventPoint:
    return EventPoint(
        id=uuid.uuid4(),
        node_id=node,
        node_kind=kind,
        lat=VARNA[0] + dlat,
        lon=VARNA[1] + dlon,
        observed_at=NOW - timedelta(seconds=age_s),
        event_class=cls,
        confidence=conf,
        cn0_drop=drop,
        sim=sim,
    )


def test_nearby_events_form_one_hotspot_and_far_ones_another() -> None:
    hs = build_hotspots([pt(1), pt(2, 0.01, 0.01), pt(3, 0.5, 0.5)], NOW)
    assert sorted(h.node_count for h in hs) == [1, 2]
    assert all(h.kind == "jamming" for h in hs)


def test_jamming_and_spoofing_never_merge() -> None:
    hs = build_hotspots([pt(1, cls=JAM), pt(2, cls=SPOOF)], NOW)
    assert sorted(h.kind for h in hs) == ["jamming", "spoofing"]


def test_time_gap_splits_a_hotspot() -> None:
    hs = build_hotspots([pt(1, age_s=60), pt(1, age_s=4 * 3600)], NOW, HotspotParams(gap_s=3600))
    assert len(hs) == 2


def test_non_adverse_events_make_no_hotspot() -> None:
    assert build_hotspots([pt(1, cls=NORMAL), pt(2, cls=TAMPER)], NOW) == []


def test_state_active_then_closed() -> None:
    assert build_hotspots([pt(1, age_s=60)], NOW)[0].state == "active"
    assert build_hotspots([pt(1, age_s=7200)], NOW)[0].state == "closed"


def test_id_is_stable_when_events_are_added() -> None:
    first = pt(1, age_s=600)
    before = build_hotspots([first], NOW)[0].id
    after = build_hotspots([first, pt(2, 0.005, 0.005, age_s=30)], NOW)[0].id
    assert before == after


def test_single_event_gets_the_minimum_circle() -> None:
    lon, lat, a, b, _ = localize([pt(1)], NOW)
    assert (lat, lon) == pytest.approx(VARNA, abs=1e-6) and a == b == 1500.0


def test_centroid_leans_toward_the_stronger_report() -> None:
    strong, weak = pt(1, 0.0, 0.0, conf=31, drop=30), pt(2, 0.0, 0.04, conf=4, drop=1)
    lon, _, *_ = localize([strong, weak], NOW)
    assert VARNA[1] <= lon < VARNA[1] + 0.02


def test_ellipse_follows_the_spread_of_the_witnesses() -> None:
    group = [pt(1, 0.0, 0.0), pt(2, 0.0, 0.05), pt(3, 0.0, 0.1)]  # strung out east-west
    _, _, a, b, bearing = localize(group, NOW)
    assert a > b and (80 <= bearing <= 100)
    h = build_hotspots(group, NOW, HotspotParams(eps_m=20000))[0]
    poly = wkt.loads(h.ellipse_wkt)
    assert poly.is_valid and poly.contains(wkt.loads(f"POINT ({h.centroid_lon} {h.centroid_lat})"))


def test_support_only_cells_are_trusted_over_a_disk() -> None:
    cells = build_cells([pt(1, cls=NORMAL)], NOW)
    assert len(cells) == len(h3.grid_disk(h3.latlng_to_cell(*VARNA, 8), DISK_K)) == 61
    assert {c.state for c in cells} == {TrustState.TRUSTED}


def test_jamming_report_marks_its_own_cell_jammed() -> None:
    cells = {c.h3_index: c for c in build_cells([pt(1, cls=JAM, conf=28)], NOW)}
    centre = cells[h3.latlng_to_cell(*VARNA, 8)]
    assert centre.state is TrustState.JAMMED and centre.score is not None and centre.score <= 30
    assert centre.source_kinds == ("expert",) and centre.independent_sources == 1


def test_contested_cell_is_degraded_and_old_evidence_fades() -> None:
    contested = build_cells([pt(1, cls=JAM, conf=6), pt(2, cls=NORMAL, conf=31)], NOW)
    assert {c.state for c in contested} == {TrustState.DEGRADED}
    fresh = build_cells([pt(1, cls=NORMAL, age_s=60)], NOW)[0]
    stale = build_cells([pt(1, cls=NORMAL, age_s=3 * 3600)], NOW)[0]
    assert stale.score < fresh.score  # type: ignore[operator]


def test_tamper_and_sim_handling() -> None:
    assert build_cells([pt(1, cls=TAMPER)], NOW) == []
    assert all(c.sim for c in build_cells([pt(1, cls=NORMAL, sim=True, kind="sim")], NOW))


def test_a_weak_report_cannot_bridge_two_strong_groups() -> None:
    # two strong groups 8 km apart, one weak witness 4 km from each
    a = [pt(1, 0.0, 0.0, conf=30), pt(2, 0.005, 0.0, conf=30)]
    b = [pt(3, 0.0, 0.1, conf=30), pt(4, 0.005, 0.1, conf=30)]
    bridge = pt(5, 0.0, 0.05, conf=5)
    hs = build_hotspots([*a, *b, bridge], NOW)
    assert sorted(len(h.event_ids) for h in hs) == [2, 3]  # the weak report joined one group only


def test_weak_reports_alone_still_raise_a_hotspot() -> None:
    hs = build_hotspots([pt(1, conf=5), pt(2, 0.002, conf=6)], NOW)
    assert len(hs) == 1 and hs[0].node_count == 2


def test_hotspot_id_is_stable_for_events_in_the_same_second() -> None:
    import dataclasses

    a = dataclasses.replace(pt(1, age_s=60), seq=1)
    b = dataclasses.replace(pt(1, 0.001, age_s=60), seq=2)  # same node, same second, later seq
    ids = {build_hotspots(order, NOW)[0].id for order in ([a, b], [b, a])}
    assert len(ids) == 1


def test_weak_report_far_in_time_does_not_attach() -> None:
    strong = [pt(1, cls=JAM, conf=30, age_s=600), pt(2, 0.003, cls=JAM, conf=30, age_s=540)]
    early_weak = pt(3, 0.004, cls=JAM, conf=5, age_s=3000)  # 40 min before: not this event
    hs = build_hotspots([*strong, early_weak], NOW)
    assert sorted(len(h.event_ids) for h in hs) == [1, 2]


def test_ids_survive_a_late_arriving_earlier_report() -> None:
    first = [pt(1, age_s=300), pt(2, 0.003, age_s=240)]
    before = build_hotspots(first, NOW)[0]
    late = pt(3, 0.002, age_s=900)  # a delayed satellite report from earlier
    grown = build_hotspots([*first, late], NOW)[0]
    assert grown.id != before.id  # the earliest event changed, so a bare event-derived id would too
    kept = reuse_ids([grown], {before.id: (before.kind, frozenset(before.event_ids))})[0]
    assert kept.id == before.id and set(kept.event_ids) >= set(before.event_ids)


def test_reuse_claims_each_previous_hotspot_once_and_respects_kind() -> None:
    a, b = build_hotspots([pt(1, 0.0), pt(2, 0.5, cls=SPOOF)], NOW)
    prev = {uuid.uuid4(): (a.kind, frozenset(a.event_ids))}
    out = reuse_ids([a, b], prev)
    assert out[0].id in prev and out[1].id == b.id
