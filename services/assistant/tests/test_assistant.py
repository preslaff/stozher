import json
from typing import Any

import httpx
import pytest
from assistant.agent import ask
from assistant.app import create_app
from assistant.config import Settings
from assistant.engines import Engines
from assistant.guard import NO_EVIDENCE, refusal
from assistant.providers import (
    AnthropicProvider,
    EngineError,
    LlamaCppProvider,
    OllamaProvider,
    ToolCall,
    Turn,
)
from assistant.report import report
from assistant.tools import Tools

BBOX = "27.5,42.9,28.4,43.6"


class Scripted:
    """A provider that plays back turns, to drive the loop without a model."""

    name, model = "scripted", "test"

    def __init__(self, *turns: Turn) -> None:
        self.turns, self.seen = list(turns), []

    async def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
        self.seen.append([dict(m) for m in messages])
        return self.turns.pop(0)


def api(routes: dict[str, Any], keys: list[str] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if keys is not None:
            keys.append(request.headers.get("x-api-key", ""))
        hit = routes.get(request.url.path)
        if hit is None:
            return httpx.Response(404, json={})
        return hit if isinstance(hit, httpx.Response) else httpx.Response(200, json=hit)

    return httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))


SITUATION_EMPTY = {
    "hotspots": {"active": 0, "by_kind": {}, "items": []},
    "trust": {"cells": 0, "by_state": {}},
    "sources": ["witness_nodes"],
    "data_age_s": None,
    "confidence": 0.0,
    "sim": False,
}
SITUATION_LIVE = {
    "hotspots": {
        "active": 1,
        "by_kind": {"jamming": 1},
        "items": [
            {"id": "h1", "kind": "jamming", "severity": 3, "source_ellipse": {"type": "Polygon"}}
        ],
    },
    "trust": {"cells": 4, "by_state": {"JAMMED": 4}},
    "sources": ["witness_nodes"],
    "data_age_s": 120,
    "confidence": 0.7,
    "sim": False,
}


def call_situation() -> Turn:
    return Turn(calls=[ToolCall("c1", "situation", {})])


def test_questions_about_people_vehicles_or_weapons_are_refused_by_code() -> None:
    for q in (
        "Where is the driver of the grey truck?",
        "track the convoy",
        "who is following the convoy",
        "which weapon works",
    ):
        assert refusal(q), q
    for q in (
        "Can I trust GNSS on the coastal road?",
        "Is there jamming near Varna?",
        "what hazards are reported",
    ):
        assert refusal(q) is None, q


async def test_a_refused_question_never_reaches_the_model() -> None:
    p = Scripted()
    a = await ask(p, Tools(api({}), "k"), "find the soldier near the bridge", BBOX)
    assert a.grounded is False and a.tools == [] and "not people" in a.answer and p.seen == []


async def test_an_answer_is_grounded_in_tools_and_labelled_by_code() -> None:
    p = Scripted(call_situation(), Turn(text="State: jammed. Area: coastal."))
    a = await ask(
        p, Tools(api({"/v1/situation": SITUATION_LIVE}), "k", BBOX), "is there jamming?", BBOX
    )
    assert a.grounded and a.data_age_s == 120 and a.sources == ["witness_nodes"] and a.sim is False
    assert [t.name for t in a.tools] == ["situation"] and a.caveats == []
    tool_msg = json.loads(p.seen[1][-1]["text"])
    assert "source_ellipse" not in json.dumps(
        tool_msg
    )  # geometry is compacted away before the model sees it


async def test_no_evidence_is_never_left_unmarked() -> None:
    p = Scripted(call_situation(), Turn(text="Everything looks clear."))  # a model that misleads
    a = await ask(p, Tools(api({"/v1/situation": SITUATION_EMPTY}), "k", BBOX), "is it safe?", BBOX)
    assert a.data_age_s is None and NO_EVIDENCE in a.caveats and NO_EVIDENCE in a.answer


async def test_simulated_data_is_labelled() -> None:
    sim = {**SITUATION_LIVE, "sim": True}
    a = await ask(
        Scripted(call_situation(), Turn(text="Jamming.")),
        Tools(api({"/v1/situation": sim}), "k", BBOX),
        "q?",
        BBOX,
    )
    assert a.sim and a.answer.startswith("[SIMULATED]") and any("SIMULATED" in c for c in a.caveats)


async def test_an_ungrounded_answer_is_replaced_not_passed_on() -> None:
    p = Scripted(Turn(text="I recall it is fine."), Turn(text="Yes, fine."))
    a = await ask(p, Tools(api({}), "k"), "is the coast safe?", BBOX)
    assert a.grounded is False and "will not guess" in a.answer and "fine" not in a.answer


async def test_tools_use_the_askers_key_and_a_refusal_by_the_api_is_reported() -> None:
    keys: list[str] = []
    routes = {"/v1/situation": httpx.Response(403, json={})}
    p = Scripted(call_situation(), Turn(text="I could not read it."))
    a = await ask(p, Tools(api(routes, keys), "caller-key", BBOX), "status?", BBOX)
    assert keys == ["caller-key"] and a.tools[0].error and any("incomplete" in c for c in a.caveats)
    assert json.loads(p.seen[1][-1]["text"]) == {"error": "this key's role may not read that"}


async def test_history_is_marked_expired_and_events_hide_identities() -> None:
    hist = {
        "features": [{"properties": {"last_state": "JAMMED"}}],
        "data_age_s": 30000,
        "sim": False,
        "sources": ["witness_nodes"],
    }
    events = {
        "items": [
            {
                "class": 0,
                "node_id": 4243,
                "observed_at": "2026-10-18T09:00:00+00:00",
                "position": {"coordinates": [0.0, 0.0]},
            },
            {
                "class": 1,
                "node_id": 4243,
                "observed_at": "2026-10-18T09:05:00+00:00",
                "position": {"coordinates": [27.9, 43.2]},
            },
        ],
        "count": 2,
        "data_age_s": 100,
        "sources": ["witness_nodes"],
    }
    t = Tools(api({"/v1/trust/history": hist, "/v1/events": events}), "k", BBOX)
    h = await t.call("trust_history", {})
    assert h.expired and h.no_recent_evidence and h.data["expired"] is True
    e = await t.call("recent_events", {"hours": 24})
    blob = json.dumps(e.data)
    assert e.data["without_gnss_position"] == 1 and e.data["count"] == 2
    assert (
        e.data_age_s is not None and e.data_age_s > 100
    )  # the API said 100 s, but that reading had no position: the placed one counts
    assert "4243" not in blob and "27.9" not in blob  # no node IDs, no positions


async def test_a_report_without_any_engine_still_states_the_facts() -> None:
    routes = {
        "/v1/situation": SITUATION_EMPTY,
        "/v1/events": {"items": [], "count": 0, "data_age_s": None, "sources": []},
        "/v1/hotspots": {"items": [], "count": 0, "data_age_s": None, "sources": []},
        "/v1/alerts": {"items": [], "count": 0, "data_age_s": None, "sources": []},
        "/v1/hazards": {"items": [], "count": 0, "data_age_s": None, "sources": []},
        "/v1/trust/history": {"features": [], "data_age_s": None, "sources": []},
    }
    a = await report(None, Tools(api(routes), "k", BBOX), BBOX)
    assert a.engine == "none" and a.grounded
    assert "not an all-clear" in a.answer and "no active hotspot" in a.answer.lower()
    assert (
        "do not mean the roads are clear" not in a.answer
        and "does not mean the roads are clear" in a.answer
    )


async def test_readings_without_a_position_do_not_count_as_recent_evidence() -> None:
    events = {
        "items": [
            {
                "class": "benign",
                "observed_at": "2026-10-18T09:00:00+00:00",
                "position": {"coordinates": [0.0, 0.0]},
            }
        ],
        "count": 1,
        "data_age_s": 5,
        "sources": ["witness_nodes"],
    }
    routes = {"/v1/situation": SITUATION_EMPTY, "/v1/events": events}
    a = await report(None, Tools(api(routes), "k", BBOX), BBOX)
    assert NO_EVIDENCE in a.caveats and a.data_age_s is None
    assert "1 reading(s) arrived without a GNSS position" in a.answer


async def test_a_report_survives_an_engine_failure() -> None:
    class Down:
        name, model = "down", "x"

        async def complete(self, *a):  # type: ignore[no-untyped-def]
            raise EngineError("down")

    a = await report(Down(), Tools(api({"/v1/situation": SITUATION_LIVE}), "k", BBOX), BBOX)
    assert a.engine == "none" and "1 active hotspot" in a.answer


async def test_ollama_and_anthropic_translate_tool_turns() -> None:
    seen: dict[str, Any] = {}

    def ollama(request: httpx.Request) -> httpx.Response:
        seen["ollama"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "situation", "arguments": {"bbox": BBOX}}}
                    ],
                }
            },
        )

    def claude(request: httpx.Request) -> httpx.Response:
        seen["claude"] = json.loads(request.content)
        seen["claude_key"] = request.headers["x-api-key"]
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "tool_use", "id": "t9", "name": "alerts", "input": {}},
                ]
            },
        )

    history = [
        {"role": "user", "text": "q"},
        {
            "role": "assistant",
            "text": "",
            "calls": [ToolCall("c1", "situation", {}), ToolCall("c2", "alerts", {})],
        },
        {"role": "tool", "id": "c1", "name": "situation", "text": "{}"},
        {"role": "tool", "id": "c2", "name": "alerts", "text": "{}"},
    ]
    o = OllamaProvider(
        httpx.AsyncClient(base_url="http://o", transport=httpx.MockTransport(ollama)), "m"
    )
    turn = await o.complete(
        "sys",
        history,
        [{"name": "situation", "description": "d", "parameters": {"type": "object"}}],
    )
    assert turn.calls[0].name == "situation" and turn.calls[0].args == {"bbox": BBOX}
    assert seen["ollama"]["messages"][0] == {"role": "system", "content": "sys"}
    assert [m["role"] for m in seen["ollama"]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    c = AnthropicProvider(
        httpx.AsyncClient(base_url="http://c", transport=httpx.MockTransport(claude)),
        "model",
        "sk-test",
    )
    turn = await c.complete(
        "sys", history, [{"name": "alerts", "description": "d", "parameters": {"type": "object"}}]
    )
    assert turn.text == "ok" and turn.calls[0].id == "t9" and seen["claude_key"] == "sk-test"
    msgs = seen["claude"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == [
        "c1",
        "c2",
    ]  # results share one user message


async def test_llamacpp_translates_openai_style_tool_turns() -> None:
    seen: dict[str, Any] = {}

    def server(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "x1",
                                    "type": "function",
                                    "function": {
                                        "name": "situation",
                                        "arguments": json.dumps({"bbox": BBOX}),
                                    },
                                },
                                {
                                    "type": "function",
                                    "function": {"name": "alerts", "arguments": ""},
                                },
                            ],
                        }
                    }
                ]
            },
        )

    history = [
        {"role": "user", "text": "q"},
        {"role": "assistant", "text": "", "calls": [ToolCall("c1", "situation", {"bbox": BBOX})]},
        {"role": "tool", "id": "c1", "name": "situation", "text": "{}"},
    ]
    p = LlamaCppProvider(
        httpx.AsyncClient(base_url="http://l", transport=httpx.MockTransport(server)), ""
    )
    turn = await p.complete(
        "sys", history, [{"name": "situation", "description": "d", "parameters": {}}]
    )
    assert seen["path"] == "/v1/chat/completions" and seen["body"]["model"] == "local"
    assert turn.calls[0].id == "x1" and turn.calls[0].args == {"bbox": BBOX}
    assert turn.calls[1].id == "call_1" and turn.calls[1].args == {}  # no id, empty arguments
    sent = seen["body"]["messages"]
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == json.dumps(
        {"bbox": BBOX}, separators=(",", ":")
    )  # arguments travel as a JSON string
    assert sent[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}

    broken = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"tool_calls": [{"function": {"name": "a", "arguments": "{oops"}}]}}
                ]
            },
        )
    )
    bad = LlamaCppProvider(httpx.AsyncClient(base_url="http://l", transport=broken), "")
    with pytest.raises(EngineError):
        await bad.complete("s", history, [])


def test_the_cloud_needs_both_the_switch_and_a_key() -> None:
    assert not Settings(stozher_assistant_cloud="on", anthropic_api_key="").cloud_enabled
    assert not Settings(stozher_assistant_cloud="off", anthropic_api_key="sk").cloud_enabled
    assert Settings(stozher_assistant_cloud="on", anthropic_api_key="sk").cloud_enabled
    local = Engines(Settings(stozher_assistant_cloud="off", anthropic_api_key="sk"))
    assert [p.name for p in local.candidates()] == ["llamacpp"]
    both = Engines(Settings(stozher_assistant_cloud="on", anthropic_api_key="sk"))
    assert [p.name for p in both.candidates()] == ["anthropic", "llamacpp"]
    only_local = Engines(
        Settings(
            stozher_assistant_engine="local", stozher_assistant_cloud="on", anthropic_api_key="sk"
        )
    )
    assert [p.name for p in only_local.candidates()] == ["llamacpp"]
    ollama = Engines(Settings(stozher_assistant_local_backend="ollama"))
    assert [p.name for p in ollama.candidates()] == ["ollama"]


async def test_the_http_face_needs_a_key_and_degrades_when_no_engine_is_there() -> None:
    s = Settings(
        stozher_assistant_engine="cloud", stozher_assistant_cloud="off"
    )  # asks for the cloud, which is off
    app = create_app(s)
    async with app.router.lifespan_context(app):
        app.state.api = api(
            {
                "/v1/situation": SITUATION_EMPTY,
                "/v1/events": {"items": [], "count": 0, "data_age_s": None, "sources": []},
            }
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            assert (await c.post("/query", json={"question": "q"})).status_code == 401
            r = await c.post("/query", json={"question": "is it safe?"}, headers={"X-API-Key": "k"})
            assert r.status_code == 503
            bad = await c.post(
                "/query", json={"question": "q", "bbox": "nonsense"}, headers={"X-API-Key": "k"}
            )
            assert bad.status_code == 422
            rep = await c.post(
                "/query", json={"mode": "report", "bbox": BBOX}, headers={"X-API-Key": "k"}
            )
            assert rep.status_code == 200 and rep.json()["engine"] == "none"
            assert (await c.get("/health")).json()["cloud"] == "off"


GRID_ONE_SOURCE = {
    "type": "FeatureCollection",
    "features": [{"properties": {"state": "TRUSTED", "confidence": 0.03, "independent_sources": 1}}]
    * 61,
}
SITUATION_THIN = {
    "hotspots": {"active": 0, "by_kind": {}, "items": []},
    "trust": {"cells": 61, "by_state": {"TRUSTED": 61}},
    "sources": ["witness_nodes"],
    "data_age_s": 2012,
    "confidence": 0.03,
    "sim": False,
}


async def test_situation_names_cells_and_sources_apart() -> None:
    res = await Tools(
        api({"/v1/situation": SITUATION_THIN, "/v1/trust/grid": GRID_ONE_SOURCE}), "k", BBOX
    ).call("situation", {})
    assert res.data["cells_with_evidence"] == 61
    assert res.data["max_independent_sources_in_a_cell"] == 1 and res.sources_count == 1
    assert "trust" not in res.data  # the ambiguous bare count is gone


async def test_thin_evidence_gets_a_caveat_and_overclaims_are_removed() -> None:
    p = Scripted(
        call_situation(),
        Turn(
            text="State: TRUSTED. Data age: 33 minutes. Source: 61 witness nodes. "
            "Suggested action: No immediate action required."
        ),
    )
    a = await ask(
        p,
        Tools(api({"/v1/situation": SITUATION_THIN, "/v1/trust/grid": GRID_ONE_SOURCE}), "k", BBOX),
        "can I trust GNSS here?",
        BBOX,
    )
    assert "No immediate action required" not in a.answer
    assert any("thin evidence (confidence 0.03, 1 independent source" in c for c in a.caveats)
    assert any("removed" in c for c in a.caveats) and a.grounded


async def test_strong_evidence_keeps_neutral_wording() -> None:
    strong = {**SITUATION_LIVE, "confidence": 0.8}
    grid = {"features": [{"properties": {"independent_sources": 3}}]}
    p = Scripted(call_situation(), Turn(text="State: jammed. It is not safe to rely on GNSS."))
    a = await ask(
        p,
        Tools(api({"/v1/situation": strong, "/v1/trust/grid": grid}), "k", BBOX),
        "is there jamming?",
        BBOX,
    )
    assert "not safe to rely" in a.answer or "State: jammed" in a.answer
    assert not any("thin evidence" in c for c in a.caveats)


async def test_an_answer_that_is_only_an_overclaim_becomes_ungrounded() -> None:
    p = Scripted(call_situation(), Turn(text="All clear, nothing to worry about."))
    a = await ask(p, Tools(api({"/v1/situation": SITUATION_EMPTY}), "k", BBOX), "is it safe?", BBOX)
    assert a.grounded is False and "could not ground" in a.answer
