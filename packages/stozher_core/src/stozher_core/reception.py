"""A node's own reception condition from its recent records (docs/trust.md "Reception is not trust"). Pure, no I/O.

It describes the witness, not the place: "limited sky view" means the node's receiver hears satellites weakly or keeps
losing its fix (indoors, by a window, under cover, or local noise; one node cannot tell which). It never feeds the trust
grid, hotspots or alerts, and it ignores the jamming, spoofing and time classes, which stay the detector's business.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import median

WINDOW_S = 6 * 3600  # records looked at
QUIET_S = (
    3 * 3600
)  # nothing newer than this: unknown (heartbeats are hourly), never "good" by absence
LOSS_MEMORY_S = 3 * 3600  # a lost fix in this time keeps the node at "limited sky view"
RECENT_GOOD = 3  # the signal level is the median of the newest this many normal records with a fix
# Provisional until the open-sky reference run: a4b0 (NEO-6M) indoors and by a window gave medians of 16-21 dB-Hz.
GOOD_CN0_DBHZ = 28
GOOD_SATS = 6  # extended records only (satellites in view)

_ANALYSED_TYPES = (0, 1, 2)  # heartbeat, event, summary; test records say nothing
_NORMAL = 0
_BENIGN = 3  # loss of reception (docs/packet.md)


class ReceptionState(StrEnum):
    GOOD = "good"
    LIMITED = "limited_sky"
    NONE = "no_reception"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ReceptionRecord:
    msg_type: int
    event_class: int
    no_fix: bool
    cn0_mean: int
    observed_at: datetime
    seq: int = 0
    satellites: int | None = None  # extended records only


@dataclass(frozen=True)
class Reception:
    state: ReceptionState
    reasons: tuple[str, ...]
    since: datetime | None  # start of the current loss of fix (no_reception only)
    median_cn0: float | None  # of the newest normal records with a fix
    losses: int  # separate losses of fix in the last LOSS_MEMORY_S
    records: int  # records looked at


def _is_loss(r: ReceptionRecord) -> bool:
    return r.no_fix or r.event_class == _BENIGN


def _hhmm(t: datetime) -> str:
    return t.strftime("%H:%M UTC")


def reception(records: Sequence[ReceptionRecord], now: datetime) -> Reception:
    rs = sorted(
        (
            r
            for r in records
            if r.msg_type in _ANALYSED_TYPES
            and timedelta(seconds=-60) <= now - r.observed_at <= timedelta(seconds=WINDOW_S)
        ),
        key=lambda r: (r.observed_at, r.seq),
    )
    if not rs or (now - rs[-1].observed_at).total_seconds() > QUIET_S:
        return Reception(
            ReceptionState.UNKNOWN, ("nothing heard for over 3 h",), None, None, 0, len(rs)
        )

    losses = 0
    in_loss = False
    loss_start: datetime | None = None
    for r in rs:
        if _is_loss(r):
            if not in_loss and (now - r.observed_at).total_seconds() <= LOSS_MEMORY_S:
                losses += 1
            if not in_loss:
                loss_start = r.observed_at
            in_loss = True
        else:
            in_loss = False

    good = [r for r in rs if r.event_class == _NORMAL and not r.no_fix and r.cn0_mean > 0][
        -RECENT_GOOD:
    ]
    cn0 = float(median(r.cn0_mean for r in good)) if good else None

    if in_loss and loss_start is not None:
        return Reception(
            ReceptionState.NONE,
            (f"no fix since {_hhmm(loss_start)}",),
            loss_start,
            cn0,
            losses,
            len(rs),
        )

    reasons: list[str] = []
    if losses:
        reasons.append(f"lost fix {losses} time{'s' if losses > 1 else ''} in 3 h")
    if cn0 is not None and cn0 < GOOD_CN0_DBHZ:
        reasons.append(f"median signal {cn0:g} dB-Hz")
    sats = [r.satellites for r in good if r.satellites is not None]
    if sats and median(sats) < GOOD_SATS:
        reasons.append(f"median {median(sats):g} satellites")
    if reasons:
        return Reception(ReceptionState.LIMITED, tuple(reasons), None, cn0, losses, len(rs))
    if len(good) < RECENT_GOOD:
        return Reception(
            ReceptionState.UNKNOWN, ("too few records with a fix yet",), None, cn0, losses, len(rs)
        )
    return Reception(
        ReceptionState.GOOD, (f"median signal {cn0:g} dB-Hz",), None, cn0, losses, len(rs)
    )
