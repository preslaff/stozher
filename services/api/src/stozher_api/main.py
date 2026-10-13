import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select

from . import problems
from .auth import hash_key, parse_prefix
from .config import Settings, get_settings
from .db import make_engine, make_sessionmaker
from .models import ApiKey
from .routes import events, health, hotspots, ingest, me, nodes, trust

log = logging.getLogger("stozher.api")


async def bootstrap_admin(app: FastAPI) -> None:
    key = app.state.settings.stozher_bootstrap_admin_key
    if not key:
        return
    prefix = parse_prefix(key)
    if prefix is None:
        log.warning(
            "STOZHER_BOOTSTRAP_ADMIN_KEY is not in stz_<env>_<8 chars>_<secret> form; ignored"
        )
        return
    async with app.state.sessionmaker() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(ApiKey).where(ApiKey.role == "admin")
            )
        ).scalar_one()
        if count == 0:
            session.add(
                ApiKey(prefix=prefix, hash=hash_key(key), role="admin", label="bootstrap admin")
            )
            await session.commit()
            log.info("created bootstrap admin key %s", prefix)


def create_app(settings: Settings | None = None, **engine_kw: object) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await bootstrap_admin(app)
        yield
        await app.state.engine.dispose()

    app = FastAPI(title="STOZHER API", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url, **engine_kw)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    problems.install(app)
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(ingest.router)
    app.include_router(nodes.router)
    app.include_router(events.router)
    app.include_router(hotspots.router)
    app.include_router(trust.router)
    return app


def app_factory() -> FastAPI:
    """uvicorn --factory stozher_api.main:app_factory"""
    return create_app()
