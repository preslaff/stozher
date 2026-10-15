import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from . import problems
from .auth import hash_key, parse_prefix
from .config import Settings, get_settings
from .db import make_engine, make_sessionmaker
from .models import ApiKey
from .ratelimit import RateHeadersMiddleware, RateLimiter
from .routes import (
    access,
    admin,
    alerts,
    control,
    events,
    hazards,
    health,
    hotspots,
    ingest,
    me,
    nodes,
    reports,
    sim,
    situation,
    system,
    trust,
    watched,
)
from .signing import load_signer

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
    app.state.limiter = RateLimiter()
    app.state.usage = {}  # prefix -> in-memory request counters since start
    app.state.touched = {}  # prefix -> when last_used_at was last written
    app.state.signer = load_signer(settings.stozher_ed25519_seed, settings.cloud_key_id)
    app.state.engine = make_engine(settings.database_url, **engine_kw)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    problems.install(app)
    app.add_middleware(RateHeadersMiddleware)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["X-API-Key", "Content-Type", "Last-Event-ID"],
        # browsers hide other response headers from scripts unless they are listed
        expose_headers=[
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
        ],
    )
    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(access.router)
    app.include_router(ingest.router)
    app.include_router(nodes.router)
    app.include_router(events.router)
    app.include_router(hotspots.router)
    app.include_router(situation.router)
    app.include_router(trust.router)
    app.include_router(sim.router)
    app.include_router(system.router)
    app.include_router(alerts.router)
    app.include_router(control.router)
    app.include_router(watched.router)
    app.include_router(admin.router)
    app.include_router(reports.router)
    app.include_router(hazards.router)
    dashboard = Path(settings.stozher_dashboard_dir) if settings.stozher_dashboard_dir else None
    if dashboard is not None:
        if (dashboard / "index.html").is_file():
            # last, so every /v1 and /health route wins; Starlette answers Range requests (the offline map file)
            app.mount("/", StaticFiles(directory=dashboard, html=True), name="dashboard")
        else:
            logging.getLogger("stozher.api").warning(
                "STOZHER_DASHBOARD_DIR has no index.html; the dashboard is not served"
            )
    return app


def app_factory() -> FastAPI:
    """uvicorn --factory stozher_api.main:app_factory"""
    return create_app()
