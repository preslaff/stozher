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
