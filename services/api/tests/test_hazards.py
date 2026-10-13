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


async def test_reading_hazards_needs_a_responder_key(client):  # type: ignore[no-untyped-def]
    for role in ("public", "reporter", "system"):
        assert (await client.get("/v1/hazards", headers=hdr(client, role))).status_code == 403, role
    assert (await client.get("/v1/hazards")).status_code == 401
    for role in ("responder", "operator", "admin"):
        assert (await client.get("/v1/hazards", headers=hdr(client, role))).status_code == 200


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
