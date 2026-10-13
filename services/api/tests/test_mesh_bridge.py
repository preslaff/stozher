"""A Meshtastic text through the bridge into the real API and database."""

import asyncio

from mesh_bridge.forwarder import Forwarder
from mesh_bridge.protocol import MeshText
from sqlalchemy import func, select
from stozher_api.models import Event, RawMessage

from .test_api import MASTER, hdr, packet, rec


async def register_mesh_node(client, ext: str) -> int:  # type: ignore[no-untyped-def]
    r = await client.post(
        "/v1/nodes",
        headers=hdr(client, "operator"),
        json={
            "name": f"tbeam-{ext}",
            "kind": "tbeam",
            "master_key_hex": MASTER.hex(),
            "transport_ids": [{"transport": "mesh", "external_id": ext}],
        },
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def drain(f: Forwarder) -> None:
    task = asyncio.create_task(f.run())
    async with asyncio.timeout(10):
        while f.queue:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
    f.stop()
    await task


async def test_mesh_text_becomes_a_stored_event_with_chat_ignored(client):  # type: ignore[no-untyped-def]
    node = await register_mesh_node(client, "!a1b2c3d4")
    f = Forwarder(client, client.keys["system"])
    good = "STZ1:" + packet(rec(seq=1, mesh=True), node)
    assert f.submit(MeshText("!a1b2c3d4", good, hops=2))
    assert not f.submit(MeshText("!a1b2c3d4", "lunch at the port?"))
    assert not f.submit(
        MeshText("!a1b2c3d4", good)
    )  # a mesh rebroadcast: the bridge drops it itself
    assert f.submit(
        MeshText("!deadbeef", good)
    )  # a stranger relaying the same bytes: the API rejects it
    forged = packet(rec(seq=2), node)
    assert f.submit(
        MeshText("!a1b2c3d4", "STZ1:" + forged[:20] + "00" + forged[22:])
    )  # bit-flipped in transit
    await drain(f)

    assert (f.stats["accepted"], f.stats["rejected"]) == (1, 2)
    assert f.rejections == {"unknown_transport_id": 1, "bad_tag": 1}
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        ev = (await s.execute(select(Event))).scalar_one()
        assert ev.received_via == "mesh" and ev.sim is False and ev.node_id == node
        assert (await s.execute(select(func.count()).select_from(RawMessage))).scalar_one() == 3
        meta = (
            await s.execute(
                select(RawMessage.transport_meta).where(RawMessage.verify_status == "accepted")
            )
        ).scalar_one()
        assert meta == {"hops": 2}


async def test_mesh_record_leads_to_a_verifiable_signed_ack(signed_client):  # type: ignore[no-untyped-def]
    import json
    from pathlib import Path

    from mesh_bridge.acks import AckPusher
    from mesh_bridge.radio import FakeRadio
    from stozher_core.control import ack_from_text, public_key_from_bytes, verify_ack

    vectors = json.loads(
        (Path(__file__).parents[3] / "tests/vectors/ack_messages.json").read_text()
    )
    trusted = {1: public_key_from_bytes(bytes.fromhex(vectors["public_key_hex"]))}
    c = signed_client
    node = await register_mesh_node(c, "!bdf0a4b0")
    radio = FakeRadio("!bdf0abac")
    radio.start(lambda m: None)
    now = [0.0]
    pusher = AckPusher(c, c.keys["system"], radio, delay_s=5, clock=lambda: now[0])
    f = Forwarder(c, c.keys["system"], on_handled=pusher.note)
    for seq in (1, 2, 4):  # the third record arrived, the second to last was lost on the way
        f.submit(MeshText("!bdf0a4b0", "STZ1:" + packet(rec(seq=seq, mesh=True), node), hops=0))
    await drain(f)
    now[0] = 6
    assert await pusher.tick() == 1

    (text, channel), dest = radio.sent[0], radio.sent_to[0]
    assert dest == "!bdf0a4b0" and channel == 0 and text.startswith("STZA1:")
    ack = verify_ack(ack_from_text(text), trusted)  # exactly what the node does with it
    assert (ack.node_id, ack.key_id, ack.max_seq, ack.mask) == (node, 0, 2, 0b1)
    assert [s for s in range(1, 8) if ack.covers(s)] == [1, 2, 4]  # 3 must be retransmitted
