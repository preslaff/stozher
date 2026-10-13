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
