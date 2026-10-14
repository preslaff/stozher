import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, text
from stozher_api.auth import hash_key, new_key
from stozher_api.hazard_service import reconcile
from stozher_api.models import ApiKey, AuditLog, HazardHistory, HazardReport, RoadHazard
from stozher_worker.main import HANDLERS
from stozher_worker.queue import run_pending

from .test_api import hdr

LON, LAT = 27.9100, 43.2000


def iso(minutes: float = -5) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


class Team:
    """A reporter key issued to a team: `label` is the team, `prefix` is what must never be exposed."""

    def __init__(self, headers: dict[str, str], prefix: str, label: str) -> None:
        self.headers, self.prefix, self.label = headers, prefix, label


async def team(c: httpx.AsyncClient, label: str) -> Team:
    full, prefix = new_key("live")
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        s.add(ApiKey(prefix=prefix, hash=hash_key(full), role="reporter", label=label))
        await s.commit()
    c.app.state.settings.report_limit_per_hour = 1000  # type: ignore[attr-defined]
    return Team({"X-API-Key": full}, prefix, label)


async def report(c, t: Team, *, kind="debris", passability="blocked", east=0.0, minutes=-5):  # type: ignore[no-untyped-def]
    r = await c.post(
        "/v1/reports",
        headers=t.headers,
        json={
            "kind": kind,
            "passability": passability,
            "observed_at": iso(minutes),
            "geometry": {"type": "Point", "coordinates": [round(LON + east / 81_000.0, 5), LAT]},
        },
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]


async def run(c, now: datetime | None = None):  # type: ignore[no-untyped-def]
    async with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        return await reconcile(s, now or datetime.now(UTC))


async def hazards(c, role="responder", **params):  # type: ignore[no-untyped-def]
    return (await c.get("/v1/hazards", params=params, headers=hdr(c, role))).json()


async def decide(c, hid: str, action: str, reason: str, role="operator"):  # type: ignore[no-untyped-def]
    return await c.post(
        f"/v1/hazards/{hid}/{action}", json={"reason": reason}, headers=hdr(c, role)
    )


async def test_one_report_makes_one_unverified_hazard(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Rescue team 3")
    rid = await report(client, t)
    summary = await run(client)
    assert summary.created == 1
    d = await hazards(client)
    assert d["count"] == 1 and d["sources"] == ["community", "operator"]
    h = d["items"][0]
    assert (h["kind"], h["tier"], h["status"], h["passability"]) == (
        "debris",
        "unverified",
        "active",
        "blocked",
    )
    assert h["sources"] == ["community"] and h["evidence"] == {
        "reports": 1,
        "clear_reports": 0,
        "independent_teams": 1,
    }
    assert h["geometry"]["type"] == "Point" and h["data_age_s"] is not None and h["sim"] is False
    mine = (await client.get("/v1/reports?mine=true", headers=t.headers)).json()["items"][0]
    assert mine["id"] == rid and mine["hazard_id"] == h["id"] and mine["tier"] == "unverified"


async def test_independence_is_by_team_not_by_key(client):  # type: ignore[no-untyped-def]
    a, b, c3 = (
        await team(client, "Rescue team 3"),
        await team(client, "Rescue team 3"),
        await team(client, "Fire department"),
    )
    await report(client, a)
    await report(client, a, east=20)  # the same key twice
    await run(client)
    assert (await hazards(client))["items"][0]["tier"] == "unverified"
    await report(
        client, b, east=40
    )  # a second KEY of the same team: a coordinated fake looks like this
    await run(client)
    h = (await hazards(client))["items"][0]
    assert (
        h["tier"] == "unverified"
        and h["evidence"]["independent_teams"] == 1
        and h["evidence"]["reports"] == 3
    )
    await report(client, c3, east=60)  # a different team
    await run(client)
    h = (await hazards(client))["items"][0]
    assert h["tier"] == "corroborated" and h["evidence"]["independent_teams"] == 2
    timeline = (
        await client.get(f"/v1/hazards/{h['id']}", headers=hdr(client, "responder"))
    ).json()["timeline"]
    assert [(e["event"], e["value"], e["by"]) for e in timeline] == [
        ("created", "unverified", "community"),
        ("tier", "corroborated", "community"),
    ]


async def test_what_merges_and_what_does_not(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Rescue team 3")
    await report(client, t)
    await report(client, t, east=250)  # 250 m: merges
    await report(client, t, east=900)  # 650 m from the nearest: a separate hazard
    await report(client, t, kind="flood", east=10)  # another kind: separate
    await report(client, t, minutes=-60 * 7)  # 7 hours earlier: outside the 6 h window, still valid
    await run(client)
    d = await hazards(client)
    assert d["count"] == 4
    by = sorted((h["kind"], h["evidence"]["reports"]) for h in d["items"])
    assert by == [("debris", 1), ("debris", 1), ("debris", 2), ("flood", 1)]
    merged = next(h for h in d["items"] if h["evidence"]["reports"] == 2)
    assert merged["geometry"]["type"] == "LineString"  # the convex hull of two points
    only_flood = await hazards(client, kind="flood")
    assert only_flood["count"] == 1
    assert (await hazards(client, bbox="0,0,1,1"))["count"] == 0
    assert (await hazards(client, min_tier="corroborated"))["count"] == 0


async def test_passability_is_the_most_severe_report(client):  # type: ignore[no-untyped-def]
    a, b = await team(client, "Team A"), await team(client, "Team B")
    await report(client, a, passability="passable_with_care")
    await report(client, b, passability="blocked", east=30)
    await run(client)
    assert (await hazards(client))["items"][0]["passability"] == "blocked"


async def test_only_operators_decide_and_each_decision_is_audited(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t)
    await run(client)
    hid = (await hazards(client))["items"][0]["id"]
    for role in ("public", "reporter", "responder", "system"):
        assert (
            await decide(client, hid, "confirm", "verified_on_site", role)
        ).status_code == 403, role
    assert (
        await client.post(f"/v1/hazards/{hid}/confirm", json={"reason": "verified_on_site"})
    ).status_code == 401
    r = await decide(client, hid, "confirm", "verified_on_site")
    assert r.status_code == 200
    h = r.json()
    assert (h["tier"], h["status"], h["decision"], h["decision_reason"]) == (
        "confirmed",
        "active",
        "confirmed",
        "verified_on_site",
    )
    assert h["sources"] == ["community", "operator"] and "decided_by" not in h
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        log = (
            await s.execute(select(AuditLog).where(AuditLog.action == "hazard.confirmed"))
        ).scalar_one()
        assert log.target == hid
    timeline = (await client.get(f"/v1/hazards/{hid}", headers=hdr(client, "responder"))).json()[
        "timeline"
    ]
    assert ("decision", "confirmed", "operator") in [
        (e["event"], e["value"], e["by"]) for e in timeline
    ]
    assert ("tier", "confirmed", "operator") in [
        (e["event"], e["value"], e["by"]) for e in timeline
    ]


async def test_decisions_take_a_reason_code_from_a_closed_list_and_nothing_else(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t)
    await run(client)
    hid = (await hazards(client))["items"][0]["id"]
    for action, bad in (
        ("confirm", "false_report"),
        ("close", "verified_on_site"),
        ("dismiss", "cleared_on_site"),
        ("confirm", "because I said so"),
        ("dismiss", ""),
    ):
        assert (await decide(client, hid, action, bad)).status_code == 422, (action, bad)
    for extra in ({"reason": "duplicate", "note": "two men with weapons"}, {}):
        r = await client.post(
            f"/v1/hazards/{hid}/dismiss", json=extra, headers=hdr(client, "operator")
        )
        assert r.status_code == 422
    assert (await hazards(client))["items"][0][
        "status"
    ] == "active"  # none of them changed anything


async def test_dismissing_and_closing_end_a_hazard_and_cannot_be_repeated(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t)
    await report(client, t, kind="flood")
    await run(client)
    ids = {h["kind"]: h["id"] for h in (await hazards(client))["items"]}
    d = (await decide(client, ids["debris"], "dismiss", "false_report")).json()
    assert d["status"] == "dismissed" and d["ended_at"]
    c = (await decide(client, ids["flood"], "close", "cleared_on_site")).json()
    assert c["status"] == "closed" and c["passability"] == "reported_clear"
    assert (await hazards(client))["count"] == 0  # neither is active any more
    assert (await hazards(client, status="all"))["count"] == 2
    for hid in ids.values():
        r = await decide(client, hid, "confirm", "verified_on_site")
        assert r.status_code == 409 and r.json()["type"].endswith("not-open")
    assert (
        await decide(client, "00000000-0000-0000-0000-000000000000", "confirm", "verified_on_site")
    ).status_code == 404


async def test_two_independent_clears_close_but_one_or_a_confirmed_hazard_does_not(client):  # type: ignore[no-untyped-def]
    a, b = await team(client, "Team A"), await team(client, "Team B")
    c3, d4 = await team(client, "Team C"), await team(client, "Team D")
    await report(client, a, minutes=-120)
    await run(client)
    await report(client, b, passability="reported_clear", minutes=-30)
    await run(client)
    assert (await hazards(client))["items"][0]["status"] == "active"  # one clear is not enough
    await report(client, c3, passability="reported_clear", minutes=-20)
    await run(client)
    assert (await hazards(client))["count"] == 0
    closed = (await hazards(client, status="closed"))["items"][0]
    assert closed["passability"] == "reported_clear" and closed["tier"] == "unverified"
    # a confirmed hazard stays until a human closes it, whatever the clear reports say
    await report(client, a, kind="flood", minutes=-120)
    await run(client)
    fid = (await hazards(client, kind="flood"))["items"][0]["id"]
    await decide(client, fid, "confirm", "official_source")
    await report(client, b, kind="flood", passability="reported_clear", minutes=-30)
    await report(client, d4, kind="flood", passability="reported_clear", minutes=-20)
    await run(client)
    still = (await hazards(client, kind="flood"))["items"][0]
    assert still["status"] == "active" and still["tier"] == "confirmed"
    assert (await decide(client, fid, "close", "cleared_on_site")).json()["status"] == "closed"


async def test_expiry_is_automatic_and_is_not_clear(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t)
    await run(client)
    later = datetime.now(UTC) + timedelta(hours=13)  # debris is valid for 12 h
    s = await run(client, later)
    assert s.changed == 1
    assert (await hazards(client))[
        "count"
    ] == 0  # not in the active list, so no route alert would use it
    gone = (await hazards(client, status="expired"))["items"][0]
    assert (
        gone["status"] == "expired"
        and gone["passability"] == "blocked"
        and gone["tier"] == "unverified"
    )
    assert gone["ended_at"] is not None
    # an operator who confirms it again brings it back
    r = await decide(client, gone["id"], "confirm", "verified_on_site")
    assert (
        r.status_code == 200 and r.json()["status"] == "active" and r.json()["tier"] == "confirmed"
    )


async def test_withdrawing_reports_withdraws_their_support(client):  # type: ignore[no-untyped-def]
    a, b = await team(client, "Team A"), await team(client, "Team B")
    r1, r2 = await report(client, a), await report(client, b, east=30)
    await run(client)
    assert (await hazards(client))["items"][0]["tier"] == "corroborated"
    await client.post(f"/v1/reports/{r2}/withdraw", headers=b.headers)
    await run(client)
    assert (await hazards(client))["items"][0]["tier"] == "unverified"  # back to one team
    await client.post(f"/v1/reports/{r1}/withdraw", headers=a.headers)
    await run(client)
    assert (await hazards(client))["count"] == 0
    assert (await hazards(client, status="dismissed"))["count"] == 1  # nothing left to stand on


async def test_reconcile_is_idempotent_and_the_history_does_not_repeat(client):  # type: ignore[no-untyped-def]
    a, b = await team(client, "Team A"), await team(client, "Team B")
    await report(client, a)
    await report(client, b, east=20)
    first = await run(client)
    again, third = await run(client), await run(client)
    assert (
        (first.created, first.changed) == (1, 1)
        and (again.created, again.changed) == (0, 0)
        and third.changed == 0
    )
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert len((await s.execute(select(HazardHistory))).scalars().all()) == 1  # just "created"


async def test_no_response_ever_contains_a_reporter_prefix_or_a_decider(client):  # type: ignore[no-untyped-def]
    a, b = await team(client, "Team A"), await team(client, "Team B")
    await report(client, a)
    await report(client, b, east=20)
    await run(client)
    hid = (await hazards(client))["items"][0]["id"]
    op = (await client.get("/v1/me", headers=hdr(client, "operator"))).json()["prefix"]
    await decide(client, hid, "confirm", "multiple_sources")
    seen = [
        json.dumps(await hazards(client, status="all")),
        (await client.get(f"/v1/hazards/{hid}", headers=hdr(client, "admin"))).text,
        (await client.get("/v1/reports?mine=true", headers=a.headers)).text,
    ]
    for blob in seen:
        for prefix in (a.prefix, b.prefix, op):
            assert prefix not in blob
    assert "Team A" not in "".join(seen)  # team labels are not exposed either


async def test_reading_hazards_needs_a_key_and_lower_roles_get_the_coarse_view(client):  # type: ignore[no-untyped-def]
    assert (await client.get("/v1/hazards", headers=hdr(client, "system"))).status_code == 403
    assert (await client.get("/v1/hazards")).status_code == 401
    for role in ("public", "reporter", "responder", "operator", "admin"):
        assert (await client.get("/v1/hazards", headers=hdr(client, role))).status_code == 200


async def test_public_and_reporters_see_only_confirmed_listed_kinds_on_a_coarse_grid(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t, kind="flood")
    await report(client, t, kind="debris", east=2000)
    await run(client)
    items = (await hazards(client))["items"]
    flood = [h for h in items if h["kind"] == "flood"][0]
    debris = [h for h in items if h["kind"] == "debris"][0]
    for role in ("public", "reporter"):
        assert (await hazards(client, role=role))["count"] == 0  # unverified is never public
    await decide(client, flood["id"], "confirm", "verified_on_site")
    await decide(client, debris["id"], "confirm", "verified_on_site")
    for role in ("public", "reporter"):
        got = await hazards(client, role=role, min_tier="unverified", status="all", kind="debris")
        assert (
            got["count"] == 0
        )  # debris is not a public kind, and the filters cannot widen the view
        got = await hazards(client, role=role)
        assert got["count"] == 1
        h = got["items"][0]
        assert h["kind"] == "flood" and h["tier"] == "confirmed" and h["blur_m"] == 100
        assert h["geometry"]["type"] == "Point" and h["geometry"]["coordinates"] != [LON, LAT]
        assert (
            abs(h["geometry"]["coordinates"][1] - LAT) < 0.001
            and abs(h["geometry"]["coordinates"][0] - LON) < 0.002
        )
        assert not {
            "sources",
            "evidence",
            "decision",
            "decision_reason",
            "first_seen",
            "decided_at",
        } & set(h)
        one = await client.get(f"/v1/hazards/{h['id']}", headers=hdr(client, role))
        assert one.status_code == 200 and "timeline" not in one.json()
        assert (
            await client.get(f"/v1/hazards/{debris['id']}", headers=hdr(client, role))
        ).status_code == 404
    assert (await hazards(client))["count"] == 2  # responders still see everything, exact


def test_blur_snaps_to_a_100_m_grid_and_keeps_only_the_vertices():
    from stozher_api.hazard_logic import blur_geometry

    a = blur_geometry({"type": "Point", "coordinates": [27.91004, 43.20002]})
    b = blur_geometry({"type": "Point", "coordinates": [27.91012, 43.20006]})
    assert a == b and a["type"] == "Point"  # nearby points share a cell
    line = blur_geometry({"type": "LineString", "coordinates": [[27.91, 43.2], [27.93, 43.2]]})
    assert line["type"] == "MultiPoint" and len(line["coordinates"]) == 2  # vertices only


async def test_along_route_lists_hazards_in_order_with_tier_and_age(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t, kind="flood", east=1500)
    await report(client, t, kind="debris", east=500)
    await report(client, t, kind="landslide", east=0, minutes=-5)
    await report(
        client, t, kind="road_collapse", east=0, minutes=-5
    )  # same place: merges only if same kind
    await run(client)
    far = await report(client, t, kind="debris", east=9000)  # far from the route
    del far
    await run(client)
    route = {"type": "LineString", "coordinates": [[LON - 0.005, LAT], [LON + 0.03, LAT]]}
    r = await client.post(
        "/v1/hazards/along-route?buffer_m=100", json=route, headers=hdr(client, "responder")
    )
    assert r.status_code == 200, r.text
    d = r.json()
    kinds = [h["kind"] for h in d["items"]]
    assert "flood" in kinds and "debris" in kinds and d["count"] == len(kinds) <= 4
    at = [h["at_m"] for h in d["items"]]
    assert at == sorted(at) and d["length_m"] > 2500
    assert all("tier" in h and "data_age_s" in h and h["distance_m"] <= 100 for h in d["items"])
    assert not any(
        abs(h["geometry"]["coordinates"][0] - (LON + 9000 / 81_000.0)) < 1e-3 for h in d["items"]
    )
    only_flood = await client.post(
        "/v1/hazards/along-route?min_tier=confirmed", json=route, headers=hdr(client, "responder")
    )
    assert only_flood.json()["count"] == 0
    for role in ("public", "reporter", "system"):
        assert (
            await client.post("/v1/hazards/along-route", json=route, headers=hdr(client, role))
        ).status_code == 403
    bad = await client.post(
        "/v1/hazards/along-route",
        json={"type": "LineString", "coordinates": [[0, 0]]},
        headers=hdr(client, "responder"),
    )
    assert bad.status_code == 422


async def test_ended_hazards_and_their_reports_are_deleted_after_the_retention_period(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t)
    await run(client)
    hid = (await hazards(client))["items"][0]["id"]
    await decide(client, hid, "dismiss", "test_data")
    await run(client, datetime.now(UTC) + timedelta(days=29))
    assert (await hazards(client, status="all"))["count"] == 1  # not yet
    s = await run(client, datetime.now(UTC) + timedelta(days=31))
    assert s.purged >= 1  # the hazard; its reports and history go with it (checked below)
    async with client.app.state.sessionmaker() as sess:  # type: ignore[attr-defined]
        for table in ("road_hazards", "hazard_reports", "hazard_history"):
            assert (await sess.execute(text(f"select count(*) from {table}"))).scalar_one() == 0, (
                table
            )


async def test_the_worker_job_merges_reports_without_being_called_directly(client):  # type: ignore[no-untyped-def]
    t = await team(client, "Team A")
    await report(client, t)  # the submit enqueues a `hazards` job
    assert await run_pending(client.app.state.sessionmaker, HANDLERS) >= 1  # type: ignore[attr-defined]
    assert (await hazards(client))["count"] == 1
    async with client.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(HazardReport.hazard_id))).scalar_one() is not None
        assert (await s.execute(select(RoadHazard.sim))).scalar_one() is False


async def watch(c, **over):  # type: ignore[no-untyped-def]
    body = {
        "name": "Coast road",
        "geometry": {"type": "LineString", "coordinates": [[LON - 0.005, LAT], [LON + 0.03, LAT]]},
        "buffer_m": 100,
        **over,
    }
    r = await c.post("/v1/watched-routes", json=body, headers=hdr(c, "responder"))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def tick(c, now=None):  # type: ignore[no-untyped-def]
    from stozher_worker.hazard_alerts import watch_hazards

    async with c.app.state.sessionmaker() as s:
        await reconcile(s, now or datetime.now(UTC))
        n = await watch_hazards(s, now or datetime.now(UTC))
        await s.commit()
        return n


async def route_alerts(c, wid: str):  # type: ignore[no-untyped-def]
    r = await c.get(f"/v1/alerts?route_id={wid}&limit=50", headers=hdr(c, "responder"))
    return [a for a in r.json()["items"] if a["kind"].startswith("route_hazard")]


async def test_a_watched_route_alerts_when_a_hazard_of_enough_tier_appears_and_when_it_closes(
    client,
):  # type: ignore[no-untyped-def]
    wid = await watch(client)
    a, b = await team(client, "Team A"), await team(client, "Team B")
    await report(client, a, east=500)
    assert await tick(client) == 0  # unverified is below the default corroborated
    await report(client, b, east=520)  # an independent team: corroborated
    assert await tick(client) == 1
    got = await route_alerts(client, wid)
    assert len(got) == 1 and got[0]["kind"] == "route_hazard" and got[0]["level"] == "critical"
    msg = got[0]["message"]
    assert (
        "corroborated" in msg
        and "2 reports" in msg
        and "km 0." in msg
        and "min old" in msg
        or "s old" in msg
    )
    assert a.prefix not in msg and "Team A" not in msg
    assert await tick(client) == 0  # nothing changed: nothing raised
    hid = (await hazards(client))["items"][0]["id"]
    await decide(client, hid, "close", "cleared_on_site")
    assert await tick(client) == 1
    got = await route_alerts(client, wid)
    assert [x["kind"] for x in got][0] == "route_hazard_cleared" and "closed" in got[0]["message"]
    assert await tick(client) == 0


async def test_expiry_and_dismissal_never_say_cleared_and_unverified_is_opt_in(client):  # type: ignore[no-untyped-def]
    wid = await watch(client, hazard_min_tier="unverified")
    a = await team(client, "Team A")
    await report(client, a, east=500, minutes=-5)
    assert (
        await tick(client) == 1
        and (await route_alerts(client, wid))[0]["message"].count("unverified") == 1
    )
    await tick(client, datetime.now(UTC) + timedelta(hours=13))  # debris is valid for 12 h
    assert [x["kind"] for x in await route_alerts(client, wid)] == [
        "route_hazard"
    ]  # expired is not clear
    b = await team(client, "Team B")
    await report(client, b, kind="flood", east=900)
    assert await tick(client) == 1
    fl = [h for h in (await hazards(client))["items"] if h["kind"] == "flood"][0]["id"]
    await decide(client, fl, "dismiss", "false_report")
    await tick(client)
    kinds = [x["kind"] for x in await route_alerts(client, wid)]
    assert "route_hazard_cleared" not in kinds


async def test_a_worse_passability_alerts_again_and_far_hazards_do_not(client):  # type: ignore[no-untyped-def]
    wid = await watch(client, hazard_min_tier="unverified")
    a = await team(client, "Team A")
    await report(client, a, east=300, passability="restricted")
    await report(client, a, east=20000)  # nowhere near the route
    assert await tick(client) == 1
    await report(client, a, east=310, passability="blocked")
    assert await tick(client) == 1
    got = await route_alerts(client, wid)
    assert len(got) == 2 and got[0]["level"] == "critical" and got[1]["level"] == "warning"
    assert (
        await client.patch(
            f"/v1/watched-routes/{wid}",
            json={"hazard_min_tier": "confirmed"},
            headers=hdr(client, "responder"),
        )
    ).json()["hazard_min_tier"] == "confirmed"
