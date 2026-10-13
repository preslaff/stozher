import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text
from stozher_api.auth import hash_key, new_key
from stozher_api.models import ApiKey, HazardReport
from stozher_api.routes.reports import ReportIn

from .test_api import hdr


def iso(delta: timedelta = timedelta(minutes=-5)) -> str:
    return (datetime.now(UTC) + delta).isoformat().replace("+00:00", "Z")


def body(**over):  # type: ignore[no-untyped-def]
    base = {
        "kind": "flood",
        "passability": "blocked",
        "observed_at": iso(),
        "geometry": {"type": "Point", "coordinates": [27.91, 43.2]},
    }
    return {**base, **over}


async def submit(c: httpx.AsyncClient, role: str = "reporter", **over):  # type: ignore[no-untyped-def]
    return await c.post("/v1/reports", json=body(**over), headers=hdr(c, role))


async def new_reporter(c: httpx.AsyncClient) -> dict[str, str]:
    full, prefix = new_key("live")
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        s.add(ApiKey(prefix=prefix, hash=hash_key(full), role="reporter", label="second team"))
        await s.commit()
    return {"X-API-Key": full}


async def test_a_report_is_four_fields_and_comes_back_unverified(client):  # type: ignore[no-untyped-def]
    r = await submit(client, geometry={"type": "Point", "coordinates": [27.9100049, 43.2000051]})
    assert r.status_code == 202
    d = r.json()
    assert set(d) == {"id", "hazard_id", "tier", "status"}
    assert (d["hazard_id"], d["tier"], d["status"]) == (None, "unverified", "accepted")
    me = (await client.get("/v1/me", headers=hdr(client, "reporter"))).json()
    assert me["prefix"] not in r.text  # no response carries a reporter prefix
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        row = (await s.execute(select(HazardReport))).scalar_one()
        assert (row.kind, row.passability, row.status, row.sim) == (
            "flood",
            "blocked",
            "accepted",
            False,
        )
        assert row.reporter_prefix == me["prefix"]
        lon, lat = (
            await s.execute(text("select st_x(geometry), st_y(geometry) from hazard_reports"))
        ).one()
        assert (lon, lat) == (
            27.91,
            43.20001,
        )  # 27.9100049 and 43.2000051 rounded to 5 decimals (about 1 m)


async def test_roles(client):  # type: ignore[no-untyped-def]
    assert (await submit(client, "public")).status_code == 403
    assert (await submit(client, "system")).status_code == 403
    assert (await client.post("/v1/reports", json=body())).status_code == 401
    for role in ("reporter", "responder", "operator", "admin"):
        assert (await submit(client, role)).status_code == 202, role
    # a reporter can do nothing beyond reporting and public reads
    rep = hdr(client, "reporter")
    assert (await client.get("/v1/events", headers=rep)).status_code == 403
    assert (await client.get("/v1/hotspots", headers=rep)).status_code == 403
    assert (await client.get("/v1/watched-routes", headers=rep)).status_code == 403
    assert (
        await client.get("/v1/trust", params={"lat": 43.2, "lon": 27.9}, headers=rep)
    ).status_code == 200
    assert (await client.get("/v1/admin/api-keys", headers=rep)).status_code == 403


async def test_closed_taxonomy_and_geometry_limits(client):  # type: ignore[no-untyped-def]
    client.app.state.settings.report_limit_per_hour = (
        1000  # this test sends many reports on purpose
    )

    def line(n: int, step: float) -> dict:
        return {"type": "LineString", "coordinates": [[27.9 + i * step, 43.2] for i in range(n)]}

    bad = [
        {"kind": "armed_group"},
        {"kind": "checkpoint"},
        {"kind": "other"},
        {"kind": ""},
        {"kind": "closed_by_authority"},  # official sources only
        {"passability": "dangerous"},
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[27.9, 43.2], [27.91, 43.2], [27.9, 43.21], [27.9, 43.2]]],
            }
        },
        {"geometry": {"type": "MultiPoint", "coordinates": [[27.9, 43.2]]}},
        {"geometry": {"type": "Point", "coordinates": [27.9, 43.2, 120.0]}},  # no altitude
        {"geometry": {"type": "Point", "coordinates": [200.0, 43.2]}},
        {"geometry": {"type": "Point", "coordinates": [27.9, 91.0]}},
        {"geometry": line(2, 0.01)},  # about 800 m: over the 500 m limit
        {"geometry": line(51, 0.00001)},  # too many vertices
        {"geometry": {"type": "LineString", "coordinates": [[27.9, 43.2]]}},
        {"observed_at": "2026-11-28T10:00:00"},  # no time zone
        {"observed_at": iso(timedelta(hours=1))},  # the future
        {"observed_at": iso(timedelta(hours=-49))},  # too old to be a current hazard
        {"observed_at": "yesterday"},
    ]
    for over in bad:
        r = await submit(client, **over)
        assert r.status_code == 422 and r.headers["content-type"].startswith(
            "application/problem+json"
        ), over
    ok_line = (await submit(client, geometry=line(3, 0.0015))).status_code  # about 250 m
    assert ok_line == 202
    assert "official" in (await submit(client, kind="closed_by_authority")).json()["detail"]
    for kind in (
        "flood",
        "landslide",
        "debris",
        "road_collapse",
        "bridge_damage",
        "fire_near_road",
        "snow_ice",
    ):
        for passability in ("blocked", "restricted", "passable_with_care", "reported_clear"):
            assert (await submit(client, kind=kind, passability=passability)).status_code == 202


async def test_nothing_identifying_or_free_form_is_accepted(client):  # type: ignore[no-untyped-def]
    for extra in (
        "note",
        "description",
        "text",
        "comment",
        "photo",
        "image",
        "audio",
        "lat",
        "lon",
        "device_location",
        "contact",
        "name",
        "phone",
        "email",
        "reporter",
        "ip",
        "sim",
    ):
        r = await client.post(
            "/v1/reports", json={**body(), extra: "x"}, headers=hdr(client, "reporter")
        )
        assert r.status_code == 422, extra
        assert extra in r.json()["detail"], extra  # the refusal names the field
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(HazardReport))).first() is None  # none of them was stored


def test_schema_guard_the_report_stores_and_accepts_only_what_the_privacy_rules_allow() -> None:
    """If this fails, you are adding a field to reports: read docs/hazards.md sections 3 and 9 first."""
    assert set(ReportIn.model_fields) == {"kind", "passability", "geometry", "observed_at"}
    assert {c.name for c in HazardReport.__table__.columns} == {  # type: ignore[attr-defined]
        "id",
        "reporter_prefix",
        "kind",
        "passability",
        "geometry",
        "observed_at",
        "received_at",
        "sim",
        "status",
        "hazard_id",
    }


async def test_rate_limits_are_per_key_durable_and_withdrawing_does_not_reset_them(client):  # type: ignore[no-untyped-def]
    cfg = client.app.state.settings  # type: ignore[attr-defined]
    cfg.report_limit_per_hour, cfg.report_limit_per_day = 3, 100
    ids = []
    for _ in range(3):
        r = await submit(client)
        assert r.status_code == 202
        ids.append(r.json()["id"])
    blocked = await submit(client)
    assert blocked.status_code == 429 and blocked.json()["type"].endswith("report-rate-limited")
    assert 0 < int(blocked.headers["retry-after"]) <= 3600
    await client.post(f"/v1/reports/{ids[0]}/withdraw", headers=hdr(client, "reporter"))
    assert (await submit(client)).status_code == 429  # withdrawn reports still count
    assert (await submit(client, "responder")).status_code == 202  # limits are per key
    assert (
        await client.get("/v1/reports?mine=true", headers=hdr(client, "reporter"))
    ).status_code == 200  # reads are free


async def test_daily_limit(client):  # type: ignore[no-untyped-def]
    cfg = client.app.state.settings  # type: ignore[attr-defined]
    cfg.report_limit_per_hour, cfg.report_limit_per_day = 100, 4
    me = (await client.get("/v1/me", headers=hdr(client, "reporter"))).json()["prefix"]
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        for _ in range(
            4
        ):  # four reports two hours ago: outside the hourly window, inside the daily one
            s.add(
                HazardReport(
                    reporter_prefix=me,
                    kind="debris",
                    passability="blocked",
                    geometry="SRID=4326;POINT(27.9 43.2)",
                    observed_at=datetime.now(UTC),
                    received_at=datetime.now(UTC) - timedelta(hours=2),
                    status="accepted",
                )
            )
        await s.commit()
    r = await submit(client)
    assert r.status_code == 429 and 20 * 3600 < int(r.headers["retry-after"]) <= 22 * 3600


async def test_withdraw_is_for_the_reporting_key_only(client):  # type: ignore[no-untyped-def]
    rid = (await submit(client)).json()["id"]
    stranger = await new_reporter(client)
    assert (await client.post(f"/v1/reports/{rid}/withdraw", headers=stranger)).status_code == 404
    assert (
        await client.post(f"/v1/reports/{rid}/withdraw", headers=hdr(client, "responder"))
    ).status_code == 404
    assert (
        await client.post(f"/v1/reports/{rid}/withdraw", headers=hdr(client, "admin"))
    ).status_code == 404
    ok = await client.post(f"/v1/reports/{rid}/withdraw", headers=hdr(client, "reporter"))
    assert ok.status_code == 200 and ok.json() == {"id": rid, "status": "withdrawn"}
    assert (
        await client.post(f"/v1/reports/{rid}/withdraw", headers=hdr(client, "reporter"))
    ).status_code == 200  # idempotent
    assert (
        await client.post(
            f"/v1/reports/{'0' * 8}-0000-0000-0000-{'0' * 12}/withdraw",
            headers=hdr(client, "reporter"),
        )
    ).status_code == 404
    mine = (await client.get("/v1/reports?mine=true", headers=hdr(client, "reporter"))).json()
    assert [i["status"] for i in mine["items"]] == ["withdrawn"]


async def test_you_can_list_only_your_own_reports_and_never_see_prefixes(client):  # type: ignore[no-untyped-def]
    other = await new_reporter(client)
    mine_id = (await submit(client, kind="landslide")).json()["id"]
    theirs = (await client.post("/v1/reports", json=body(kind="debris"), headers=other)).json()[
        "id"
    ]
    a = (await client.get("/v1/reports?mine=true", headers=hdr(client, "reporter"))).json()
    b = (await client.get("/v1/reports?mine=true", headers=other)).json()
    assert [i["id"] for i in a["items"]] == [mine_id] and [i["id"] for i in b["items"]] == [theirs]
    item = a["items"][0]
    assert set(item) == {
        "id",
        "kind",
        "passability",
        "geometry",
        "observed_at",
        "received_at",
        "status",
        "hazard_id",
        "tier",
    }
    assert (
        item["geometry"]["type"] == "Point"
        and item["tier"] == "unverified"
        and item["hazard_id"] is None
    )
    me = (await client.get("/v1/me", headers=hdr(client, "reporter"))).json()["prefix"]
    other_me = (await client.get("/v1/me", headers=other)).json()["prefix"]
    for text_ in (json.dumps(a), json.dumps(b)):
        assert me not in text_ and other_me not in text_
    for bad in (
        "/v1/reports",
        "/v1/reports?mine=false",
    ):  # there is no way to list anyone else's reports
        assert (await client.get(bad, headers=hdr(client, "admin"))).status_code == 422
    assert (
        await client.get("/v1/reports?mine=true", headers=hdr(client, "public"))
    ).status_code == 403
    assert (await client.get("/v1/reports?mine=true", headers=hdr(client, "admin"))).json()[
        "count"
    ] == 0  # an admin has no reports of their own


async def test_an_admin_can_issue_reporter_keys(client):  # type: ignore[no-untyped-def]
    r = await client.post(
        "/v1/admin/api-keys",
        headers=hdr(client, "admin"),
        json={
            "label": "Municipal rescue team 3",
            "role": "reporter",
            "kind": "field",
            "purpose": "community reports",
            "organisation_id": client.orgs["rescue_service"],
        },
    )
    assert r.status_code == 201
    d = r.json()
    assert d["role"] == "reporter" and d["effective_rate_limit"] == 120
    key = d["key"]
    assert (await client.get("/v1/me", headers={"X-API-Key": key})).json()["role"] == "reporter"
    assert (
        await client.post("/v1/reports", json=body(), headers={"X-API-Key": key})
    ).status_code == 202
