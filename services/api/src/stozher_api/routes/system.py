"""POST /v1/system/heartbeat (system): an optional module reports that it is alive."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from ..auth import Principal, require_role
from ..heartbeat import DEFAULT_INSTANCE, beat

router = APIRouter(prefix="/v1/system")
system_only = require_role("system")


class HeartbeatIn(BaseModel):
    module: Literal["mesh_bridge", "kineis_adapter", "assistant"]
    info: dict[str, Any] = Field(
        default_factory=dict, description="Small status facts, for example `radio_connected`"
    )
    instance: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,15}$",
        description="Which copy of the module, when a kit runs more than one (a mesh bridge per LoRa band, "
        "e.g. `433`). Omitted: the default copy, listed as `main` in /health",
    )

    @field_validator("instance")
    @classmethod
    def _not_the_default_name(cls, v: str | None) -> str | None:
        if v == DEFAULT_INSTANCE:
            raise ValueError(f"`{DEFAULT_INSTANCE}` is the default copy: omit instance instead")
        return v


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    body: HeartbeatIn, request: Request, _: Principal = Depends(system_only)
) -> None:
    async with request.app.state.sessionmaker() as session:
        await beat(session, body.module, body.info, body.instance)
