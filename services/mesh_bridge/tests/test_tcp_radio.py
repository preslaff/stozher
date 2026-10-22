"""A gateway on the network: `--port tcp:<host>[:<port>]` (Meshtastic's client API over Wi-Fi)."""

import sys
import types
from typing import Any

import pytest
from mesh_bridge.meshtastic_radio import MeshtasticRadio, parse_tcp_port


def test_tcp_ports_are_parsed_and_serial_paths_are_left_alone() -> None:
    assert parse_tcp_port("/dev/serial/by-id/usb-Silicon_Labs_CP2104_02066B17-if00-port0") is None
    assert parse_tcp_port("tcp:192.168.4.20") == ("192.168.4.20", 4403)
    assert parse_tcp_port("tcp:gateway-433.local:4404") == ("gateway-433.local", 4404)
    for bad in ("tcp:", "tcp::4403", "tcp:host:0", "tcp:host:http", "tcp:a b", "tcp:host:70000"):
        with pytest.raises(ValueError):
            parse_tcp_port(bad)


class _Iface:
    made: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, kind: str, **kw: Any) -> None:
        self.made.append((kind, kw))
        self.myInfo = types.SimpleNamespace(my_node_num=0x06E4365B)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_meshtastic(monkeypatch):  # type: ignore[no-untyped-def]
    """Stand-ins for the GPL library's interfaces and pubsub, so this runs without the `radio` extra."""
    _Iface.made = []
    tcp = types.ModuleType("meshtastic.tcp_interface")
    tcp.TCPInterface = lambda **kw: _Iface("tcp", **kw)  # type: ignore[attr-defined]
    ser = types.ModuleType("meshtastic.serial_interface")
    ser.SerialInterface = lambda **kw: _Iface("serial", **kw)  # type: ignore[attr-defined]
    pubsub = types.ModuleType("pubsub")
    pubsub.pub = types.SimpleNamespace(subscribe=lambda *a: None, unsubscribe=lambda *a: None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "meshtastic", types.ModuleType("meshtastic"))
    monkeypatch.setitem(sys.modules, "meshtastic.tcp_interface", tcp)
    monkeypatch.setitem(sys.modules, "meshtastic.serial_interface", ser)
    monkeypatch.setitem(sys.modules, "pubsub", pubsub)
    return _Iface


def test_a_tcp_port_connects_over_the_network_and_warns(fake_meshtastic, caplog) -> None:  # type: ignore[no-untyped-def]
    r = MeshtasticRadio("tcp:192.168.4.20")
    r.start(lambda m: None)
    assert fake_meshtastic.made == [("tcp", {"hostname": "192.168.4.20", "portNumber": 4403})]
    assert r.alive and r.node_id == "!06e4365b"
    assert "no authentication" in caplog.text
    r.close()


def test_a_serial_path_still_opens_the_serial_port(fake_meshtastic) -> None:  # type: ignore[no-untyped-def]
    path = "/dev/serial/by-id/usb-Silicon_Labs_CP2104_02066B17-if00-port0"
    r = MeshtasticRadio(path)
    r.start(lambda m: None)
    assert fake_meshtastic.made == [("serial", {"devPath": path})]
    r.close()
