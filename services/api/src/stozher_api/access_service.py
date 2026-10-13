"""Organisations, named staff, credentials and the two-person workflow (docs/access.md), on the database.

The rules themselves live in `access_policy` (pure). This module applies them: a sensitive action becomes a
pending request that a different named administrator approves; while fewer than two named administrators
exist the platform is in setup mode and one administrator may complete a request (every such entry in the
audit log is flagged). Every rule is checked again when a request executes, not only when it is made.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .access_policy import (
    STAFF_ROLES,
    Actor,
    PolicyError,
    can_approve,
    check_kind_role,
    check_organisation,
    expiry_for,
    is_bootstrap,
)
from .audit import audit
from .auth import RANK, Principal, hash_key
from .models import AdminRequest, ApiKey, Organisation, StaffUser

REQUEST_LIFETIME = timedelta(hours=24)


def actor_of(p: Principal) -> Actor:
    return Actor("user", p.user_id) if p.user_id else Actor("key", p.prefix)


def ref_of(a: Actor) -> str:
    return f"{a.kind}:{a.ref}"


def actor_from_ref(ref: str) -> Actor:
    kind, _, value = ref.partition(":")
    return Actor(kind, value)


async def named_admin_count(session: AsyncSession, now: datetime) -> int:
    """Active administrator accounts that hold at least one live administrator key."""
    return (
        await session.execute(
            select(func.count(func.distinct(StaffUser.id)))
            .join(ApiKey, ApiKey.user_id == StaffUser.id)
            .where(
                StaffUser.role == "admin",
                StaffUser.status == "active",
                ApiKey.role == "admin",
                ApiKey.revoked_at.is_(None),
                ApiKey.expires_at > now,
            )
        )
    ).scalar_one()


async def bootstrap_mode(session: AsyncSession, now: datetime) -> bool:
    return is_bootstrap(await named_admin_count(session, now))


def request_view(r: AdminRequest) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "action": r.action,
        "summary": {k: v for k, v in r.payload.items() if k != "created_by"},
        "requested_by": r.requested_by,
        "requested_at": r.requested_at,
        "expires_at": r.expires_at,
        "status": r.status,
        "decided_by": r.decided_by,
        "decided_at": r.decided_at,
        "result": r.result,
    }


@dataclass
class Outcome:
    status: str  # done or pending
    request: AdminRequest | None
    result: dict[str, Any] | None = None
    secret: str | None = None


# ---- validation shared by request time and execution time ------------------------------------------------------------


async def _org(session: AsyncSession, org_id: str | None) -> Organisation:
    try:
        org = await session.get(Organisation, uuid.UUID(str(org_id))) if org_id else None
    except ValueError:
        org = None
    if org is None:
        raise PolicyError("organisation_id does not name an organisation")
    return org


async def check_key_request(
    session: AsyncSession, payload: dict[str, Any], now: datetime
) -> tuple[Organisation, StaffUser | None, datetime]:
    kind, role = payload["kind"], payload["role"]
    check_kind_role(kind, role)
    org = await _org(session, payload.get("organisation_id"))
    check_organisation(org.type, org.status, org.max_role, role)
    if not (payload.get("purpose") or "").strip():
        raise PolicyError("a credential needs a purpose")
    requested = datetime.fromisoformat(payload["expires_at"]) if payload.get("expires_at") else None
    expires = expiry_for(kind, requested, now)
    user: StaffUser | None = None
    if kind == "staff":
        try:
            user = await session.get(StaffUser, uuid.UUID(str(payload.get("user_id"))))
        except ValueError:
            user = None
        if user is None or user.status != "active":
            raise PolicyError(
                "a staff credential must be bound to an active named account (user_id)"
            )
        if user.organisation_id != org.id:
            raise PolicyError("the account belongs to a different organisation")
        if RANK[role] > RANK[user.role]:
            raise PolicyError(f"the account is a {user.role}; it cannot hold a {role} credential")
    elif payload.get("user_id"):
        raise PolicyError("only staff credentials are bound to a named account")
    return org, user, expires


# ---- executors --------------------------------------------------------------------------------------------------------


async def create_key(
    session: AsyncSession,
    payload: dict[str, Any],
    p: Principal,
    now: datetime,
    env: str,
    bootstrap: bool,
) -> tuple[dict[str, Any], str]:
    org, user, expires = await check_key_request(session, payload, now)
    secret = secrets.token_urlsafe(32)
    for _ in range(5):
        prefix = secrets.token_hex(4)
        if (
            await session.execute(select(ApiKey.id).where(ApiKey.prefix == prefix))
        ).first() is None:
            break
    key = ApiKey(
        prefix=prefix,
        hash=hash_key(f"stz_{env}_{prefix}_{secret}"),
        role=payload["role"],
        label=payload["label"],
        owner_contact=payload.get("owner_contact"),
        rate_limit_per_min=payload.get("rate_limit_per_min"),
        allowed_origins=payload.get("allowed_origins"),
        expires_at=expires,
        created_by=p.prefix,
        organisation_id=org.id,
        purpose=payload["purpose"].strip(),
        kind=payload["kind"],
        user_id=user.id if user else None,
    )
    session.add(key)
    await session.flush()
    session.add(audit(p, "key.create", prefix, bootstrap=bootstrap))
    return {
        "id": str(key.id),
        "prefix": prefix,
        "role": key.role,
        "kind": key.kind,
        "organisation_id": str(org.id),
        "user_id": str(user.id) if user else None,
        "expires_at": expires.isoformat(),
    }, f"stz_{env}_{prefix}_{secret}"


async def activate_organisation(
    session: AsyncSession,
    payload: dict[str, Any],
    p: Principal,
    now: datetime,
    bootstrap: bool,
    agreement: str,
) -> dict[str, Any]:
    org = await _org(session, payload.get("organisation_id"))
    if org.status not in ("proposed", "suspended"):
        raise PolicyError(f"a {org.status} organisation cannot be activated")
    if org.agreement_version != agreement:
        raise PolicyError(f"the organisation has not accepted terms of use version {agreement}")
    org.status = "active"
    org.onboarded_at = org.onboarded_at or now
    session.add(audit(p, "organisation.activate", str(org.id), bootstrap=bootstrap))
    return {"organisation_id": str(org.id), "status": "active"}


async def create_user(
    session: AsyncSession, payload: dict[str, Any], p: Principal, now: datetime, bootstrap: bool
) -> dict[str, Any]:
    org = await _org(session, payload.get("organisation_id"))
    role = payload["role"]
    if role not in STAFF_ROLES:
        raise PolicyError("a staff account is an operator or an admin")
    check_organisation(org.type, org.status, org.max_role, role)
    if (
        await session.execute(
            select(StaffUser.id).where(
                func.lower(StaffUser.work_email) == payload["work_email"].lower()
            )
        )
    ).first():
        raise PolicyError("an account with that work email already exists")
    user = StaffUser(
        organisation_id=org.id,
        display_name=payload["display_name"].strip(),
        work_email=payload["work_email"].strip().lower(),
        role=role,
        status="active",
        created_by=ref_of(actor_of(p)),
    )
    session.add(user)
    await session.flush()
    session.add(audit(p, "user.create", str(user.id), bootstrap=bootstrap))
    return {"user_id": str(user.id), "role": role, "organisation_id": str(org.id)}


async def change_user_role(
    session: AsyncSession, payload: dict[str, Any], p: Principal, now: datetime, bootstrap: bool
) -> dict[str, Any]:
    try:
        user = await session.get(StaffUser, uuid.UUID(str(payload.get("user_id"))))
    except ValueError:
        user = None
    if user is None or user.status != "active":
        raise PolicyError("no such active account")
    role = payload["role"]
    if role not in STAFF_ROLES:
        raise PolicyError("a staff account is an operator or an admin")
    user.role = role
    if RANK[role] < RANK["admin"]:  # a demoted account keeps no key stronger than its role
        await session.execute(
            update(ApiKey)
            .where(ApiKey.user_id == user.id, ApiKey.role == "admin", ApiKey.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    session.add(audit(p, "user.role", str(user.id), bootstrap=bootstrap))
    return {"user_id": str(user.id), "role": role}


async def _execute(
    session: AsyncSession,
    req: AdminRequest,
    p: Principal,
    now: datetime,
    env: str,
    agreement: str,
    bootstrap: bool,
) -> tuple[dict[str, Any], str | None]:
    if req.action == "create_key":
        result, secret = await create_key(session, req.payload, p, now, env, bootstrap)
        return result, secret
    if req.action == "activate_organisation":
        return await activate_organisation(session, req.payload, p, now, bootstrap, agreement), None
    if req.action == "create_user":
        return await create_user(session, req.payload, p, now, bootstrap), None
    if req.action == "change_user_role":
        return await change_user_role(session, req.payload, p, now, bootstrap), None
    raise PolicyError(f"unknown action {req.action}")


# ---- the two-person workflow --------------------------------------------------------------------------------------------


async def submit(
    session: AsyncSession,
    p: Principal,
    action: str,
    payload: dict[str, Any],
    now: datetime,
    *,
    env: str,
    agreement: str,
) -> Outcome:
    """Ask for a sensitive action. In setup mode it runs at once (audited as such); otherwise it waits for a second admin."""
    bootstrap = await bootstrap_mode(session, now)
    req = AdminRequest(
        action=action,
        payload=payload,
        requested_by=ref_of(actor_of(p)),
        expires_at=now + REQUEST_LIFETIME,
    )
    session.add(req)
    await session.flush()
    session.add(audit(p, "request.create", f"{action}:{req.id}", bootstrap=bootstrap))
    if not bootstrap:
        return Outcome("pending", req)
    result, secret = await _execute(session, req, p, now, env, agreement, True)
    req.status, req.decided_by, req.decided_at, req.result = (
        "approved",
        req.requested_by,
        now,
        result,
    )
    session.add(audit(p, "request.approve", f"{action}:{req.id}", bootstrap=True))
    return Outcome("done", req, result, secret)


async def approve(
    session: AsyncSession,
    p: Principal,
    request_id: uuid.UUID,
    now: datetime,
    *,
    env: str,
    agreement: str,
) -> Outcome:
    req = await session.get(AdminRequest, request_id, with_for_update=True)
    if req is None:
        raise LookupError("request not found")
    if req.status != "pending":
        raise PolicyError(f"the request is {req.status}")
    if req.expires_at <= now:
        req.status = "expired"
        raise PolicyError("the request expired; make it again")
    bootstrap = await bootstrap_mode(session, now)
    can_approve(actor_from_ref(req.requested_by), actor_of(p), bootstrap=bootstrap)
    result, secret = await _execute(session, req, p, now, env, agreement, bootstrap)
    req.status, req.decided_by, req.decided_at, req.result = (
        "approved",
        ref_of(actor_of(p)),
        now,
        result,
    )
    session.add(audit(p, "request.approve", f"{req.action}:{req.id}", bootstrap=bootstrap))
    return Outcome("done", req, result, secret)


# ---- suspension and offboarding ---------------------------------------------------------------------------------------------


async def end_organisation(
    session: AsyncSession, p: Principal, org: Organisation, status: str, now: datetime
) -> dict[str, int]:
    """Suspend or offboard: every credential of the organisation is revoked and its staff accounts disabled, at once."""
    revoked = (
        (
            await session.execute(
                update(ApiKey)
                .where(ApiKey.organisation_id == org.id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=now, prev_hash=None, prev_hash_expires_at=None)
                .returning(ApiKey.id)
            )
        )
        .scalars()
        .all()
    )
    disabled = (
        (
            await session.execute(
                update(StaffUser)
                .where(StaffUser.organisation_id == org.id, StaffUser.status == "active")
                .values(status="disabled", disabled_at=now)
                .returning(StaffUser.id)
            )
        )
        .scalars()
        .all()
    )
    org.status = status
    session.add(
        audit(p, f"organisation.{'suspend' if status == 'suspended' else 'offboard'}", str(org.id))
    )
    return {"credentials_revoked": len(revoked), "accounts_disabled": len(disabled)}


async def disable_user(session: AsyncSession, p: Principal, user: StaffUser, now: datetime) -> int:
    user.status, user.disabled_at = "disabled", now
    revoked = (
        (
            await session.execute(
                update(ApiKey)
                .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=now)
                .returning(ApiKey.id)
            )
        )
        .scalars()
        .all()
    )
    session.add(audit(p, "user.disable", str(user.id)))
    return len(revoked)
