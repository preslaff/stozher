"""Optional modules (mesh bridge, worker, later the Kinéis adapter and assistant) say they are alive; /health reads it.

A kit can run more than one mesh bridge (one gateway radio per LoRa band). Each named bridge beats under its own row
(`mesh_bridge:<name>`); the unnamed one keeps the plain `mesh_bridge` row. The module counts as present when any of its
rows is.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ModuleHeartbeat

REPORTING_MODULES = ("mesh_bridge", "kineis_adapter", "worker", "assistant")
STALE_AFTER = timedelta(minutes=2)  # modules beat every 15-30 s
LISTED_FOR = timedelta(hours=24)  # a bridge silent for longer drops out of the per-bridge list
DEFAULT_INSTANCE = "main"  # how the unnamed bridge is listed


def row_key(module: str, instance: str | None) -> str:
    return f"{module}:{instance}" if instance else module


def split_key(key: str) -> tuple[str, str]:
    module, _, instance = key.partition(":")
    return module, instance or DEFAULT_INSTANCE


async def beat(
    session: AsyncSession,
    module: str,
    info: dict[str, Any] | None = None,
    instance: str | None = None,
) -> None:
    stmt = pg_insert(ModuleHeartbeat).values(module=row_key(module, instance), info=info)
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


async def module_states(
    session: AsyncSession, now: datetime
) -> tuple[set[str], dict[str, dict[str, bool]]]:
    """(modules present, {module: {instance: present}} for every instance heard within LISTED_FOR)."""
    rows = (await session.execute(select(ModuleHeartbeat))).scalars().all()
    present: set[str] = set()
    instances: dict[str, dict[str, bool]] = {}
    for r in rows:
        module, instance = split_key(r.module)
        ok = is_present(module, r.last_seen, r.info, now)
        if ok:
            present.add(module)
        if now - r.last_seen <= LISTED_FOR:
            instances.setdefault(module, {})[instance] = ok
    return present, instances


async def present_modules(session: AsyncSession, now: datetime) -> set[str]:
    return (await module_states(session, now))[0]
