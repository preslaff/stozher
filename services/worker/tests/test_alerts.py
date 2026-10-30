import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

from stozher_worker.alerts import SINGLE_WITNESS_HOLD, Previous, derive_alerts
from stozher_worker.hotspots import Hotspot, ellipse_polygon

T = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)


def hs(
    state: str = "active", severity: float = 0.5, hid: uuid.UUID | None = None, sim: bool = False
) -> Hotspot:
    return Hotspot(
        id=hid or uuid.uuid4(),
        kind="jamming",
        state=state,
        centroid_lon=27.9,
        centroid_lat=43.2,
        semi_major_m=2000,
        semi_minor_m=1500,
        bearing_deg=0,
        ellipse_wkt=ellipse_polygon(27.9, 43.2, 2000, 1500, 0),
        severity=severity,
        confidence=0.8,
        first_seen=T,
        last_seen=T,
        event_ids=(uuid.uuid4(),) * 3,
        node_count=2,
        sim=sim,
    )


def test_new_active_hotspot_alerts_once_with_severity_level() -> None:
    h = hs(severity=0.9)
    (a,) = derive_alerts({}, [h])
    assert (a.kind, a.level, a.hotspot_id, a.dedupe_key) == (
        "hotspot_new",
        "critical",
        h.id,
        f"hotspot_new:{h.id}",
    )
    assert "Jamming interference detected" in a.message and a.area["type"] == "Polygon"
    assert derive_alerts({h.id: Previous("active", 0.9)}, [h]) == []  # unchanged: nothing new


def test_hotspot_first_seen_already_closed_is_silent() -> None:
    assert derive_alerts({}, [hs(state="closed")]) == []


def test_escalation_and_clearing() -> None:
    h = hs(severity=0.85)
    (esc,) = derive_alerts({h.id: Previous("active", 0.5)}, [h])
    assert esc.kind == "hotspot_escalated" and esc.level == "critical"
    closed = hs(state="closed", hid=h.id)
    (clr,) = derive_alerts({h.id: Previous("active", 0.5)}, [closed])
    assert clr.kind == "hotspot_cleared" and clr.level == "info"


def test_sim_flag_is_carried() -> None:
    assert derive_alerts({}, [hs(sim=True)])[0].sim is True


def test_a_closed_hotspot_that_resumes_alerts() -> None:
    h = hs(severity=0.5)
    (a,) = derive_alerts({h.id: Previous("closed", 0.5)}, [h])
    assert a.kind == "hotspot_resumed" and a.level == "warning"


def lone(h: Hotspot) -> Hotspot:
    """The same hotspot heard by one witness only (node 7)."""
    return dataclasses.replace(h, node_count=1, node_ids=(7,))


MIN = timedelta(minutes=1)


def test_two_witnesses_alert_at_once() -> None:
    assert derive_alerts({}, [hs()], T, {})[0].kind == "hotspot_new"


def test_one_witness_waits_for_the_hold() -> None:
    h = lone(hs())
    assert derive_alerts({}, [h], T + SINGLE_WITNESS_HOLD - MIN, {}) == []
    (a,) = derive_alerts({h.id: Previous("active", 0.5, False)}, [h], T + SINGLE_WITNESS_HOLD, {})
    assert a.kind == "hotspot_new"  # still adverse after five minutes: announced now


# a4b0 indoors, 28 Oct 2026: a minute of the receiver's warning, then normal again. No alert, ever.
def test_one_witness_that_reports_normal_within_the_hold_is_a_blip() -> None:
    h = lone(hs())
    ended = {7: [T + 90 * timedelta(seconds=1)]}
    later = T + timedelta(minutes=20)
    assert derive_alerts({}, [h], later, ended) == []
    assert derive_alerts({}, [h], later, {8: [T + MIN]}) != []  # another node's normal says nothing


def test_an_unannounced_hotspot_does_not_escalate_clear_or_resume() -> None:
    h = lone(hs(severity=0.85))
    quiet = Previous("active", 0.5, False)
    blip = {7: [T + MIN]}
    now = T + timedelta(minutes=20)
    assert derive_alerts({h.id: quiet}, [h], now, blip) == []
    closed = lone(hs(state="closed", hid=h.id))
    assert derive_alerts({h.id: quiet}, [closed], now, blip) == []
    assert derive_alerts({h.id: Previous("closed", 0.5, False)}, [h], now, blip) == []


def test_a_second_witness_confirms_a_waiting_hotspot() -> None:
    h = lone(hs())
    assert derive_alerts({}, [h], T + MIN, {}) == []
    both = dataclasses.replace(h, node_count=2, node_ids=(7, 8))
    (a,) = derive_alerts({h.id: Previous("active", 0.5, False)}, [both], T + 2 * MIN, {})
    assert a.kind == "hotspot_new"
