"""Language-model providers behind one interface. The tool set is the same for every engine."""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

ToolSchema = dict[str, Any]  # {"name", "description", "parameters": JSON schema}


class EngineError(Exception):
    """The engine is unreachable or answered badly. The caller degrades; it never blocks anything else."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Turn:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)


# Conversation items are plain dicts so every provider can translate them:
#   {"role": "user", "text": ...} | {"role": "assistant", "text": ..., "calls": [ToolCall]} |
#   {"role": "tool", "id": ..., "name": ..., "text": <json>}
Message = dict[str, Any]


class Provider(Protocol):
    name: str
    model: str

    async def complete(
        self, system: str, messages: list[Message], tools: list[ToolSchema]
    ) -> Turn: ...


class OllamaProvider:
    """Local engine (Ollama /api/chat). Works offline."""

    name = "ollama"

    def __init__(self, http: httpx.AsyncClient, model: str) -> None:
        self.http, self.model = http, model

    @staticmethod
    def _messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": m.get("text", "")}
                if m.get("calls"):
                    item["tool_calls"] = [
                        {"function": {"name": c.name, "arguments": c.args}} for c in m["calls"]
                    ]
                out.append(item)
            else:
                out.append({"role": "tool", "content": m["text"], "tool_name": m["name"]})
        return out

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> Turn:
        body = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.1},
            "messages": self._messages(system, messages),
            "tools": [{"type": "function", "function": t} for t in tools],
        }
        try:
            r = await self.http.post("/api/chat", json=body)
            r.raise_for_status()
            msg = r.json()["message"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EngineError(f"ollama: {exc.__class__.__name__}") from exc
        calls = [
            ToolCall(f"call_{i}", c["function"]["name"], dict(c["function"].get("arguments") or {}))
            for i, c in enumerate(msg.get("tool_calls") or [])
        ]
        return Turn(text=msg.get("content") or "", calls=calls)


class LlamaCppProvider:
    """Local engine: llama.cpp `llama-server` (OpenAI-style /v1/chat/completions). Works offline.

    Tool calls need the server to be started with `--jinja` and a model whose chat template supports tools.
    """

    name = "llamacpp"

    def __init__(self, http: httpx.AsyncClient, model: str) -> None:
        self.http, self.model = (
            http,
            model or "local",
        )  # llama-server answers with the model it loaded

    @staticmethod
    def _messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": m.get("text", "")}
                if m.get("calls"):
                    item["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": dump(c.args)},
                        }
                        for c in m["calls"]
                    ]
                out.append(item)
            else:
                out.append({"role": "tool", "tool_call_id": m["id"], "content": m["text"]})
        return out

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> Turn:
        body = {
            "model": self.model,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 1500,
            "messages": self._messages(system, messages),
            "tools": [{"type": "function", "function": t} for t in tools],
        }
        try:
            r = await self.http.post("/v1/chat/completions", json=body)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            calls = [
                ToolCall(
                    c.get("id") or f"call_{i}",
                    c["function"]["name"],
                    _arguments(c["function"].get("arguments")),
                )
                for i, c in enumerate(msg.get("tool_calls") or [])
            ]
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            raise EngineError(f"llamacpp: {exc.__class__.__name__}") from exc
        return Turn(text=msg.get("content") or "", calls=calls)


def _arguments(raw: Any) -> dict[str, Any]:
    """OpenAI-style arguments are a JSON string; a small model may send an empty or broken one."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("tool arguments are not an object")
    return value


class AnthropicProvider:
    """Cloud engine (Messages API). Built only when the operator switched the cloud on and set a key."""

    name = "anthropic"

    def __init__(self, http: httpx.AsyncClient, model: str, api_key: str) -> None:
        self.http, self.model, self._key = http, model, api_key

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("text"):
                    blocks.append({"type": "text", "text": m["text"]})
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args}
                    for c in m.get("calls", [])
                ]
                out.append({"role": "assistant", "content": blocks})
            else:  # consecutive tool results share one user message
                block = {"type": "tool_result", "tool_use_id": m["id"], "content": m["text"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> Turn:
        body = {
            "model": self.model,
            "max_tokens": 1500,
            "temperature": 0.1,
            "system": system,
            "messages": self._messages(messages),
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ],
        }
        try:
            r = await self.http.post(
                "/v1/messages",
                headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"},
                json=body,
            )
            r.raise_for_status()
            content = r.json()["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EngineError(f"anthropic: {exc.__class__.__name__}") from exc
        text = "".join(b["text"] for b in content if b.get("type") == "text")
        calls = [
            ToolCall(b["id"], b["name"], dict(b.get("input") or {}))
            for b in content
            if b.get("type") == "tool_use"
        ]
        return Turn(text=text, calls=calls)


def dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
