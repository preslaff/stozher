"""Real radio through the Meshtastic Python library (GPL-3.0; see LICENSE-NOTE.md).

The library is imported lazily so everything else in the bridge works without it.
"""

import logging
from collections.abc import Callable
from typing import Any

from .protocol import MeshText, to_mesh_text

log = logging.getLogger("stozher.mesh")


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
        from meshtastic.serial_interface import SerialInterface  # GPL-3.0, imported on use
        from pubsub import pub

        self._on_text = on_text
        # Subscribe first so no early packet is missed; weak refs mean we must keep these bound methods.
        pub.subscribe(self._on_receive, "meshtastic.receive.text")
        pub.subscribe(self._on_lost, "meshtastic.connection.lost")
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

    def send_text(self, text: str, channel: int = 0) -> None:
        self._iface.sendText(text, channelIndex=channel)

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
