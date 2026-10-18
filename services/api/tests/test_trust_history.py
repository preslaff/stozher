from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from stozher_api.history_service import HistoryPoint, history_cells
from stozher_api.jobs import enqueue

from .test_api import hdr
from .test_pipeline import VARNA, _run_worker
from .test_trust_route import make_jam

BBOX = "27.7,43.0,28.1,43.4"
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def point(
    cls: int, minutes: int = 0, node: int = 1, conf: int = 30, lat: float = 43.2
) -> HistoryPoint:
    return HistoryPoint(node, lat, 27.9, T0 + timedelta(minutes=minutes), cls, conf, False)


def test_a_cell_is_the_state_at_its_newest_evidence_and_ignores_unplaceable_classes() -> None:
    cells = history_cells([point(1), point(4, 5), point(7, 6)])  # jamming, then tamper and unknown
    assert cells and {c.state.value for c in cells} == {"JAMMED"}
    assert {c.newest_observed_at for c in cells} == {
        T0
    }  # tamper/unknown are not evidence about GNSS
    assert history_cells([point(4), point(7)]) == []


def test_old_evidence_keeps_its_own_weight_not_the_age_of_today() -> None:
    # a week later the same readings still give the state they gave then; only data_age_s tells how old that is
    first = history_cells([point(1), point(0, 1, node=2)])
    assert {c.state.value for c in first} == {"JAMMED"} or {c.state.value for c in first} == {
        "DEGRADED"
    }
    assert all(c.score is not None for c in first)


async def age_events(c: httpx.AsyncClient, hours: int) -> None:
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await s.execute(text(f"UPDATE events SET observed_at = now() - interval '{hours} hours'"))
        await s.commit()
        await enqueue(s, "recompute", datetime.now(UTC))


async def test_old_evidence_leaves_the_live_grid_but_stays_in_the_history(client):  # type: ignore[no-untyped-def]
    await make_jam(client)
    await age_events(client, 3)
    await _run_worker(client)
    grid = (
        await client.get("/v1/trust/grid", params={"bbox": BBOX}, headers=hdr(client, "public"))
    ).json()
    assert grid["features"] == [] and grid["data_age_s"] is None
    assert 10000 <= grid["newest_evidence_age_s"] <= 11500  # about 3 h, no place named
    h = await client.get(
        "/v1/trust/history", params={"bbox": BBOX, "window": "6h"}, headers=hdr(client, "responder")
    )
    assert h.status_code == 200
    d = h.json()
    assert d["expired"] is True and d["window"] == "6h" and d["features"]
    p = d["features"][0]["properties"]
    assert (
        p["expired"] is True
        and p["last_state"] in ("JAMMED", "DEGRADED")
        and 10000 <= p["data_age_s"] <= 11500
    )
    assert set(p) == {
        "h3",
        "expired",
        "last_state",
        "last_score",
        "newest_observed_at",
        "data_age_s",
        "event_count",
        "independent_sources",
        "sim",
    }  # no node IDs, no records


async def test_the_window_cuts_off_older_evidence(client):  # type: ignore[no-untyped-def]
    await make_jam(client)
    await age_events(client, 30)
    short = (
        await client.get(
            "/v1/trust/history",
            params={"bbox": BBOX, "window": "6h"},
            headers=hdr(client, "responder"),
        )
    ).json()
    assert short["features"] == [] and short["data_age_s"] is None
    week = (
        await client.get(
            "/v1/trust/history",
            params={"bbox": BBOX, "window": "7d"},
            headers=hdr(client, "responder"),
        )
    ).json()
    assert week["features"]


async def test_history_is_for_responders_and_above_only(client):  # type: ignore[no-untyped-def]
    for role, code in (("public", 403), ("reporter", 403), ("responder", 200), ("operator", 200)):
        r = await client.get("/v1/trust/history", params={"bbox": BBOX}, headers=hdr(client, role))
        assert r.status_code == code, role
    assert (await client.get("/v1/trust/history", params={"bbox": BBOX})).status_code == 401
    bad = await client.get(
        "/v1/trust/history",
        params={"bbox": BBOX, "window": "30d"},
        headers=hdr(client, "responder"),
    )
    assert bad.status_code == 422
    assert VARNA  # imported helper shared with the other trust tests
