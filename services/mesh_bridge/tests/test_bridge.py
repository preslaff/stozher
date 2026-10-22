import asyncio
import json
from pathlib import Path

import httpx
import pytest
from mesh_bridge.app import RadioSupervisor
from mesh_bridge.forwarder import Forwarder
from mesh_bridge.heartbeat import HeartbeatPusher
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


class Clocks:
    """Monotonic and boot clocks under test control: sleeping advances only the boot clock."""

    def __init__(self) -> None:
        self.mono = self.boot = 1000.0

    def run(self, s: float) -> None:
        self.mono += s
        self.boot += s

    def sleep_machine(self, s: float) -> None:
        self.boot += s


def started_supervisor(clocks: Clocks, stale_after_s: float = 600.0) -> RadioSupervisor:
    sup = RadioSupervisor(
        FakeRadio,
        lambda m: None,
        stale_after_s=stale_after_s,
        mono=lambda: clocks.mono,
        boot=lambda: clocks.boot,
    )
    sup.radio = FakeRadio()
    sup.radio.start(sup.on_text)
    return sup


def test_a_healthy_link_is_left_alone() -> None:
    clocks = Clocks()
    sup = started_supervisor(clocks)
    for _ in range(10):
        clocks.run(50)
        sup.radio.inject(msg("STZ1:" + HEX28))  # type: ignore[union-attr]
        assert sup.stale_reason() is None
    assert sup.reconnects == 0


def test_the_link_is_reopened_after_the_machine_sleeps() -> None:
    clocks = Clocks()
    sup = started_supervisor(clocks)
    assert sup.stale_reason() is None
    clocks.sleep_machine(36000)  # ten hours suspended
    clocks.run(5)
    reason = sup.stale_reason()
    assert reason is not None and "slept" in reason
    sup.reopen(reason)
    assert not sup.alive and sup.reconnects == 1 and sup.last_reconnect_reason == reason
    assert sup.stale_reason() is None  # the sleep is counted once


def test_the_link_is_reopened_after_a_long_silence_unless_switched_off() -> None:
    clocks = Clocks()
    sup = started_supervisor(clocks, stale_after_s=600)
    clocks.run(599)
    assert sup.stale_reason() is None
    clocks.run(2)
    assert "no text" in (sup.stale_reason() or "")
    off = started_supervisor(clocks, stale_after_s=0)
    clocks.run(100000)
    assert off.stale_reason() is None


async def test_supervisor_reconnects_after_a_sleep() -> None:
    clocks = Clocks()
    opened = {"n": 0}

    class Counting(FakeRadio):
        def start(self, on_text):  # type: ignore[no-untyped-def]
            opened["n"] += 1
            super().start(on_text)

    sup = RadioSupervisor(
        Counting, lambda m: None, retry_s=0.01, mono=lambda: clocks.mono, boot=lambda: clocks.boot
    )
    task = asyncio.create_task(sup.run())
    async with asyncio.timeout(2):
        while opened["n"] < 1:
            await asyncio.sleep(0.01)
    clocks.sleep_machine(3600)
    async with asyncio.timeout(2):
        while opened["n"] < 2:
            await asyncio.sleep(0.01)
    assert sup.reconnects == 1 and sup.alive
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_stats_do_not_shadow_the_queue_length() -> None:
    f = Forwarder(client(lambda r: httpx.Response(503)), "K")
    f.submit(msg("STZ1:" + HEX28))
    assert "queued" not in f.stats and f.stats["enqueued"] == 1 and len(f.queue) == 1


# ---- acknowledgement pusher ------------------------------------------------------------------

from mesh_bridge.acks import AckPusher  # noqa: E402

ACK_ITEM = {"text": "STZA1:" + "ab" * 79, "key_id": 0}


def pusher(handler, radio: FakeRadio | None = None, now=None, **kw):  # type: ignore[no-untyped-def]
    radio = radio or FakeRadio()
    if not radio.alive:
        radio.start(lambda m: None)
    clock = (lambda: now[0]) if now is not None else (lambda: 0.0)
    return AckPusher(client(handler), "K", radio, clock=clock, sleep=no_sleep, **kw), radio


async def test_ack_is_sent_directly_to_the_node_after_a_delay() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": [ACK_ITEM]})

    now = [0.0]
    p, radio = pusher(handler, now=now, delay_s=15)
    p.note("!a1b2c3d4")
    assert await p.tick() == 0 and not radio.sent  # not due yet: records are coalesced first
    now[0] = 16
    assert await p.tick() == 1
    assert radio.sent == [(ACK_ITEM["text"], 0)] and radio.sent_to == [
        "!a1b2c3d4"
    ]  # a direct message
    assert (
        seen[0].url.params["transport"] == "mesh"
        and seen[0].url.params["external_id"] == "!a1b2c3d4"
    )
    assert seen[0].headers["x-api-key"] == "K"


async def test_many_records_make_one_ack_and_the_rate_limit_holds() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"items": [ACK_ITEM]})

    now = [0.0]
    p, radio = pusher(handler, now=now, delay_s=10, min_interval_s=120)
    for _ in range(5):
        p.note("!a1b2c3d4")  # five records, or repeats from a node that missed the last ack
    now[0] = 10
    await p.tick()
    assert calls == 1 and len(radio.sent) == 1
    p.note("!a1b2c3d4")  # more traffic right after: must wait for the per-node interval
    now[0] = 30
    assert await p.tick() == 0
    now[0] = 130
    assert await p.tick() == 1 and calls == 2
    p.note("!ffffffff")  # other nodes are limited independently
    now[0] = 145
    assert await p.tick() == 1


async def test_unregistered_node_signing_off_and_errors() -> None:
    now = [0.0]
    codes = iter([404, 503])
    p, radio = pusher(lambda r: httpx.Response(next(codes)), now=now, delay_s=0)
    p.note("!00000001")
    assert (
        await p.tick() == 1 and not radio.sent and p.disabled_reason is None
    )  # nothing to acknowledge
    p.note("!00000002")
    assert await p.tick() == 1 and p.disabled_reason and "STOZHER_ED25519_SEED" in p.disabled_reason
    p.note("!00000003")
    assert p.due == {}  # with signing off the pusher stops scheduling at all


async def test_outage_or_a_down_radio_retries_later_and_loses_nothing() -> None:
    state = {"fail": True}

    def handler(req: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"items": [ACK_ITEM]})

    now = [0.0]
    p, radio = pusher(handler, now=now, delay_s=0, retry_s=30)
    p.note("!a1b2c3d4")
    assert await p.tick() == 0 and p.fetch_errors == 1 and "!a1b2c3d4" in p.due
    state["fail"] = False
    radio.close()  # the radio dropped as well
    now[0] = 31
    assert await p.tick() == 0 and not radio.sent and "!a1b2c3d4" in p.due
    radio.start(lambda m: None)
    now[0] = 62
    assert await p.tick() == 1 and len(radio.sent) == 1


async def test_forwarder_notifies_only_for_handled_records() -> None:
    answers = iter(["accepted", "duplicate", "rejected"])
    noted: list[tuple[str, str]] = []
    f = Forwarder(
        client(lambda r: httpx.Response(202, json={"status": next(answers), "reason": "bad_tag"})),
        "K",
        sleep=no_sleep,
        on_handled=lambda n, s: noted.append((n, s)),
    )
    for i in range(3):
        f.submit(msg("STZ1:" + f"{i + 1:02x}" * 28, from_id=f"!0000000{i}"))
    await drain(f)
    assert noted == [
        ("!00000000", "accepted"),
        ("!00000001", "duplicate"),
    ]  # a rejection earns no ack


def test_direct_messages_ask_for_a_link_layer_ack_but_broadcasts_do_not() -> None:
    """The shared LoRa channel drops many single-shot packets (measured on the bench), so a message
    for one node uses Meshtastic's own retransmit-until-acknowledged mode. Alerts are broadcasts."""
    from unittest.mock import MagicMock

    from mesh_bridge.meshtastic_radio import MeshtasticRadio

    radio = MeshtasticRadio("/dev/null")
    radio._iface = MagicMock()
    radio.send_text("STZA1:00", 0, "!bdf0a4b0")
    radio.send_text("STOZHER CRITICAL: x", 0)
    direct, broadcast = radio._iface.sendText.call_args_list
    assert direct.kwargs == {"destinationId": "!bdf0a4b0", "wantAck": True, "channelIndex": 0}
    assert broadcast.kwargs == {"destinationId": "^all", "wantAck": False, "channelIndex": 0}


async def test_heartbeat_reports_the_radio_state_and_survives_an_api_that_is_away() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]
    up = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if not up["on"]:
            raise httpx.ConnectError("api away")
        seen.append({"key": request.headers["x-api-key"], "body": json.loads(request.content)})
        return httpx.Response(204)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")
    state = {"radio_connected": True, "node_id": "!604e1da4"}
    beats = HeartbeatPusher(http, "k", lambda: dict(state))
    assert await beats.beat()
    assert seen[0]["body"] == {"module": "mesh_bridge", "info": state} and seen[0]["key"] == "k"
    state["radio_connected"] = False
    await beats.beat()
    assert seen[1]["body"]["info"]["radio_connected"] is False
    up["on"] = False
    assert not await beats.beat() and beats.last_error == "ConnectError"
    up["on"] = True
    assert await beats.beat() and beats.last_error is None and (beats.sent, beats.errors) == (3, 1)
    named = HeartbeatPusher(http, "k2", lambda: dict(state), instance="433")
    assert await named.beat()  # a second bridge, e.g. the 433 MHz gateway, names itself
    assert seen[-1]["body"] == {"module": "mesh_bridge", "info": state, "instance": "433"}
    assert seen[-1]["key"] == "k2"


def test_the_bridge_name_is_checked_on_the_command_line() -> None:
    import argparse

    import pytest
    from mesh_bridge.cli import _bridge_name

    assert _bridge_name("433") == "433" and _bridge_name("") is None
    for bad in ("main", "Band 433", "x" * 17):
        with pytest.raises(argparse.ArgumentTypeError):
            _bridge_name(bad)
