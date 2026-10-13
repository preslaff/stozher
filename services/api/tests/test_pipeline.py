"""ingest -> worker -> read API, end to end."""

import httpx
from sqlalchemy import func, select
from stozher_api.models import Job
from stozher_worker.main import HANDLERS
from stozher_worker.queue import run_pending

from .conftest import now_ts
from .test_api import hdr, packet, post, rec, register

VARNA = (431989000, 279166000)  # lat/lon in 1e-7 degrees


async def _node(
    c: httpx.AsyncClient, ext: str, kind: str = "expert", transport: str = "direct"
) -> int:
    return await register(c, kind=kind, ext=ext, transport=transport)


async def _run_worker(c: httpx.AsyncClient) -> int:
    return await run_pending(c.app.state.sessionmaker, HANDLERS)  # type: ignore[attr-defined]


async def test_ingest_bursts_enqueue_one_recompute_job(client):  # type: ignore[no-untyped-def]
    a = await _node(client, "a")
    for seq in (1, 2, 3):
        await post(client, packet(rec(seq=seq), node_id=a), ext="a")
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (
            await s.execute(select(func.count()).select_from(Job).where(Job.state == "pending"))
        ).scalar_one() == 1
    assert await _run_worker(client) == 1


async def test_two_witnesses_make_a_hotspot_and_a_trust_picture(client):  # type: ignore[no-untyped-def]
    a, b, c = await _node(client, "a"), await _node(client, "b", "tbeam"), await _node(client, "c")
    await post(client, packet(rec(seq=1, lat_e7=VARNA[0], lon_e7=VARNA[1]), a), ext="a")
    await post(
        client, packet(rec(seq=1, lat_e7=VARNA[0] + 90000, lon_e7=VARNA[1] + 90000), b), ext="b"
    )
    # a quiet node 60 km away reports normal
    far = rec(
        seq=1, event_class=0, type=0, rx_jamming=False, lat_e7=VARNA[0] + 5400000, lon_e7=VARNA[1]
    )
    await post(client, packet(far, c), ext="c")
    assert await _run_worker(client) == 1

    hs = (await client.get("/v1/hotspots?state=active", headers=hdr(client, "responder"))).json()
    assert hs["count"] == 1 and hs["sources"] == ["witness_nodes"]
    h = hs["items"][0]
    assert h["kind"] == "jamming" and h["independent_sources"] == 2 and h["event_count"] == 2
    assert h["source_ellipse"]["type"] == "Polygon" and h["centroid"]["type"] == "Point"
    assert h["ellipse"]["semi_major_m"] >= h["ellipse"]["semi_minor_m"] >= 1500
    detail = (await client.get(f"/v1/hotspots/{h['id']}", headers=hdr(client, "responder"))).json()
    assert len(detail["timeline"]) == 2

    near = (
        await client.get(
            "/v1/trust", params={"lat": 43.1989, "lon": 27.9166}, headers=hdr(client, "public")
        )
    ).json()
    assert near["state"] == "JAMMED" and near["independent_sources"] >= 1 and near["sim"] is False
    assert set(near["sources"]) <= {"expert", "tbeam", "phone"} and "node_id" not in near
    quiet = (
        await client.get(
            "/v1/trust",
            params={"lat": 43.1989 + 0.54, "lon": 27.9166},
            headers=hdr(client, "public"),
        )
    ).json()
    assert quiet["state"] == "TRUSTED"
    nowhere = (
        await client.get("/v1/trust", params={"lat": 10, "lon": 10}, headers=hdr(client, "public"))
    ).json()
    assert (
        nowhere["state"] == "UNKNOWN"
        and nowhere["score"] is None
        and nowhere["independent_sources"] == 0
    )

    grid = (
        await client.get(
            "/v1/trust/grid", params={"bbox": "27.8,43.1,28.0,43.3"}, headers=hdr(client, "public")
        )
    ).json()
    assert grid["type"] == "FeatureCollection" and grid["features"]
    assert {f["properties"]["state"] for f in grid["features"]} <= {"JAMMED", "DEGRADED", "TRUSTED"}
    assert "JAMMED" in {f["properties"]["state"] for f in grid["features"]}

    health = (await client.get("/health")).json()
    assert health["modules"]["worker"] == "present"


async def test_events_endpoint_filters_and_roles(client):  # type: ignore[no-untyped-def]
    a = await _node(client, "a")
    await post(client, packet(rec(seq=1, confidence=30), a), ext="a")
    await post(
        client,
        packet(rec(seq=2, event_class=0, type=0, confidence=2, rx_jamming=False), a),
        ext="a",
    )
    r = hdr(client, "responder")
    assert (await client.get("/v1/events", headers=r)).json()["count"] == 2
    only_jam = (await client.get("/v1/events", params={"class": "jamming"}, headers=r)).json()
    assert only_jam["count"] == 1 and only_jam["items"][0]["position"]["type"] == "Point"
    assert (await client.get("/v1/events", params={"min_confidence": 20}, headers=r)).json()[
        "count"
    ] == 1
    assert (await client.get("/v1/events", params={"bbox": "0,0,1,1"}, headers=r)).json()[
        "count"
    ] == 0
    one = (await client.get(f"/v1/events/{only_jam['items'][0]['id']}", headers=r)).json()
    assert one["class"] == "jamming" and one["data_age_s"] is not None
    assert (await client.get("/v1/events", headers=hdr(client, "public"))).status_code == 403
    assert (await client.get("/v1/events", params={"bbox": "bad"}, headers=r)).status_code == 422


async def test_simulated_data_is_labelled_through_the_whole_pipeline(client):  # type: ignore[no-untyped-def]
    s = await _node(client, "sim-1", kind="sim", transport="sim")
    await post(
        client, packet(rec(seq=1, lat_e7=VARNA[0], lon_e7=VARNA[1]), s), path="sim", ext="sim-1"
    )
    await _run_worker(client)
    h = (await client.get("/v1/hotspots", headers=hdr(client, "responder"))).json()["items"][0]
    t = (
        await client.get(
            "/v1/trust", params={"lat": 43.1989, "lon": 27.9166}, headers=hdr(client, "public")
        )
    ).json()
    assert h["sim"] is True and t["sim"] is True


async def test_stale_evidence_leaves_the_grid(client):  # type: ignore[no-untyped-def]
    a = await _node(client, "a")
    await post(
        client,
        packet(rec(seq=1, ts=now_ts() - 3 * 3600, lat_e7=VARNA[0], lon_e7=VARNA[1]), a),
        ext="a",
    )
    await _run_worker(client)
    t = (
        await client.get(
            "/v1/trust", params={"lat": 43.1989, "lon": 27.9166}, headers=hdr(client, "public")
        )
    ).json()
    assert t["state"] == "UNKNOWN"  # older than the trust window
    assert (await client.get("/v1/hotspots", headers=hdr(client, "responder"))).json()["items"][0][
        "state"
    ] == "closed"


async def test_alerts_are_raised_once_listed_and_streamed(client):  # type: ignore[no-untyped-def]
    from stozher_api.jobs import enqueue
    from stozher_api.routes.alerts import StreamState, poll

    a = await _node(client, "a")
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        state = StreamState(last_id=0, last_refresh=None)
        assert await poll(s, state) == []  # nothing yet; first look records the baseline
    first = rec(seq=1, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1])
    await post(client, packet(first, a), ext="a")
    await _run_worker(client)
    await post(
        client, packet(rec(seq=2, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1]), a), ext="a"
    )
    await _run_worker(client)  # a second report lifts severity over 0.7: escalation
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        from datetime import UTC, datetime

        await enqueue(s, "recompute", datetime.now(UTC))
    await _run_worker(client)  # nothing changed: no repeat alerts

    items = (await client.get("/v1/alerts", headers=hdr(client, "responder"))).json()["items"]
    assert [i["kind"] for i in items] == ["hotspot_escalated", "hotspot_new"]  # newest first
    assert len({i["hotspot_id"] for i in items}) == 1 and items[1]["area"]["type"] == "Polygon"
    assert "interference detected" in items[1]["message"]

    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        events = await poll(s, state)
    assert [e["event"] for e in events] == ["alert", "alert", "refresh"]
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert await poll(s, state) == []  # nothing new on the next poll
    assert (await client.get("/v1/stream/alerts", headers=hdr(client, "public"))).status_code == 403
    assert (await client.get("/v1/alerts", headers=hdr(client, "public"))).status_code == 403


async def test_a_watched_route_alerts_once_when_trouble_arrives_and_clears_when_it_ages_out(client):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from stozher_api.models import WatchedRoute

    route = {
        "name": "Coast road",
        "buffer_m": 300,
        "min_level": "degraded",
        "geometry": {
            "type": "LineString",
            "coordinates": [[27.85, 43.1989], [27.9166, 43.1989], [27.98, 43.1989]],
        },
    }
    r = await client.post("/v1/watched-routes", json=route, headers=hdr(client, "responder"))
    wid = r.json()["id"]
    assert r.json()["overall"]["state"] == "UNKNOWN"
    await _run_worker(client)  # nothing has happened yet
    assert (
        await client.get("/v1/alerts", params={"route_id": wid}, headers=hdr(client, "responder"))
    ).json()["count"] == 0

    a = await _node(client, "a")
    await post(
        client, packet(rec(seq=1, confidence=30, lat_e7=VARNA[0], lon_e7=VARNA[1]), a), ext="a"
    )
    await _run_worker(client)
    await _run_worker(client)  # the second recompute changes nothing: no repeat alert
    items = (
        await client.get("/v1/alerts", params={"route_id": wid}, headers=hdr(client, "responder"))
    ).json()["items"]
    assert [i["kind"] for i in items] == ["route_affected"]
    assert (
        items[0]["route_id"] == wid
        and items[0]["level"] == "critical"
        and items[0]["area"]["type"] == "LineString"
    )
    assert "Route 'Coast road'" in items[0]["message"] and "likely JAMMED" in items[0]["message"]
    status = (
        await client.get(f"/v1/watched-routes/{wid}", headers=hdr(client, "responder"))
    ).json()
    assert status["overall"]["state"] == "JAMMED"
    # the alerts stream can be narrowed to this route
    from stozher_api.routes.alerts import StreamState, poll

    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        import uuid

        mine = await poll(s, StreamState(last_id=0, last_refresh=None, route_id=uuid.UUID(wid)))
        others = await poll(s, StreamState(last_id=0, last_refresh=None, route_id=uuid.uuid4()))
    assert [e["event"] for e in mine] == ["alert"] and others == []

    # two hours later the evidence has aged out of the grid: the route clears
    from stozher_api.jobs import enqueue
    from stozher_worker.main import HANDLERS as H
    from stozher_worker.queue import run_pending as rp

    later = datetime.now(UTC) + timedelta(hours=2)
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await enqueue(s, "recompute", later)
    await rp(client.app.state.sessionmaker, H, now=later)  # type: ignore[attr-defined]
    kinds = [
        i["kind"]
        for i in (
            await client.get(
                "/v1/alerts", params={"route_id": wid}, headers=hdr(client, "responder")
            )
        ).json()["items"]
    ]
    assert kinds == ["route_cleared", "route_affected"]  # newest first

    # expired watches are removed by the worker
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        w = await s.get(WatchedRoute, uuid.UUID(wid))
        w.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # type: ignore[union-attr]
        await s.commit()
        await enqueue(s, "recompute", datetime.now(UTC))
    await _run_worker(client)
    assert (await client.get("/v1/watched-routes", headers=hdr(client, "responder"))).json()[
        "count"
    ] == 0
