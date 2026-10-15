"""Optional modules (mesh bridge, worker, later the Kinéis adapter and assistant) say they are alive; /health reads it."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ModuleHeartbeat

REPORTING_MODULES = ("mesh_bridge", "kineis_adapter", "worker", "assistant")
STALE_AFTER = timedelta(minutes=2)  # modules beat every 15-30 s


async def beat(session: AsyncSession, module: str, info: dict[str, Any] | None = None) -> None:
    stmt = pg_insert(ModuleHeartbeat).values(module=module, info=info)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["module"], set_={"last_seen": datetime.now(UTC), "info": info}
        )
    )
    await session.commit()


def is_present(
    module: str, last_seen: datetime, info: dict[str, Any] | None, now: datetime
) -> bool:
    """Alive recently, and for the mesh bridge also holding a connected radio (a bridge without a radio is not a bridge)."""
    if now - last_seen > STALE_AFTER:
        return False
    if module == "mesh_bridge":
        return bool(info and info.get("radio_connected"))
    return True


async def present_modules(session: AsyncSession, now: datetime) -> set[str]:
    rows = (await session.execute(select(ModuleHeartbeat))).scalars().all()
    return {r.module for r in rows if is_present(r.module, r.last_seen, r.info, now)}
