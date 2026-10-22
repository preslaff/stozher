"""A simulated gateway radio: a TCP server on this machine that stands in for one LoRa band (docs/mesh.md, sim mode).

A virtual node (`firmware/node/sim/virtual_node.cpp`) connects once per band and first sends `SIMID:<id>`, the
transport ID its radio on that band would have. After that every line is one text message, both ways, exactly as the
Meshtastic Serial module carries them. A bridge with this radio runs in sim mode: records go to `/v1/ingest/sim`, never
to live ingest, so simulated data stays labelled.

Only 127.0.0.1 is accepted: nothing from the network can feed records through a simulated radio.
"""

import logging
import socket
import threading
from collections.abc import Callable

from .protocol import MeshText

log = logging.getLogger("stozher.bridge.sim")

PREFIX = "sim-tcp:"
LOOPBACK = "127.0.0.1"
MAX_LINE = 512


def parse_port(port: str) -> tuple[str, int] | None:
    """`sim-tcp:127.0.0.1:47868` -> (host, port); None if it is not a simulated port. Only loopback is allowed."""
    if not port.startswith(PREFIX):
        return None
    host, _, num = port[len(PREFIX) :].rpartition(":")
    if host != LOOPBACK:
        raise ValueError(f"a simulated radio only listens on {LOOPBACK}, not {host!r}")
    n = int(num)
    if not 1024 <= n <= 65535:
        raise ValueError("port must be 1024-65535")
    return host, n


class SimTcpRadio:
    """The `Radio` protocol (radio.py) over loopback TCP. One connection per simulated node radio."""

    def __init__(self, host: str, port: int, node_id: str = "!5100000a") -> None:
        if host != LOOPBACK:
            raise ValueError(f"a simulated radio only listens on {LOOPBACK}")
        self.host, self.port, self.node_id = host, port, node_id
        self._on_text: Callable[[MeshText], None] | None = None
        self._server: socket.socket | None = None
        self._conns: dict[str, socket.socket] = {}
        self._lock = threading.Lock()
        self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def bound_port(self) -> int:
        return self._server.getsockname()[1] if self._server else self.port

    def start(self, on_text: Callable[[MeshText], None]) -> None:
        self._on_text = on_text
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(4)
        self._server, self._alive = srv, True
        threading.Thread(target=self._accept, daemon=True, name="sim-radio-accept").start()
        log.info(
            "simulated radio listening on %s:%d (SIM: records go to /v1/ingest/sim)",
            self.host,
            self.bound_port,
        )

    def _accept(self) -> None:
        assert self._server is not None
        while self._alive:
            try:
                conn, addr = self._server.accept()
            except OSError:
                return
            if addr[0] != LOOPBACK:
                conn.close()
                continue
            threading.Thread(
                target=self._serve, args=(conn,), daemon=True, name="sim-radio-conn"
            ).start()

    def _serve(self, conn: socket.socket) -> None:
        sim_id: str | None = None
        buf = b""
        try:
            while self._alive:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    if sim_id is None:
                        if not line.startswith("SIMID:") or not 1 <= len(line) - 6 <= 64:
                            log.warning("simulated radio: first line must be SIMID:<id>; closing")
                            return
                        sim_id = line[6:]
                        with self._lock:
                            self._conns[sim_id] = conn
                        continue
                    if self._on_text is not None and len(line) <= MAX_LINE:
                        self._on_text(MeshText(from_id=sim_id, text=line))
                if len(buf) > MAX_LINE:
                    buf = b""  # an over-long line is dropped, as the radio would
        except OSError:
            pass
        finally:
            if sim_id is not None:
                with self._lock:
                    if self._conns.get(sim_id) is conn:
                        del self._conns[sim_id]
            conn.close()

    def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None:
        """Direct to one simulated radio, or to all of them when `dest` is None (a broadcast)."""
        with self._lock:
            targets = (
                list(self._conns.values())
                if dest is None
                else [c for k, c in self._conns.items() if k == dest]
            )
        for c in targets:
            try:
                c.sendall(text.encode() + b"\n")
            except OSError:
                pass

    def known_nodes(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "id": k,
                    "short_name": "SIM",
                    "long_name": "simulated radio",
                    "hw": "sim",
                    "snr": None,
                }
                for k in self._conns
            ]

    def close(self) -> None:
        self._alive = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        with self._lock:
            conns, self._conns = list(self._conns.values()), {}
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
