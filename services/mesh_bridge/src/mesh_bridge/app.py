"""Wire the radio, the forwarder, the optional alert relay and the health endpoint together."""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .acks import AckPusher
from .forwarder import Forwarder
from .health import serve_health
from .heartbeat import HeartbeatPusher
from .protocol import MeshText
from .radio import Radio
from .relay import AlertRelay

log = logging.getLogger("stozher.bridge")


class RadioSupervisor:
    """Keeps a radio connected: (re)creates it whenever it is missing or has dropped.

    A missing radio never stops the bridge. It keeps serving its health endpoint and retries.
    """

    def __init__(
        self,
        factory: Callable[[], Radio],
        on_text: Callable[[MeshText], None],
        retry_s: float = 5.0,
    ) -> None:
        self.factory, self.on_text, self.retry_s = factory, on_text, retry_s
        self.radio: Radio | None = None
        self.last_error: str | None = None

    @property
    def node_id(self) -> str:
        return self.radio.node_id if self.radio else "?"

    @property
    def alive(self) -> bool:
        return self.radio is not None and self.radio.alive

    def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None:
        if self.radio is None or not self.radio.alive:
            raise RuntimeError("radio not connected")
        self.radio.send_text(text, channel, dest)

    def known_nodes(self) -> list[dict[str, object]]:
        return self.radio.known_nodes() if self.radio else []

    def close(self) -> None:
        if self.radio is not None:
            self.radio.close()

    async def run(self) -> None:
        while True:
            if not self.alive:
                self.close()
                try:
                    radio = self.factory()
                    await asyncio.to_thread(radio.start, self.on_text)  # opening the port can block
                    self.radio, self.last_error = radio, None
                    log.info("radio connected as %s", radio.node_id)
                except Exception as exc:  # a missing or busy port must not stop the bridge
                    self.radio, self.last_error = None, f"{exc.__class__.__name__}: {exc}"[:200]
                    log.warning(
                        "radio unavailable (%s); retrying in %.0f s", self.last_error, self.retry_s
                    )
            await asyncio.sleep(self.retry_s)


async def run_bridge(
    *,
    api_url: str,
    system_key: str,
    radio_factory: Callable[[], Radio],
    spool: Path | None = None,
    responder_key: str | None = None,
    relay_sim: bool = False,
    relay_interval_s: float = 60.0,
    send_acks: bool = True,
    health_port: int | None = None,
    http: httpx.AsyncClient | None = None,
) -> None:
    own_http = http is None
    http = http or httpx.AsyncClient(base_url=api_url, timeout=15)
    loop = asyncio.get_running_loop()
    pusher_ref: list[AckPusher] = []

    def handled(from_id: str, status: str) -> None:
        if pusher_ref:
            pusher_ref[0].note(from_id, status)

    forwarder = Forwarder(http, system_key, spool=spool, on_handled=handled)

    def on_text(msg: MeshText) -> None:  # called from the radio library's thread
        loop.call_soon_threadsafe(forwarder.submit, msg)

    supervisor = RadioSupervisor(radio_factory, on_text)
    relay = (
        AlertRelay(
            http, responder_key, supervisor, relay_sim=relay_sim, min_interval_s=relay_interval_s
        )
        if responder_key
        else None
    )
    acks = AckPusher(http, system_key, supervisor) if send_acks else None
    if acks:
        pusher_ref.append(acks)
    started = time.monotonic()

    def snapshot() -> dict[str, Any]:
        return {
            "status": "ok" if supervisor.alive else "degraded",
            "radio": {
                "connected": supervisor.alive,
                "node_id": supervisor.node_id,
                "last_error": supervisor.last_error,
            },
            "uplink": {
                "queued": len(forwarder.queue),
                "last_error": forwarder.last_error,
                **forwarder.stats,
                "rejections": forwarder.rejections,
            },
            "alert_relay": None
            if relay is None
            else {"sent": relay.sent, "skipped": relay.skipped, "throttled": relay.throttled},
            "acks": None
            if acks is None
            else {
                "sent": acks.sent,
                "waiting": len(acks.due),
                "fetch_errors": acks.fetch_errors,
                "disabled": acks.disabled_reason,
            },
            "uptime_s": round(time.monotonic() - started),
        }

    server = await serve_health(health_port, snapshot) if health_port else None
    beats = HeartbeatPusher(
        http,
        system_key,
        lambda: {"radio_connected": supervisor.alive, "node_id": supervisor.node_id},
    )
    tasks = [
        asyncio.create_task(forwarder.run()),
        asyncio.create_task(supervisor.run()),
        asyncio.create_task(beats.run()),
    ]
    if relay:
        tasks.append(asyncio.create_task(relay.run()))
    if acks:
        tasks.append(asyncio.create_task(acks.run()))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        forwarder.stop()
        supervisor.close()
        if server:
            server.close()
        if own_http:
            await http.aclose()
