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


def _boot_clock() -> float:
    """Seconds that keep counting while the machine sleeps (Linux CLOCK_BOOTTIME; wall clock elsewhere)."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    return time.clock_gettime(clock) if clock is not None else time.time()


class RadioSupervisor:
    """Keeps a radio connected: (re)creates it whenever it is missing, has dropped, or may have gone stale.

    A missing radio never stops the bridge. It keeps serving its health endpoint and retries.
    A serial link can die without the radio library noticing (seen after the machine suspended: the bridge said
    "connected" for hours while the gateway held the node's messages), so the link is also reopened
    - after the machine resumes from sleep: the monotonic clock stops during suspend, the boot clock does not;
    - after `stale_after_s` with no text from the radio (0 turns this off);
    - after `quiet_after_s` with no packet of any kind (0 turns this off), for radios that report packets
      (`last_packet_mono`). A gateway sends its own telemetry and relays node info every ~30 min, so total silence means
      the client link is dead even though the port is open (seen with a 2.8.2 gateway, Oct 2026). Reopening
      re-handshakes the client API, and a store-and-forward gateway then hands over what it kept.
    Reopening a healthy link costs a second.
    """

    def __init__(
        self,
        factory: Callable[[], Radio],
        on_text: Callable[[MeshText], None],
        retry_s: float = 5.0,
        stale_after_s: float = 3 * 3600.0,
        resume_gap_s: float = 30.0,
        mono: Callable[[], float] = time.monotonic,
        boot: Callable[[], float] = _boot_clock,
        quiet_after_s: float = 45 * 60.0,
    ) -> None:
        self.factory, self.retry_s = factory, retry_s
        self.stale_after_s, self.resume_gap_s = stale_after_s, resume_gap_s
        self.quiet_after_s = quiet_after_s
        self._connected_mono = mono()
        self._mono, self._boot = mono, boot
        self._deliver = on_text
        self.radio: Radio | None = None
        self.last_error: str | None = None
        self.reconnects = 0
        self.last_reconnect_reason: str | None = None
        self._last_rx = boot()  # last text from the radio, or the last (re)connect
        self._clocks = (mono(), boot())

    def on_text(self, msg: MeshText) -> None:  # called from the radio library's thread
        self._last_rx = self._boot()
        self._deliver(msg)

    @property
    def last_rx_age_s(self) -> float:
        return self._boot() - self._last_rx

    @property
    def last_packet_age_s(self) -> float | None:
        """Seconds since the last packet of any kind (or since the connect), None if the radio does not report packets."""
        if self.radio is None or not hasattr(self.radio, "last_packet_mono"):
            return None
        last = getattr(self.radio, "last_packet_mono", None)
        return self._mono() - max(last if last is not None else 0.0, self._connected_mono)

    def stale_reason(self) -> str | None:
        """Why the current link should be reopened, if it should. Also advances the sleep detector."""
        mono, boot = self._mono(), self._boot()
        slept = (boot - self._clocks[1]) - (mono - self._clocks[0])
        self._clocks = (mono, boot)
        if not self.alive:
            return None
        if slept > self.resume_gap_s:
            return f"the machine slept for about {round(slept)} s"
        if self.stale_after_s > 0 and boot - self._last_rx > self.stale_after_s:
            return f"no text from the radio for {round(boot - self._last_rx)} s"
        quiet = self.last_packet_age_s
        if self.quiet_after_s > 0 and quiet is not None and quiet > self.quiet_after_s:
            return f"no packet of any kind from the radio for {round(quiet)} s"
        return None

    def reopen(self, reason: str) -> None:
        log.warning("reopening the radio link: %s", reason)
        self.reconnects += 1
        self.last_reconnect_reason = reason
        self.close()
        self.radio = None

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
            if (reason := self.stale_reason()) is not None:
                self.reopen(reason)
            if not self.alive:
                self.close()
                try:
                    radio = self.factory()
                    await asyncio.to_thread(radio.start, self.on_text)  # opening the port can block
                    self.radio, self.last_error = radio, None
                    self._last_rx = self._boot()
                    self._connected_mono = self._mono()
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
    stale_after_s: float = 3 * 3600.0,
    quiet_after_s: float = 45 * 60.0,
    http: httpx.AsyncClient | None = None,
    name: str | None = None,
    sim: bool = False,
) -> None:
    """`sim`: the radio is a simulated one (sim_radio.py). Records then go to /v1/ingest/sim, acks are looked up as
    transport `sim`, and the bridge sends no heartbeat, so a simulated bridge never shows up as a real gateway."""
    own_http = http is None
    http = http or httpx.AsyncClient(base_url=api_url, timeout=15)
    loop = asyncio.get_running_loop()
    pusher_ref: list[AckPusher] = []

    def handled(from_id: str, status: str) -> None:
        if pusher_ref:
            pusher_ref[0].note(from_id, status)

    forwarder = Forwarder(http, system_key, spool=spool, on_handled=handled, sim=sim)

    def on_text(msg: MeshText) -> None:  # called from the radio library's thread
        loop.call_soon_threadsafe(forwarder.submit, msg)

    supervisor = RadioSupervisor(
        radio_factory, on_text, stale_after_s=stale_after_s, quiet_after_s=quiet_after_s
    )
    relay = (
        AlertRelay(
            http,
            responder_key,
            supervisor,
            relay_sim=relay_sim
            or sim,  # a simulated mesh may carry [SIM] alerts; a real radio only with --relay-sim
            min_interval_s=relay_interval_s,
        )
        if responder_key
        else None
    )
    acks = (
        AckPusher(http, system_key, supervisor, transport="sim" if sim else "mesh")
        if send_acks
        else None
    )
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
                "last_text_age_s": round(supervisor.last_rx_age_s),
                "last_packet_age_s": None
                if (q := supervisor.last_packet_age_s) is None
                else round(q),
                "reconnects": supervisor.reconnects,
                "last_reconnect_reason": supervisor.last_reconnect_reason,
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
            "name": name,
            "sim": sim,
        }

    server = await serve_health(health_port, snapshot) if health_port else None
    beats = HeartbeatPusher(
        http,
        system_key,
        lambda: {"radio_connected": supervisor.alive, "node_id": supervisor.node_id},
        instance=name,
    )
    tasks = [
        asyncio.create_task(forwarder.run()),
        asyncio.create_task(supervisor.run()),
    ]
    if not sim:
        tasks.append(asyncio.create_task(beats.run()))
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
