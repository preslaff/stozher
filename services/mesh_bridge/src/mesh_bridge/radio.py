"""The radio the bridge listens to. A Protocol so the bridge can be tested without hardware."""

from collections.abc import Callable
from typing import Protocol

from .protocol import MeshText


class Radio(Protocol):
    node_id: str

    @property
    def alive(self) -> bool: ...

    def start(self, on_text: Callable[[MeshText], None]) -> None:
        """Connect and call `on_text` (from any thread) for every received text message."""

    def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None:
        """Send text; `dest` is a Meshtastic node ID for a direct message, None to broadcast."""

    def known_nodes(self) -> list[dict[str, object]]: ...

    def close(self) -> None: ...


class FakeRadio:
    """In-memory radio for tests and for running the bridge with no hardware."""

    def __init__(self, node_id: str = "!00000001") -> None:
        self.node_id = node_id
        self.sent: list[tuple[str, int]] = []
        self.sent_to: list[str | None] = []  # destination of each sent text
        self._on_text: Callable[[MeshText], None] | None = None
        self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self, on_text: Callable[[MeshText], None]) -> None:
        self._on_text, self._alive = on_text, True

    def inject(self, msg: MeshText) -> None:
        assert self._on_text is not None, "radio not started"
        self._on_text(msg)

    def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None:
        self.sent.append((text, channel))
        self.sent_to.append(dest)

    def known_nodes(self) -> list[dict[str, object]]:
        return []

    def close(self) -> None:
        self._alive = False
