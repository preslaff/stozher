"""A link that has gone completely quiet is reopened (a gateway's client API can stop delivering while the port stays
open), and the gateway's own log is kept for diagnosis without anything that could hold a position or key."""

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from mesh_bridge.app import RadioSupervisor
from mesh_bridge.meshtastic_radio import MeshtasticRadio
from mesh_bridge.radio import FakeRadio


class Clocks:
    def __init__(self) -> None:
        self.mono = self.boot = 1000.0

    def run(self, s: float) -> None:
        self.mono += s
        self.boot += s


class PacketRadio(FakeRadio):
    """A radio that reports its last packet of any kind, as MeshtasticRadio does."""

    last_packet_mono: float | None = None


def supervisor(clocks: Clocks, quiet_after_s: float, radio: FakeRadio) -> RadioSupervisor:
    sup = RadioSupervisor(
        type(radio),
        lambda m: None,
        stale_after_s=3 * 3600,
        quiet_after_s=quiet_after_s,
        mono=lambda: clocks.mono,
        boot=lambda: clocks.boot,
    )
    sup.radio = radio
    radio.start(sup.on_text)
    return sup


def test_no_packet_at_all_for_the_quiet_time_reopens_the_link_once() -> None:
    clocks = Clocks()
    radio = PacketRadio()
    sup = supervisor(clocks, 2700, radio)
    clocks.run(2000)
    radio.last_packet_mono = (
        clocks.mono
    )  # the gateway's telemetry: not a text, but the link is alive
    clocks.run(2000)
    assert sup.stale_reason() is None and sup.last_packet_age_s == 2000
    clocks.run(701)
    reason = sup.stale_reason()
    assert reason is not None and "no packet of any kind" in reason
    sup.reopen(reason)
    assert sup.reconnects == 1 and not sup.alive


def test_quiet_detection_is_off_with_zero_or_for_radios_that_do_not_report_packets() -> None:
    clocks = Clocks()
    off = supervisor(clocks, 0, PacketRadio())
    plain = supervisor(clocks, 2700, FakeRadio())  # FakeRadio, SimTcpRadio: no last_packet_mono
    clocks.run(3000)
    assert off.stale_reason() is None and plain.stale_reason() is None
    assert plain.last_packet_age_s is None


@pytest.fixture
def pub(monkeypatch):  # type: ignore[no-untyped-def]
    subs: dict[str, Any] = {}
    fake = types.SimpleNamespace(
        subscribe=lambda f, topic: subs.__setitem__(topic, f),
        unsubscribe=lambda f, topic: subs.pop(topic, None),
    )
    mod = types.ModuleType("pubsub")
    mod.pub = fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pubsub", mod)
    ser = types.ModuleType("meshtastic.serial_interface")
    ser.SerialInterface = lambda **kw: types.SimpleNamespace(  # type: ignore[attr-defined]
        myInfo=types.SimpleNamespace(my_node_num=0xA39FCA13), close=lambda: None
    )
    monkeypatch.setitem(sys.modules, "meshtastic", types.ModuleType("meshtastic"))
    monkeypatch.setitem(sys.modules, "meshtastic.serial_interface", ser)
    return subs


def test_every_packet_counts_as_life_and_the_device_log_drops_private_lines(
    pub, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    log = tmp_path / "device.log"
    r = MeshtasticRadio("/dev/serial/by-id/x", device_log=log)
    r.start(lambda m: None)
    assert {"meshtastic.receive", "meshtastic.receive.text", "meshtastic.log.line"} <= set(pub)
    assert r.last_packet_mono is None
    pub["meshtastic.receive"](packet={"decoded": {"portnum": "TELEMETRY_APP"}}, interface=r._iface)
    assert r.last_packet_mono is not None
    pub["meshtastic.receive"](packet={}, interface=object())  # another interface: ignored
    for line in [
        "INFO | Lost phone connection",
        "DEBUG | position lat=43.1 lon=27.9",
        "WARN | ToPhone queue full",
        "psk set",
    ]:
        pub["meshtastic.log.line"](line=line, interface=r._iface)
    pub["meshtastic.log.line"](line="from another radio", interface=object())
    text = log.read_text()
    assert "Lost phone connection" in text and "ToPhone queue full" in text
    assert "lat=" not in text and "psk" not in text and "another radio" not in text
    r.close()
    assert "meshtastic.log.line" not in pub


def test_without_a_device_log_nothing_is_written(pub, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    r = MeshtasticRadio("/dev/serial/by-id/x")
    r.start(lambda m: None)
    assert "meshtastic.log.line" not in pub
    r.close()
