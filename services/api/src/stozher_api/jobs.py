"""Enqueue background jobs. Lives in the API package because ingest enqueues them."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Job


async def enqueue(
    session: AsyncSession, kind: str, run_after: datetime, *, commit: bool = True
) -> None:
    """Add a pending job unless one of this kind is already pending (bursts collapse)."""
    await session.execute(
        pg_insert(Job)
        .values(kind=kind, state="pending", run_after=run_after)
        .on_conflict_do_nothing(index_elements=["kind"], index_where=text("state = 'pending'"))
    )
    if commit:
        await session.commit()
