import uuid
from datetime import UTC, datetime

from stozher_worker.alerts import Previous, derive_alerts
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
