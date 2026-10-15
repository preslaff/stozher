"""Worker loop: run due jobs, and enqueue a periodic recompute so evidence ages out."""

import asyncio
import logging
import os
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from stozher_api.config import get_settings
from stozher_api.hazard_service import reconcile
from stozher_api.heartbeat import beat
from stozher_api.sync_service import run_sync
from stozher_api.webhook_service import deliver_due, dispatch

from .hazard_alerts import watch_hazards
from .queue import Handler, enqueue, run_pending
from .recompute import recompute

log = logging.getLogger("stozher.worker")
PERIOD_S = 60.0
POLL_S = 1.0
WEBHOOK_PERIOD_S = 10.0  # new alerts are matched to webhooks and due deliveries are sent this often
SYNC_PERIOD_S = (
    60.0  # a kit with a cloud URL configured syncs this often (optional: no URL, no sync)
)
BEAT_S = 15.0  # tells /health the worker is alive even when no job ran lately


async def _handle_recompute(session: AsyncSession, now: datetime) -> object:
    summary = await recompute(session, now)
    log.info("recompute: %s", summary)
    return summary


async def _handle_hazards(session: AsyncSession, now: datetime) -> object:
    summary = await reconcile(session, now)
    await watch_hazards(session, now)
    await session.commit()
    if summary.created or summary.changed or summary.purged:
        log.info("hazards: %s", summary)
    return summary


async def _handle_webhooks(session: AsyncSession, now: datetime) -> object:
    created = await dispatch(session, now)
    async with httpx.AsyncClient(follow_redirects=False) as client:
        stats = await deliver_due(session, now, kek=get_settings().stozher_kek, client=client)
    if created or any(stats.values()):
        log.info("webhooks: %d new, %s", created, stats)
    return stats


async def _handle_sync(session: AsyncSession, now: datetime) -> object:
    cfg = get_settings()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        stats = await run_sync(
            session,
            client,
            cloud_url=cfg.stozher_cloud_url,
            cloud_key=cfg.stozher_cloud_key,
            kek=cfg.stozher_kek,
            replay_window_s=cfg.replay_window_s,
        )
    if stats.pulled or stats.pushed or stats.rejected or stats.error:
        log.info("sync: %s", stats)
    return stats


HANDLERS: dict[str, Handler] = {
    "recompute": _handle_recompute,
    "hazards": _handle_hazards,
    "webhooks": _handle_webhooks,
    "sync": _handle_sync,
}


async def run() -> None:
    engine = create_async_engine(os.environ.get("DATABASE_URL") or get_settings().database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    last_periodic = 0.0
    last_beat = -BEAT_S
    last_webhooks = -WEBHOOK_PERIOD_S
    last_sync = -SYNC_PERIOD_S
    loop = asyncio.get_running_loop()
    while True:
        if loop.time() - last_periodic >= PERIOD_S:
            async with maker() as session:
                await enqueue(session, "recompute", datetime.now(UTC))
                await enqueue(session, "hazards", datetime.now(UTC))
            last_periodic = loop.time()
        if loop.time() - last_webhooks >= WEBHOOK_PERIOD_S:
            async with maker() as session:
                await enqueue(session, "webhooks", datetime.now(UTC))
            last_webhooks = loop.time()
        cfg = get_settings()
        if (
            cfg.stozher_cloud_url
            and cfg.stozher_cloud_key
            and loop.time() - last_sync >= SYNC_PERIOD_S
        ):
            async with maker() as session:
                await enqueue(session, "sync", datetime.now(UTC))
            last_sync = loop.time()
        if loop.time() - last_beat >= BEAT_S:
            async with maker() as session:
                await beat(session, "worker")
            last_beat = loop.time()
        await run_pending(maker, HANDLERS)
        await asyncio.sleep(POLL_S)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
