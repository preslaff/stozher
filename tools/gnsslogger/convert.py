"""Turn an Android GnssLogger file into witness-style features, without any position.

Only the `Raw` and `Status` lines are read. `Fix` lines (the phone's position), sensors and navigation messages are skipped.
Output is one JSON object per window: time (UTC), duration, satellites, C/N0 mean/drop/spread, AGC and clock continuity.
With --records the windows are also encoded as signed TEST-type witness records (docs/packet.md) with no position (0, 0),
for pipeline checks only: the phone's AGC is in dB and is not on the receiver scale of the packet, so the packet AGC byte is 0.

    uv run python tools/gnsslogger/convert.py gnss_log_*.txt [--window 10] [--records]

With --records the node ID and master key come from STOZHER_PHONE_NODE_ID and STOZHER_PHONE_MASTER_KEY (hex), never the command line.
"""

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Window:
    start_utc: str
    duration_s: int
    satellites: int
    signals: int
    cn0_mean: float
    cn0_drop: int  # dB below the file's median window mean (>= 0)
    cn0_spread: int  # dB between the 10th and 90th percentile of the window's signals
    agc_db: float | None
    clock_discontinuity: bool


def _columns(lines: Iterable[str], tag: str) -> dict[str, int] | None:
    for line in lines:
        if line.startswith(f"# {tag},"):
            return {name: i for i, name in enumerate(line[2:].strip().split(","))}
    return None


def _float(row: list[str], cols: dict[str, int], name: str) -> float | None:
    i = cols.get(name)
    if i is None or i >= len(row) or row[i] == "":
        return None
    try:
        return float(row[i])
    except ValueError:
        return None


def read(path: str, window_s: int = 10) -> list[Window]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    raw_cols = _columns(lines, "Raw")
    status_cols = _columns(lines, "Status")
    if raw_cols is None:
        raise SystemExit(
            "No Raw measurements in this file: the phone does not expose raw GNSS, or the option was off."
        )
    cn0: dict[int, list[float]] = defaultdict(list)
    agc: dict[int, list[float]] = defaultdict(list)
    discont: dict[int, int] = {}
    used: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for line in lines:
        if line.startswith("Raw,"):
            row = line.split(",")
            t = _float(row, raw_cols, "utcTimeMillis")
            c = _float(row, raw_cols, "Cn0DbHz")
            if t is None or c is None or c <= 0:
                continue
            w = int(t // 1000) // window_s
            cn0[w].append(c)
            if (a := _float(row, raw_cols, "AgcDb")) is not None:
                agc[w].append(a)
            if (d := _float(row, raw_cols, "HardwareClockDiscontinuityCount")) is not None:
                discont[w] = max(discont.get(w, 0), int(d))
        elif status_cols is not None and line.startswith("Status,"):
            row = line.split(",")
            t = _float(row, status_cols, "UnixTimeMillis")
            con = _float(row, status_cols, "ConstellationType")
            sv = _float(row, status_cols, "Svid")
            if t is None or con is None or sv is None or _float(row, status_cols, "UsedInFix") != 1:
                continue
            used[int(t // 1000) // window_s].add((int(con), int(sv)))
    if not cn0:
        raise SystemExit("No usable C/N0 values in this file.")
    baseline = statistics.median(statistics.fmean(v) for v in cn0.values())
    out: list[Window] = []
    previous: int | None = None
    for w in sorted(cn0):
        vals = sorted(cn0[w])
        mean = statistics.fmean(vals)
        q = statistics.quantiles(vals, n=10) if len(vals) >= 10 else [vals[0]] * 9 + [vals[-1]]
        d = discont.get(w)
        out.append(
            Window(
                start_utc=datetime.fromtimestamp(w * window_s, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                duration_s=window_s,
                satellites=len(used[w]),
                signals=len(vals),
                cn0_mean=round(mean, 1),
                cn0_drop=max(0, round(baseline - mean)),
                cn0_spread=max(0, round(q[-1] - q[0])),
                agc_db=round(statistics.fmean(agc[w]), 1) if agc[w] else None,
                clock_discontinuity=previous is not None and d is not None and d != previous,
            )
        )
        previous = d if d is not None else previous
    return out


def to_hex(windows: list[Window], node_id: int, master: bytes, first_seq: int = 0) -> list[str]:
    """Signed TEST-type records, no position. Imported here so the feature output needs nothing but the standard library."""
    from stozher_core.codec import encode_hex
    from stozher_core.crypto import derive_keys
    from stozher_core.record import EventClass, MsgType, Osnma, WitnessRecord

    keys = derive_keys(master, node_id, 0)
    records: list[str] = []
    for i, w in enumerate(windows):
        ts = int(
            datetime.strptime(w.start_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        )
        rec = WitnessRecord(
            type=MsgType.TEST,
            seq=(first_seq + i) & 0xFFFF,
            key_id=0,
            event_class=EventClass.NORMAL,
            confidence=0,
            osnma=Osnma.NOT_AVAILABLE,
            stationary=False,
            rx_jamming=False,
            rx_spoofing=False,
            tamper=False,
            mesh=False,
            lat_e7=0,
            lon_e7=0,
            timestamp=ts,
            duration_s=w.duration_s,
            cn0_mean=min(255, round(w.cn0_mean)),
            cn0_drop=min(255, w.cn0_drop),
            cn0_spread=min(255, w.cn0_spread),
            agc=0,
        )
        records.append(encode_hex(rec, node_id, keys))
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log")
    ap.add_argument("--window", type=int, default=10, help="seconds per output window")
    ap.add_argument("--records", action="store_true", help="also print signed TEST records (hex)")
    ap.add_argument("--first-seq", type=int, default=0)
    args = ap.parse_args(argv)
    windows = read(args.log, args.window)
    for w in windows:
        print(json.dumps(asdict(w)))
    if args.records:
        try:
            node_id = int(os.environ["STOZHER_PHONE_NODE_ID"])
            master = bytes.fromhex(os.environ["STOZHER_PHONE_MASTER_KEY"])
        except (KeyError, ValueError):
            print(
                "--records needs STOZHER_PHONE_NODE_ID and STOZHER_PHONE_MASTER_KEY (hex) in the environment.",
                file=sys.stderr,
            )
            return 2
        for h in to_hex(windows, node_id, master, args.first_seq):
            print(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
