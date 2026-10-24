"""Real radio through the Meshtastic Python library (GPL-3.0; see LICENSE-NOTE.md).

The library is imported lazily so everything else in the bridge works without it. The gateway is reached over USB serial
(`/dev/serial/by-id/...`) or over the network (`tcp:<host>[:<port>]`, Meshtastic's client API on an ESP32 with Wi-Fi,
port 4403 by default). Meshtastic's network API has no authentication: anyone on that network can read the channel and
change the radio's settings, so use it only on the kit's own private network.
"""

import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .protocol import MeshText, to_mesh_text

log = logging.getLogger("stozher.mesh")

# Device log lines that could carry a position or key material are never written (docs/mesh.md, device log).
_PRIVATE = re.compile(r"lat|lon|posit|psk|key|secret|passw", re.I)
DEVICE_LOG_MAX_BYTES = 50 * 1024 * 1024  # then the file is moved to <name>.1 and started again
# Meshtastic's boot banner ("S:B:<hw>,<version>,..."), printed as plain serial text at every start, so it reaches the
# client even with the device log setting off. After a restart the open port carries no packets until the client
# handshakes again (seen with 2.8.2 gateways, Oct 2026).
_BOOT_BANNER = re.compile(r"\bS:B:\d+,")

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
    def __init__(self, port: str, device_log: Path | None = None) -> None:
        self.port = port
        self.node_id = "?"
        self._iface: Any = None
        self._alive = False
        self._on_text: Callable[[MeshText], None] | None = None
        self.device_log = device_log
        # time.monotonic() of the last packet of any kind from the radio (the gateway's own telemetry, node info, ...):
        # the supervisor reopens a link that has gone completely quiet (app.py, quiet_after_s).
        self.last_packet_mono: float | None = None
        # time.monotonic() when the radio was last seen starting up (its boot banner): the supervisor reopens the link.
        self.restarted_mono: float | None = None

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self, on_text: Callable[[MeshText], None]) -> None:
        from pubsub import pub

        tcp = parse_tcp_port(self.port)
        self._on_text = on_text
        # Subscribe first so no early packet is missed; weak refs mean we must keep these bound methods.
        pub.subscribe(self._on_receive, "meshtastic.receive.text")
        pub.subscribe(self._on_any, "meshtastic.receive")
        pub.subscribe(self._on_lost, "meshtastic.connection.lost")
        pub.subscribe(self._on_log, "meshtastic.log.line")
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

    def _on_any(self, packet: dict[str, Any] | None = None, interface: Any = None) -> None:
        if interface is self._iface:
            self.last_packet_mono = time.monotonic()

    def _on_log(self, line: str = "", interface: Any = None) -> None:
        """The gateway's own log: its boot banner marks a restart; with `device_log` the lines are kept for diagnosis
        (all of them need `security.debug_log_api_enabled` on the radio, boot text arrives without it)."""
        if interface is not self._iface or not line:
            return
        if _BOOT_BANNER.search(line):
            self.restarted_mono = time.monotonic()
        if self.device_log is None or _PRIVATE.search(line):
            return
        try:
            path = self.device_log
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > DEVICE_LOG_MAX_BYTES:
                path.replace(path.with_name(path.name + ".1"))
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            with path.open("a") as f:
                f.write(f"{stamp} {line.rstrip()}\n")
        except OSError:
            log.debug("device log write failed", exc_info=True)

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
            pub.unsubscribe(self._on_any, "meshtastic.receive")
            pub.unsubscribe(self._on_lost, "meshtastic.connection.lost")
            pub.unsubscribe(self._on_log, "meshtastic.log.line")
        except Exception:
            pass
