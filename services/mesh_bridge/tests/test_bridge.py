import asyncio
import json
from pathlib import Path

import httpx
import pytest
from mesh_bridge.app import RadioSupervisor
from mesh_bridge.forwarder import Forwarder
from mesh_bridge.protocol import (
    MeshText,
    alert_text,
    extract_record,
    hops_used,
    node_id,
    to_mesh_text,
)
from mesh_bridge.radio import FakeRadio
from mesh_bridge.relay import AlertRelay, parse_sse

HEX28 = "ab" * 28
HEX32 = "cd" * 32


def msg(text: str, from_id: str = "!a1b2c3d4") -> MeshText:
    return MeshText(from_id=from_id, text=text, hops=1)


# ---- protocol --------------------------------------------------------------------------------


def test_extract_record_accepts_core_and_extended_and_ignores_chat() -> None:
    assert extract_record("STZ1:" + HEX28) == HEX28
    assert extract_record("  stz1:" + HEX32.upper() + "\n") == HEX32
    for bad in (
        "hello mesh",
        "STZ1:",
        "STZ1:" + HEX28[:-2],
        "STZ1:" + "zz" * 28,
        HEX28,
        "STZ2:" + HEX28,
    ):
        assert extract_record(bad) is None


def test_packet_conversion() -> None:
    p = {
        "fromId": "!A1B2C3D4",
        "from": 0xA1B2C3D4,
        "decoded": {"text": "STZ1:" + HEX28},
        "hopStart": 3,
        "hopLimit": 2,
        "rxSnr": 6.5,
        "rxRssi": -92,
        "channel": 0,
    }
    m = to_mesh_text(p)
    assert m == MeshText("!a1b2c3d4", "STZ1:" + HEX28, hops=1, snr=6.5, rssi=-92, channel=0)
    assert node_id({"from": 255}) == "!000000ff" and node_id({}) is None
    assert hops_used({"hopStart": 3}) is None and hops_used({"hopStart": 1, "hopLimit": 3}) is None
    assert to_mesh_text({"fromId": "!a", "decoded": {"portnum": "TELEMETRY_APP"}}) is None


def test_alert_text_rules() -> None:
    a = {
        "level": "critical",
        "message": "Jamming   interference detected near 43.2, 27.9",
        "sim": False,
    }
    assert alert_text(a) == "STOZHER CRITICAL: Jamming interference detected near 43.2, 27.9"
    assert alert_text({**a, "level": "info"}) is None
    assert alert_text({**a, "sim": True}) is None  # simulated alerts never go over a real radio
    assert alert_text({**a, "sim": True}, relay_sim=True).startswith("[SIM] STOZHER CRITICAL")  # type: ignore[union-attr]
    assert len(alert_text({**a, "message": "x" * 500}).encode()) <= 200  # type: ignore[union-attr]
    assert alert_text({**a, "message": "  "}) is None


# ---- forwarder -------------------------------------------------------------------------------


def client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")


async def drain(f: Forwarder, timeout: float = 2.0) -> None:
    task = asyncio.create_task(f.run())
    try:
        async with asyncio.timeout(timeout):
            while f.queue:
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.01)
    finally:
        f.stop()
        await task


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


async def test_forwards_records_and_counts_outcomes() -> None:
    seen: list[dict] = []
    answers = iter(["accepted", "duplicate", "rejected"])

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/ingest/mesh" and req.headers["x-api-key"] == "K"
        seen.append(json.loads(req.content))
        status = next(answers)
        return httpx.Response(
            202, json={"status": status, "reason": "bad_tag" if status == "rejected" else None}
        )

    f = Forwarder(client(handler), "K", sleep=no_sleep)
    for i, hx in enumerate(("aa", "bb", "cc")):
        assert f.submit(msg("STZ1:" + hx * 28, from_id=f"!0000000{i}"))
    await drain(f)
    assert [s["origin_node"] for s in seen] == ["!00000000", "!00000001", "!00000002"]
    assert seen[0]["hops"] == 1 and seen[0]["payload_hex"] == "aa" * 28
    assert (f.stats["accepted"], f.stats["duplicate"], f.stats["rejected"]) == (1, 1, 1)
    assert f.rejections == {"bad_tag": 1}


async def test_chat_and_repeats_are_not_forwarded() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(202, json={"status": "accepted"})

    f = Forwarder(client(handler), "K", sleep=no_sleep)
    assert not f.submit(msg("anyone at the port?"))
    assert f.submit(msg("STZ1:" + HEX28)) and not f.submit(msg("STZ1:" + HEX28))  # mesh repeat
    assert f.submit(
        msg("STZ1:" + HEX28, from_id="!ffffffff")
    )  # same bytes, other sender: not a repeat
    await drain(f)
    assert calls == 2 and f.stats["ignored"] == 1 and f.stats["deduped"] == 1


async def test_dedupe_entries_expire() -> None:
    now = [0.0]
    f = Forwarder(
        client(lambda r: httpx.Response(202, json={"status": "accepted"})),
        "K",
        clock=lambda: now[0],
        dedupe_s=100,
        sleep=no_sleep,
    )
    assert f.submit(msg("STZ1:" + HEX28))
    now[0] = 101
    assert f.submit(msg("STZ1:" + HEX28))  # old enough to count as a new transmission


async def test_backend_outage_keeps_records_and_retries() -> None:
    state = {"up": False, "calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.ConnectError("down")
        if state["calls"] == 2:
            return httpx.Response(503)
        return httpx.Response(202, json={"status": "accepted"})

    f = Forwarder(client(handler), "K", sleep=no_sleep)
    f.submit(msg("STZ1:" + HEX28))
    await drain(f)
    assert (
        f.stats["retries"] == 2 and f.stats["accepted"] == 1 and not f.queue and state["calls"] == 3
    )


async def test_refused_key_keeps_the_record_but_bad_shape_drops_it() -> None:
    codes = iter([401, 202])
    f = Forwarder(
        client(lambda r: httpx.Response(next(codes), json={"status": "accepted"})),
        "K",
        sleep=no_sleep,
    )
    f.submit(msg("STZ1:" + HEX28))
    await drain(f)
    assert (
        f.stats["retries"] == 1 and f.stats["accepted"] == 1
    )  # waited for a working key, lost nothing
    g = Forwarder(client(lambda r: httpx.Response(422)), "K", sleep=no_sleep)
    g.submit(msg("STZ1:" + HEX28))
    await drain(g)
    assert g.stats["bad_request"] == 1 and not g.queue


def test_full_queue_drops_the_oldest(tmp_path: Path) -> None:
    f = Forwarder(client(lambda r: httpx.Response(500)), "K", max_queue=2)
    for i in range(3):
        f.submit(msg("STZ1:" + f"{i:02x}" * 28, from_id=f"!{i:08x}"))
    assert [q["from_id"] for q in f.queue] == ["!00000001", "!00000002"] and f.stats[
        "dropped_full"
    ] == 1


async def test_spool_survives_a_restart_and_clears_when_done(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    down = Forwarder(client(lambda r: httpx.Response(503)), "K", spool=spool)
    down.submit(msg("STZ1:" + HEX28))
    assert len(spool.read_text().splitlines()) == 1  # written before any delivery attempt
    spool.write_text(spool.read_text() + "{not json\n")  # a damaged line must not block the rest
    up = Forwarder(
        client(lambda r: httpx.Response(202, json={"status": "accepted"})),
        "K",
        spool=spool,
        sleep=no_sleep,
    )
    assert len(up.queue) == 1  # replayed after the "restart"
    await drain(up)
    assert up.stats["accepted"] == 1 and spool.read_text() == ""


# ---- relay and supervisor --------------------------------------------------------------------


def test_relay_rate_limits_and_marks_sim() -> None:
    now = [0.0]
    radio = FakeRadio()
    radio.start(lambda m: None)
    r = AlertRelay(
        client(lambda x: httpx.Response(200)), "K", radio, min_interval_s=60, clock=lambda: now[0]
    )
    a = {"level": "critical", "message": "m1", "sim": False}
    assert r.handle_alert(a) and not r.handle_alert({**a, "message": "m2"})  # second one throttled
    now[0] = 61
    assert r.handle_alert({**a, "message": "m3"})
    assert [t for t, _ in radio.sent] == ["STOZHER CRITICAL: m1", "STOZHER CRITICAL: m3"]
    assert (r.sent, r.throttled) == (2, 1)
    assert r.handle_alert({**a, "sim": True}) is None  # sim alerts stay off the air by default


def test_relay_does_not_send_when_the_radio_is_down() -> None:
    radio = FakeRadio()  # never started: not alive
    r = AlertRelay(client(lambda x: httpx.Response(200)), "K", radio)
    assert r.handle_alert({"level": "critical", "message": "m"}) is None and r.skipped == 1


def test_sse_parser_splits_events_and_keeps_the_tail() -> None:
    ev, rest = parse_sse(
        'event: hello\r\ndata: {"a":1}\r\n\r\nevent: alert\r\ndata: x\r\n\r\nevent: al'
    )
    assert ev == [("hello", '{"a":1}'), ("alert", "x")] and rest == "event: al"


async def test_supervisor_survives_a_missing_radio_and_reconnects() -> None:
    attempts = {"n": 0}
    got: list[MeshText] = []

    class Flaky(FakeRadio):
        def start(self, on_text):  # type: ignore[no-untyped-def]
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("could not open port /dev/ttyUSB0")
            super().start(on_text)

    sup = RadioSupervisor(Flaky, got.append, retry_s=0.01)
    task = asyncio.create_task(sup.run())
    async with asyncio.timeout(2):
        while not sup.alive:
            await asyncio.sleep(0.01)
    assert attempts["n"] == 2 and sup.last_error is None
    sup.radio.inject(msg("hi"))  # type: ignore[union-attr]
    assert got[0].text == "hi"
    sup.radio.close()  # simulate the cable being pulled
    async with asyncio.timeout(2):
        while attempts["n"] < 3:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
