import uuid
from datetime import UTC, datetime, timedelta

import pytest
from stozher_api.route_trust import CellInfo, evaluate_route, needed_cells
from stozher_worker.route_alerts import decide, derive, meets

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
LINE = [[27.90, 43.2], [28.00, 43.2]]
RID = uuid.uuid4()


@pytest.mark.parametrize(
    ("previous", "new", "level", "expected"),
    [
        ("TRUSTED", "DEGRADED", "degraded", "route_affected"),
        ("UNKNOWN", "JAMMED", "degraded", "route_affected"),
        ("DEGRADED", "JAMMED", "degraded", "route_affected"),  # got worse while at the level
        ("JAMMED", "SPOOFED", "degraded", "route_affected"),
        ("JAMMED", "JAMMED", "degraded", None),  # unchanged: no repeat
        ("JAMMED", "DEGRADED", "degraded", None),  # better but still at the level: quiet
        ("JAMMED", "TRUSTED", "degraded", "route_cleared"),
        ("DEGRADED", "UNKNOWN", "degraded", "route_cleared"),  # the evidence aged out
        ("TRUSTED", "UNKNOWN", "degraded", None),  # no evidence is not evidence of trouble
        ("TRUSTED", "DEGRADED", "jammed", None),  # below this watch's level
        ("TRUSTED", "JAMMED", "jammed", "route_affected"),
        ("JAMMED", "SPOOFED", "jammed", "route_affected"),
        ("TRUSTED", "JAMMED", "spoofed", None),
        ("TRUSTED", "SPOOFED", "spoofed", "route_affected"),
        (None, "JAMMED", "degraded", "route_affected"),
    ],
)
def test_decide(previous: str | None, new: str, level: str, expected: str | None) -> None:
    assert decide(previous, new, level) == expected


def test_ladder_membership() -> None:
    assert meets("DEGRADED", "degraded") and not meets("DEGRADED", "jammed")
    assert (
        meets("SPOOFED", "jammed")
        and not meets("UNKNOWN", "degraded")
        and not meets(None, "degraded")
    )


def trust(state: str, score: int = 10, sources: int = 3):  # type: ignore[no-untyped-def]
    cells = sorted(needed_cells(LINE))
    info = {c: CellInfo("TRUSTED", 95, 0.8, 2, NOW, False) for c in cells}
    for c in cells[3:6]:
        info[c] = CellInfo(state, score, 0.9, sources, NOW - timedelta(seconds=240), False)
    return evaluate_route(LINE, info)


def test_affected_alert_names_the_segment_and_carries_its_geometry() -> None:
    a = derive(RID, "Port to ring road", "TRUSTED", 0, "degraded", trust("JAMMED"), NOW)
    assert a is not None and a.kind == "route_affected" and a.level == "critical"
    assert "Route 'Port to ring road': segment 2" in a.message and "likely JAMMED" in a.message
    assert "3 sources" in a.message and "data 4 min old" in a.message and "km " in a.message
    assert a.area is not None and a.area["type"] == "LineString" and a.sim is False
    assert a.dedupe_key == f"route:{RID}:1"
    assert (
        derive(RID, "r", "TRUSTED", 4, "degraded", trust("JAMMED"), NOW).dedupe_key
        == f"route:{RID}:5"
    )  # type: ignore[union-attr]
    assert derive(RID, "r", "TRUSTED", 0, "degraded", trust("DEGRADED"), NOW).level == "warning"  # type: ignore[union-attr]


def test_cleared_alert_is_informational_and_has_no_area() -> None:
    a = derive(RID, "r", "JAMMED", 1, "degraded", trust("TRUSTED"), NOW)
    assert a is not None and (a.kind, a.level, a.area) == ("route_cleared", "info", None)
    assert derive(RID, "r", "JAMMED", 1, "degraded", trust("JAMMED"), NOW) is None
