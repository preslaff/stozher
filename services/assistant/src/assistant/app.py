"""The assistant's HTTP face. The STOZHER API forwards questions here (POST /v1/assistant/query); it listens on loopback only.

The caller's own key arrives in X-API-Key and is used for every tool call, so the API decides what may be read.
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .agent import Answer, ask
from .config import Settings
from .engines import Engines
from .providers import EngineError
from .report import report
from .tools import Tools

log = logging.getLogger("stozher.assistant")


class QueryIn(BaseModel):
    question: str = Field(default="", max_length=600)
    mode: Literal["ask", "report"] = "ask"
    bbox: str | None = Field(
        default=None,
        max_length=80,
        pattern=r"^-?\d+(\.\d+)?(,-?\d+(\.\d+)?){3}$",
        description="the user's map view",
    )


def create_app(settings: Settings | None = None, engines: Engines | None = None) -> FastAPI:
    s = settings or Settings()
    eng = engines or Engines(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.api = httpx.AsyncClient(base_url=s.stozher_api_url, timeout=15.0)
        yield
        await app.state.api.aclose()
        await eng.aclose()

    app = FastAPI(title="STOZHER assistant", lifespan=lifespan)
    app.state.settings, app.state.engines = s, eng

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine_mode": s.stozher_assistant_engine,
            "cloud": "on" if eng.cloud else "off",
            "local_model": s.stozher_assistant_local_model,
            "local_up": await eng.local_up(),
        }

    @app.post("/query")
    async def query(body: QueryIn, x_api_key: Annotated[str | None, Header()] = None) -> Answer:
        if not x_api_key:
            raise HTTPException(401, "the caller's API key is required")
        tools = Tools(app.state.api, x_api_key, body.bbox)
        if body.mode == "report":
            for provider in eng.candidates():
                return await report(provider, tools, body.bbox)
            return await report(None, tools, body.bbox)  # no engine: the facts alone
        if not body.question.strip():
            raise HTTPException(422, "a question is required")
        last = "no engine is available"
        for provider in eng.candidates():
            try:
                return await ask(
                    provider, tools, body.question, body.bbox, s.stozher_assistant_max_steps
                )
            except EngineError as exc:
                last = str(exc)
                log.warning("engine failed: %s", last)
        raise HTTPException(503, f"no engine could answer ({last})")

    return app
