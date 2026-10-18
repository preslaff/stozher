"""python -m assistant: serve on loopback and report a heartbeat to the API."""

import asyncio
import logging

import httpx
import uvicorn

from . import heartbeat
from .app import create_app
from .config import Settings


async def main() -> None:
    s = Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = create_app(s)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host=s.stozher_assistant_host, port=s.stozher_assistant_port, log_level="info"
        )
    )
    tasks: list[asyncio.Task[None]] = []
    if s.stozher_system_key:
        http = httpx.AsyncClient(base_url=s.stozher_api_url, timeout=5.0)

        async def info() -> dict[str, object]:
            eng = app.state.engines
            return {
                "engine": "cloud" if eng.cloud else "local",
                "cloud": "on" if eng.cloud else "off",
                "local_up": await eng.local_up(),
            }

        tasks.append(asyncio.create_task(heartbeat.run(http, s.stozher_system_key, info)))
    else:
        logging.getLogger("stozher.assistant").warning(
            "STOZHER_SYSTEM_KEY not set: no heartbeat, the dashboard will show the assistant as absent"
        )
    try:
        await server.serve()
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
