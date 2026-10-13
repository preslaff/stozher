import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from stozher_api.config import get_settings
from stozher_api.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("DATABASE_URL")
        or get_settings().database_url
    )


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(_online())
