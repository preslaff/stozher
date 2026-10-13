import httpx
from sqlalchemy import func, select
from stozher_api.auth import hash_key, new_key
from stozher_api.models import AuditLog, WatchedRoute

from .test_api import hdr

BODY = {
    "name": "Port to ring road",
    "geometry": {"type": "LineString", "coordinates": [[27.85, 43.1989], [27.98, 43.1989]]},
    "buffer_m": 300,
}


async def make(c: httpx.AsyncClient, role: str = "responder", **over):  # type: ignore[no-untyped-def]
    return await c.post("/v1/watched-routes", json={**BODY, **over}, headers=hdr(c, role))


async def other_responder(c: httpx.AsyncClient) -> dict[str, str]:
    from stozher_api.models import ApiKey

    full, prefix = new_key("test")
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        s.add(
            ApiKey(prefix=prefix, hash=hash_key(full), role="responder", label="second responder")
        )
        await s.commit()
    return {"X-API-Key": full}


async def test_create_returns_the_current_status_and_records_a_baseline(client):  # type: ignore[no-untyped-def]
    r = await make(client)
    assert r.status_code == 201
    d = r.json()
    assert (
        d["name"] == "Port to ring road" and d["buffer_m"] == 300 and d["min_level"] == "degraded"
    )
    assert d["notify"] == {"sse": True} and d["overall"]["state"] == "UNKNOWN" and d["segments"]
    assert d["id"] and d["expires_at"] > d["created_at"] and d["sources"] == ["witness_nodes"]
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        w = (await s.execute(select(WatchedRoute))).scalar_one()
        assert (
            w.last_state == "UNKNOWN"
            and w.transitions == 0
            and w.coordinates[0] == [27.85, 43.1989]
        )
        assert (
            await s.execute(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == "route.watch")
            )
        ).scalar_one() == 1


async def test_list_get_patch_delete(client):  # type: ignore[no-untyped-def]
    wid = (await make(client)).json()["id"]
    listed = (await client.get("/v1/watched-routes", headers=hdr(client, "responder"))).json()
    assert listed["count"] == 1 and listed["items"][0]["id"] == wid
    assert (
        listed["items"][0]["geometry"]["type"] == "LineString"
        and listed["items"][0]["state"] == "UNKNOWN"
    )
    assert (await client.get(f"/v1/watched-routes/{wid}", headers=hdr(client, "responder"))).json()[
        "id"
    ] == wid
    p = await client.patch(
        f"/v1/watched-routes/{wid}",
        json={"name": "Renamed", "min_level": "jammed", "buffer_m": 500},
        headers=hdr(client, "responder"),
    )
    assert p.status_code == 200 and (
        p.json()["name"],
        p.json()["min_level"],
        p.json()["buffer_m"],
    ) == ("Renamed", "jammed", 500)
    geo = await client.patch(
        f"/v1/watched-routes/{wid}",
        json={"geometry": BODY["geometry"]},
        headers=hdr(client, "responder"),
    )
    assert geo.status_code == 422  # the geometry cannot change: create a new watch
    assert (
        await client.delete(f"/v1/watched-routes/{wid}", headers=hdr(client, "responder"))
    ).status_code == 204
    assert (
        await client.get(f"/v1/watched-routes/{wid}", headers=hdr(client, "responder"))
    ).status_code == 404
    assert (await client.get("/v1/watched-routes", headers=hdr(client, "responder"))).json()[
        "count"
    ] == 0


async def test_watches_belong_to_their_key(client):  # type: ignore[no-untyped-def]
    wid = (await make(client)).json()["id"]
    stranger = await other_responder(client)
    assert (
        await client.get(f"/v1/watched-routes/{wid}", headers=stranger)
    ).status_code == 404  # 404, not 403
    assert (await client.delete(f"/v1/watched-routes/{wid}", headers=stranger)).status_code == 404
    assert (
        await client.patch(f"/v1/watched-routes/{wid}", json={"name": "x"}, headers=stranger)
    ).status_code == 404
    assert (await client.get("/v1/watched-routes", headers=stranger)).json()["count"] == 0
    # operators and admins can see and manage any
    assert (await client.get("/v1/watched-routes", headers=hdr(client, "operator"))).json()[
        "count"
    ] == 1
    assert (
        await client.get(f"/v1/watched-routes/{wid}", headers=hdr(client, "admin"))
    ).status_code == 200
    assert (
        await client.delete(f"/v1/watched-routes/{wid}", headers=hdr(client, "operator"))
    ).status_code == 204


async def test_roles_and_input_validation(client):  # type: ignore[no-untyped-def]
    assert (await make(client, "public")).status_code == 403
    assert (await client.post("/v1/watched-routes", json=BODY)).status_code == 401
    bad = [
        {"buffer_m": 10},
        {"buffer_m": 5000},
        {"min_level": "catastrophic"},
        {"name": ""},
        {"notify": {"sse": True, "mesh": True}},  # mesh delivery is not built: refused, not ignored
        {"notify": {"webhook_ids": ["x"]}},
        {"geometry": {"type": "LineString", "coordinates": [[27.9, 43.2]]}},
        {"expires_at": "2020-01-01T00:00:00Z"},
        {"expires_at": "2099-01-01T00:00:00Z"},
        {"expires_at": "2026-12-01T00:00:00"},  # no time zone
    ]
    for over in bad:
        r = await make(client, **over)
        assert r.status_code == 422 and r.headers["content-type"].startswith(
            "application/problem+json"
        ), over
    ok = await make(
        client, expires_at="2099-01-01T00:00:00Z".replace("2099", "2026") if False else None
    )
    assert ok.status_code == 201


async def test_at_most_twenty_active_watches_per_key(client):  # type: ignore[no-untyped-def]
    for i in range(20):
        assert (await make(client, name=f"r{i}")).status_code == 201
    over = await make(client, name="one too many")
    assert over.status_code == 409 and over.json()["type"].endswith("limit-reached")
    assert (
        await make(client, role="operator")
    ).status_code == 201  # a different key has its own allowance
    wid = (await client.get("/v1/watched-routes", headers=hdr(client, "responder"))).json()[
        "items"
    ][0]["id"]
    await client.delete(f"/v1/watched-routes/{wid}", headers=hdr(client, "responder"))
    assert (await make(client, name="fits again")).status_code == 201
