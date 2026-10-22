"""The simulated radio (one LoRa band over loopback TCP) and the bridge's sim mode."""

import json
import socket
import time

import httpx
import pytest
from mesh_bridge.acks import AckPusher
from mesh_bridge.forwarder import Forwarder
from mesh_bridge.protocol import MeshText
from mesh_bridge.sim_radio import SimTcpRadio, parse_port
from test_bridge import client, drain, no_sleep


def wait_for(cond, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_only_loopback_ports_are_simulated() -> None:
    assert parse_port("/dev/ttyUSB0") is None and parse_port("fake") is None
    assert parse_port("sim-tcp:127.0.0.1:47868") == ("127.0.0.1", 47868)
    for bad in ("sim-tcp:0.0.0.0:47868", "sim-tcp:192.168.1.4:47868", "sim-tcp:127.0.0.1:80"):
        with pytest.raises(ValueError):
            parse_port(bad)


def test_lines_come_in_as_mesh_texts_and_acks_go_back_to_the_right_band_radio() -> None:
    got: list[MeshText] = []
    radio = SimTcpRadio("127.0.0.1", 0)
    radio.start(got.append)
    try:
        a = socket.create_connection(("127.0.0.1", radio.bound_port))
        b = socket.create_connection(("127.0.0.1", radio.bound_port))
        a.sendall(b"SIMID:dual-1-868\nSTZ1:" + b"ab" * 28 + b"\n")
        b.sendall(b"SIMID:dual-1-433\nhello chat\n")
        assert wait_for(lambda: len(got) == 2)
        assert {(m.from_id, m.text[:5]) for m in got} == {
            ("dual-1-868", "STZ1:"),
            ("dual-1-433", "hello"),
        }
        assert {n["id"] for n in radio.known_nodes()} == {"dual-1-868", "dual-1-433"}
        radio.send_text("STZA1:00", dest="dual-1-433")  # an ack for the 433 radio only
        b.settimeout(2)
        assert b.recv(100) == b"STZA1:00\n"
        a.settimeout(0.2)
        with pytest.raises(socket.timeout):
            a.recv(100)
        a.close()
        b.close()
    finally:
        radio.close()


def test_a_connection_without_simid_first_is_dropped() -> None:
    got: list[MeshText] = []
    radio = SimTcpRadio("127.0.0.1", 0)
    radio.start(got.append)
    try:
        c = socket.create_connection(("127.0.0.1", radio.bound_port))
        c.sendall(b"STZ1:" + b"ab" * 28 + b"\n")
        c.settimeout(2)
        assert c.recv(10) == b""  # closed by the radio
        assert got == []
    finally:
        radio.close()


async def test_sim_mode_posts_to_sim_ingest_never_to_live_ingest() -> None:
    seen: list[tuple[str, dict]] = []  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, json.loads(req.content)))
        return httpx.Response(202, json={"status": "accepted", "reason": None})

    f = Forwarder(client(handler), "K", sleep=no_sleep, sim=True)
    assert f.submit(MeshText(from_id="dual-1-433", text="STZ1:" + "cd" * 28))
    await drain(f)
    assert f.stats["accepted"] == 1
    assert seen == [("/v1/ingest/sim", {"external_id": "dual-1-433", "payload_hex": "cd" * 28})]


async def test_sim_mode_asks_for_acks_as_transport_sim() -> None:
    asked: list[dict] = []  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        asked.append(dict(req.url.params))
        return httpx.Response(200, json={"items": [{"text": "STZA1:00"}]})

    class Sender:
        alive = True
        sent: list[tuple[str, int, str | None]] = []

        def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None:
            self.sent.append((text, channel, dest))

    radio = Sender()
    p = AckPusher(client(handler), "K", radio, transport="sim")
    assert await p.push("dual-1-868")
    assert asked == [{"transport": "sim", "external_id": "dual-1-868"}]
    assert radio.sent == [("STZA1:00", 0, "dual-1-868")]
