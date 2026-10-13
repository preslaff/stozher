import uuid

from fastapi import APIRouter, Depends, Request

from ..auth import Principal, get_principal
from ..models import Organisation, StaffUser

router = APIRouter(prefix="/v1")


@router.get("/me")
async def me(request: Request, p: Principal = Depends(get_principal)) -> dict[str, object]:
    org_name = account = None
    if p.organisation_id or p.user_id:
        async with request.app.state.sessionmaker() as session:
            if p.organisation_id:
                o = await session.get(Organisation, uuid.UUID(str(p.organisation_id)))
                org_name = o.name if o else None
            if p.user_id:
                u = await session.get(StaffUser, uuid.UUID(str(p.user_id)))
                account = u.display_name if u else None
    return {
        "prefix": p.prefix,
        "label": p.label,
        "role": p.role,
        "rate_limit_per_min": p.rate_limit,
        "expires_at": p.expires_at,
        "organisation": org_name,
        "kind": p.kind,
        "purpose": p.purpose,
        "account": account,
    }
