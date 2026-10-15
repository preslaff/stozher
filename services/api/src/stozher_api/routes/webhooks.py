"""POST/GET/DELETE /v1/webhooks (operator): push subscriptions for other systems (docs/api.md section 6)."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from ..audit import audit
from ..auth import Principal, require_role
from ..models import Alert, Webhook, WebhookDelivery
from ..problems import ApiError
from ..secrets_box import KekMissingError, seal
from ..webhook_logic import EVENTS, UnsafeUrl
from ..webhook_service import ensure_public, secret_aad, system_resolver
from .geo import parse_bbox

router = APIRouter(prefix="/v1/webhooks")
operator = require_role(minimum="operator")
MAX_WEBHOOKS = 50


class WebhookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=9, max_length=500)
    events: list[Literal["alert", "hotspot", "trust_change", "route"]] = Field(min_length=1)
    bbox: list[float] | None = Field(default=None, description="minLon,minLat,maxLon,maxLat")
    min_level: Literal["info", "warning", "critical"] = "info"
    include_sim: bool = Field(
        default=False, description="Also deliver simulated alerts (they carry sim=true)"
    )

    @field_validator("events")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v), key=EVENTS.index)


def _view(
    h: Webhook, counts: dict[str, int] | None = None, secret: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(h.id),
        "url": h.url,
        "events": h.events,
        "bbox": h.bbox,
        "min_level": h.min_level,
        "include_sim": h.include_sim,
        "created_by": h.created_by,
        "created_at": h.created_at,
    }
    if counts is not None:
        out["deliveries"] = {s: counts.get(s, 0) for s in ("pending", "ok", "failed")}
    if secret is not None:
        out["secret"] = secret  # shown once; only a sealed copy is stored
    return out


@router.post("", status_code=201)
async def create(
    body: WebhookIn, request: Request, p: Principal = Depends(operator)
) -> dict[str, Any]:
    box = None
    if body.bbox is not None:
        parsed = parse_bbox(",".join(str(v) for v in body.bbox))
        box = list(parsed) if parsed else None
    try:
        resolver = getattr(request.app.state, "webhook_resolver", system_resolver)
        await ensure_public(body.url, resolver)
    except UnsafeUrl as exc:
        raise ApiError(422, "unsafe-url", "That URL cannot be used", str(exc)) from exc
    kek = request.app.state.settings.stozher_kek
    secret = "whsec_" + uuid.uuid4().hex + uuid.uuid4().hex
    hook_id = uuid.uuid4()
    async with request.app.state.sessionmaker() as session:
        total = (await session.execute(select(func.count()).select_from(Webhook))).scalar_one()
        if total >= MAX_WEBHOOKS:
            raise ApiError(
                409, "too-many-webhooks", "Webhook limit reached", f"at most {MAX_WEBHOOKS}"
            )
        try:
            sealed = seal(kek, secret.encode(), secret_aad(hook_id))
        except KekMissingError as exc:
            raise ApiError(503, "kek-missing", "Server cannot store secrets", str(exc)) from exc
        newest = (await session.execute(select(func.coalesce(func.max(Alert.id), 0)))).scalar_one()
        hook = Webhook(
            id=hook_id,
            url=body.url,
            events=body.events,
            bbox=box,
            min_level=body.min_level,
            include_sim=body.include_sim,
            secret_enc=sealed,
            created_by=p.prefix,
            last_alert_id=int(newest),  # only alerts raised from now on
            created_at=datetime.now(UTC),
        )
        session.add(hook)
        session.add(audit(p, "webhook.create", str(hook_id)))
        await session.commit()
        return _view(hook, {}, secret)


@router.get("")
async def list_webhooks(request: Request, _: Principal = Depends(operator)) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        hooks = (
            (await session.execute(select(Webhook).order_by(Webhook.created_at))).scalars().all()
        )
        rows = (
            await session.execute(
                select(WebhookDelivery.webhook_id, WebhookDelivery.state, func.count()).group_by(
                    WebhookDelivery.webhook_id, WebhookDelivery.state
                )
            )
        ).all()
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for wid, state, n in rows:
        counts.setdefault(wid, {})[state] = n
    return {"items": [_view(h, counts.get(h.id, {})) for h in hooks], "count": len(hooks)}


@router.delete("/{webhook_id}", status_code=204)
async def delete(webhook_id: uuid.UUID, request: Request, p: Principal = Depends(operator)) -> None:
    async with request.app.state.sessionmaker() as session:
        hook = await session.get(Webhook, webhook_id)
        if hook is None:
            raise ApiError(404, "not-found", "Webhook not found")
        await session.delete(hook)  # its deliveries go with it
        session.add(audit(p, "webhook.delete", str(webhook_id)))
        await session.commit()
