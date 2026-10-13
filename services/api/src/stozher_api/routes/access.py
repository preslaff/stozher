"""Organisations, named staff accounts, the two-person request queue and the access inventory (docs/access.md).

Admin role only. A sensitive action (activating an organisation, creating an account, changing its role,
issuing an admin, operator, staff or break-glass credential) becomes a pending request that a different
named admin approves; in setup mode (fewer than two named admins) one admin completes it and the audit
entry is flagged.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import access_service as svc
from ..access_policy import KINDS, ORG_TYPES, STAFF_ROLES, PolicyError
from ..audit import audit
from ..auth import Principal, require_role
from ..models import AdminRequest, ApiKey, Organisation, StaffUser
from ..problems import ApiError

router = APIRouter(prefix="/v1/admin")
admin = require_role("admin")
OrgRole = Literal["public", "reporter", "responder", "operator", "admin", "system"]


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    type: str
    contact_name: str = Field(min_length=2, max_length=120)
    contact_address: str = Field(min_length=3, max_length=200)
    max_role: OrgRole = "responder"
    review_date: date | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ORG_TYPES:
            raise ValueError(f"type must be one of {', '.join(ORG_TYPES)}")
        return v


class OrgPatch(BaseModel):
    model_config = {"extra": "forbid"}
    contact_name: str | None = Field(default=None, min_length=2, max_length=120)
    contact_address: str | None = Field(default=None, min_length=3, max_length=200)
    max_role: OrgRole | None = None
    review_date: date | None = None


class AgreementIn(BaseModel):
    version: str = Field(min_length=1, max_length=16)


class UserIn(BaseModel):
    organisation_id: str
    display_name: str = Field(min_length=2, max_length=120)
    work_email: str = Field(min_length=5, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["operator", "admin"]


class UserRole(BaseModel):
    role: Literal["operator", "admin"]


def _org_view(o: Organisation) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "name": o.name,
        "type": o.type,
        "status": o.status,
        "max_role": o.max_role,
        "contact_name": o.contact_name,
        "contact_address": o.contact_address,
        "agreement_version": o.agreement_version,
        "agreement_accepted_at": o.agreement_accepted_at,
        "review_date": o.review_date,
        "created_at": o.created_at,
        "created_by": o.created_by,
        "onboarded_at": o.onboarded_at,
    }


def _user_view(u: StaffUser) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "organisation_id": str(u.organisation_id),
        "display_name": u.display_name,
        "work_email": u.work_email,
        "role": u.role,
        "status": u.status,
        "created_at": u.created_at,
        "created_by": u.created_by,
        "disabled_at": u.disabled_at,
    }


async def _org(session: AsyncSession, org_id: uuid.UUID) -> Organisation:
    o = await session.get(Organisation, org_id)
    if o is None:
        raise ApiError(404, "not-found", "Organisation not found")
    return o


def _settings(request: Request) -> tuple[str, str]:
    s = request.app.state.settings
    return s.env_name, s.agreement_version


def _policy(exc: PolicyError) -> ApiError:
    return ApiError(422, "policy", "Not allowed by the access rules", str(exc))


async def _gated(
    request: Request, response: Response, p: Principal, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now(UTC)
    env, agreement = _settings(request)
    async with request.app.state.sessionmaker() as session:
        try:
            out = await svc.submit(session, p, action, payload, now, env=env, agreement=agreement)
        except PolicyError as exc:
            raise _policy(exc) from exc
        await session.commit()
    if out.status == "pending":
        response.status_code = 202
    assert out.request is not None
    return {"status": out.status, "request": svc.request_view(out.request), "result": out.result}


# ---- organisations ----------------------------------------------------------------------------------------------------


@router.post("/organisations", status_code=201)
async def create_organisation(
    body: OrgIn, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """A new organisation starts as `proposed`: it can hold no credential until it accepts the terms and is activated."""
    async with request.app.state.sessionmaker() as session:
        if (
            await session.execute(select(Organisation.id).where(Organisation.name == body.name))
        ).first():
            raise ApiError(409, "conflict", "An organisation with that name exists")
        o = Organisation(**body.model_dump(), status="proposed", created_by=p.prefix)
        session.add(o)
        await session.flush()
        session.add(audit(p, "organisation.create", str(o.id)))
        await session.commit()
        await session.refresh(o)
        return _org_view(o)


@router.get("/organisations")
async def list_organisations(
    request: Request, status: str | None = None, _: Principal = Depends(admin)
) -> dict[str, Any]:
    q = select(Organisation).order_by(Organisation.name)
    if status:
        q = q.where(Organisation.status == status)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    return {"items": [_org_view(o) for o in rows], "count": len(rows)}


@router.get("/organisations/{org_id}")
async def get_organisation(
    org_id: uuid.UUID, request: Request, _: Principal = Depends(admin)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        return _org_view(await _org(session, org_id))


@router.patch("/organisations/{org_id}")
async def patch_organisation(
    org_id: uuid.UUID, body: OrgPatch, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    async with request.app.state.sessionmaker() as session:
        o = await _org(session, org_id)
        if "max_role" in changes and changes["max_role"] is not None:
            from ..auth import RANK

            if RANK[changes["max_role"]] > RANK[o.max_role] and changes["max_role"] in (
                "operator",
                "admin",
                "system",
            ):
                raise ApiError(
                    422,
                    "policy",
                    "Not allowed by the access rules",
                    "raising an organisation to operator or above is a policy decision, not a patch",
                )
        for field, value in changes.items():
            if value is not None or field == "review_date":
                setattr(o, field, value)
        session.add(audit(p, "organisation.patch", str(o.id)))
        await session.commit()
        await session.refresh(o)
        return _org_view(o)


@router.post("/organisations/{org_id}/agreement")
async def record_agreement(
    org_id: uuid.UUID, body: AgreementIn, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """Record that the organisation accepted this version of the terms of use (the signed copy is kept outside the platform)."""
    async with request.app.state.sessionmaker() as session:
        o = await _org(session, org_id)
        o.agreement_version, o.agreement_accepted_at = body.version, datetime.now(UTC)
        session.add(audit(p, "organisation.agreement", f"{o.id}:{body.version}"))
        await session.commit()
        await session.refresh(o)
        return _org_view(o)


@router.post("/organisations/{org_id}/activate")
async def activate_organisation(
    org_id: uuid.UUID, request: Request, response: Response, p: Principal = Depends(admin)
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        await _org(session, org_id)
    return await _gated(
        request, response, p, "activate_organisation", {"organisation_id": str(org_id)}
    )


async def _end(org_id: uuid.UUID, request: Request, p: Principal, status: str) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        o = await _org(session, org_id)
        counts = await svc.end_organisation(session, p, o, status, datetime.now(UTC))
        await session.commit()
        await session.refresh(o)
        return {**_org_view(o), **counts}


@router.post("/organisations/{org_id}/suspend")
async def suspend_organisation(
    org_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """Immediate and single-admin on purpose: every credential of the organisation is revoked and its accounts disabled at once."""
    return await _end(org_id, request, p, "suspended")


@router.post("/organisations/{org_id}/offboard")
async def offboard_organisation(
    org_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    return await _end(org_id, request, p, "offboarded")


# ---- named staff accounts ----------------------------------------------------------------------------------------------


@router.post("/users", status_code=201)
async def create_user(
    body: UserIn, request: Request, response: Response, p: Principal = Depends(admin)
) -> dict[str, Any]:
    out = await _gated(request, response, p, "create_user", body.model_dump())
    response.status_code = 202 if out["status"] == "pending" else 201
    return out


@router.get("/users")
async def list_users(
    request: Request, organisation_id: uuid.UUID | None = None, _: Principal = Depends(admin)
) -> dict[str, Any]:
    q = select(StaffUser).order_by(StaffUser.display_name)
    if organisation_id:
        q = q.where(StaffUser.organisation_id == organisation_id)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    return {"items": [_user_view(u) for u in rows], "count": len(rows)}


@router.post("/users/{user_id}/role")
async def change_role(
    user_id: uuid.UUID,
    body: UserRole,
    request: Request,
    response: Response,
    p: Principal = Depends(admin),
) -> dict[str, Any]:
    return await _gated(
        request, response, p, "change_user_role", {"user_id": str(user_id), "role": body.role}
    )


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """Immediate, single-admin: the account is disabled and every key bound to it is revoked."""
    async with request.app.state.sessionmaker() as session:
        u = await session.get(StaffUser, user_id)
        if u is None:
            raise ApiError(404, "not-found", "Account not found")
        if u.status == "active":
            n = await svc.disable_user(session, p, u, datetime.now(UTC))
        else:
            n = 0
        await session.commit()
        await session.refresh(u)
        return {**_user_view(u), "credentials_revoked": n}


# ---- the request queue --------------------------------------------------------------------------------------------------


@router.get("/requests")
async def list_requests(
    request: Request, status: str | None = "pending", _: Principal = Depends(admin)
) -> dict[str, Any]:
    q = select(AdminRequest).order_by(AdminRequest.requested_at.desc()).limit(200)
    if status:
        q = q.where(AdminRequest.status == status)
    async with request.app.state.sessionmaker() as session:
        rows = (await session.execute(q)).scalars().all()
    return {"items": [svc.request_view(r) for r in rows], "count": len(rows)}


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    """A different named admin approves. A new credential's secret is returned here, once, to the approver."""
    now = datetime.now(UTC)
    env, agreement = _settings(request)
    async with request.app.state.sessionmaker() as session:
        try:
            out = await svc.approve(session, p, request_id, now, env=env, agreement=agreement)
        except LookupError as exc:
            raise ApiError(404, "not-found", "Request not found") from exc
        except PolicyError as exc:
            await session.commit()  # keeps an "expired" status
            raise _policy(exc) from exc
        await session.commit()
        assert out.request is not None
        return {
            "status": out.status,
            "request": svc.request_view(out.request),
            "result": out.result,
            "key": out.secret,
        }


async def _close(
    request_id: uuid.UUID, request: Request, p: Principal, status: str, own_only: bool
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        r = await session.get(AdminRequest, request_id, with_for_update=True)
        if r is None:
            raise ApiError(404, "not-found", "Request not found")
        if r.status != "pending":
            raise ApiError(409, "conflict", f"The request is {r.status}")
        if own_only and r.requested_by != svc.ref_of(svc.actor_of(p)):
            raise ApiError(403, "forbidden", "Only the requester can cancel a request")
        r.status, r.decided_by, r.decided_at = (
            status,
            svc.ref_of(svc.actor_of(p)),
            datetime.now(UTC),
        )
        session.add(audit(p, f"request.{status}", f"{r.action}:{r.id}"))
        await session.commit()
        return svc.request_view(r)


@router.post("/requests/{request_id}/reject")
async def reject_request(
    request_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    return await _close(request_id, request, p, "rejected", False)


@router.post("/requests/{request_id}/cancel")
async def cancel_request(
    request_id: uuid.UUID, request: Request, p: Principal = Depends(admin)
) -> dict[str, Any]:
    return await _close(request_id, request, p, "cancelled", True)


# ---- the access inventory ------------------------------------------------------------------------------------------------


@router.get("/inventory")
async def inventory(request: Request, _: Principal = Depends(admin)) -> dict[str, Any]:
    """Every active credential with its organisation, purpose, kind and (for staff) named account.

    Credentials without an organisation or purpose are legacy and flagged: they work, but must be reissued.
    """
    now = datetime.now(UTC)
    async with request.app.state.sessionmaker() as session:
        keys = (
            await session.execute(
                select(ApiKey, Organisation.name, StaffUser.display_name)
                .outerjoin(Organisation, Organisation.id == ApiKey.organisation_id)
                .outerjoin(StaffUser, StaffUser.id == ApiKey.user_id)
                .where(ApiKey.revoked_at.is_(None))
                .order_by(ApiKey.created_at)
            )
        ).all()
        bootstrap = await svc.bootstrap_mode(session, now)
        named = await svc.named_admin_count(session, now)
    items = []
    for k, org_name, user_name in keys:
        live = k.expires_at is None or k.expires_at > now
        if not live:
            continue
        items.append(
            {
                "prefix": k.prefix,
                "label": k.label,
                "role": k.role,
                "kind": k.kind,
                "purpose": k.purpose,
                "organisation": org_name,
                "account": user_name,
                "created_at": k.created_at,
                "created_by": k.created_by,
                "last_used_at": k.last_used_at,
                "expires_at": k.expires_at,
                "rotating_until": k.prev_hash_expires_at
                if k.prev_hash_expires_at and k.prev_hash_expires_at > now
                else None,
                "legacy": k.organisation_id is None or not k.purpose or k.expires_at is None,
            }
        )
    return {
        "items": items,
        "count": len(items),
        "legacy": sum(1 for i in items if i["legacy"]),
        "setup_mode": bootstrap,
        "named_admins": named,
        "generated_at": now,
        "kinds": list(KINDS),
        "staff_roles": list(STAFF_ROLES),
    }
