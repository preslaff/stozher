"""Postgres job queue: claim with FOR UPDATE SKIP LOCKED. No Redis."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from stozher_api.jobs import enqueue
from stozher_api.models import Job

MAX_ATTEMPTS = 5
Handler = Callable[[AsyncSession, datetime], Awaitable[object]]

__all__ = ["enqueue", "run_pending"]


async def run_pending(
    maker: async_sessionmaker[AsyncSession],
    handlers: dict[str, Handler],
    now: datetime | None = None,
) -> int:
    """Run every due job once; return how many finished. Failed jobs retry with backoff."""
    done = 0
    while True:
        now_ = now or datetime.now(UTC)
        async with maker() as session:
            job = (
                await session.execute(
                    select(Job)
                    .where(Job.state == "pending", Job.run_after <= now_)
                    .order_by(Job.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return done
            job.state, job.attempts = "running", job.attempts + 1
            await session.commit()
            try:
                await handlers[job.kind](session, now_)
                job.state, job.finished_at = "done", datetime.now(UTC)
                done += 1
            except Exception as exc:  # job failures must not kill the worker
                await session.rollback()
                job.last_error = repr(exc)[:500]
                if job.attempts >= MAX_ATTEMPTS:
                    job.state, job.finished_at = "failed", datetime.now(UTC)
                else:
                    job.state = "pending"
                    job.run_after = now_ + timedelta(seconds=2**job.attempts)
            await session.commit()
