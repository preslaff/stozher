"""Choose the engine: cloud when switched on and reachable, else the local one. Absent engines never block anything."""

import logging

import httpx

from .config import Settings
from .providers import AnthropicProvider, LlamaCppProvider, OllamaProvider, Provider

log = logging.getLogger("stozher.assistant")


class Engines:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        t = settings.stozher_assistant_timeout_s
        self.ollama_mode = settings.stozher_assistant_local_backend.lower() == "ollama"
        self.local_http = httpx.AsyncClient(
            base_url=settings.stozher_ollama_url
            if self.ollama_mode
            else settings.stozher_llamacpp_url,
            timeout=t,
        )
        self.cloud_http = httpx.AsyncClient(base_url="https://api.anthropic.com", timeout=t)
        model = settings.stozher_assistant_local_model
        self.local: Provider = (
            OllamaProvider(self.local_http, model or "qwen2.5:7b-instruct")
            if self.ollama_mode
            else LlamaCppProvider(self.local_http, model)
        )
        self.cloud: Provider | None = (
            AnthropicProvider(
                self.cloud_http, settings.stozher_assistant_cloud_model, settings.anthropic_api_key
            )
            if settings.cloud_enabled
            else None
        )

    async def local_up(self) -> bool:
        try:
            r = await self.local_http.get(
                "/api/tags" if self.ollama_mode else "/health", timeout=2.0
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def candidates(self) -> list[Provider]:
        """Engines to try in order. `local` never uses the cloud, `cloud` never uses the local one."""
        mode = self.s.stozher_assistant_engine
        if mode == "local":
            return [self.local]
        if mode == "cloud":
            return [self.cloud] if self.cloud else []
        return [p for p in (self.cloud, self.local) if p is not None]

    async def aclose(self) -> None:
        await self.local_http.aclose()
        await self.cloud_http.aclose()
