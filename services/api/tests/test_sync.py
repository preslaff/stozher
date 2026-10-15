"""Field-kit sync: the cloud serves and accepts signed records, the kit pulls and pushes them, nothing unverified gets in."""

import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text
from stozher_api.models import Event, SyncCursor
from stozher_api.sync_service import run_sync

from .conftest import KEK
from .test_api import hdr, packet, rec, register
from .test_mesh_bridge import register_mesh_node


async def age(c: httpx.AsyncClient) -> None:
    """Make stored events old enough to be served (the settle delay)."""
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await s.execute(text("UPDATE events SET ingested_at = now() - interval '1 minute'"))
        await s.commit()


async def ingest_direct(c: httpx.AsyncClient, ext: str, payload: str) -> str:
    r = await c.post(
        "/v1/ingest/direct",
        headers=hdr(c, "system"),
        json={"external_id": ext, "payload_hex": payload},
    )
    return str(r.json()["status"])


async def test_events_are_served_in_cursor_order_without_sim_and_only_to_system(client):  # type: ignore[no-untyped-def]
    node = await register(client, ext="dev-s1")
    for seq in (1, 2, 3):
        assert await ingest_direct(client, "dev-s1", packet(rec(seq=seq), node)) == "accepted"
    r = await client.get("/v1/sync/events", headers=hdr(client, "system"))
    assert r.json()["items"] == []  # too young: may still be committing out of order
    await age(client)
    r = await client.get("/v1/sync/events?limit=2", headers=hdr(client, "system"))
    body = r.json()
    assert len(body["items"]) == 2 and body["has_more"] is True
    assert body["items"][0]["cursor"] < body["items"][1]["cursor"] == body["next_cursor"]
    assert set(body["items"][0]) == {
        "cursor",
        "node_id",
        "payload_hex",
        "received_via",
        "ingested_at",
    }
    r2 = await client.get(
        f"/v1/sync/events?since={body['next_cursor']}", headers=hdr(client, "system")
    )
    assert len(r2.json()["items"]) == 1 and r2.json()["has_more"] is False
    for role in ("operator", "responder", "public", "reporter", "admin"):
        assert (await client.get("/v1/sync/events", headers=hdr(client, role))).status_code == 403
    assert (await client.get("/v1/sync/events")).status_code == 401


async def test_upload_accepts_old_signed_records_and_refuses_the_rest(client):  # type: ignore[no-untyped-def]
    node = await register(client, ext="dev-s2")
    old = int(
        (datetime.now(UTC) - timedelta(days=9)).timestamp()
    )  # far outside the live replay window
    future = int((datetime.now(UTC) + timedelta(days=9)).timestamp())
    good = packet(rec(seq=1, ts=old), node)
    forged = packet(
        rec(seq=2, ts=old), node + 1
    )  # signed for another node ID: the tag does not verify
    ahead = packet(rec(seq=3, ts=future), node)
    records = [
        {"node_id": node, "payload_hex": good, "received_via": "mesh"},
        {"node_id": node, "payload_hex": good, "received_via": "mesh"},
        {"node_id": node, "payload_hex": forged},
        {"node_id": node, "payload_hex": ahead},
        {"node_id": 99999, "payload_hex": good},
    ]
    r = await client.post(
        "/v1/sync/upload", headers=hdr(client, "system"), json={"records": records}
    )
    assert r.status_code == 200, r.text
    got = [(x["status"], x["reason"]) for x in r.json()["results"]]
    assert got == [
        ("accepted", None),
        ("duplicate", None),
        ("rejected", "bad_tag"),
        ("rejected", "outside_time_window"),
        ("rejected", "unknown_transport_id"),
    ]
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        ev = (await s.execute(select(Event))).scalars().all()
    assert len(ev) == 1 and ev[0].origin == "synced" and ev[0].received_via == "mesh"
    big = {"records": [records[0]] * 201}
    assert (
        await client.post("/v1/sync/upload", headers=hdr(client, "system"), json=big)
    ).status_code == 422
    assert (
        await client.post("/v1/sync/upload", headers=hdr(client, "operator"), json={"records": []})
    ).status_code == 403


async def test_sim_nodes_never_sync(client):  # type: ignore[no-untyped-def]
    node = await register(client, kind="sim", ext="sim-1", transport="sim")
    r = await client.post(
        "/v1/sync/upload",
        headers=hdr(client, "system"),
        json={"records": [{"node_id": node, "payload_hex": packet(rec(seq=1), node)}]},
    )
    assert r.json()["results"][0]["reason"] == "sim_mismatch"


def cloud(events: list[dict], upload_log: list[dict], fail: bool = False) -> httpx.AsyncClient:  # type: ignore[type-arg]
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "cloud-key"
        if fail:
            return httpx.Response(503)
        if request.url.path == "/v1/sync/events":
            since = int(request.url.params["since"])
            items = [e for e in events if e["cursor"] > since]
            return httpx.Response(
                200,
                json={
                    "items": items,
                    "next_cursor": items[-1]["cursor"] if items else since,
                    "has_more": False,
                },
            )
        body = json.loads(request.content)
        upload_log.append(body)
        return httpx.Response(
            200, json={"results": [{"status": "accepted", "reason": None} for _ in body["records"]]}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def sync(c: httpx.AsyncClient, http: httpx.AsyncClient):  # type: ignore[no-untyped-def]
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        return await run_sync(
            s,
            http,
            cloud_url="https://cloud.example",
            cloud_key="cloud-key",
            kek=KEK,
            replay_window_s=3600,
        )


async def test_kit_pulls_what_the_cloud_has_and_pushes_only_its_own(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, "!aaaa0001")
    mine = packet(rec(seq=1), node)
    assert (
        await client.post(
            "/v1/ingest/mesh",
            headers=hdr(client, "system"),
            json={"origin_node": "!aaaa0001", "payload_hex": mine},
        )
    ).json()["status"] == "accepted"
    ts = int(
        (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    )  # old: the live window would refuse it
    theirs = {
        "cursor": 7,
        "node_id": node,
        "payload_hex": packet(rec(seq=2, ts=ts), node),
        "received_via": "kineis",
        "ingested_at": "2026-10-15T00:00:00+00:00",
    }
    bad = {**theirs, "cursor": 8, "payload_hex": packet(rec(seq=3, ts=ts), node + 5)}
    await age(client)
    uploads: list[dict] = []  # type: ignore[type-arg]
    stats = await sync(client, cloud([theirs, bad], uploads))
    assert (stats.pulled, stats.pushed, stats.rejected, stats.error) == (1, 1, 1, None)
    assert [r["payload_hex"] for r in uploads[0]["records"]] == [
        mine
    ]  # the pulled event is not sent back
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        pulled = (await s.execute(select(Event).where(Event.seq == 2))).scalar_one()
        assert pulled.origin == "synced" and pulled.received_via == "kineis"
        cur = {c.name: c for c in (await s.execute(select(SyncCursor))).scalars()}
        assert cur["download"].cursor == 8 and cur["upload"].cursor > 0
        assert cur["download"].last_ok_at is not None and cur["download"].last_error is None
    again: list[dict] = []  # type: ignore[type-arg]
    stats = await sync(client, cloud([theirs, bad], again))
    assert (stats.pulled, stats.pushed, again) == (0, 0, [])  # nothing new either way


async def test_unreachable_cloud_is_recorded_and_nothing_is_lost(client):  # type: ignore[no-untyped-def]
    node = await register(client, ext="dev-s3")
    await ingest_direct(client, "dev-s3", packet(rec(seq=1), node))
    await age(client)
    stats = await sync(client, cloud([], [], fail=True))
    assert stats.error is not None and stats.pushed == 0
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        cur = {c.name: c for c in (await s.execute(select(SyncCursor))).scalars()}
    assert cur["upload"].cursor == 0 and cur["upload"].last_error is not None
    uploads: list[dict] = []  # type: ignore[type-arg]
    stats = await sync(client, cloud([], uploads))
    assert stats.pushed == 1 and stats.error is None  # the next round sends it
