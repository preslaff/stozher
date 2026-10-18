"""POST /v1/assistant/query (responder): forwards a question to the optional assistant service.

The assistant is a client of this same API: it receives the caller's own key and uses it for every read, so what it can
see is exactly what the caller can see. It is never in the detection or alert path (CLAUDE.md rule 5). The question text
is not stored; the audit entry records only that a question was asked, by whom, and its mode.
"""

from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..audit import audit
from ..auth import Principal, require_role
from ..problems import ApiError

router = APIRouter(prefix="/v1/assistant")
responder = require_role(minimum="responder")


class QueryIn(BaseModel):
    question: str = Field(default="", max_length=600, description="required in `ask` mode")
    mode: Literal["ask", "report"] = Field(
        default="ask", description="`report` writes a situation report for `bbox`"
    )
    bbox: str | None = Field(
        default=None,
        max_length=80,
        pattern=r"^-?\d+(\.\d+)?(,-?\d+(\.\d+)?){3}$",
        description="minLon,minLat,maxLon,maxLat: the area the question is about (the map view)",
    )


@router.post("/query")
async def query(
    body: QueryIn, request: Request, p: Principal = Depends(responder)
) -> dict[str, Any]:
    url = request.app.state.settings.stozher_assistant_url
    if not url:
        raise ApiError(
            503,
            "assistant-absent",
            "The assistant is not available",
            "the optional assistant module is not running",
        )
    if body.mode == "ask" and not body.question.strip():
        raise ApiError(422, "validation-error", "A question is required")
    async with request.app.state.sessionmaker() as session:
        session.add(audit(p, "assistant.query", body.mode))
        await session.commit()
    try:
        async with httpx.AsyncClient(base_url=url, timeout=120.0) as http:
            r = await http.post(
                "/query",
                json=body.model_dump(),
                headers={"X-API-Key": request.headers.get("X-API-Key", "")},
            )
    except httpx.HTTPError as exc:
        raise ApiError(
            503, "assistant-absent", "The assistant is not available", exc.__class__.__name__
        ) from exc
    if r.status_code >= 400:
        raise ApiError(
            503 if r.status_code >= 500 else r.status_code,
            "assistant-error",
            "The assistant could not answer",
            "no engine is available"
            if r.status_code >= 500
            else "the assistant refused the request",
        )
    return r.json()  # type: ignore[no-any-return]
