"""POST /v1/system/heartbeat (system): an optional module reports that it is alive."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..auth import Principal, require_role
from ..heartbeat import beat

router = APIRouter(prefix="/v1/system")
system_only = require_role("system")


class HeartbeatIn(BaseModel):
    module: Literal["mesh_bridge", "kineis_adapter", "assistant"]
    info: dict[str, Any] = Field(
        default_factory=dict, description="Small status facts, for example `radio_connected`"
    )


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    body: HeartbeatIn, request: Request, _: Principal = Depends(system_only)
) -> None:
    async with request.app.state.sessionmaker() as session:
        await beat(session, body.module, body.info)
