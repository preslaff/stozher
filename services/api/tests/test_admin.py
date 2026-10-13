from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from stozher_api.main import bootstrap_admin
from stozher_api.models import ApiKey, AuditLog

from .test_api import hdr, packet, post, rec, register


def key_hdr(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


async def create(c: httpx.AsyncClient, **over):  # type: ignore[no-untyped-def]
    """A credential for the test rescue service (or the platform for staff roles), the way the rules require."""
    role = over.get("role", "public")
    body: dict = {
        "label": "Port authority routing",
        "role": role,
        "purpose": "tests",
        "kind": "field",
        "organisation_id": c.orgs["rescue_service"],
        **over,
    }  # type: ignore[attr-defined]
    if role in ("operator", "admin"):
        body["organisation_id"] = c.orgs["platform"]  # type: ignore[attr-defined]
        body["kind"] = "break_glass"
        if role == "operator":
            body["kind"] = "staff"
            u = await c.post(
                "/v1/admin/users",
                headers=hdr(c, "admin"),
                json={
                    "organisation_id": body["organisation_id"],
                    "display_name": "Test Operator",
                    "work_email": f"op{len(str(over))}{id(over)}@example.org",
                    "role": "operator",
                },
            )
            body["user_id"] = u.json()["result"]["user_id"]
    elif role == "system":
        body["kind"] = "service"
    elif role == "reporter":
        pass
    return await c.post("/v1/admin/api-keys", json=body, headers=hdr(c, "admin"))


async def me(c: httpx.AsyncClient, key: str, **headers: str):  # type: ignore[no-untyped-def]
    return await c.get("/v1/me", headers={**key_hdr(key), **headers})


async def test_created_key_is_shown_once_and_works(client):  # type: ignore[no-untyped-def]
    r = await create(client, owner_contact="ops@example.org", rate_limit_per_min=30)
    assert r.status_code == 201
    d = r.json()
    key = d.pop("key")
    assert (
        key.startswith(f"stz_live_{d['prefix']}_") and d["role"] == "public" and d["active"] is True
    )
    assert d["effective_rate_limit"] == 30 and d["owner_contact"] == "ops@example.org"
    got = (await me(client, key)).json()
    assert (got["role"], got["label"], got["rate_limit_per_min"]) == (
        "public",
        "Port authority routing",
        30,
    )
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        row = (await s.execute(select(ApiKey).where(ApiKey.prefix == d["prefix"]))).scalar_one()
        assert (
            key not in (row.hash, row.prefix) and key.split("_", 3)[3] not in row.hash
        )  # only a hash is kept
        log = (
            await s.execute(select(AuditLog).where(AuditLog.action == "key.create"))
        ).scalar_one()
        assert log.target == d["prefix"] and key not in f"{log.actor}{log.action}{log.target}"
    listed = (await client.get("/v1/admin/api-keys", headers=hdr(client, "admin"))).json()
    assert "key" not in str(listed) and "hash" not in str(listed) and d["prefix"] in str(listed)
    one = (await client.get(f"/v1/admin/api-keys/{d['id']}", headers=hdr(client, "admin"))).json()
    assert (
        one["usage"]["requests"] >= 1
        and one["usage"]["since"] == "process start"
        and one["last_used_at"]
    )


async def test_only_admins_manage_keys_and_input_is_validated(client):  # type: ignore[no-untyped-def]
    some = (await create(client)).json()["id"]
    for role in ("public", "responder", "operator", "system"):
        for method, path in (
            ("GET", "/v1/admin/api-keys"),
            ("GET", f"/v1/admin/api-keys/{some}"),
            ("DELETE", f"/v1/admin/api-keys/{some}"),
            ("GET", "/v1/admin/audit-log"),
            ("POST", f"/v1/admin/api-keys/{some}/rotate"),
        ):
            r = await client.request(method, path, headers=hdr(client, role))
            assert r.status_code == 403, (role, path)
    assert (await client.get("/v1/admin/api-keys")).status_code == 401
    for bad in (
        {"role": "root"},
        {"rate_limit_per_min": 0},
        {"label": ""},
        {"expires_at": "2020-01-01T00:00:00Z"},
        {"expires_at": "2099-01-01T00:00:00"},
        {"allowed_origins": ["ftp://x"]},
        {"allowed_origins": ["x"] * 21},
    ):
        r = await create(client, **bad)
        assert r.status_code == 422 and r.headers["content-type"].startswith(
            "application/problem+json"
        ), bad
    assert (
        await client.get(
            f"/v1/admin/api-keys/{'0' * 8}-0000-0000-0000-{'0' * 12}", headers=hdr(client, "admin")
        )
    ).status_code == 404


async def test_patch_changes_settings_but_never_the_role(client):  # type: ignore[no-untyped-def]
    d = (await create(client, rate_limit_per_min=30)).json()
    url = f"/v1/admin/api-keys/{d['id']}"
    r = await client.patch(
        url,
        json={
            "label": "Renamed",
            "owner_contact": "x@y.z",
            "rate_limit_per_min": 90,
            "allowed_origins": ["https://dash.example/"],
        },
        headers=hdr(client, "admin"),
    )
    assert r.status_code == 200
    v = r.json()
    assert (v["label"], v["effective_rate_limit"], v["allowed_origins"]) == (
        "Renamed",
        90,
        ["https://dash.example"],
    )
    cleared = (
        await client.patch(url, json={"rate_limit_per_min": None}, headers=hdr(client, "admin"))
    ).json()
    assert (
        cleared["rate_limit_per_min"] is None and cleared["effective_rate_limit"] == 60
    )  # back to the role default
    for bad in ({"role": "admin"}, {"label": None}, {"expires_at": "2020-01-01T00:00:00Z"}):
        assert (
            await client.patch(url, json=bad, headers=hdr(client, "admin"))
        ).status_code == 422, bad


async def test_revoking_takes_effect_at_once_and_the_last_admin_is_protected(client):  # type: ignore[no-untyped-def]
    d = (await create(client, role="responder")).json()
    key = d.pop("key")
    assert (await me(client, key)).status_code == 200
    assert (
        await client.delete(f"/v1/admin/api-keys/{d['id']}", headers=hdr(client, "admin"))
    ).status_code == 200
    r = await me(client, key)
    assert r.status_code == 401 and r.json()["type"].endswith(
        "invalid-key"
    )  # same answer as an unknown key
    assert (
        await client.delete(f"/v1/admin/api-keys/{d['id']}", headers=hdr(client, "admin"))
    ).status_code == 200  # idempotent
    assert d["id"] not in [
        k["id"]
        for k in (await client.get("/v1/admin/api-keys", headers=hdr(client, "admin"))).json()[
            "items"
        ]
    ]
    withrev = (
        await client.get("/v1/admin/api-keys?include_revoked=true", headers=hdr(client, "admin"))
    ).json()["items"]
    assert [k["revoked_at"] is not None for k in withrev if k["id"] == d["id"]] == [True]

    admins = [
        k
        for k in (
            await client.get("/v1/admin/api-keys?role=admin", headers=hdr(client, "admin"))
        ).json()["items"]
    ]
    assert len(admins) == 1
    last = await client.delete(
        f"/v1/admin/api-keys/{admins[0]['id']}", headers=hdr(client, "admin")
    )
    assert last.status_code == 409 and last.json()["type"].endswith("last-admin")
    second = (await create(client, role="admin")).json()
    assert (
        await client.delete(f"/v1/admin/api-keys/{admins[0]['id']}", headers=key_hdr(second["key"]))
    ).status_code == 200
    assert (
        await client.get("/v1/admin/api-keys", headers=hdr(client, "admin"))
    ).status_code == 401  # the old admin key is gone


async def test_rotation_keeps_the_old_secret_for_the_grace_period(client):  # type: ignore[no-untyped-def]
    d = (await create(client, role="operator")).json()
    old = d.pop("key")
    r = await client.post(
        f"/v1/admin/api-keys/{d['id']}/rotate", json={"grace_s": 600}, headers=hdr(client, "admin")
    )
    assert r.status_code == 200
    rot = r.json()
    new = rot.pop("key")
    assert (
        rot["prefix"] == d["prefix"]
        and new != old
        and new.startswith(f"stz_live_{d['prefix']}_")
        and rot["rotating_until"]
    )
    assert (await me(client, new)).status_code == 200 and (
        await me(client, old)
    ).status_code == 200  # both, during grace
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        row = (await s.execute(select(ApiKey).where(ApiKey.prefix == d["prefix"]))).scalar_one()
        row.prev_hash_expires_at = datetime.now(UTC) - timedelta(seconds=1)  # the grace period ends
        await s.commit()
    assert (await me(client, old)).status_code == 401 and (await me(client, new)).status_code == 200
    again = (
        await client.post(
            f"/v1/admin/api-keys/{d['id']}/rotate",
            json={"grace_s": 0},
            headers=hdr(client, "admin"),
        )
    ).json()
    assert (await me(client, new)).status_code == 401 and (
        await me(client, again["key"])
    ).status_code == 200  # no grace
    assert (
        await client.post(
            f"/v1/admin/api-keys/{d['id']}/rotate",
            json={"grace_s": 90000},
            headers=hdr(client, "admin"),
        )
    ).status_code == 422
    await client.delete(f"/v1/admin/api-keys/{d['id']}", headers=hdr(client, "admin"))
    assert (
        await client.post(f"/v1/admin/api-keys/{d['id']}/rotate", headers=hdr(client, "admin"))
    ).status_code == 409
    actions = [
        a["action"]
        for a in (
            await client.get("/v1/admin/audit-log?limit=100", headers=hdr(client, "admin"))
        ).json()["items"]
    ]
    assert actions.count("key.rotate") == 2 and "key.revoke" in actions and "key.create" in actions


async def test_expired_keys_are_refused(client):  # type: ignore[no-untyped-def]
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    d = (await create(client, expires_at=soon)).json()
    key = d.pop("key")
    assert (await me(client, key)).json()["expires_at"] is not None
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        row = (await s.execute(select(ApiKey).where(ApiKey.prefix == d["prefix"]))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    assert (await me(client, key)).status_code == 401


async def test_rate_limit_headers_429_and_window_reset(client):  # type: ignore[no-untyped-def]
    d = (await create(client, rate_limit_per_min=3)).json()
    key = d.pop("key")
    clock = [1_000_020.0]
    client.app.state.limiter.clock = lambda: clock[0]  # type: ignore[attr-defined]
    seen = []
    for _ in range(3):
        r = await me(client, key)
        assert r.status_code == 200 and r.headers["x-ratelimit-limit"] == "3"
        seen.append(int(r.headers["x-ratelimit-remaining"]))
    assert seen == [2, 1, 0] and r.headers["x-ratelimit-reset"] == "1000080"
    blocked = await me(client, key)
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    assert blocked.headers["x-ratelimit-remaining"] == "0" and blocked.headers[
        "content-type"
    ].startswith("application/problem+json")
    assert blocked.json()["type"].endswith("rate-limited")
    clock[0] += 61  # the next window
    ok = await me(client, key)
    assert ok.status_code == 200 and ok.headers["x-ratelimit-remaining"] == "2"
    other = await me(client, client.keys["admin"])  # limits are per key
    assert other.status_code == 200 and other.headers["x-ratelimit-limit"] == "600"
    usage = (
        await client.get(f"/v1/admin/api-keys/{d['id']}", headers=hdr(client, "admin"))
    ).json()["usage"]
    assert usage["rate_limited"] == 1 and usage["requests"] == 5


async def test_headers_reach_responses_built_by_handlers_and_defaults_follow_the_role(client):  # type: ignore[no-untyped-def]
    node = await register(client)
    r = await post(
        client, packet(rec(seq=1), node)
    )  # the ingest handler returns its own JSONResponse
    assert r.status_code == 202 and r.headers["x-ratelimit-limit"] == "6000"
    for role, limit in (("public", "60"), ("responder", "300"), ("operator", "600")):
        assert (await client.get("/v1/me", headers=hdr(client, role))).headers[
            "x-ratelimit-limit"
        ] == limit
    assert (
        "x-ratelimit-limit" not in (await client.get("/health")).headers
    )  # unauthenticated routes are not limited


async def test_a_key_can_be_tied_to_browser_origins(client):  # type: ignore[no-untyped-def]
    d = (await create(client, allowed_origins=["https://dash.example"])).json()
    key = d.pop("key")
    assert (await me(client, key, Origin="https://dash.example")).status_code == 200
    bad = await me(client, key, Origin="https://evil.example")
    assert bad.status_code == 403 and bad.json()["type"].endswith("origin-not-allowed")
    assert (await me(client, key)).status_code == 200  # scripts and servers send no Origin


async def test_audit_log_can_be_filtered(client):  # type: ignore[no-untyped-def]
    d = (await create(client)).json()
    await client.patch(
        f"/v1/admin/api-keys/{d['id']}", json={"label": "x"}, headers=hdr(client, "admin")
    )
    log = (
        await client.get("/v1/admin/audit-log?action=key.patch", headers=hdr(client, "admin"))
    ).json()
    assert log["count"] == 1 and log["items"][0]["target"] == d["prefix"]
    admin_prefix = log["items"][0]["actor"]
    assert (
        await client.get(
            f"/v1/admin/audit-log?actor={admin_prefix}&limit=1", headers=hdr(client, "admin")
        )
    ).json()["count"] == 1
    assert (
        await client.get("/v1/admin/audit-log?actor=nobody", headers=hdr(client, "admin"))
    ).json()["count"] == 0
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert (
        await client.get(
            "/v1/admin/audit-log", params={"from": future}, headers=hdr(client, "admin")
        )
    ).json()["count"] == 0


async def test_bootstrap_admin_is_created_once_from_the_environment(client):  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        await s.execute(text("TRUNCATE api_keys CASCADE"))
        await s.commit()
    boot = "stz_live_0badcafe_" + "s" * 43
    client.app.state.settings.stozher_bootstrap_admin_key = boot  # type: ignore[attr-defined]
    await bootstrap_admin(client.app)  # type: ignore[arg-type]
    await bootstrap_admin(client.app)  # type: ignore[arg-type]
    assert (await me(client, boot)).json()["role"] == "admin"
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert len((await s.execute(select(ApiKey))).scalars().all()) == 1


async def test_browsers_may_read_the_rate_limit_headers(client):  # type: ignore[no-untyped-def]
    r = await client.get(
        "/v1/me", headers={**hdr(client, "responder"), "Origin": "http://localhost:5173"}
    )
    exposed = r.headers["access-control-expose-headers"].lower()
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert all(h in exposed for h in ("x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"))
