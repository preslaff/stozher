from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func, select, text

from ..heartbeat import present_modules
from ..models import Job

router = APIRouter()

# Optional modules report present/absent. The mesh bridge, worker, Kinéis adapter and assistant send heartbeats
# (`module_heartbeats`); ack signing is present when the key is configured; Copernicus is not wired yet.
OPTIONAL_MODULES = (
    "ack_signing",
    "mesh_bridge",
    "kineis_adapter",
    "worker",
    "assistant",
    "copernicus",
)


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    try:
        async with request.app.state.sessionmaker() as session:
            await session.execute(text("select 1"))
        db = "ok"
    except Exception:
        db = "down"
    modules = {m: "absent" for m in OPTIONAL_MODULES}
    if request.app.state.signer is not None:
        modules["ack_signing"] = "present"
    try:
        async with request.app.state.sessionmaker() as session:
            for m in await present_modules(session, datetime.now(UTC)):
                modules[m] = "present"
    except Exception:
        pass
    try:  # a worker that predates heartbeats still counts when it finished a job lately
        async with request.app.state.sessionmaker() as session:
            last = (
                await session.execute(select(func.max(Job.finished_at)).where(Job.state == "done"))
            ).scalar_one()
        if last is not None and datetime.now(UTC) - last < timedelta(minutes=3):
            modules["worker"] = "present"
    except Exception:
        pass
    return {
        "status": "ok" if db == "ok" else "degraded",
        "db": db,
        "modules": modules,
    }
