import asyncio
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from stozher_api.auth import hash_key, new_key
from stozher_api.config import Settings
from stozher_api.main import create_app
from stozher_api.models import ApiKey

ADMIN_URL = os.environ.get(
    "STOZHER_TEST_DB_ADMIN_URL", "postgresql://preslaff@/postgres?host=/var/run/postgresql"
)
TEST_DB = "stozher_test"
TEST_URL = f"postgresql+asyncpg://preslaff@/{TEST_DB}?host=/var/run/postgresql"
KEK = "ab" * 32
API_DIR = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    async def create() -> None:
        conn = await asyncpg.connect(ADMIN_URL, timeout=5)
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
        await conn.close()

    try:
        asyncio.run(create())
    except Exception as exc:  # no PostGIS database reachable
        pytest.skip(f"test database unavailable: {exc}")
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")
    yield TEST_URL


TEST_SEED = bytes(range(32)).hex()  # the public test seed of tests/vectors/ack_messages.json


async def _client(database: str, seed: str) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=database, stozher_kek=KEK, stozher_ed25519_seed=seed, cloud_key_id=1
    )
    app = create_app(settings, poolclass=NullPool)
    async with app.router.lifespan_context(app):
        async with app.state.sessionmaker() as s:
            await s.execute(
                text(
                    "TRUNCATE hazard_reports, road_hazards, watched_routes, acks, alerts, jobs, trust_cells, hotspot_events, hotspots, audit_log, api_keys, events, raw_messages, transport_ids, node_keys, nodes RESTART IDENTITY CASCADE"
                )
            )
            keys = {}
            for role in ("admin", "operator", "responder", "reporter", "public", "system"):
                full, prefix = new_key("test")
                s.add(ApiKey(prefix=prefix, hash=hash_key(full), role=role, label=f"test {role}"))
                keys[role] = full
            await s.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            c.keys = keys  # type: ignore[attr-defined]
            c.app = app  # type: ignore[attr-defined]
            yield c


@pytest_asyncio.fixture
async def client(database: str) -> AsyncIterator[httpx.AsyncClient]:
    """No signing key configured: acknowledgements are disabled (the optional module is absent)."""
    async for c in _client(database, ""):
        yield c


@pytest_asyncio.fixture
async def signed_client(database: str) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(database, TEST_SEED):
        yield c


def now_ts() -> int:
    return int(time.time())
