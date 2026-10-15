import httpx
from sqlalchemy import func, select
from stozher_api.models import Event, RawMessage
from stozher_core.codec import encode_hex
from stozher_core.crypto import derive_keys
from stozher_core.record import EventClass, MsgType, Osnma, WitnessRecord

from .conftest import now_ts

MASTER = bytes(range(16))


def rec(seq: int = 1, ts: int | None = None, **kw: object) -> WitnessRecord:
    base = dict(
        type=MsgType.EVENT,
        seq=seq,
        key_id=0,
        event_class=EventClass.JAMMING,
        confidence=24,
        osnma=Osnma.UNKNOWN,
        stationary=True,
        rx_jamming=True,
        rx_spoofing=False,
        tamper=False,
        mesh=False,
        lat_e7=431989000,
        lon_e7=279166000,
        timestamp=ts or now_ts(),
        duration_s=45,
        cn0_mean=32,
        cn0_drop=14,
        cn0_spread=6,
        agc=181,
    )
    return WitnessRecord(**{**base, **kw})  # type: ignore[arg-type]


def packet(r: WitnessRecord, node_id: int = 1) -> str:
    return encode_hex(r, node_id, derive_keys(MASTER, node_id, r.key_id))


def hdr(c: httpx.AsyncClient, role: str) -> dict[str, str]:
    return {"X-API-Key": c.keys[role]}  # type: ignore[attr-defined]


async def register(
    c: httpx.AsyncClient, kind: str = "expert", ext: str = "dev-1", transport: str = "direct"
) -> int:
    r = await c.post(
        "/v1/nodes",
        headers=hdr(c, "operator"),
        json={
            "name": f"node-{ext}",
            "kind": kind,
            "master_key_hex": MASTER.hex(),
            "transport_ids": [{"transport": transport, "external_id": ext}],
        },
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def post(c: httpx.AsyncClient, hexstr: str, path: str = "direct", ext: str = "dev-1"):  # type: ignore[no-untyped-def]
    body = {"external_id": ext, "payload_hex": hexstr}
    if path == "mesh":
        body = {"origin_node": ext, "payload_hex": hexstr}
    return await c.post(f"/v1/ingest/{path}", headers=hdr(c, "system"), json=body)


async def test_health_lists_optional_modules_absent(client):  # type: ignore[no-untyped-def]
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] == "ok" and set(r.json()["modules"].values()) == {"absent"}


async def test_auth_errors_are_problem_details(client):  # type: ignore[no-untyped-def]
    r = await client.get("/v1/me")
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("missing-key")
    r = await client.get("/v1/me", headers={"X-API-Key": "stz_test_abcd1234_wrong"})
    assert r.status_code == 401 and r.json()["type"].endswith("invalid-key")
    r = await client.get("/v1/nodes", headers=hdr(client, "responder"))
    assert r.status_code == 403
    r = await client.post(
        "/v1/ingest/direct",
        headers=hdr(client, "operator"),
        json={"external_id": "x", "payload_hex": "00"},
    )
    assert r.status_code == 403
    assert (await client.get("/v1/me", headers=hdr(client, "admin"))).json()["role"] == "admin"


async def test_register_node_never_returns_key_and_rejects_duplicates(client):  # type: ignore[no-untyped-def]
    node_id = await register(client)
    r = await client.get(f"/v1/nodes/{node_id}", headers=hdr(client, "admin"))
    assert "master" not in r.text and r.json()["transport_ids"][0]["external_id"] == "dev-1"
    dup = await client.post(
        "/v1/nodes",
        headers=hdr(client, "operator"),
        json={
            "name": "again",
            "kind": "expert",
            "master_key_hex": MASTER.hex(),
            "transport_ids": [{"transport": "direct", "external_id": "dev-1"}],
        },
    )
    assert dup.status_code == 409
    bad = await client.post(
        "/v1/nodes",
        headers=hdr(client, "operator"),
        json={"name": "x", "kind": "expert", "master_key_hex": "zz"},
    )
    assert bad.status_code == 422 and bad.headers["content-type"].startswith(
        "application/problem+json"
    )


async def test_accept_then_duplicate_records_both_arrivals(client):  # type: ignore[no-untyped-def]
    await register(client)
    p = packet(rec(seq=5))
    first = (await post(client, p)).json()
    second = (await post(client, p)).json()
    assert first["status"] == "accepted" and second["status"] == "duplicate"
    assert first["event_id"] == second["event_id"]
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(func.count()).select_from(Event))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(RawMessage))).scalar_one() == 2
        ev = (await s.execute(select(Event))).scalar_one()
        assert ev.received_via == "direct" and ev.sim is False and ev.event_class == 1
        lon, lat = (
            await s.execute(select(func.ST_X(Event.position), func.ST_Y(Event.position)))
        ).one()
        assert abs(lat - 43.1989) < 1e-6 and abs(lon - 27.9166) < 1e-6


async def test_out_of_order_sequences_are_both_accepted(client):  # type: ignore[no-untyped-def]
    await register(client)
    assert (await post(client, packet(rec(seq=9)))).json()["status"] == "accepted"
    assert (await post(client, packet(rec(seq=3)))).json()["status"] == "accepted"


async def test_same_seq_different_content_is_rejected(client):  # type: ignore[no-untyped-def]
    await register(client)
    await post(client, packet(rec(seq=2)))
    r = (await post(client, packet(rec(seq=2, confidence=3)))).json()
    assert r == {"status": "rejected", "reason": "seq_reuse", "event_id": None}


async def test_rejections_carry_reasons_and_are_audited(client):  # type: ignore[no-untyped-def]
    await register(client)
    good = packet(rec(seq=1))
    tampered = good[:20] + ("0" if good[20] != "0" else "1") + good[21:]
    cases = [
        (tampered, "dev-1", "bad_tag"),
        (good, "unknown-dev", "unknown_transport_id"),
        ("zz" + good[2:], "dev-1", "bad_hex"),
        (good[:-2], "dev-1", "bad_length"),
        (packet(rec(seq=7, ts=now_ts() - 30 * 86400)), "dev-1", "outside_time_window"),
    ]
    for payload, ext, reason in cases:
        r = await post(client, payload, ext=ext)
        assert r.status_code == 202 and r.json()["status"] == "rejected", reason
        assert r.json()["reason"] == reason
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(func.count()).select_from(Event))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(RawMessage))).scalar_one() == len(
            cases
        )


async def test_mesh_prefix_and_sim_labelling(client):  # type: ignore[no-untyped-def]
    await register(client, ext="!a1b2c3d4", transport="mesh")
    r = await post(client, "STZ1:" + packet(rec(seq=1)), path="mesh", ext="!a1b2c3d4")
    assert r.json()["status"] == "accepted"
    sim_id = await register(client, kind="sim", ext="sim-1", transport="sim")
    ok = await post(client, packet(rec(seq=1), node_id=sim_id), path="sim", ext="sim-1")
    assert ok.json()["status"] == "accepted"
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        flags = dict((await s.execute(select(Event.received_via, Event.sim))).all())
    assert flags == {"mesh": False, "sim": True}


async def test_sim_and_live_nodes_cannot_cross_transports(client):  # type: ignore[no-untyped-def]
    live = await register(client, ext="live-1")
    r = await post(client, packet(rec(seq=1), node_id=live), path="sim", ext="live-1")
    assert r.json()["reason"] == "unknown_transport_id"  # not registered on the sim transport
    sim_id = await register(client, kind="sim", ext="sim-9", transport="direct")
    r = await post(client, packet(rec(seq=1), node_id=sim_id), path="direct", ext="sim-9")
    assert r.json()["reason"] == "sim_mismatch"


async def test_the_api_can_serve_the_built_dashboard_with_range_requests(database, tmp_path):  # type: ignore[no-untyped-def]
    """A field kit serves the dashboard and the offline map from the API's own origin (docs/field-kit.md)."""
    from sqlalchemy.pool import NullPool
    from stozher_api.config import Settings
    from stozher_api.main import create_app

    (tmp_path / "basemap").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>kit</title>")
    blob = bytes(range(256)) * 64  # 16 KiB stands in for the map file
    (tmp_path / "basemap" / "map.pmtiles").write_bytes(blob)
    settings = Settings(
        database_url=database, stozher_kek="ab" * 32, stozher_dashboard_dir=str(tmp_path)
    )
    app = create_app(settings, poolclass=NullPool)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            page = await c.get("/")
            assert page.status_code == 200 and "<title>kit</title>" in page.text
            part = await c.get("/basemap/map.pmtiles", headers={"Range": "bytes=100-199"})
            assert part.status_code == 206 and part.content == blob[100:200]
            assert part.headers["content-range"] == f"bytes 100-199/{len(blob)}"
            assert (await c.get("/health")).json()["db"] == "ok"  # the API routes still win
            assert (await c.get("/v1/me")).status_code == 401


async def test_modules_report_present_from_heartbeats_and_absent_when_stale(client):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update
    from stozher_api.models import ModuleHeartbeat

    async def modules() -> dict[str, str]:
        return (await client.get("/health")).json()["modules"]  # type: ignore[no-any-return]

    assert (await modules())["mesh_bridge"] == "absent"
    beat = {"module": "mesh_bridge", "info": {"radio_connected": True, "node_id": "!604e1da4"}}
    for role in ("operator", "responder", "public"):  # only the machine lane may report
        r = await client.post("/v1/system/heartbeat", headers=hdr(client, role), json=beat)
        assert r.status_code == 403, role
    assert (await client.post("/v1/system/heartbeat", json=beat)).status_code == 401
    bad = {"module": "worker", "info": {}}  # the worker beats through the database, not the API
    r = await client.post("/v1/system/heartbeat", headers=hdr(client, "system"), json=bad)
    assert r.status_code == 422
    r = await client.post("/v1/system/heartbeat", headers=hdr(client, "system"), json=beat)
    assert r.status_code == 204
    assert (await modules())["mesh_bridge"] == "present"

    no_radio = {"module": "mesh_bridge", "info": {"radio_connected": False}}
    await client.post("/v1/system/heartbeat", headers=hdr(client, "system"), json=no_radio)
    assert (await modules())["mesh_bridge"] == "absent"  # alive but no radio is not a bridge

    await client.post("/v1/system/heartbeat", headers=hdr(client, "system"), json=beat)
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await s.execute(
            update(ModuleHeartbeat).values(last_seen=datetime.now(UTC) - timedelta(minutes=5))
        )
        await s.commit()
    assert (await modules())["mesh_bridge"] == "absent"  # silent for minutes


async def test_the_worker_heartbeat_makes_the_worker_present(client):  # type: ignore[no-untyped-def]
    from stozher_api.heartbeat import beat

    assert (await client.get("/health")).json()["modules"]["worker"] == "absent"
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await beat(s, "worker")
    assert (await client.get("/health")).json()["modules"]["worker"] == "present"


async def test_patch_node_renames_retires_and_surveys_and_never_touches_kind_or_keys(client):  # type: ignore[no-untyped-def]
    node = await register(client, kind="expert", ext="p1")
    url = f"/v1/nodes/{node}"
    op = hdr(client, "operator")
    r = await client.patch(url, headers=op, json={"name": "Quay 3", "mode": "fixed"})
    assert r.status_code == 200 and r.json()["name"] == "Quay 3" and r.json()["kind"] == "expert"
    r = await client.patch(url, headers=op, json={"surveyed_lat": 43.2, "surveyed_lon": 27.9})
    assert r.json()["position"]["type"] == "Point"
    r = await client.patch(url, headers=op, json={"surveyed_lat": None, "surveyed_lon": None})
    assert r.status_code == 200 and r.json()["position"] is None  # explicit null clears
    # what may not change, and what is malformed
    for bad in (
        {"kind": "phone"},
        {"master_key_hex": "00" * 16},
        {"transport_ids": []},
        {"id": 5},
        {},
        {"name": None},
        {"status": "gone"},
        {"surveyed_lat": 43.2},
        {"surveyed_lat": 43.2, "surveyed_lon": None},
    ):
        assert (await client.patch(url, headers=op, json=bad)).status_code == 422, bad
    assert (
        await client.patch("/v1/nodes/999999", headers=op, json={"name": "x"})
    ).status_code == 404
    for role in ("responder", "reporter", "public", "system"):
        assert (
            await client.patch(url, headers=hdr(client, role), json={"name": "x"})
        ).status_code == 403
    # retiring stops ingest: the node is inactive, its events stay
    good = packet(rec(seq=1), node_id=node)
    assert (await post(client, good, ext="p1")).json()["status"] == "accepted"
    r = await client.patch(url, headers=op, json={"status": "retired"})
    assert r.json()["status"] == "retired"
    out = (await post(client, packet(rec(seq=2), node_id=node), ext="p1")).json()
    assert out["status"] == "rejected" and out["reason"] == "node_inactive"
    await client.patch(url, headers=op, json={"status": "active"})
    assert (await post(client, packet(rec(seq=3), node_id=node), ext="p1")).json()[
        "status"
    ] == "accepted"
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        from stozher_api.models import AuditLog

        acts = (await s.execute(select(AuditLog.action, AuditLog.target))).all()
    assert sum(1 for a, _ in acts if a == "node.update") == 5
