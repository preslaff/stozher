"""Field-kit sync (docs/api.md section 8): the cloud side (serve and accept records) and the kit side (pull and push).

Only witness records travel, never derived state. Each record is verified again by whoever receives it, under the node's own
key, so a sync peer cannot make up evidence: it can only pass on what a node signed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .ingest_service import ingest
from .models import Event, SyncCursor

SETTLE = timedelta(
    seconds=5
)  # a row this young may still have an older sync_seq committing: not served yet
PAGE = 200
MAX_PAGES = 25  # per run; the next run carries on
TIMEOUT_S = 10.0


async def events_since(
    session: AsyncSession, since: int, limit: int, now: datetime
) -> tuple[list[Event], bool]:
    """Non-simulated events after the cursor, in cursor order, and whether more follow."""
    rows = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.sync_seq > since, Event.sim.is_(False), Event.ingested_at <= now - SETTLE
                )
                .order_by(Event.sync_seq)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    return list(rows[:limit]), len(rows) > limit


def event_item(e: Event) -> dict[str, Any]:
    return {
        "cursor": e.sync_seq,
        "node_id": e.node_id,
        "payload_hex": e.payload_hex,
        "received_via": e.received_via,
        "ingested_at": e.ingested_at.isoformat(),
    }


async def accept_records(
    session: AsyncSession, records: list[dict[str, Any]], *, kek: str, replay_window_s: int
) -> list[dict[str, Any]]:
    """Verify and store uploaded records, one result per record, in order. Old records are fine; future ones are not."""
    out: list[dict[str, Any]] = []
    for rec in records:
        res = await ingest(
            session,
            transport="sync",
            external_id="kit",
            payload=rec["payload_hex"],
            kek=kek,
            replay_window_s=replay_window_s,
            meta={"via": rec.get("received_via")},
            node_id=rec["node_id"],
            origin="synced",
            via=rec.get("received_via") or "sync",
            check_window=False,
        )
        out.append({"status": res.status, "reason": res.reason})
    return out


@dataclass
class SyncStats:
    pulled: int = 0
    pushed: int = 0
    rejected: int = 0
    error: str | None = None


async def _cursor(session: AsyncSession, name: str) -> SyncCursor:
    c = await session.get(SyncCursor, name)
    if c is None:
        c = SyncCursor(name=name, cursor=0)
        session.add(c)
        await session.flush()
    return c


async def run_sync(
    session: AsyncSession,
    client: httpx.AsyncClient,
    *,
    cloud_url: str,
    cloud_key: str,
    kek: str,
    replay_window_s: int,
    now: datetime | None = None,
) -> SyncStats:
    """One round on a kit: catch up from the cloud, then upload what only this kit has. Network trouble is recorded, not raised."""
    now = now or datetime.now(UTC)
    base = cloud_url.rstrip("/")
    headers = {"X-API-Key": cloud_key}
    stats = SyncStats()
    dcur = (await _cursor(session, "download")).cursor  # plain ints: a commit expires ORM objects
    ucur = (await _cursor(session, "upload")).cursor
    try:
        for _ in range(MAX_PAGES):
            r = await client.get(
                f"{base}/v1/sync/events",
                params={"since": dcur, "limit": PAGE},
                headers=headers,
                timeout=TIMEOUT_S,
            )
            r.raise_for_status()
            page = r.json()
            for item in page["items"]:
                res = await ingest(
                    session,
                    transport="sync",
                    external_id="cloud",
                    payload=item["payload_hex"],
                    kek=kek,
                    replay_window_s=replay_window_s,
                    node_id=item["node_id"],
                    origin="synced",
                    via=item.get("received_via"),
                    check_window=False,
                )
                if res.status == "accepted":
                    stats.pulled += 1
                elif res.status == "rejected":
                    stats.rejected += 1  # e.g. a node this kit does not know; not retried
            dcur = page["next_cursor"]
            (await _cursor(session, "download")).cursor = dcur
            await session.commit()
            if not page["has_more"]:
                break
        for _ in range(MAX_PAGES):
            rows = (
                (
                    await session.execute(
                        select(Event)
                        .where(
                            Event.sync_seq > ucur,
                            Event.origin != "synced",
                            Event.sim.is_(False),
                            Event.ingested_at <= now - SETTLE,
                        )
                        .order_by(Event.sync_seq)
                        .limit(PAGE)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            r = await client.post(
                f"{base}/v1/sync/upload",
                json={
                    "records": [
                        {
                            "node_id": e.node_id,
                            "payload_hex": e.payload_hex,
                            "received_via": e.received_via,
                        }
                        for e in rows
                    ]
                },
                headers=headers,
                timeout=TIMEOUT_S,
            )
            r.raise_for_status()
            results = r.json()["results"]
            stats.pushed += sum(1 for x in results if x["status"] == "accepted")
            stats.rejected += sum(1 for x in results if x["status"] == "rejected")
            ucur = rows[-1].sync_seq or ucur
            (await _cursor(session, "upload")).cursor = ucur
            await session.commit()
            if len(rows) < PAGE:
                break
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        stats.error = f"{type(exc).__name__}"[:200]
    await session.rollback()
    for name in ("download", "upload"):
        c = await _cursor(session, name)
        c.updated_at = now
        if stats.error is None:
            c.last_ok_at, c.last_error = now, None
        else:
            c.last_error = stats.error
    await session.commit()
    return stats
