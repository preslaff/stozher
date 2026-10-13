import uuid
from datetime import UTC, datetime, timedelta

from stozher_api.hazard_logic import (
    MERGE_M,
    VALIDITY,
    Haz,
    Rep,
    assign,
    derive,
    distance_m,
)

T0 = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
HERE = (27.9100, 43.2000)


def at_m(east: float) -> tuple[float, float]:
    """A point `east` metres east of HERE."""
    return (HERE[0] + east / 81_000.0, HERE[1])  # one degree of longitude is about 81 km here


def rep(
    team: str,
    *,
    kind: str = "debris",
    passability: str = "blocked",
    east: float = 0.0,
    minutes: float = 0,
    received: float | None = None,
) -> Rep:
    t = T0 + timedelta(minutes=minutes)
    return Rep(
        uuid.uuid4(),
        team,
        kind,
        passability,
        (at_m(east),),
        t,
        T0 + timedelta(minutes=minutes if received is None else received),
    )


def build(*reports: Rep, decision: str | None = None, decided_at: datetime | None = None) -> Haz:
    h = Haz(id=uuid.uuid4(), kind=reports[0].kind, decision=decision, decided_at=decided_at)
    h.members.extend(reports)
    return h


def test_distance_is_geodesic_metres() -> None:
    assert abs(distance_m([HERE], [at_m(250)]) - 250) < 3
    assert distance_m([HERE, at_m(1000)], [at_m(900)]) < 110  # the nearest pair counts


# ---- merging --------------------------------------------------------------------------------------------------------


def test_nearby_reports_of_one_kind_within_six_hours_merge_and_far_ones_do_not() -> None:
    a, b, c, d, e = (
        rep("t1"),
        rep("t2", east=250, minutes=30),
        rep("t3", east=400, minutes=40),
        rep("t4", kind="flood", east=10),
        rep("t5", minutes=420),
    )
    ids, created = assign([], [a, b, c, d, e])
    assert ids[a.id] == ids[b.id]  # 250 m, 30 minutes apart: one hazard
    # 400 m from the first report but 150 m from the second: it joins by its nearest member (single linkage)
    assert ids[c.id] == ids[a.id]
    assert ids[d.id] != ids[a.id]  # another kind never merges
    assert ids[e.id] != ids[a.id]  # 6 h 20 min after the last support: a new hazard
    assert len(created) == 3


def test_the_distance_limit() -> None:
    near, far = rep("t1"), rep("t2", east=MERGE_M + 60)
    ids, created = assign([], [near, far])
    assert ids[near.id] != ids[far.id] and len(created) == 2


def test_closed_expired_and_dismissed_hazards_take_no_new_reports() -> None:
    for status in ("closed", "expired", "dismissed"):
        h = build(rep("t1"))
        h.status = status
        ids, created = assign([h], [rep("t2", minutes=10)])
        assert len(created) == 1 and list(ids.values()) != [h.id], status


def test_a_clear_report_attaches_to_the_nearest_active_hazard_of_its_kind_any_time_later() -> None:
    h = build(rep("t1"))
    clear = rep("t2", passability="reported_clear", east=100, minutes=60 * 20)  # 20 hours later
    ids, created = assign([h], [clear])
    assert ids[clear.id] == h.id and created == []
    stray = rep("t3", passability="reported_clear", kind="flood")
    ids, created = assign([h], [stray])
    assert ids[stray.id] is None and created == []  # a clear alone never creates a hazard


def test_assignment_is_deterministic_regardless_of_input_order() -> None:
    a, b, c = rep("t1"), rep("t2", east=100, minutes=5), rep("t3", east=200, minutes=10)
    fixed = iter(uuid.UUID(int=i) for i in range(1, 99))
    one, _ = assign([], [a, b, c], new_id=lambda: next(fixed))
    fixed2 = iter(uuid.UUID(int=i) for i in range(1, 99))
    two, _ = assign([], [c, a, b], new_id=lambda: next(fixed2))
    assert one == two


# ---- tiers ----------------------------------------------------------------------------------------------------------


def test_one_report_is_unverified() -> None:
    d = derive(build(rep("t1")), T0 + timedelta(minutes=5))
    assert (d.tier, d.status, d.passability, d.independent_teams) == (
        "unverified",
        "active",
        "blocked",
        1,
    )


def test_the_same_team_twice_does_not_corroborate_even_with_two_keys() -> None:
    # two keys of one team arrive here as the same `team` label
    d = derive(
        build(rep("rescue-3"), rep("rescue-3", minutes=10, east=50)), T0 + timedelta(minutes=20)
    )
    assert d.tier == "unverified" and d.independent_teams == 1


def test_two_independent_teams_corroborate() -> None:
    d = derive(
        build(rep("rescue-3"), rep("fire-dept", minutes=10, east=50)), T0 + timedelta(minutes=20)
    )
    assert d.tier == "corroborated" and d.independent_teams == 2


def test_only_a_human_confirms_and_confirmation_is_the_top_tier() -> None:
    h = build(rep("t1"), decision="confirmed", decided_at=T0 + timedelta(minutes=30))
    assert derive(h, T0 + timedelta(minutes=40)).tier == "confirmed"
    many = build(*(rep(f"team{i}", minutes=i) for i in range(6)))
    assert (
        derive(many, T0 + timedelta(hours=1)).tier == "corroborated"
    )  # however many, never confirmed by itself


def test_passability_is_the_most_severe_of_the_current_reports() -> None:
    h = build(
        rep("t1", passability="passable_with_care"),
        rep("t2", passability="blocked", minutes=5),
        rep("t3", passability="restricted", minutes=9),
    )
    assert derive(h, T0 + timedelta(minutes=30)).passability == "blocked"


# ---- closing, expiry, dismissal -----------------------------------------------------------------------------------


def test_two_independent_clear_reports_close_an_unconfirmed_hazard() -> None:
    h = build(
        rep("t1"),
        rep("t2", minutes=10, east=40),
        rep("rescue-9", passability="reported_clear", minutes=120),
        rep("fire-dept", passability="reported_clear", minutes=150),
    )
    d = derive(h, T0 + timedelta(hours=4))
    assert (
        d.status == "closed"
        and d.passability == "reported_clear"
        and d.ended_at == T0 + timedelta(minutes=150)
    )


def test_one_clear_two_clears_from_one_team_or_stale_clears_do_not_close() -> None:
    one = build(rep("t1"), rep("t2", passability="reported_clear", minutes=60))
    assert derive(one, T0 + timedelta(hours=2)).status == "active"
    same_team = build(
        rep("t1"),
        rep("t2", passability="reported_clear", minutes=60),
        rep("t2", passability="reported_clear", minutes=70),
    )
    assert derive(same_team, T0 + timedelta(hours=2)).status == "active"
    stale = build(
        rep("t1", minutes=100),
        rep("t2", passability="reported_clear", minutes=60),
        rep("t3", passability="reported_clear", minutes=70),
    )  # the clears are older than the last report
    d = derive(stale, T0 + timedelta(hours=3))
    assert d.status == "active" and d.passability == "blocked"


def test_a_confirmed_hazard_is_closed_only_by_a_human() -> None:
    h = build(
        rep("t1"),
        rep("t2", passability="reported_clear", minutes=60),
        rep("t3", passability="reported_clear", minutes=70),
        decision="confirmed",
        decided_at=T0 + timedelta(minutes=30),
    )
    assert derive(h, T0 + timedelta(hours=2)).status == "active"
    h.decision, h.decided_at = "closed", T0 + timedelta(hours=1, minutes=30)
    d = derive(h, T0 + timedelta(hours=2))
    assert d.status == "closed" and d.ended_at == T0 + timedelta(hours=1, minutes=30)


def test_expiry_is_automatic_and_is_not_clear() -> None:
    h = build(rep("t1"), rep("t2", minutes=5))
    just_before = derive(h, T0 + timedelta(minutes=5) + VALIDITY["debris"] - timedelta(minutes=1))
    after = derive(h, T0 + timedelta(minutes=5) + VALIDITY["debris"] + timedelta(minutes=1))
    assert just_before.status == "active" and after.status == "expired"
    assert (
        after.passability == "blocked" and after.tier == "corroborated"
    )  # still what was reported: NOT cleared
    assert after.ended_at == T0 + timedelta(minutes=5) + VALIDITY["debris"]


def test_a_new_supporting_report_extends_validity() -> None:
    a = derive(build(rep("t1")), T0)
    b = derive(build(rep("t1"), rep("t2", minutes=600)), T0 + timedelta(hours=10))
    assert b.valid_until == a.valid_until + timedelta(hours=10) and b.status == "active"


def test_validity_differs_by_kind() -> None:
    assert VALIDITY["landslide"] > VALIDITY["flood"] > VALIDITY["debris"]
    assert derive(build(rep("t1", kind="landslide")), T0 + timedelta(hours=30)).status == "active"
    assert derive(build(rep("t1", kind="debris")), T0 + timedelta(hours=30)).status == "expired"


def test_dismissal_and_re_confirmation() -> None:
    h = build(rep("t1"), decision="dismissed", decided_at=T0 + timedelta(minutes=20))
    d = derive(h, T0 + timedelta(minutes=30))
    assert d.status == "dismissed" and d.ended_at == T0 + timedelta(minutes=20)
    old = build(rep("t1"), decision="confirmed", decided_at=T0 + timedelta(hours=20))
    assert (
        derive(old, T0 + timedelta(hours=30)).status == "active"
    )  # confirming later extended it past 12 h
    assert derive(old, T0 + timedelta(hours=40)).status == "expired"  # and it still runs out


def test_withdrawing_every_supporting_report_leaves_nothing_standing() -> None:
    empty = Haz(id=uuid.uuid4(), kind="debris")
    d = derive(empty, T0)
    assert d.status == "dismissed" and d.independent_teams == 0
    kept = Haz(id=uuid.uuid4(), kind="debris", decision="confirmed", decided_at=T0)
    assert derive(kept, T0).status == "active"  # a human vouched for it: only a human removes it


def test_the_six_hour_window_includes_exactly_six_hours_and_excludes_a_minute_more() -> None:
    base = rep("t1")
    inside, outside = rep("t2", minutes=360), rep("t3", minutes=361)
    ids, _ = assign([], [base, inside])
    assert ids[base.id] == ids[inside.id]
    ids, _ = assign([], [base, outside])
    assert ids[base.id] != ids[outside.id]
