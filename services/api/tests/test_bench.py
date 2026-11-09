"""A node on the bench: its records are stored and shown, never evidence (docs/trust.md "The bench is not the field")."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from stozher_api.models import NodeBenchPeriod

from .conftest import now_ts
from .test_api import hdr, packet, post, rec
from .test_pipeline import VARNA, _node, _run_worker


async def _patch(c, node: int, **body):  # type: ignore[no-untyped-def]
    return await c.patch(f"/v1/nodes/{node}", headers=hdr(c, "operator"), json=body)


async def _grid(c) -> list[str]:  # type: ignore[no-untyped-def]
    g = (
        await c.get(
            "/v1/trust/grid", params={"bbox": "27.7,43.0,28.1,43.4"}, headers=hdr(c, "public")
        )
    ).json()
    return [f["properties"]["state"] for f in g["features"]]


async def test_records_made_on_the_bench_are_shown_but_are_not_evidence(client):  # type: ignore[no-untyped-def]
    c = client
    a = await _node(c, "a")
    r = await _patch(c, a, status="bench")
    assert r.status_code == 200
    node = r.json()
    assert node["status"] == "bench" and node["bench"]["since"] is not None
    until = datetime.fromisoformat(node["bench"]["until"])
    assert timedelta(hours=23) < until - datetime.now(UTC) <= timedelta(hours=24)

    async with c.app.state.sessionmaker() as s:  # the node went on the bench an hour ago
        await s.execute(
            update(NodeBenchPeriod).values(
                started_at=NodeBenchPeriod.started_at - timedelta(hours=1)
            )
        )
        await s.commit()
    jam = rec(seq=1, ts=now_ts() - 60, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1])
    assert (await post(c, packet(jam, a), ext="a")).json()["status"] == "accepted"  # still stored
    await _run_worker(c)
    assert await _grid(c) == []  # no cells
    assert (await c.get("/v1/hotspots", headers=hdr(c, "responder"))).json()["count"] == 0
    assert (await c.get("/v1/alerts", headers=hdr(c, "responder"))).json()["count"] == 0
    hist = await c.get(
        "/v1/trust/history",
        params={"bbox": "27.7,43.0,28.1,43.4", "window": "6h"},
        headers=hdr(c, "responder"),
    )
    assert hist.json()["features"] == []
    (e,) = (await c.get("/v1/events", headers=hdr(c, "responder"))).json()["items"]
    assert e["bench"] is True and e["class"] == "jamming"  # shown, labelled

    # back in the field: the desk's record stays out, the new one counts
    r = await _patch(c, a, status="active")
    assert r.json()["status"] == "active" and r.json()["bench"] is None
    jam2 = rec(seq=2, ts=now_ts() + 1, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1])
    await post(c, packet(jam2, a), ext="a")
    await _run_worker(c)
    assert set(await _grid(c)) == {"JAMMED"}
    items = (await c.get("/v1/events", headers=hdr(c, "responder"))).json()["items"]
    assert [(i["seq"], i["bench"]) for i in items] == [(2, False), (1, True)]


async def test_a_late_record_is_judged_by_when_it_was_made(client):  # type: ignore[no-untyped-def]
    c = client
    a = await _node(c, "a")
    now = datetime.now(UTC)
    async with c.app.state.sessionmaker() as s:  # a bench period that ended an hour ago
        s.add(
            NodeBenchPeriod(
                node_id=a,
                started_at=now - timedelta(hours=2),
                ends_at=now - timedelta(hours=1),
                set_by="t",
            )
        )
        await s.commit()
    # store-and-forward: both arrive now; one was made on the bench, one after it
    for seq, min_ago in ((1, 90), (2, 30)):
        r = rec(
            seq=seq, ts=now_ts() - 60 * min_ago, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1]
        )
        await post(c, packet(r, a), ext="a")
    items = (await c.get("/v1/events", headers=hdr(c, "responder"))).json()["items"]
    assert [(i["seq"], i["bench"]) for i in items] == [(2, False), (1, True)]
    assert (await c.get(f"/v1/nodes/{a}", headers=hdr(c, "operator"))).json()["status"] == "active"


async def test_bench_until_and_status_rules(client):  # type: ignore[no-untyped-def]
    c = client
    a = await _node(c, "a")
    soon = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    r = await _patch(c, a, status="bench", bench_until=soon)
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["bench"]["until"]) == datetime.fromisoformat(soon)
    too_far = (datetime.now(UTC) + timedelta(days=8)).isoformat()
    assert (await _patch(c, a, status="bench", bench_until=too_far)).status_code == 422
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert (await _patch(c, a, status="bench", bench_until=past)).status_code == 422
    assert (await _patch(c, a, status="active", bench_until=soon)).status_code == 422
    assert (
        await c.patch(f"/v1/nodes/{a}", headers=hdr(c, "responder"), json={"status": "bench"})
    ).status_code == 403
    # retired ends the bench and is still rejected at ingest
    r = await _patch(c, a, status="retired")
    assert r.json()["status"] == "retired" and r.json()["bench"] is None
    assert (await post(c, packet(rec(seq=1), a), ext="a")).json()["reason"] == "node_inactive"
    log = (await c.get("/v1/admin/audit-log", headers=hdr(c, "admin"))).json()["items"]
    assert any(x["action"] == "node.update" and "status=bench" in x["target"] for x in log)
