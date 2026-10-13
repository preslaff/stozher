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


async def _reset(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as http:
        print(await SimClient(http, os.environ.get("STOZHER_OPERATOR_KEY", ""), "").reset())
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
    sub.add_parser("reset", help="delete all simulated data")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args) if args.cmd == "run" else _reset(args)))
