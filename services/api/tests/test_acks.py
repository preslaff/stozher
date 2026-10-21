import json
from pathlib import Path

from stozher_core.control import ack_from_text, public_key_from_bytes, verify_ack

from .test_api import hdr, packet, post, rec, register

VECTORS = json.loads((Path(__file__).parents[3] / "tests/vectors/ack_messages.json").read_text())
TRUSTED = {1: public_key_from_bytes(bytes.fromhex(VECTORS["public_key_hex"]))}


async def ack_for(c, node_id: int, **params):  # type: ignore[no-untyped-def]
    return await c.get("/v1/acks", params={"node_id": node_id, **params}, headers=hdr(c, "system"))


async def test_ack_is_signed_contiguous_and_selective(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c)
    for seq in (1, 2, 3, 5, 7):  # 4 and 6 are missing (lost on a satellite link)
        assert (await post(c, packet(rec(seq=seq), node))).json()["status"] == "accepted"
    r = await ack_for(c, node)
    assert r.status_code == 200
    body = r.json()
    assert (
        body["count"] == 1
        and body["sources"] == ["witness_nodes"]
        and body["data_age_s"] is not None
    )
    item = body["items"][0]
    assert (item["node_id"], item["key_id"], item["max_seq"]) == (node, 0, 3)
    assert item["mask"] == 0b101  # sequences 5 and 7 are max_seq + 2 and + 4 -> bits 0 and 2
    assert item["sim"] is False and item["cloud_key_id"] == 1

    ack = verify_ack(bytes.fromhex(item["message_hex"]), TRUSTED)  # what a node would do
    assert (ack.node_id, ack.max_seq, ack.mask) == (node, 3, 0b101)
    assert ack_from_text(item["text"]) == bytes.fromhex(item["message_hex"])
    assert [s for s in range(1, 9) if ack.covers(s)] == [1, 2, 3, 5, 7]


async def test_ack_is_stable_until_the_state_changes(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c)
    await post(c, packet(rec(seq=1), node))
    first = (await ack_for(c, node)).json()["items"][0]
    again = (await ack_for(c, node)).json()["items"][0]
    assert (
        first["message_hex"] == again["message_hex"]
    )  # same signature and time: a gateway can repeat it
    await post(c, packet(rec(seq=2), node))
    newer = (await ack_for(c, node)).json()["items"][0]
    assert newer["max_seq"] == 2 and newer["message_hex"] != first["message_hex"]
    assert verify_ack(bytes.fromhex(newer["message_hex"]), TRUSTED).max_seq == 2


async def test_lookup_by_transport_and_per_key_id(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c, transport="mesh", ext="!a1b2c3d4")
    await post(c, packet(rec(seq=1), node), path="mesh", ext="!a1b2c3d4")
    r = await c.get(
        "/v1/acks",
        params={"transport": "mesh", "external_id": "!a1b2c3d4"},
        headers=hdr(c, "system"),
    )
    assert r.status_code == 200 and r.json()["items"][0]["node_id"] == node
    assert (await ack_for(c, node, key_id=3)).json()["count"] == 0  # no records under that key id


async def test_no_records_means_no_ack_and_unknown_nodes_404(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c)
    assert (await ack_for(c, node)).json()["items"] == []
    assert (await ack_for(c, 999999)).status_code == 404
    assert (await c.get("/v1/acks", headers=hdr(c, "system"))).status_code == 422  # which node?


async def test_only_the_system_role_may_fetch_acks(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c)
    for role in ("public", "responder", "operator", "admin"):
        assert (await ack_for(c, node)).status_code == 200  # system works
        r = await c.get("/v1/acks", params={"node_id": node}, headers=hdr(c, role))
        assert r.status_code == 403, role
    assert (await c.get("/v1/acks", params={"node_id": node})).status_code == 401


async def test_public_key_and_health_reflect_the_signing_module(signed_client, client):  # type: ignore[no-untyped-def]
    pk = (await signed_client.get("/v1/control/public-key")).json()
    assert pk == {
        "algorithm": "Ed25519",
        "cloud_key_id": 1,
        "public_key_hex": VECTORS["public_key_hex"],
    }
    assert (await signed_client.get("/health")).json()["modules"]["ack_signing"] == "present"
    # Without a key the optional module is absent and acks degrade to a clear 503; nothing else breaks.
    assert (await client.get("/health")).json()["modules"]["ack_signing"] == "absent"
    assert (await client.get("/v1/control/public-key")).status_code == 503
    r = await client.get("/v1/acks", params={"node_id": 1}, headers=hdr(client, "system"))
    assert r.status_code == 503 and r.json()["type"].endswith("signing-not-configured")
    assert (await client.get("/v1/events", headers=hdr(client, "responder"))).status_code == 200


async def test_sim_nodes_get_labelled_acks_and_reset_removes_them(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    sim_id = await register(c, kind="sim", ext="sim-1", transport="sim")
    await post(c, packet(rec(seq=1), sim_id), path="sim", ext="sim-1")
    item = (await ack_for(c, sim_id)).json()["items"][0]
    assert item["sim"] is True
    assert (await c.post("/v1/sim/reset", headers=hdr(c, "operator"))).status_code == 200
    assert (await ack_for(c, sim_id)).status_code == 404


async def test_node_listing_says_what_is_held_and_whether_the_node_was_told(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c)

    async def delivery() -> dict:  # type: ignore[type-arg]
        items = (await c.get("/v1/nodes", headers=hdr(c, "operator"))).json()
        return next(n for n in items if n["id"] == node)["delivery"]

    assert (await c.get("/v1/nodes", headers=hdr(c, "operator"))).json()[0]["delivery"] is None
    for seq in (1, 2, 3, 5, 7):
        await post(c, packet(rec(seq=seq), node))
    d = await delivery()
    assert (d["highest_seq"], d["contiguous_seq"], d["missing"], d["records"]) == (7, 3, 2, 5)
    assert d["ack_current"] is None and d["last_ack_at"] is None  # never told
    assert (d["acked_through"], d["unacked"]) == (None, 5)
    await ack_for(c, node)
    d = await delivery()
    assert d["ack_current"] is True and d["last_ack_at"] is not None
    await post(c, packet(rec(seq=4), node))  # a gap is filled after the ack was signed
    d = await delivery()
    assert d["ack_current"] is False and d["contiguous_seq"] == 5 and d["missing"] == 1
    assert (d["acked_through"], d["unacked"]) == (7, 1)  # the old ack covered 1-3, 5, 7; 4 is new


async def test_a_missing_first_record_pins_the_ack_and_the_listing_shows_it(signed_client):  # type: ignore[no-untyped-def]
    c = signed_client
    node = await register(c)
    for seq in range(2, 31):  # sequence 1 never arrives
        await post(c, packet(rec(seq=seq), node))
    await ack_for(c, node)
    d = next(
        n for n in (await c.get("/v1/nodes", headers=hdr(c, "operator"))).json() if n["id"] == node
    )["delivery"]
    assert (d["contiguous_seq"], d["acked_through"], d["unacked"], d["ack_current"]) == (
        0,
        17,
        13,
        True,
    )
