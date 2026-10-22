"""Real radio through the Meshtastic Python library (GPL-3.0; see LICENSE-NOTE.md).

The library is imported lazily so everything else in the bridge works without it. The gateway is reached over USB serial
(`/dev/serial/by-id/...`) or over the network (`tcp:<host>[:<port>]`, Meshtastic's client API on an ESP32 with Wi-Fi,
port 4403 by default). Meshtastic's network API has no authentication: anyone on that network can read the channel and
change the radio's settings, so use it only on the kit's own private network.
"""

import logging
import re
from collections.abc import Callable
from typing import Any

from .protocol import MeshText, to_mesh_text

log = logging.getLogger("stozher.mesh")

TCP_PREFIX = "tcp:"
TCP_DEFAULT_PORT = 4403  # Meshtastic's client API over the network


def parse_tcp_port(port: str) -> tuple[str, int] | None:
    """`tcp:192.168.4.20` or `tcp:gateway.local:4403` -> (host, port); None for a serial device path."""
    if not port.startswith(TCP_PREFIX):
        return None
    rest = port[len(TCP_PREFIX) :]
    host, sep, num = rest.rpartition(":")
    if not sep:
        host, num = rest, str(TCP_DEFAULT_PORT)
    if not host or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
        raise ValueError(f"not a host name or IPv4 address: {host!r}")
    if not num.isdigit() or not 1 <= int(num) <= 65535:
        raise ValueError(f"not a TCP port: {num!r}")
    return host, int(num)


class MeshtasticRadio:
    def __init__(self, port: str) -> None:
        self.port = port
        self.node_id = "?"
        self._iface: Any = None
        self._alive = False
        self._on_text: Callable[[MeshText], None] | None = None

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self, on_text: Callable[[MeshText], None]) -> None:
        from pubsub import pub

        tcp = parse_tcp_port(self.port)
        self._on_text = on_text
        # Subscribe first so no early packet is missed; weak refs mean we must keep these bound methods.
        pub.subscribe(self._on_receive, "meshtastic.receive.text")
        pub.subscribe(self._on_lost, "meshtastic.connection.lost")
        if tcp is not None:
            from meshtastic.tcp_interface import TCPInterface  # GPL-3.0, imported on use

            log.warning(
                "gateway over the network (%s:%d): Meshtastic's network API has no authentication; "
                "use it only on the kit's private network",
                *tcp,
            )
            self._iface = TCPInterface(hostname=tcp[0], portNumber=tcp[1])
        else:
            from meshtastic.serial_interface import SerialInterface  # GPL-3.0, imported on use

            self._iface = SerialInterface(devPath=self.port)
        info = self._iface.myInfo
        self.node_id = f"!{info.my_node_num:08x}" if info is not None else "?"
        self._alive = True
        log.info("connected to %s as %s", self.port, self.node_id)

    def _on_receive(self, packet: dict[str, Any], interface: Any = None) -> None:
        if interface is not self._iface:
            return
        msg = to_mesh_text(packet)
        if msg is not None and self._on_text is not None:
            self._on_text(msg)

    def _on_lost(self, interface: Any = None) -> None:
        if interface is self._iface:
            if self._alive:  # not when we closed the port ourselves
                log.warning("radio connection lost")
            self._alive = False

    def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None:
        # A direct message asks for a link-layer ack, so the radio retransmits until the node confirms:
        # the shared LoRa channel loses a large share of single-shot packets (measured on the bench).
        self._iface.sendText(
            text, destinationId=dest or "^all", wantAck=dest is not None, channelIndex=channel
        )

    def known_nodes(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for n in (self._iface.nodes or {}).values():
            user = n.get("user", {})
            out.append(
                {
                    "id": user.get("id"),
                    "long_name": user.get("longName"),
                    "short_name": user.get("shortName"),
                    "hw": user.get("hwModel"),
                    "snr": n.get("snr"),
                    "last_heard": n.get("lastHeard"),
                }
            )
        return out

    def close(self) -> None:
        self._alive = False
        if self._iface is not None:
            try:
                self._iface.close()
            except Exception:  # closing a dead port must never raise
                log.debug("close failed", exc_info=True)
            self._iface = None
        try:
            from pubsub import pub

            pub.unsubscribe(self._on_receive, "meshtastic.receive.text")
            pub.unsubscribe(self._on_lost, "meshtastic.connection.lost")
        except Exception:
            pass
