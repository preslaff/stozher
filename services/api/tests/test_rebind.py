"""Re-binding a node to a new radio ID after a reflash: only with proof the device holds the node's key."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select, update
from stozher_api.models import AuditLog, Event, RawMessage, TransportId
from stozher_api.sender_proof import verify_sender_proof
from stozher_core.codec import encode_hex
from stozher_core.crypto import derive_keys

from .test_api import MASTER, hdr, packet, rec
from .test_mesh_bridge import register_mesh_node

OLD, NEW = "!bdf0a4b0", "!0b533538"


async def send(c: httpx.AsyncClient, ext: str, text: str) -> str:
    r = await c.post(
        "/v1/ingest/mesh", headers=hdr(c, "system"), json={"origin_node": ext, "payload_hex": text}
    )
    assert r.status_code == 202, r.text
    return str(r.json()["status"]) + ":" + str(r.json().get("reason"))


async def senders(c: httpx.AsyncClient):  # type: ignore[no-untyped-def]
    r = await c.get("/v1/nodes/unrecognised-senders", headers=hdr(c, "operator"))
    assert r.status_code == 200, r.text
    return r.json()


async def rebind(c: httpx.AsyncClient, node: int, ext: str, role: str = "operator"):  # type: ignore[no-untyped-def]
    return await c.post(
        f"/v1/nodes/{node}/transport-ids/rebind",
        headers=hdr(c, role),
        json={"transport": "mesh", "external_id": ext},
    )


def test_proof_is_checked_against_the_nodes_key_and_the_arrival_time() -> None:
    now = datetime.now(UTC)
    r = rec(seq=5, ts=int(now.timestamp()))
    text = packet(r, node_id=7)

    def master(k: int) -> bytes | None:
        return MASTER if k == 0 else None

    assert verify_sender_proof(text, 7, master, now, 3600).ok
    assert verify_sender_proof("STZ1:" + text, 7, master, now, 3600).ok
    assert verify_sender_proof(text, 8, master, now, 3600).reason == "bad_tag"  # another node
    assert verify_sender_proof(text, 7, lambda _: None, now, 3600).reason == "unknown_key"
    assert verify_sender_proof("zz", 7, master, now, 3600).reason == "bad_packet"
    late = now + timedelta(hours=2)
    assert verify_sender_proof(text, 7, master, late, 3600).reason == "outside_time_window"
    other = encode_hex(r, 7, derive_keys(bytes(16), 7, 0))  # a stranger without the key
    assert verify_sender_proof(other, 7, master, now, 3600).reason == "bad_tag"


async def test_reflashed_device_is_listed_with_its_node_then_rebound_and_recovered(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, OLD)
    text = packet(rec(seq=1), node_id=node)
    assert await send(client, NEW, text) == "rejected:unknown_transport_id"

    listed = await senders(client)
    assert len(listed) == 1 and listed[0]["external_id"] == NEW
    assert listed[0]["count"] == 1 and listed[0]["candidate_node_id"] == node
    assert "payload" not in str(listed[0]).lower()

    r = await rebind(client, node, NEW)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["status"] for x in body["recovered"]] == ["accepted"]
    assert [t["external_id"] for t in body["node"]["transport_ids"]] == [NEW]

    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        ev = (await s.execute(select(Event).where(Event.node_id == node))).scalars().all()
        assert len(ev) == 1 and ev[0].received_via == "mesh"
        audit = (
            await s.execute(select(AuditLog.action, AuditLog.target).order_by(AuditLog.id.desc()))
        ).first()
        assert audit is not None and audit.action == "node.rebind_transport"
        assert OLD in audit.target and NEW in audit.target
    assert await senders(client) == []  # bound now: no longer unrecognised
    assert (
        await send(client, OLD, packet(rec(seq=2), node_id=node)) == "rejected:unknown_transport_id"
    )
    assert await send(client, NEW, packet(rec(seq=3), node_id=node)) == "accepted:None"


async def test_no_proof_without_a_packet_from_that_id(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, OLD)
    r = await rebind(client, node, NEW)
    assert r.status_code == 409 and r.json()["type"].endswith("no-proof")


async def test_a_packet_for_another_node_or_without_the_key_is_not_proof(client):  # type: ignore[no-untyped-def]
    a = await register_mesh_node(client, OLD)
    b = await register_mesh_node(client, "!11111111")
    await send(client, NEW, packet(rec(seq=1), node_id=b))  # proves node b, not a
    listed = await senders(client)
    assert listed[0]["candidate_node_id"] == b
    assert (await rebind(client, a, NEW)).status_code == 409
    forged = encode_hex(rec(seq=2), a, derive_keys(bytes(16), a, 0))
    await send(client, "!22222222", forged)
    assert (
        next(x for x in await senders(client) if x["external_id"] == "!22222222")[
            "candidate_node_id"
        ]
        is None
    )
    assert (await rebind(client, a, "!22222222")).status_code == 409


async def test_a_replayed_packet_is_not_proof(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, OLD)
    text = packet(rec(seq=1), node_id=node)
    assert await send(client, OLD, text) == "accepted:None"  # the real device delivered it
    assert await send(client, NEW, text) == "rejected:unknown_transport_id"  # someone repeats it
    r = await rebind(client, node, NEW)
    assert r.status_code == 409 and r.json()["type"].endswith("no-proof")


async def test_a_stale_proof_is_refused(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, OLD)
    await send(client, NEW, packet(rec(seq=1), node_id=node))
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await s.execute(
            update(RawMessage).values(received_at=datetime.now(UTC) - timedelta(days=2))
        )
        await s.commit()
    r = await rebind(client, node, NEW)
    assert r.status_code == 409 and r.json()["type"].endswith("no-proof")


async def test_an_id_owned_by_another_node_cannot_be_taken(client):  # type: ignore[no-untyped-def]
    a = await register_mesh_node(client, OLD)
    await register_mesh_node(client, NEW)
    r = await rebind(client, a, NEW)
    assert r.status_code == 409 and r.json()["type"].endswith("id-in-use")


async def test_only_operators_can_list_or_rebind(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, OLD)
    for role in ("responder", "reporter", "public", "system"):
        r = await client.get("/v1/nodes/unrecognised-senders", headers=hdr(client, role))
        assert r.status_code == 403, (role, r.status_code)
        assert (await rebind(client, node, NEW, role)).status_code == 403
    assert (await client.get("/v1/nodes/unrecognised-senders")).status_code == 401


async def test_simulated_nodes_are_not_rebound(client):  # type: ignore[no-untyped-def]
    r = await client.post(
        "/v1/nodes",
        headers=hdr(client, "operator"),
        json={
            "name": "sim node",
            "kind": "sim",
            "master_key_hex": MASTER.hex(),
            "transport_ids": [{"transport": "sim", "external_id": "s1"}],
        },
    )
    node = int(r.json()["id"])
    assert (await rebind(client, node, NEW)).status_code == 409
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        n = (await s.execute(select(func.count()).select_from(TransportId))).scalar_one()
        assert n == 1
