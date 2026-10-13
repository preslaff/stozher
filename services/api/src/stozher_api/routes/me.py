from fastapi import APIRouter, Depends

from ..auth import Principal, get_principal

router = APIRouter(prefix="/v1")


@router.get("/me")
async def me(p: Principal = Depends(get_principal)) -> dict[str, object]:
    return {
        "prefix": p.prefix,
        "label": p.label,
        "role": p.role,
        "rate_limit_per_min": p.rate_limit,
        "expires_at": p.expires_at,
    }
