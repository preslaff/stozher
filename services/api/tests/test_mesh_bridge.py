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
