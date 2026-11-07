from stozher_core.record import EventClass, MsgType

from .conftest import now_ts
from .test_api import hdr, packet, post, rec, register


def normal(seq: int, min_ago: int, cn0: int = 35):  # type: ignore[no-untyped-def]
    return rec(
        seq=seq,
        ts=now_ts() - 60 * min_ago,
        type=MsgType.HEARTBEAT,
        event_class=EventClass.NORMAL,
        rx_jamming=False,
        cn0_mean=cn0,
        cn0_drop=0,
        agc=3,
        duration_s=0,
    )


async def test_node_listing_says_how_well_each_node_receives(client):  # type: ignore[no-untyped-def]
    c = client
    good = await register(c, ext="dev-good")
    lost = await register(c, ext="dev-lost")

    async def state(node: int) -> dict:  # type: ignore[type-arg]
        items = (await c.get("/v1/nodes", headers=hdr(c, "operator"))).json()
        return next(n for n in items if n["id"] == node)["reception"]

    assert (await state(good))["state"] == "unknown"  # nothing heard: never "good" by absence
    for seq, m in ((1, 50), (2, 30), (3, 10)):
        assert (await post(c, packet(normal(seq, m), good), ext="dev-good")).json()[
            "status"
        ] == "accepted"
    r = await state(good)
    assert r["state"] == "good" and r["median_cn0"] == 35 and r["window_s"] == 6 * 3600

    await post(c, packet(normal(1, 40), lost), ext="dev-lost")
    benign = rec(
        seq=2,
        ts=now_ts() - 300,
        event_class=EventClass.BENIGN,
        rx_jamming=False,
        no_fix=True,
        cn0_mean=0,
        cn0_drop=0,
        agc=3,
    )
    await post(c, packet(benign, lost), ext="dev-lost")
    r = await state(lost)
    assert (
        r["state"] == "no_reception"
        and r["since"] is not None
        and r["reasons"][0].startswith("no fix since")
    )
    assert (await c.get(f"/v1/nodes/{lost}", headers=hdr(c, "operator"))).json()["reception"][
        "state"
    ] == "no_reception"
