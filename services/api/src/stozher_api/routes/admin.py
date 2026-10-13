"""API key management and the audit log (docs/api.md section 3). Admin role only.

Keys are shown once, at creation or rotation, and only a hash is stored. Nothing here ever returns
a secret or a hash, and nothing writes one to the audit log (only the key's public prefix).
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import Principal, hash_key, require_role
from ..models import ApiKey, AuditLog
from ..problems import ApiError
from ..ratelimit import DEFAULT_LIMITS

router = APIRouter(prefix="/v1/admin")
admin = require_role("admin")
Role = Literal["admin", "operator", "responder", "reporter", "public", "system"]
MAX_GRACE_S = 86_400


def _origins(v: list[str] | None) -> list[str] | None:
    if v is None:
        return v
    if len(v) > 20 or any(not (o.startswith(("http://", "https://")) and len(o) <= 200) for o in v):
        raise ValueError("allowed_origins: at most 20 origins like https://dashboard.example")
    return [o.rstrip("/") for o in v]


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("needs a time zone, e.g. 2026-12-01T00:00:00Z")
    return v


class KeyIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    role: Role
    owner_contact: str | None = Field(default=None, max_length=200)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: datetime | None = None
    allowed_origins: list[str] | None = None

    _o = field_validator("allowed_origins")(_origins)
    _t = field_validator("expires_at")(_aware)


class KeyPatch(BaseModel):
    """Role changes are not allowed here: create a new key and revoke the old one."""

    model_config = {"extra": "forbid"}
    label: str | None = Field(default=None, min_length=1, max_length=120)
    owner_contact: str | None = Field(default=None, max_length=200)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: datetime | None = None
    allowed_origins: list[str] | None = None

    _o = field_validator("allowed_origins")(_origins)
    _t = field_validator("expires_at")(_aware)


class RotateIn(BaseModel):
    grace_s: int = Field(default=3600, ge=0, le=MAX_GRACE_S)


def _view(k: ApiKey, now: datetime) -> dict[str, Any]:
    return {
        "id": str(k.id),
        "prefix": k.prefix,
        "label": k.label,
        "role": k.role,
        "owner_contact": k.owner_contact,
        "rate_limit_per_min": k.rate_limit_per_min,
        "effective_rate_limit": k.rate_limit_per_min or DEFAULT_LIMITS[k.role],
        "allowed_origins": k.allowed_origins,
        "created_at": k.created_at,
        "created_by": k.created_by,
        "expires_at": k.expires_at,
        "revoked_at": k.revoked_at,
        "last_used_at": k.last_used_at,
        "active": k.revoked_at is None and (k.expires_at is None or k.expires_at > now),
        "rotating_until": k.prev_hash_expires_at
        if k.prev_hash_expires_at and k.prev_hash_expires_at > now
        else None,
    }


def _full_key(env: str, prefix: str, secret: str) -> str:
    return f"stz_{env}_{prefix}_{secret}"


async def _get(session: AsyncSession, key_id: uuid.UUID) -> ApiKey:
    k = await session.get(ApiKey, key_id)
    if k is None:
        raise ApiError(404, "not-found", "API key not found")
    return k


def _audit(session: AsyncSession, p: Principal, action: str, k: ApiKey) -> None:
    session.add(
        AuditLog(actor=p.prefix, action=action, target=k.prefix)
    )  # the prefix only, never a secret


@router.post("/api-keys", status_code=201)
async def create_key(
    body: KeyIn, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    if body.expires_at is not None and body.expires_at <= now:
        raise ApiError(
            422, "validation-error", "Invalid expiry", "expires_at must be in the future"
        )
    env = request.app.state.settings.env_name
    secret = secrets.token_urlsafe(32)
    async with request.app.state.sessionmaker() as session:
        for _ in range(5):  # a prefix collision is astronomically unlikely, but it is unique
            prefix = secrets.token_hex(4)
            if (
                await session.execute(select(ApiKey.id).where(ApiKey.prefix == prefix))
            ).first() is None:
                break
        k = ApiKey(
            prefix=prefix,
            hash=hash_key(_full_key(env, prefix, secret)),
            role=body.role,
            label=body.label,
            owner_contact=body.owner_contact,
            rate_limit_per_min=body.rate_limit_per_min,
            allowed_origins=body.allowed_origins,
            expires_at=body.expires_at,
            created_by=p.prefix,
        )
        session.add(k)
        await session.flush()
        _audit(session, p, "key.create", k)
        await session.commit()
        await session.refresh(k)
        return {**_view(k, now), "key": _full_key(env, prefix, secret)}  # the only time it is shown


@router.get("/api-keys")
async def list_keys(
    request: Request,
    role: Role | None = None,
    include_revoked: bool = False,
    _: Principal = Depends(admin),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    q = select(ApiKey).order_by(ApiKey.created_at)
    if role:
        q = q.where(ApiKey.role == role)
    if not include_revoked:
        q = q.where(ApiKey.revoked_at.is_(None))
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    return {"items": [_view(k, now) for k in rows], "count": len(rows)}


@router.get("/api-keys/{key_id}")
async def get_key(
    key_id: uuid.UUID, request: Request, _: Principal = Depends(admin)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        k = await _get(session, key_id)
        view = _view(k, datetime.now(UTC))
    use = request.app.state.usage.get(view["prefix"], {"requests": 0, "rate_limited": 0})
    # Counters live in this API process since it started; durable usage history is not recorded.
    return {
        **view,
        "usage": {**use, "since": "process start", "last_used_at": view["last_used_at"]},
    }


@router.patch("/api-keys/{key_id}")
async def patch_key(
    key_id: uuid.UUID, body: KeyPatch, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    changes = body.model_dump(exclude_unset=True)  # an explicit null clears an optional field
    if changes.get("expires_at") is not None and changes["expires_at"] <= now:
        raise ApiError(
            422, "validation-error", "Invalid expiry", "expires_at must be in the future"
        )
    if changes.get("label", "x") is None:
        raise ApiError(422, "validation-error", "Invalid label", "label cannot be cleared")
    async with request.app.state.sessionmaker() as session:
        k = await _get(session, key_id)
        for field, value in changes.items():
            setattr(k, field, value)
        _audit(session, p, "key.patch", k)
        await session.commit()
        await session.refresh(k)
        return _view(k, now)


@router.post("/api-keys/{key_id}/rotate")
async def rotate_key(
    key_id: uuid.UUID, request: Request, body: RotateIn | None = None, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """New secret under the same prefix; the old secret keeps working for `grace_s` so consumers can switch."""
    now = datetime.now(UTC)
    grace = (body or RotateIn()).grace_s
    env = request.app.state.settings.env_name
    secret = secrets.token_urlsafe(32)
    async with request.app.state.sessionmaker() as session:
        k = await _get(session, key_id)
        if k.revoked_at is not None:
            raise ApiError(409, "revoked", "This key is revoked", "create a new key instead")
        k.prev_hash, k.prev_hash_expires_at = (
            (k.hash, now + timedelta(seconds=grace)) if grace else (None, None)
        )
        k.hash = hash_key(_full_key(env, k.prefix, secret))
        _audit(session, p, "key.rotate", k)
        await session.commit()
        await session.refresh(k)
        return {**_view(k, now), "key": _full_key(env, k.prefix, secret)}


@router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """Revoke immediately. The record stays for the audit trail."""
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as session:
        k = await _get(session, key_id)
        if k.revoked_at is None and k.role == "admin":
            others = (
                await session.execute(
                    select(func.count())
                    .select_from(ApiKey)
                    .where(
                        ApiKey.role == "admin",
                        ApiKey.revoked_at.is_(None),
                        ApiKey.id != k.id,
                        (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now),
                    )
                )
            ).scalar_one()
            if others == 0:
                raise ApiError(
                    409,
                    "last-admin",
                    "Cannot revoke the last active admin key",
                    "create another admin key first",
                )
        if k.revoked_at is None:
            k.revoked_at, k.prev_hash, k.prev_hash_expires_at = now, None, None
            _audit(session, p, "key.revoke", k)
            await session.commit()
            await session.refresh(k)
        return _view(k, now)


@router.get("/audit-log")
async def audit_log(
    request: Request,
    actor: str | None = Query(default=None, max_length=32),
    action: str | None = Query(default=None, max_length=48),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _: Principal = Depends(admin),
) -> dict[str, Any]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action == action)
    if from_:
        q = q.where(AuditLog.at >= from_)
    if to:
        q = q.where(AuditLog.at <= to)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    return {
        "items": [
            {"id": r.id, "actor": r.actor, "action": r.action, "target": r.target, "at": r.at}
            for r in rows
        ],
        "count": len(rows),
    }
