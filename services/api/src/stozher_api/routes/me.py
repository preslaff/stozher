from fastapi import APIRouter, Depends

from ..auth import Principal, get_principal

router = APIRouter(prefix="/v1")


@router.get("/me")
async def me(p: Principal = Depends(get_principal)) -> dict[str, str]:
    return {"prefix": p.prefix, "label": p.label, "role": p.role}
