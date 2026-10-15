"""Match new alerts to webhooks and deliver them, with signing, retries and the address checks (docs/api.md section 6).

Run by the worker. The network and the DNS resolver are parameters, so the tests use neither.
"""

import asyncio
import json
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Alert, Webhook, WebhookDelivery
from .routes.alerts import alert_json
from .secrets_box import unseal
from .webhook_logic import UnsafeUrl, check_address, check_url, next_attempt, sign, wants

Resolver = Callable[[str, int], Awaitable[list[str]]]
SCAN_LIMIT = 200  # alerts matched per webhook per run
DELIVER_LIMIT = 50  # deliveries sent per run
TIMEOUT_S = 5.0


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(i[4][0]) for i in infos})


async def ensure_public(url: str, resolver: Resolver) -> None:
    """Refuse a target that is not https or that resolves to a private, loopback or link-local address.

    Checked at registration and again right before each delivery, so a name that later points inside is not called.
    """
    host, port = check_url(url)
    try:
        socket.inet_pton(socket.AF_INET, host)
        return  # an IPv4 literal was already judged by check_url
    except OSError:
        pass
    if ":" in host:
        return  # an IPv6 literal, judged by check_url
    addresses = await resolver(host, port)
    if not addresses:
        raise UnsafeUrl("the host does not resolve")
    for address in addresses:
        check_address(address)


def secret_aad(webhook_id: uuid.UUID) -> bytes:
    return f"webhook:{webhook_id}".encode()


async def dispatch(session: AsyncSession, now: datetime) -> int:
    """Create a delivery for every new alert a webhook wants. Alerts older than a webhook are never sent to it."""
    created = 0
    for hook in (await session.execute(select(Webhook))).scalars().all():
        alerts = (
            (
                await session.execute(
                    select(Alert)
                    .where(Alert.id > hook.last_alert_id)
                    .order_by(Alert.id)
                    .limit(SCAN_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        for a in alerts:
            event = wants(
                events=list(hook.events),
                bbox=hook.bbox,
                min_level=hook.min_level,
                include_sim=hook.include_sim,
                kind=a.kind,
                level=a.level,
                route_id=a.route_id,
                sim=a.sim,
                area=a.area,
            )
            if event is None:
                continue
            res = cast(
                "CursorResult[Any]",
                await session.execute(
                    pg_insert(WebhookDelivery)
                    .values(
                        id=uuid.uuid4(),
                        webhook_id=hook.id,
                        alert_id=a.id,
                        event=event,
                        next_attempt_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["webhook_id", "alert_id"])
                ),
            )
            created += res.rowcount or 0
        if alerts:
            hook.last_alert_id = alerts[-1].id
    await session.commit()
    return created


async def deliver_due(
    session: AsyncSession,
    now: datetime,
    *,
    kek: str,
    client: httpx.AsyncClient,
    resolver: Resolver = system_resolver,
) -> dict[str, int]:
    """Send every due delivery once; record the outcome on the delivery and in `alerts.delivered`."""
    stats = {"ok": 0, "retry": 0, "failed": 0}
    rows = (
        await session.execute(
            select(WebhookDelivery, Webhook, Alert)
            .join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
            .join(Alert, Alert.id == WebhookDelivery.alert_id)
            .where(WebhookDelivery.state == "pending", WebhookDelivery.next_attempt_at <= now)
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(DELIVER_LIMIT)
            .with_for_update(of=WebhookDelivery, skip_locked=True)
        )
    ).all()
    for d, hook, alert in rows:
        status: int | None = None
        error: str | None = None
        try:
            await ensure_public(hook.url, resolver)
            secret = unseal(kek, hook.secret_enc, secret_aad(hook.id)).decode()
            body = json.dumps(
                {"delivery_id": str(d.id), "event": d.event, "alert": alert_json(alert)},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            r = await client.post(
                hook.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "stozher-webhook/1",
                    "X-Stozher-Event": d.event,
                    "X-Stozher-Delivery": str(d.id),
                    "X-Stozher-Signature": sign(secret, body),
                },
                timeout=TIMEOUT_S,
                follow_redirects=False,
            )
            status = r.status_code
            if not 200 <= status < 300:
                error = f"HTTP {status}"
        except UnsafeUrl as exc:
            error = f"unsafe: {exc}"[:120]
        except httpx.HTTPError as exc:
            error = exc.__class__.__name__
        d.attempts += 1
        d.last_status, d.last_error = status, error
        if error is None:
            d.state, d.delivered_at = "ok", now
            stats["ok"] += 1
        else:
            nxt = next_attempt(d.attempts, d.created_at, now)
            if nxt is None:
                d.state = "failed"
                stats["failed"] += 1
            else:
                d.next_attempt_at = nxt
                stats["retry"] += 1
        alert.delivered = {**(alert.delivered or {}), f"webhook:{hook.id}": d.state}
    await session.commit()
    return stats
