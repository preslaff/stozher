"""The assistant's evaluation set (docs/assistant.md, "Evaluating the assistant").

`eval/cases.json` holds fixed questions, canned /v1 responses ("worlds") and checks. Tier 1 plays each case with a scripted
provider (no model; part of `make test`) and proves the code-enforced rules, including against deliberately bad model answers.
Tier 2, `python -m assistant.evaluation`, asks the configured local engine the same questions and checks the answers with the
same rules. Cases are data, so a new failure becomes a new case.
"""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .agent import Answer, ask
from .config import Settings
from .engines import Engines
from .providers import Message, Provider, ToolCall, ToolSchema, Turn
from .tools import Tools

CASES = Path(__file__).parents[2] / "eval" / "cases.json"


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(CASES.read_text())
    return data


def _resolve(value: Any, now: datetime) -> Any:
    """`observed_ago_s` in a fixture becomes an `observed_at` timestamp relative to now."""
    if isinstance(value, list):
        return [_resolve(v, now) for v in value]
    if isinstance(value, dict):
        out = {k: _resolve(v, now) for k, v in value.items()}
        if "observed_ago_s" in out:
            out["observed_at"] = (now - timedelta(seconds=out.pop("observed_ago_s"))).isoformat()
        return out
    return value


def world_api(spec: dict[str, Any], world: str) -> httpx.AsyncClient:
    """A fake /v1: the base responses (nothing to report) overridden by the world's."""
    routes = _resolve({**spec["base"], **spec["worlds"][world]}, datetime.now(UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={})
        if isinstance(body, dict) and "__status__" in body:
            return httpx.Response(int(body["__status__"]), json={})
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))


class ScriptedProvider:
    """Plays back the case's turns: tool calls, then the model's (possibly bad) answer."""

    name, model = "scripted", "eval"

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.turns = list(script)

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> Turn:
        step = self.turns.pop(0)
        calls = [
            ToolCall(f"c{i}", c["name"], dict(c.get("args") or {}))
            for i, c in enumerate(step.get("calls", []))
        ]
        return Turn(text=step.get("text", ""), calls=calls)


def failures(answer: Answer, expect: dict[str, Any], live: bool) -> list[str]:
    """Why the answer broke the case's expectations; empty means it passed."""
    out: list[str] = []
    expect = dict(expect)
    scripted_only = expect.pop("scripted_only", {})
    if not live:  # what a deliberately bad scripted answer must trigger; a real model may simply not make the mistake
        for key, value in scripted_only.items():
            expect[key] = [*expect.get(key, []), *value] if isinstance(value, list) else value
    # the model's own words: caveats are added by code and legitimately contain phrases like "not an all-clear"
    own = answer.answer
    for note in answer.caveats:
        own = own.replace(note, "")
    own = own.lower()
    if "grounded" in expect and answer.grounded != expect["grounded"]:
        out.append(f"grounded is {answer.grounded}, expected {expect['grounded']}")
    if "data_age_s" in expect and answer.data_age_s != expect["data_age_s"]:
        out.append(f"data_age_s is {answer.data_age_s}, expected {expect['data_age_s']}")
    if "sim" in expect:
        if answer.sim != expect["sim"]:
            out.append(f"sim is {answer.sim}, expected {expect['sim']}")
        if expect["sim"] and not answer.answer.startswith("[SIMULATED]"):
            out.append("a simulated answer is not labelled [SIMULATED]")
    for part in expect.get("caveats_contain", []):
        if not any(part.lower() in c.lower() for c in answer.caveats):
            out.append(f"missing caveat containing {part!r}")
    for part in expect.get("caveats_absent", []):
        if any(part.lower() in c.lower() for c in answer.caveats):
            out.append(f"unexpected caveat containing {part!r}")
    for phrase in [
        *expect.get("forbidden", []),
        *(expect.get("forbidden_live", []) if live else []),
    ]:
        if phrase.lower() in own:
            out.append(f"forbidden wording {phrase!r}")
    for src in expect.get("sources_contain", []):
        if src not in answer.sources:
            out.append(f"source {src!r} missing")
    if expect.get("tools_none") and answer.tools:
        out.append("a refused question reached the tools")
    if not live:  # which tools a real model picks is not a rule; a scripted one is
        names = {t.name for t in answer.tools}
        for tool in expect.get("tools_include", []):
            if tool not in names:
                out.append(f"tool {tool!r} was not used")
    return out


async def run_case(spec: dict[str, Any], case: dict[str, Any], provider: Provider) -> list[str]:
    api = world_api(spec, case["world"])
    try:
        answer = await ask(
            provider, Tools(api, "eval-key", case["bbox"]), case["question"], case["bbox"]
        )
    finally:
        await api.aclose()
    return failures(answer, case["expect"], live=provider.name != "scripted")


async def main() -> int:
    spec = load()
    engines = Engines(Settings())
    try:
        candidates = engines.candidates()
        if not candidates:
            print("no engine configured: start llama-server (see docs/assistant.md)")
            return 2
        provider = candidates[0]
        if not await engines.local_up() and provider is engines.local:
            print(f"the local engine ({provider.name}) is not answering; start it first")
            return 2
        print(f"engine: {provider.name} ({provider.model})\n")
        bad = 0
        for case in (c for c in spec["cases"] if c["live"]):
            t0 = time.monotonic()
            try:
                why = await run_case(spec, case, provider)
            except Exception as exc:  # an engine failure is a failed case, not a crash of the run
                why = [f"engine error: {exc.__class__.__name__}"]
            bad += bool(why)
            print(
                f"{'PASS' if not why else 'FAIL'}  {case['id']:<24} {time.monotonic() - t0:5.1f}s  {'; '.join(why)}"
            )
        total = sum(1 for c in spec["cases"] if c["live"])
        print(f"\n{total - bad}/{total} passed")
        return 1 if bad else 0
    finally:
        await engines.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
