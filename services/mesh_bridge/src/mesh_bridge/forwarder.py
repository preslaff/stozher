"""Store-and-forward uplink: radio records -> POST /v1/ingest/mesh.

Degraded networks are the normal case, so a record is never lost to a slow or absent backend:
it waits in a bounded queue, is mirrored to a spool file (replayed after a restart), and is retried
with backoff. The backend's answer is final: accepted, duplicate and rejected all mean "handled".
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .protocol import MeshText, extract_record

log = logging.getLogger("stozher.forwarder")


class Forwarder:
    def __init__(
        self,
        http: httpx.AsyncClient,
        system_key: str,
        *,
        spool: Path | None = None,
        max_queue: int = 1000,
        dedupe_s: float = 600.0,
        retry_base_s: float = 2.0,
        retry_max_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_handled: Callable[[str, str], None] | None = None,
        sim: bool = False,
    ) -> None:
        self.http, self.key, self.spool = http, system_key, spool
        self.sim = sim  # simulated radio: records go to /v1/ingest/sim, never to live ingest
        self.max_queue, self.dedupe_s = max_queue, dedupe_s
        self.retry_base_s, self.retry_max_s = retry_base_s, retry_max_s
        self.clock, self.sleep = clock, sleep
        self.on_handled = on_handled  # (mesh node id, status) once the backend has answered
        self.queue: deque[dict[str, Any]] = deque()
        self.stats: dict[str, int] = {
            "seen": 0,
            "ignored": 0,
            "deduped": 0,
            "enqueued": 0,
            "dropped_full": 0,
            "retries": 0,
            "accepted": 0,
            "duplicate": 0,
            "rejected": 0,
            "bad_request": 0,
        }
        self.rejections: dict[str, int] = {}
        self.last_ok_at: float | None = None
        self.last_error: str | None = None
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._wake = asyncio.Event()
        self._stop = False
        self._load_spool()

    # ---- intake -------------------------------------------------------------------------------

    def submit(self, msg: MeshText) -> bool:
        """Queue a received message if it is a witness record. Call from the event loop thread."""
        self.stats["seen"] += 1
        record = extract_record(msg.text)
        if record is None:
            self.stats["ignored"] += 1
            return False
        now = self.clock()
        for k in [k for k, t in self._seen.items() if now - t > self.dedupe_s]:
            del self._seen[k]  # expire old entries (insertion order is time order)
        key = (msg.from_id, record)
        if key in self._seen:
            self.stats["deduped"] += (
                1  # mesh rebroadcasts and node repeats: the backend would dedupe too
            )
            return False
        self._seen[key] = now
        item = {"from_id": msg.from_id, "payload_hex": record, "hops": msg.hops, "sim": self.sim}
        if len(self.queue) >= self.max_queue:
            self.queue.popleft()
            self.stats["dropped_full"] += 1
            log.warning("queue full: dropped the oldest record")
        self.queue.append(item)
        self._append_spool(item)
        self.stats["enqueued"] += 1
        self._wake.set()
        return True

    # ---- spool --------------------------------------------------------------------------------

    def _load_spool(self) -> None:
        if self.spool is None or not self.spool.exists():
            return
        other: list[str] = []
        for line in self.spool.read_text().splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping a damaged spool line")
                continue
            # A record spooled in the other mode never goes to this mode's ingest (simulated data stays simulated,
            # CLAUDE.md rule 8). Lines from before the tag were written by a live bridge.
            if bool(item.get("sim", False)) != self.sim:
                other.append(line)
                continue
            self.queue.append(item)
        if other:
            aside = self.spool.with_name(self.spool.name + (".live" if self.sim else ".sim"))
            with aside.open("a") as f:
                f.write("".join(line + "\n" for line in other))
            log.warning(
                "%d spooled %s records belong to a %s bridge: moved to %s, not sent",
                len(other),
                "live" if self.sim else "simulated",
                "live" if self.sim else "sim",
                aside,
            )
            self.spool.write_text("".join(json.dumps(i) + "\n" for i in self.queue))
        if self.queue:
            log.info("replaying %d spooled records", len(self.queue))

    def _append_spool(self, item: dict[str, Any]) -> None:
        if self.spool is not None:
            self.spool.parent.mkdir(parents=True, exist_ok=True)
            with self.spool.open("a") as f:
                f.write(json.dumps(item) + "\n")

    def _clear_spool_if_idle(self) -> None:
        if self.spool is not None and not self.queue and self.spool.exists():
            self.spool.write_text("")

    # ---- delivery -----------------------------------------------------------------------------

    async def send_one(self, item: dict[str, Any]) -> bool:
        """Deliver one record. True when the backend gave a final answer, False to retry later."""
        if self.sim:
            path = "/v1/ingest/sim"
            body: dict[str, Any] = {
                "external_id": item["from_id"],
                "payload_hex": item["payload_hex"],
            }
        else:
            path = "/v1/ingest/mesh"
            body = {
                "origin_node": item["from_id"],
                "payload_hex": item["payload_hex"],
                "hops": item.get("hops"),
            }
        try:
            r = await self.http.post(path, headers={"X-API-Key": self.key}, json=body)
        except httpx.HTTPError as exc:
            self.last_error = f"unreachable: {exc.__class__.__name__}"
            return False
        if r.status_code == 202:
            res = r.json()
            self.stats[res["status"]] = self.stats.get(res["status"], 0) + 1
            if res["status"] == "rejected":
                reason = res.get("reason", "unknown")
                self.rejections[reason] = self.rejections.get(reason, 0) + 1
                if self.rejections[reason] in (1, 10, 100):
                    log.warning("backend rejected a record from %s: %s", item["from_id"], reason)
            self.last_ok_at, self.last_error = self.clock(), None
            if self.on_handled is not None and res["status"] in ("accepted", "duplicate"):
                self.on_handled(item["from_id"], res["status"])
            return True
        if r.status_code in (401, 403):
            self.last_error = f"api key refused ({r.status_code}); check STOZHER_SYSTEM_KEY"
            log.error(self.last_error)
            return False  # keep the record: the key can be fixed without losing data
        if r.status_code == 422:
            self.stats["bad_request"] += 1
            log.warning("backend refused the request shape (422); dropping the record")
            return True
        self.last_error = f"backend answered {r.status_code}"
        return False

    async def run(self) -> None:
        delay = self.retry_base_s
        while not self._stop:
            if not self.queue:
                self._clear_spool_if_idle()
                self._wake.clear()
                await self._wake.wait()
                continue
            if await self.send_one(self.queue[0]):
                self.queue.popleft()
                delay = self.retry_base_s
            else:
                self.stats["retries"] += 1
                await self.sleep(delay)
                delay = min(delay * 2, self.retry_max_s)

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
