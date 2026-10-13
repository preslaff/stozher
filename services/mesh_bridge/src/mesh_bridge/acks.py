"""Push cloud-signed acknowledgements back to the nodes over the mesh (docs/control.md).

After the backend has handled records from a node, fetch that node's latest signed ack and send
it back as a direct `STZA1:` message so the node can drop what the cloud already holds. The bridge
only relays: it never signs and never inspects the signature, so a compromised gateway cannot
forge an ack, and nodes verify with the cloud public key. Airtime is shared, so acks are
coalesced and rate limited per node; a duplicate arrival (the node did not get the last ack)
simply triggers another, within the same limit.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from .relay import Sender

log = logging.getLogger("stozher.acks")


class AckPusher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        key: str,
        radio: Sender,
        *,
        delay_s: float = 15.0,
        min_interval_s: float = 120.0,
        retry_s: float = 30.0,
        channel: int = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.http, self.key, self.radio = http, key, radio
        self.delay_s, self.min_interval_s, self.retry_s, self.channel = (
            delay_s,
            min_interval_s,
            retry_s,
            channel,
        )
        self.clock, self.sleep = clock, sleep
        self.due: dict[str, float] = {}
        self.last_sent: dict[str, float] = {}
        self.sent = self.fetch_errors = 0
        self.disabled_reason: str | None = None

    def note(self, from_id: str, status: str = "accepted") -> None:
        """The backend handled a record from this mesh node; schedule (and coalesce) an ack for it."""
        if self.disabled_reason is not None:
            return
        at = max(
            self.clock() + self.delay_s,
            self.last_sent.get(from_id, float("-inf")) + self.min_interval_s,
        )
        self.due.setdefault(from_id, at)

    async def push(self, from_id: str) -> bool:
        """Fetch and send the node's ack. False means try again later."""
        try:
            r = await self.http.get(
                "/v1/acks",
                params={"transport": "mesh", "external_id": from_id},
                headers={"X-API-Key": self.key},
            )
        except httpx.HTTPError:
            self.fetch_errors += 1
            return False
        if r.status_code == 503:
            self.disabled_reason = (
                "the API has no signing key (STOZHER_ED25519_SEED); acknowledgements are off"
            )
            log.warning(self.disabled_reason)
            return True
        if r.status_code == 404:
            return True  # not a registered node: nothing to acknowledge
        if r.status_code != 200:
            self.fetch_errors += 1
            return False
        if not self.radio.alive:
            return False
        for item in r.json()["items"]:
            self.radio.send_text(item["text"], self.channel, from_id)
            self.sent += 1
        return True

    async def tick(self) -> int:
        """Send every ack that is due; returns how many nodes were handled."""
        now = self.clock()
        handled = 0
        for from_id in [n for n, at in self.due.items() if at <= now]:
            del self.due[from_id]
            if await self.push(from_id):
                self.last_sent[from_id] = now
                handled += 1
            else:
                self.due[from_id] = now + self.retry_s
        return handled

    async def run(self) -> None:
        while True:
            await self.tick()
            await self.sleep(1.0)
