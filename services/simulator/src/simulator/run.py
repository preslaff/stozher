"""Run a scenario against a STOZHER API: reset sim data, register nodes, replay packets, score."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from stozher_core.codec import encode
from stozher_core.crypto import derive_keys

from .client import SimClient
from .engine import Packet, Plan, generate, master_key
from .metrics import Report, evaluate
from .scenario import Scenario


def encode_packet(pkt: Packet, node_id: int, sc: Scenario, base_ts: int) -> str:
    rec = pkt.record.model_copy(update={"timestamp": base_ts + pkt.record.timestamp})
    key = (
        master_key(sc.seed, pkt.node)
        if pkt.mutation != "wrong_key"
        else master_key(sc.seed, "attacker-" + pkt.node)
    )
    raw = bytearray(encode(rec, node_id, derive_keys(key, node_id, rec.key_id)))
    if pkt.mutation == "bit_flip":
        raw[10] ^= 0x01
    return bytes(raw).hex()


async def run_scenario(
    sc: Scenario,
    client: SimClient,
    *,
    speed: float | None = None,
    settle_timeout_s: float = 30.0,
    keep: bool = False,
    after_ingest: Callable[[], Awaitable[None]] | None = None,
) -> tuple[Plan, Report, list[dict[str, Any]]]:
    """`speed=None` replays as fast as possible with timestamps ending now; a number paces
    arrivals in real time at that multiple (timestamps then start now)."""
    plan = generate(sc)
    if not keep:
        await client.reset()
    ids = await client.register(sc)
    base = int(time.time()) - (0 if speed else int(sc.duration_s + 600))
    started = time.monotonic()
    results: list[tuple[Packet, dict[str, Any]]] = []
    for pkt in plan.packets:
        if speed:
            await asyncio.sleep(max(0.0, pkt.arrive_t / speed - (time.monotonic() - started)))
        payload = encode_packet(pkt, ids[pkt.node], sc, base)
        results.append((pkt, await client.ingest(f"sim-{sc.name}-{pkt.node}", payload)))
    if after_ingest:  # e.g. run the worker in-process (tests); otherwise a worker must be running
        await after_ingest()
    hotspots = await client.wait_for_hotspots(settle_timeout_s)
    return plan, evaluate(plan, hotspots, results), hotspots
