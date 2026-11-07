from datetime import UTC, datetime, timedelta

from stozher_core.reception import (
    GOOD_CN0_DBHZ,
    ReceptionRecord,
    ReceptionState,
    reception,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
GOOD = GOOD_CN0_DBHZ + 5
WEAK = GOOD_CN0_DBHZ - 10


def r(
    min_ago: float,
    cls: int = 0,
    cn0: int = GOOD,
    no_fix: bool = False,
    t: int = 0,
    sats: int | None = None,
):
    return ReceptionRecord(t, cls, no_fix, cn0, NOW - timedelta(minutes=min_ago), satellites=sats)


def hourly(cn0: int, hours: int = 5) -> list[ReceptionRecord]:
    return [r(60 * h + 10, t=0, cn0=cn0) for h in range(hours)]


def test_strong_hourly_heartbeats_are_good():
    v = reception(hourly(GOOD), NOW)
    assert v.state is ReceptionState.GOOD and v.median_cn0 == GOOD and v.losses == 0


def test_weak_signals_are_limited_sky_view():
    v = reception(hourly(WEAK), NOW)
    assert v.state is ReceptionState.LIMITED and v.reasons == (f"median signal {WEAK} dB-Hz",)


def test_a_node_that_never_got_a_fix_has_no_reception():
    v = reception([r(30, cls=3, cn0=0, no_fix=True), r(20, cls=0, cn0=0, no_fix=True)], NOW)
    assert v.state is ReceptionState.NONE and v.since == NOW - timedelta(minutes=30)
    assert v.reasons == ("no fix since 11:30 UTC",)


def test_a_loss_ended_by_a_normal_record_is_limited_for_3_hours_then_good():
    recs = hourly(GOOD) + [r(100, cls=3, cn0=0, no_fix=True)]
    v = reception(recs, NOW)
    assert (
        v.state is ReceptionState.LIMITED
        and v.losses == 1
        and v.reasons == ("lost fix 1 time in 3 h",)
    )
    later = recs + [
        r(-60 * h, cn0=GOOD) for h in range(1, 3)
    ]  # two more heartbeats; the loss is now 250 min old
    assert reception(later, NOW + timedelta(hours=2, minutes=30)).state is ReceptionState.GOOD


def test_the_level_is_the_newest_records_so_moving_outside_shows_quickly():
    recs = [r(60 * h + 200, cn0=WEAK) for h in range(3)] + [r(m, cn0=GOOD) for m in (5, 25, 45)]
    assert reception(recs, NOW).state is ReceptionState.GOOD


def test_jamming_spoofing_and_time_events_do_not_change_it():
    recs = hourly(GOOD) + [r(15, cls=1, cn0=5), r(14, cls=2, cn0=5), r(13, cls=5, cn0=5)]
    assert reception(recs, NOW).state is ReceptionState.GOOD


def test_silence_is_unknown_never_good():
    assert reception([r(200)] * 4, NOW).state is ReceptionState.UNKNOWN
    assert reception([], NOW).state is ReceptionState.UNKNOWN


def test_too_few_records_with_a_fix_is_unknown():
    assert reception([r(5)], NOW).state is ReceptionState.UNKNOWN


def test_test_records_are_ignored():
    assert (
        reception([r(5, t=3, cls=3, no_fix=True)] + hourly(GOOD), NOW).state is ReceptionState.GOOD
    )


def test_few_satellites_in_extended_records_are_limited():
    v = reception([r(m, sats=4) for m in (5, 25, 45)], NOW)
    assert v.state is ReceptionState.LIMITED and v.reasons == ("median 4 satellites",)
