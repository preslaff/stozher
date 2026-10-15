"""Worker loop: run due jobs, and enqueue a periodic recompute so evidence ages out."""

import asyncio
import logging
import os
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from stozher_api.config import get_settings
from stozher_api.hazard_service import reconcile
from stozher_api.heartbeat import beat

from .hazard_alerts import watch_hazards
from .queue import Handler, enqueue, run_pending
from .recompute import recompute

log = logging.getLogger("stozher.worker")
PERIOD_S = 60.0
POLL_S = 1.0
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


HANDLERS: dict[str, Handler] = {"recompute": _handle_recompute, "hazards": _handle_hazards}


async def run() -> None:
    engine = create_async_engine(os.environ.get("DATABASE_URL") or get_settings().database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    last_periodic = 0.0
    last_beat = -BEAT_S
    loop = asyncio.get_running_loop()
    while True:
        if loop.time() - last_periodic >= PERIOD_S:
            async with maker() as session:
                await enqueue(session, "recompute", datetime.now(UTC))
                await enqueue(session, "hazards", datetime.now(UTC))
            last_periodic = loop.time()
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
