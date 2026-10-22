import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict

import httpx

from .client import SimClient
from .metrics import Report
from .run import run_scenario
from .scenario import load


def format_report(rep: Report, name: str) -> str:
    lines = [
        f"scenario {name}: {rep.packets} packets ({rep.lost} lost on the link) -> {rep.outcomes}",
        "",
    ]
    lines.append(f"{'source':16}{'kind':10}{'found':7}{'report':>9}{'arrive':>9}{'loc err':>10}")
    for s in rep.sources:
        t1 = f"{s.time_to_report_s:.0f}s" if s.time_to_report_s is not None else "-"
        t2 = f"{s.time_to_arrive_s:.0f}s" if s.time_to_arrive_s is not None else "-"
        err = f"{s.localization_error_m:.0f} m" if s.localization_error_m is not None else "-"
        lines.append(
            f"{s.id:16}{s.kind:10}{'yes' if s.detected else 'NO':7}{t1:>9}{t2:>9}{err:>10}"
        )
    lines += [
        "",
        f"detection rate        {rep.detection_rate:.0%}",
        f"merged sources       {rep.merged_sources}  (distinct sources sharing one hotspot)",
        f"false-alarm hotspots  {rep.false_alarm_hotspots}",
        f"false-alarm reports   {rep.false_alarm_reports}",
        f"forged/replayed       {rep.forged_correct}/{rep.forged_total} handled correctly",
    ]
    lines += [f"MISMATCH: {m}" for m in rep.mismatches]
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    sc = load(args.scenario)
    keys = {k: os.environ.get(f"STOZHER_{k.upper()}_KEY", "") for k in ("operator", "system")}
    if not all(keys.values()):
        print(
            "set STOZHER_OPERATOR_KEY and STOZHER_SYSTEM_KEY (see docs/api.md section 3)",
            file=sys.stderr,
        )
        return 2
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as http:
        client = SimClient(http, keys["operator"], keys["system"])
        _, rep, _ = await run_scenario(sc, client, speed=args.speed, keep=args.keep)
    print(format_report(rep, sc.name))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(asdict(rep), f, indent=2)
    return 0 if rep.detection_rate == 1.0 and not rep.mismatches and not rep.merged_sources else 1


async def _run_hazards(args: argparse.Namespace) -> int:
    from .hazards import HazardScenario

    key = os.environ.get("STOZHER_OPERATOR_KEY", "")
    if not key:
        print(
            "set STOZHER_OPERATOR_KEY (see docs/api.md section 3); a worker must be running",
            file=sys.stderr,
        )
        return 2
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as http:
        card = await HazardScenario(http, key).run(keep=args.keep)
    for ch in card.checks:
        print(
            f"{'ok  ' if ch.ok else 'FAIL'} {ch.name}"
            + (f"   [{ch.detail}]" if ch.detail and not ch.ok else "")
        )
    print(f"\n{sum(c.ok for c in card.checks)}/{len(card.checks)} checks passed")
    return 0 if card.passed else 1


async def _dualband(args: argparse.Namespace) -> int:
    from .dualband import run as run_dualband

    keys = {k: os.environ.get(f"STOZHER_{k.upper()}_KEY", "") for k in ("operator", "system")}
    if not all(keys.values()):
        print(
            "set STOZHER_OPERATOR_KEY and STOZHER_SYSTEM_KEY (see docs/api.md section 3)",
            file=sys.stderr,
        )
        return 2
    node_args = ["--heartbeat-s", str(args.heartbeat_s), "--seed", str(args.seed)]
    node_args += ["--jam-at", str(args.jam_at), "--jam-for", str(args.jam_for)]
    for band in ("868", "433"):
        drop = getattr(args, f"drop_{band}")
        outage = getattr(args, f"outage_{band}")
        if drop:
            node_args += [f"--drop-{band}", str(drop)]
        if outage:
            node_args += [f"--outage-{band}", outage]
    return await run_dualband(
        args.api, keys["operator"], keys["system"], node_args, round(args.minutes * 60), args.reset
    )


async def _reset(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as http:
        print(await SimClient(http, os.environ.get("STOZHER_OPERATOR_KEY", ""), "").reset())
    return 0


def cmd_packet(args: argparse.Namespace) -> int:
    """Print a signed witness record as mesh text (STZ1:<hex>) for a radio or ingest test.

    Test data only: the node must be registered with the same master key (the default is the
    well-known test key, never a real one).
    """
    import time

    from stozher_core.codec import encode_hex
    from stozher_core.crypto import derive_keys
    from stozher_core.record import EventClass, MsgType, Osnma, WitnessRecord

    cls = EventClass[args.cls.upper()]
    rec = WitnessRecord(
        type=MsgType.HEARTBEAT if cls is EventClass.NORMAL else MsgType.EVENT,
        seq=args.seq,
        key_id=0,
        event_class=cls,
        confidence=0 if cls is EventClass.NORMAL else args.confidence,
        osnma=Osnma.UNKNOWN,
        stationary=True,
        rx_jamming=cls is EventClass.JAMMING,
        rx_spoofing=cls is EventClass.SPOOFING,
        tamper=False,
        mesh=True,
        lat_e7=round(args.lat * 1e7),
        lon_e7=round(args.lon * 1e7),
        timestamp=int(time.time()),
        duration_s=30,
        cn0_mean=30,
        cn0_drop=14 if cls is not EventClass.NORMAL else 0,
        cn0_spread=5,
        agc=180 if cls is EventClass.JAMMING else 100,
    )
    keys = derive_keys(bytes.fromhex(args.master_key), args.node_id, 0)
    print("STZ1:" + encode_hex(rec, args.node_id, keys))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="simulator", description="STOZHER network simulator (data only)"
    )
    ap.add_argument("--api", default=os.environ.get("STOZHER_API_URL", "http://localhost:8000"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a scenario and score it")
    r.add_argument("scenario", help="scenario name (e.g. coastal) or a YAML path")
    r.add_argument(
        "--speed",
        type=float,
        default=None,
        help="pace in real time at this multiple (default: as fast as possible)",
    )
    r.add_argument("--keep", action="store_true", help="do not reset earlier simulated data first")
    r.add_argument("--out", help="write the report as JSON")
    h = sub.add_parser("hazards", help="run the road-hazard scenario (needs a running worker)")
    h.add_argument("--keep", action="store_true", help="do not reset earlier simulated data first")
    d = sub.add_parser(
        "dualband",
        help="a simulated dual-band node through two simulated bridges (SIM data; make fw-sim first)",
    )
    d.add_argument("--minutes", type=float, default=5.0)
    d.add_argument(
        "--heartbeat-s", type=int, default=60, help="heartbeat interval of the virtual node"
    )
    d.add_argument(
        "--jam-at", type=int, default=120, help="seconds into the run the jamming episode starts"
    )
    d.add_argument("--jam-for", type=int, default=30)
    for band in ("868", "433"):
        d.add_argument(
            f"--drop-{band}",
            type=float,
            default=0.0,
            help=f"share of {band} MHz messages lost on air",
        )
        d.add_argument(f"--outage-{band}", default=None, help="FROM:TO seconds with the band down")
    d.add_argument("--seed", type=int, default=1, help="seed of the simulated losses")
    d.add_argument("--reset", action="store_true", help="delete all simulated data first")
    sub.add_parser("reset", help="delete all simulated data")
    k = sub.add_parser("packet", help="print one signed record as STZ1:<hex> mesh text (test data)")
    k.add_argument("--node-id", type=int, required=True, help="the node's uint32 id from the API")
    k.add_argument(
        "--seq", type=int, required=True, help="sequence number; never reuse one per node"
    )
    k.add_argument(
        "--master-key",
        default="000102030405060708090a0b0c0d0e0f",
        help="32 hex chars (test key by default)",
    )
    k.add_argument("--cls", default="jamming", choices=["normal", "jamming", "spoofing"])
    k.add_argument("--confidence", type=int, default=24)
    k.add_argument("--lat", type=float, default=43.1989)
    k.add_argument("--lon", type=float, default=27.9166)
    args = ap.parse_args()
    if args.cmd == "packet":
        sys.exit(cmd_packet(args))
    sys.exit(
        asyncio.run(
            {"run": _run, "hazards": _run_hazards, "dualband": _dualband}.get(args.cmd, _reset)(
                args
            )
        )
    )
