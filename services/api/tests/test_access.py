"""Phase A of docs/access.md: organisations, named staff, the two-person rule, the inventory, suspension."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from stozher_api.models import AuditLog

from .test_api import hdr


def kh(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def key_body(c: httpx.AsyncClient, **over):  # type: ignore[no-untyped-def]
    return {
        "label": "Rescue team 3",
        "role": "responder",
        "kind": "field",
        "purpose": "field reports",
        "organisation_id": c.orgs["rescue_service"],
        **over,  # type: ignore[attr-defined]
    }


async def post_key(c: httpx.AsyncClient, headers: dict[str, str] | None = None, **over):  # type: ignore[no-untyped-def]
    return await c.post(
        "/v1/admin/api-keys", json=key_body(c, **over), headers=headers or hdr(c, "admin")
    )


async def named_admin(c: httpx.AsyncClient, n: int):  # type: ignore[no-untyped-def]
    """Create a named admin account with a break-glass-free staff key; returns its key. Works in setup mode."""
    org = c.orgs["platform"]  # type: ignore[attr-defined]
    u = await c.post(
        "/v1/admin/users",
        headers=hdr(c, "admin"),
        json={
            "organisation_id": org,
            "display_name": f"Admin {n}",
            "work_email": f"admin{n}@example.org",
            "role": "admin",
        },
    )
    assert u.status_code in (201, 202), u.text
    k = await c.post(
        "/v1/admin/api-keys",
        headers=hdr(c, "admin"),
        json={
            "label": f"admin {n}",
            "role": "admin",
            "kind": "staff",
            "purpose": "administration",
            "organisation_id": org,
            "user_id": u.json()["result"]["user_id"],
        },
    )
    assert k.status_code == 201, k.text
    return k.json()["key"], u.json()["result"]["user_id"]


async def test_a_key_needs_an_organisation_a_purpose_and_a_matching_kind(client):  # type: ignore[no-untyped-def]
    assert (
        await client.post(
            "/v1/admin/api-keys",
            headers=hdr(client, "admin"),
            json={"label": "x", "role": "public"},
        )
    ).status_code == 422
    for over in (
        {"purpose": ""},
        {"organisation_id": "nope"},
        {"kind": "staff"},
        {"role": "admin"},
        {"role": "operator", "kind": "field"},
        {"kind": "break_glass"},
    ):
        r = await post_key(client, **over)
        assert r.status_code == 422, over
    ok = await post_key(client)
    assert ok.status_code == 201
    d = ok.json()
    assert d["organisation_id"] == client.orgs["rescue_service"] and d["purpose"] == "field reports"  # type: ignore[attr-defined]
    exp = datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00"))
    assert (
        timedelta(days=80) < exp - datetime.now(UTC) < timedelta(days=100)
    )  # field default: 90 days
    too_long = (datetime.now(UTC) + timedelta(days=4000)).isoformat()
    assert (await post_key(client, expires_at=too_long)).status_code == 422
    me = (await client.get("/v1/me", headers=kh(d["key"]))).json()
    assert (
        me["organisation"] == "Test rescue service"
        and me["purpose"] == "field reports"
        and me["account"] is None
    )


async def test_an_organisation_must_be_active_and_permit_the_role(client):  # type: ignore[no-untyped-def]
    org = await client.post(
        "/v1/admin/organisations",
        headers=hdr(client, "admin"),
        json={
            "name": "Harbour police",
            "type": "police",
            "contact_name": "Duty desk",
            "contact_address": "duty@harbour.example",
        },
    )
    assert org.status_code == 201 and org.json()["status"] == "proposed"
    oid = org.json()["id"]
    assert (await post_key(client, organisation_id=oid)).status_code == 422  # not active yet
    # activation needs the accepted terms
    assert (
        await client.post(f"/v1/admin/organisations/{oid}/activate", headers=hdr(client, "admin"))
    ).status_code == 422
    await client.post(
        f"/v1/admin/organisations/{oid}/agreement",
        headers=hdr(client, "admin"),
        json={"version": "0.1"},
    )
    act = await client.post(f"/v1/admin/organisations/{oid}/activate", headers=hdr(client, "admin"))
    assert (
        act.status_code == 200 and act.json()["result"]["status"] == "active"
    )  # setup mode: one admin completes it
    assert (await post_key(client, organisation_id=oid)).status_code == 201
    assert (
        await post_key(client, organisation_id=oid, role="system", kind="service")
    ).status_code == 422
    assert (
        await post_key(client, organisation_id=oid, role="operator", kind="staff")
    ).status_code == 422
    assert (
        await client.post(
            "/v1/admin/organisations",
            headers=hdr(client, "admin"),
            json={
                "name": "Harbour police",
                "type": "police",
                "contact_name": "x1",
                "contact_address": "x@y.z",
            },
        )
    ).status_code == 409
    assert (
        await client.post(
            "/v1/admin/organisations",
            headers=hdr(client, "admin"),
            json={
                "name": "Odd one",
                "type": "pirates",
                "contact_name": "x1",
                "contact_address": "x@y.z",
            },
        )
    ).status_code == 422


async def test_setup_mode_ends_when_two_named_admins_exist_and_then_two_people_are_needed(client):  # type: ignore[no-untyped-def]
    inv = (await client.get("/v1/admin/inventory", headers=hdr(client, "admin"))).json()
    assert inv["setup_mode"] is True and inv["named_admins"] == 0
    a1, _ = await named_admin(client, 1)
    assert (await client.get("/v1/admin/inventory", headers=hdr(client, "admin"))).json()[
        "setup_mode"
    ] is True
    a2, _ = await named_admin(client, 2)
    inv = (await client.get("/v1/admin/inventory", headers=kh(a1))).json()
    assert inv["setup_mode"] is False and inv["named_admins"] == 2
    # a sensitive credential now waits for a different named admin
    r = await client.post(
        "/v1/admin/api-keys",
        headers=kh(a1),
        json={
            "label": "Ops desk",
            "role": "operator",
            "kind": "staff",
            "purpose": "operations desk",
            "organisation_id": client.orgs["platform"],
            "user_id": None,
        },
    )  # type: ignore[attr-defined]
    assert r.status_code == 422  # a staff key without an account is refused up front
    u = await client.post(
        "/v1/admin/users",
        headers=kh(a1),
        json={
            "organisation_id": client.orgs["platform"],
            "display_name": "Olga Operator",  # type: ignore[attr-defined]
            "work_email": "olga@example.org",
            "role": "operator",
        },
    )
    assert u.status_code == 202 and u.json()["status"] == "pending"
    req = u.json()["request"]["id"]
    assert (
        await client.post(f"/v1/admin/requests/{req}/approve", headers=kh(a1))
    ).status_code == 422  # not oneself
    assert (
        await client.post(f"/v1/admin/requests/{req}/approve", headers=hdr(client, "admin"))
    ).status_code == 422  # unnamed
    done = await client.post(f"/v1/admin/requests/{req}/approve", headers=kh(a2))
    assert done.status_code == 200 and done.json()["result"]["role"] == "operator"
    uid = done.json()["result"]["user_id"]
    k = await client.post(
        "/v1/admin/api-keys",
        headers=kh(a1),
        json={
            "label": "Ops desk",
            "role": "operator",
            "kind": "staff",
            "purpose": "operations desk",
            "organisation_id": client.orgs["platform"],
            "user_id": uid,
        },
    )  # type: ignore[attr-defined]
    assert k.status_code == 202 and "key" not in k.json()
    kreq = k.json()["request"]["id"]
    assert "secret" not in str(k.json()) and "hash" not in str(k.json())
    ap = await client.post(f"/v1/admin/requests/{kreq}/approve", headers=kh(a2))
    assert ap.status_code == 200
    staff_key = ap.json()["key"]  # shown once, to the approver
    me = (await client.get("/v1/me", headers=kh(staff_key))).json()
    assert me["role"] == "operator" and me["account"] == "Olga Operator" and me["kind"] == "staff"
    assert (
        await client.post(f"/v1/admin/requests/{kreq}/approve", headers=kh(a2))
    ).status_code == 422  # only once
    # the audit log names the person for a staff action, and the field key action stays organisation + purpose
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        rows = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(AuditLog.action == "request.approve")
                    .order_by(AuditLog.id.desc())
                )
            )
            .scalars()
            .all()
        )
    assert rows[0].actor_user_id is not None and rows[0].bootstrap is False
    log = (await client.get("/v1/admin/audit-log?action=request.approve", headers=kh(a1))).json()[
        "items"
    ]
    assert log[0]["account"] == "Admin 2" and log[0]["setup_mode"] is False
    # an unrequested approval is impossible; a requester can cancel their own request, others cannot
    u2 = await client.post(
        "/v1/admin/users",
        headers=kh(a1),
        json={
            "organisation_id": client.orgs["platform"],
            "display_name": "Pavel P",  # type: ignore[attr-defined]
            "work_email": "pavel@example.org",
            "role": "operator",
        },
    )
    r2 = u2.json()["request"]["id"]
    assert (await client.post(f"/v1/admin/requests/{r2}/cancel", headers=kh(a2))).status_code == 403
    assert (await client.post(f"/v1/admin/requests/{r2}/cancel", headers=kh(a1))).status_code == 200
    assert (
        await client.post(f"/v1/admin/requests/{r2}/approve", headers=kh(a2))
    ).status_code == 422


async def test_setup_mode_actions_are_flagged_in_the_audit_log(client):  # type: ignore[no-untyped-def]
    await named_admin(client, 1)
    log = (await client.get("/v1/admin/audit-log?limit=200", headers=hdr(client, "admin"))).json()[
        "items"
    ]
    assert any(i["setup_mode"] for i in log if i["action"] == "request.approve")


async def test_the_inventory_lists_every_credential_and_flags_legacy_ones(client):  # type: ignore[no-untyped-def]
    await post_key(client, label="Rescue radio hub", purpose="field reports")
    inv = (await client.get("/v1/admin/inventory", headers=hdr(client, "admin"))).json()
    bound = [i for i in inv["items"] if i["label"] == "Rescue radio hub"][0]
    assert (
        bound["organisation"] == "Test rescue service"
        and bound["purpose"] == "field reports"
        and not bound["legacy"]
    )
    assert inv["legacy"] >= 6 and all(
        "hash" not in i and "key" not in i for i in inv["items"]
    )  # the seeded test keys
    for role in ("public", "reporter", "responder", "operator", "system"):
        assert (
            await client.get("/v1/admin/inventory", headers=hdr(client, role))
        ).status_code == 403
        assert (
            await client.get("/v1/admin/organisations", headers=hdr(client, role))
        ).status_code == 403
        assert (await client.get("/v1/admin/users", headers=hdr(client, role))).status_code == 403
        assert (
            await client.get("/v1/admin/requests", headers=hdr(client, role))
        ).status_code == 403


async def test_suspending_an_organisation_revokes_all_its_credentials_at_once(client):  # type: ignore[no-untyped-def]
    keys = [(await post_key(client, label=f"team {i}")).json()["key"] for i in range(3)]
    for k in keys:
        assert (await client.get("/v1/me", headers=kh(k))).status_code == 200
    res = await client.post(
        f"/v1/admin/organisations/{client.orgs['rescue_service']}/suspend",
        headers=hdr(client, "admin"),
    )  # type: ignore[attr-defined]
    assert (
        res.status_code == 200
        and res.json()["status"] == "suspended"
        and res.json()["credentials_revoked"] == 3
    )
    for k in keys:
        assert (await client.get("/v1/me", headers=kh(k))).status_code == 401
    assert (
        await post_key(client)
    ).status_code == 422  # no new credentials for a suspended organisation


async def test_disabling_an_account_revokes_its_keys(client):  # type: ignore[no-untyped-def]
    key, uid = await named_admin(client, 1)
    assert (await client.get("/v1/me", headers=kh(key))).status_code == 200
    r = await client.post(f"/v1/admin/users/{uid}/disable", headers=hdr(client, "admin"))
    assert (
        r.status_code == 200
        and r.json()["credentials_revoked"] == 1
        and r.json()["status"] == "disabled"
    )
    assert (await client.get("/v1/me", headers=kh(key))).status_code == 401


async def test_no_endpoint_returns_reporter_identity_to_non_admins(client):  # type: ignore[no-untyped-def]
    prefixes = [
        k["prefix"]
        for k in (await client.get("/v1/admin/api-keys", headers=hdr(client, "admin"))).json()[
            "items"
        ]
    ]
    assert prefixes
    body = {
        "hazard_type": "road_blocked",
        "lat": 59.33,
        "lon": 18.06,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    await client.post("/v1/reports", json=body, headers=hdr(client, "reporter"))
    for role in ("public", "responder", "operator"):
        for path in ("/v1/hazards", "/v1/alerts", "/v1/nodes", "/v1/me"):
            r = await client.get(path, headers=hdr(client, role))
            if r.status_code != 200:
                continue
            text = r.text
            if path == "/v1/me":
                continue  # a caller sees their own prefix
            assert not any(p in text for p in prefixes), (role, path)
            assert (
                "team_label" not in text
                and "reporter" not in text.replace("reporter_", "")
                or path != "/v1/hazards"
            ), (role, path)
