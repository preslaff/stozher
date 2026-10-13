"""API key authentication and roles (docs/api.md section 3)."""

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ApiKey
from .problems import ApiError
from .ratelimit import DEFAULT_LIMITS

# Hierarchical roles; `system` is a separate lane (ingest, sync, acks only).
RANK = {"public": 0, "responder": 1, "operator": 2, "admin": 3}
ROLES = {*RANK, "system"}


@dataclass(frozen=True)
class Principal:
    prefix: str
    role: str
    label: str
    rate_limit: int = 0
    expires_at: datetime | None = None
    allowed_origins: tuple[str, ...] = ()


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def new_key(env_name: str) -> tuple[str, str]:
    """Return (full_key, prefix). The full key is shown once and only its hash is stored."""
    prefix = secrets.token_hex(4)
    return f"stz_{env_name}_{prefix}_{secrets.token_urlsafe(32)}", prefix


def parse_prefix(full_key: str) -> str | None:
    parts = full_key.split("_", 3)
    return parts[2] if len(parts) == 4 and parts[0] == "stz" and len(parts[2]) == 8 else None


def _secret_matches(row: ApiKey, full_key: str, now: datetime) -> bool:
    """The current secret, or the previous one while a rotation's grace period lasts."""
    h = hash_key(full_key)
    if hmac.compare_digest(row.hash, h):
        return True
    return (
        row.prev_hash is not None
        and row.prev_hash_expires_at is not None
        and row.prev_hash_expires_at > now
        and hmac.compare_digest(row.prev_hash, h)
    )


async def authenticate(session: AsyncSession, full_key: str) -> Principal | None:
    prefix = parse_prefix(full_key)
    if prefix is None:
        return None
    row = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or not _secret_matches(row, full_key, now):
        return None
    if row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now):
        return None
    return Principal(
        prefix=row.prefix,
        role=row.role,
        label=row.label,
        rate_limit=row.rate_limit_per_min or DEFAULT_LIMITS[row.role],
        expires_at=row.expires_at,
        allowed_origins=tuple(row.allowed_origins or ()),
    )


TOUCH_EVERY_S = 60


async def get_principal(
    request: Request, x_api_key: str | None = Header(default=None)
) -> Principal:
    if not x_api_key:
        raise ApiError(
            401, "missing-key", "Missing API key", "Send the key in the X-API-Key header"
        )
    app = request.app
    async with app.state.sessionmaker() as session:
        principal = await authenticate(session, x_api_key)
        if principal is None:
            raise ApiError(401, "invalid-key", "Invalid API key")
        # Browsers only: a key can be tied to the origins that may use it. Other clients send no Origin.
        origin = request.headers.get("origin")
        if principal.allowed_origins and origin and origin not in principal.allowed_origins:
            raise ApiError(403, "origin-not-allowed", "This key cannot be used from this origin")
        now = time.time()
        if now - app.state.touched.get(principal.prefix, 0.0) >= TOUCH_EVERY_S:
            app.state.touched[principal.prefix] = (
                now  # last_used_at costs one write a minute at most
            )
            await session.execute(
                update(ApiKey)
                .where(ApiKey.prefix == principal.prefix)
                .values(last_used_at=datetime.now(UTC))
            )
            await session.commit()
    state = app.state.limiter.hit(principal.prefix, principal.rate_limit)
    request.state.ratelimit = state
    use = app.state.usage.setdefault(principal.prefix, {"requests": 0, "rate_limited": 0})
    use["requests"] += 1
    if not state.allowed:
        use["rate_limited"] += 1
        raise ApiError(
            429,
            "rate-limited",
            "Too many requests",
            f"limit is {state.limit} requests per minute for this key",
            headers={"Retry-After": str(state.retry_after)},
        )
    return principal


def require_role(*allowed: str, minimum: str | None = None) -> Callable[..., Principal]:
    """Allow explicit roles, or any hierarchical role at or above `minimum`."""

    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        ok = principal.role in allowed or (
            minimum is not None and principal.role in RANK and RANK[principal.role] >= RANK[minimum]
        )
        if not ok:
            raise ApiError(403, "insufficient-role", "Insufficient role")
        return principal

    return dep
