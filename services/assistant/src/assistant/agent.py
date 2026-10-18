"""The question loop: the model may call read-only tools; the answer is labelled by code."""

from typing import Any

from pydantic import BaseModel, Field

from .guard import caveats, freshest_live_age, refusal
from .providers import EngineError, Message, Provider, ToolCall, dump
from .tools import TOOLS, ToolResult, Tools

SYSTEM = """You are the STOZHER assistant. STOZHER is a situational awareness platform for degraded environments; its first module \
tells people whether they can trust GNSS (satellite navigation) at a place and time, from signed reports by witness nodes.

Rules you must follow:
- Answer ONLY from tool results. Call tools before answering. If a tool returns nothing, say there is no recent evidence. \
No evidence is never an all-clear and you must never call an area safe or clear.
- Always state how old the data is. Expired history (trust_history) shows what was seen, not what is true now.
- Hazard reports: say their tier. Unverified is unverified. Never judge whether a road is passable; only report what the record says.
- Simulated data (sim true) must be called simulated.
- Talk about places and conditions, never about people, vehicles or units. Refuse anything that asks to find or follow someone.
- You never create alerts, change data or give orders. You may suggest cautious actions such as checking a second source of position.
- Keep answers short, plain and civil. Use this shape for situation questions: State; Area; Data age; Source; Suggested action."""


class ToolUse(BaseModel):
    name: str
    args: dict[str, Any]
    data_age_s: int | None
    expired: bool
    error: bool


class Answer(BaseModel):
    answer: str
    engine: str
    model: str
    grounded: bool
    data_age_s: int | None = Field(
        description="age of the freshest live evidence consulted; null if none"
    )
    sim: bool
    sources: list[str]
    caveats: list[str]
    tools: list[ToolUse]


def compose(text: str, notes: list[str], sim: bool) -> str:
    body = text.strip()
    if sim:
        body = f"[SIMULATED] {body}"
    return body + "".join(f"\n\n- {n}" for n in notes)


def context_line(bbox: str | None) -> str:
    return f"(The user's map view is {bbox}.)" if bbox else "(No map view was given.)"


async def ask(
    provider: Provider, tools: Tools, question: str, bbox: str | None, max_steps: int = 6
) -> Answer:
    if (r := refusal(question)) is not None:
        return Answer(
            answer=r,
            engine=provider.name,
            model=provider.model,
            grounded=False,
            data_age_s=None,
            sim=False,
            sources=[],
            caveats=[],
            tools=[],
        )
    messages: list[Message] = [{"role": "user", "text": f"{question}\n{context_line(bbox)}"}]
    used: list[tuple[str, ToolResult]] = []
    uses: list[ToolUse] = []
    nudged = False
    text = ""
    for _ in range(max_steps):
        turn = await provider.complete(SYSTEM, messages, TOOLS)
        if turn.calls:
            messages.append({"role": "assistant", "text": turn.text, "calls": turn.calls})
            for call in turn.calls[:4]:
                res = await tools.call(call.name, call.args)
                used.append((call.name, res))
                uses.append(
                    ToolUse(
                        name=call.name,
                        args=call.args,
                        data_age_s=res.data_age_s,
                        expired=res.expired,
                        error=res.error,
                    )
                )
                messages.append(
                    {"role": "tool", "id": call.id, "name": call.name, "text": dump(res.data)}
                )
            continue
        if not used:
            if nudged:
                break
            nudged = True  # a model that answers from memory gets one reminder
            messages += [
                {"role": "assistant", "text": turn.text},
                {
                    "role": "user",
                    "text": "Use the tools to look up the current data first, then answer from the results only.",
                },
            ]
            continue
        text = turn.text
        break
    grounded = bool(used) and bool(text)
    if not grounded:
        text = "I could not ground an answer in STOZHER data, so I will not guess. Try a more specific question about an area, a point or a route."
    notes = caveats(used)
    sim = any(r.sim for _, r in used)
    return Answer(
        answer=compose(text, notes, sim),
        engine=provider.name,
        model=provider.model,
        grounded=grounded,
        data_age_s=freshest_live_age(used),
        sim=sim,
        sources=sorted({s for _, r in used for s in r.sources}),
        caveats=notes,
        tools=uses,
    )


__all__ = ["Answer", "ToolCall", "EngineError", "ask"]
