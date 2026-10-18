import httpx
from sqlalchemy import text

from .test_api import hdr


def fake_assistant(seen: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"answer": "x", "engine": "fake"})

    return httpx.MockTransport(handler)


async def test_the_assistant_is_absent_until_it_is_configured(client):  # type: ignore[no-untyped-def]
    r = await client.post(
        "/v1/assistant/query", json={"question": "status?"}, headers=hdr(client, "responder")
    )
    assert r.status_code == 503 and r.json()["type"].endswith("assistant-absent")


async def test_only_responders_and_above_may_ask(client):  # type: ignore[no-untyped-def]
    client.app.state.settings.stozher_assistant_url = "http://assistant"  # type: ignore[attr-defined]
    for role, code in (("public", 403), ("reporter", 403), ("system", 403)):
        r = await client.post(
            "/v1/assistant/query", json={"question": "q"}, headers=hdr(client, role)
        )
        assert r.status_code == code, role
    assert (await client.post("/v1/assistant/query", json={"question": "q"})).status_code == 401


async def test_a_question_is_forwarded_with_the_askers_key_and_audited_without_its_text(
    client, monkeypatch
):  # type: ignore[no-untyped-def]
    client.app.state.settings.stozher_assistant_url = "http://assistant"  # type: ignore[attr-defined]
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def patched(*a, **kw):  # type: ignore[no-untyped-def]
        kw["transport"] = fake_assistant(seen)
        return real(*a, **kw)

    monkeypatch.setattr("stozher_api.routes.assistant.httpx.AsyncClient", patched)
    key = client.keys["responder"]
    r = await client.post(
        "/v1/assistant/query",
        json={"question": "secret wording", "bbox": "27.5,42.9,28.4,43.6"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 200 and r.json()["engine"] == "fake"
    assert seen[0].headers["x-api-key"] == key and seen[0].url.path == "/query"
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        rows = (
            await s.execute(
                text("SELECT action, target FROM audit_log WHERE action = 'assistant.query'")
            )
        ).all()
    assert rows == [("assistant.query", "ask")]  # the question itself is not stored
    bad = await client.post(
        "/v1/assistant/query", json={"question": "q", "bbox": "x"}, headers=hdr(client, "responder")
    )
    assert bad.status_code == 422
    empty = await client.post(
        "/v1/assistant/query", json={"question": "  "}, headers=hdr(client, "responder")
    )
    assert empty.status_code == 422


async def test_an_assistant_that_is_down_degrades_to_503(client):  # type: ignore[no-untyped-def]
    client.app.state.settings.stozher_assistant_url = "http://127.0.0.1:9"  # nothing listens here  # type: ignore[attr-defined]
    r = await client.post(
        "/v1/assistant/query", json={"question": "q"}, headers=hdr(client, "responder")
    )
    assert r.status_code == 503
    assert (await client.get("/health")).json()["modules"]["assistant"] == "absent"
