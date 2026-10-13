from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()

# Optional modules report present/absent; none of them are wired yet.
OPTIONAL_MODULES = ("mesh_bridge", "kineis_adapter", "worker", "assistant", "copernicus")


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    try:
        async with request.app.state.sessionmaker() as session:
            await session.execute(text("select 1"))
        db = "ok"
    except Exception:
        db = "down"
    return {
        "status": "ok" if db == "ok" else "degraded",
        "db": db,
        "modules": {m: "absent" for m in OPTIONAL_MODULES},
    }
