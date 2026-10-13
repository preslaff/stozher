"""API key authentication and roles (docs/api.md section 3)."""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ApiKey
from .problems import ApiError

# Hierarchical roles; `system` is a separate lane (ingest, sync, acks only).
RANK = {"public": 0, "responder": 1, "operator": 2, "admin": 3}
ROLES = {*RANK, "system"}


@dataclass(frozen=True)
class Principal:
    prefix: str
    role: str
    label: str


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def new_key(env_name: str) -> tuple[str, str]:
    """Return (full_key, prefix). The full key is shown once and only its hash is stored."""
    prefix = secrets.token_hex(4)
    return f"stz_{env_name}_{prefix}_{secrets.token_urlsafe(32)}", prefix


def parse_prefix(full_key: str) -> str | None:
    parts = full_key.split("_", 3)
    return parts[2] if len(parts) == 4 and parts[0] == "stz" and len(parts[2]) == 8 else None


async def authenticate(session: AsyncSession, full_key: str) -> Principal | None:
    prefix = parse_prefix(full_key)
    if prefix is None:
        return None
    row = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    ).scalar_one_or_none()
    if row is None or not hmac.compare_digest(row.hash, hash_key(full_key)):
        return None
    now = datetime.now(UTC)
    if row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now):
        return None
    return Principal(prefix=row.prefix, role=row.role, label=row.label)


async def get_principal(
    request: Request, x_api_key: str | None = Header(default=None)
) -> Principal:
    if not x_api_key:
        raise ApiError(
            401, "missing-key", "Missing API key", "Send the key in the X-API-Key header"
        )
    async with request.app.state.sessionmaker() as session:
        principal = await authenticate(session, x_api_key)
    if principal is None:
        raise ApiError(401, "invalid-key", "Invalid API key")
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
