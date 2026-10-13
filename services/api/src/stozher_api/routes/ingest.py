"""POST /v1/ingest/{kineis,mesh,direct,sim}. Always 202 with accepted|duplicate|rejected."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import Principal, require_role
from ..ingest_service import IngestResult, ingest

router = APIRouter(prefix="/v1/ingest")
system_only = require_role("system")


class IngestResponse(BaseModel):
    status: str
    reason: str | None = None
    event_id: uuid.UUID | None = None


class KineisBody(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    payload_hex: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] | None = None  # e.g. Doppler position


class MeshBody(BaseModel):
    origin_node: str = Field(min_length=1, max_length=64)  # Meshtastic node number
    payload_hex: str = Field(min_length=1, max_length=210)  # may carry the STZ1: prefix
    hops: int | None = None


class DirectBody(BaseModel):
    external_id: str = Field(min_length=1, max_length=64)
    payload_hex: str = Field(min_length=1, max_length=200)


async def _run(
    request: Request, transport: str, external_id: str, payload: str, meta: dict[str, Any] | None
) -> JSONResponse:
    s = request.app.state.settings
    async with request.app.state.sessionmaker() as session:
        res: IngestResult = await ingest(
            session,
            transport=transport,
            external_id=external_id,
            payload=payload,
            kek=s.stozher_kek,
            replay_window_s=s.replay_window_s,
            meta=meta,
        )
    return JSONResponse(
        IngestResponse(status=res.status, reason=res.reason, event_id=res.event_id).model_dump(
            mode="json"
        ),
        status_code=202,
    )


@router.post("/kineis", status_code=202, response_model=IngestResponse)
async def kineis(
    body: KineisBody, request: Request, _: Principal = Depends(system_only)
) -> JSONResponse:
    return await _run(request, "kineis", body.device_id, body.payload_hex, body.metadata)


@router.post("/mesh", status_code=202, response_model=IngestResponse)
async def mesh(
    body: MeshBody, request: Request, _: Principal = Depends(system_only)
) -> JSONResponse:
    return await _run(request, "mesh", body.origin_node, body.payload_hex, {"hops": body.hops})


@router.post("/direct", status_code=202, response_model=IngestResponse)
async def direct(
    body: DirectBody, request: Request, _: Principal = Depends(system_only)
) -> JSONResponse:
    return await _run(request, "direct", body.external_id, body.payload_hex, None)


@router.post("/sim", status_code=202, response_model=IngestResponse)
async def sim(
    body: DirectBody, request: Request, _: Principal = Depends(system_only)
) -> JSONResponse:
    return await _run(request, "sim", body.external_id, body.payload_hex, None)
