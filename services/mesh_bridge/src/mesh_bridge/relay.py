"""Alerts -> mesh text (optional). Listens to the API's alert stream and sends short messages.

Deterministic and rate limited: LoRa airtime is shared and limited, so at most one alert is sent
per `min_interval_s`; the most severe waiting alert goes first and the rest are dropped.
Simulated alerts are never sent over a real radio unless `relay_sim` is set.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from .protocol import alert_text

log = logging.getLogger("stozher.relay")


def parse_sse(buffer: str) -> tuple[list[tuple[str, str]], str]:
    """Complete (event, data) pairs from an SSE text buffer, and the unconsumed tail."""
    text = buffer.replace("\r\n", "\n").replace("\r", "\n")
    blocks = text.split("\n\n")
    rest = blocks.pop()
    events: list[tuple[str, str]] = []
    for block in blocks:
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append((event, "\n".join(data)))
    return events, rest


class Sender(Protocol):
    """The part of a radio the relay needs."""

    @property
    def alive(self) -> bool: ...

    def send_text(self, text: str, channel: int = 0, dest: str | None = None) -> None: ...


class AlertRelay:
    def __init__(
        self,
        http: httpx.AsyncClient,
        key: str,
        radio: Sender,
        *,
        min_interval_s: float = 60.0,
        min_level: str = "warning",
        relay_sim: bool = False,
        channel: int = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.http, self.key, self.radio = http, key, radio
        self.min_interval_s, self.min_level, self.relay_sim, self.channel = (
            min_interval_s,
            min_level,
            relay_sim,
            channel,
        )
        self.clock, self.sleep = clock, sleep
        self.sent = self.skipped = self.throttled = 0
        self._last_sent: float | None = None
        self._last_id: str | None = None

    def handle_alert(self, alert: dict[str, Any]) -> str | None:
        """Send this alert if it qualifies and the airtime budget allows; returns the text sent."""
        text = alert_text(alert, relay_sim=self.relay_sim, min_level=self.min_level)
        if text is None:
            self.skipped += 1
            return None
        now = self.clock()
        if self._last_sent is not None and now - self._last_sent < self.min_interval_s:
            self.throttled += 1
            return None
        if not self.radio.alive:
            self.skipped += 1
            return None
        self.radio.send_text(text, self.channel)
        self._last_sent = now
        self.sent += 1
        return text

    async def run(self) -> None:
        delay = 1.0
        while True:
            try:
                headers = {"X-API-Key": self.key, "Accept": "text/event-stream"}
                if self._last_id:
                    headers["Last-Event-ID"] = self._last_id
                async with self.http.stream(
                    "GET", "/v1/stream/alerts", headers=headers, timeout=None
                ) as r:
                    if r.status_code in (401, 403):
                        log.error(
                            "alert stream refused the key (%s); relay disabled", r.status_code
                        )
                        return
                    r.raise_for_status()
                    delay, buf = 1.0, ""
                    async for chunk in r.aiter_text():
                        buf += chunk
                        events, buf = parse_sse(buf)
                        for event, data in events:
                            if event == "alert":
                                alert = json.loads(data)
                                self._last_id = str(alert.get("id", self._last_id))
                                self.handle_alert(alert)
            except (httpx.HTTPError, json.JSONDecodeError):
                log.debug("alert stream interrupted", exc_info=True)
            await self.sleep(delay)
            delay = min(delay * 2, 30.0)
