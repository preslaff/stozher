"""Dual-band run: the virtual node (firmware/node/sim) sends every record on two simulated LoRa bands, each through its
own simulated bridge (services/mesh_bridge, sim mode), into the API as SIM data (docs/scenario.md, "Dual-band run").

Data only: no radio is touched. The node is registered with `kind: sim` and the public test key; records arrive through
`/v1/ingest/sim`, so every event is labelled SIM. The kit's real bridges and radios keep running untouched.
"""

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[4]
VIRTUAL_NODE = ROOT / "firmware/node/.pio/sim/virtual_node"
STATE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "stozher/sim-dualband"
)
TEST_MASTER_KEY = bytes(range(16)).hex()  # the PUBLIC test key: only ever for SIM nodes
NODE_NAME = "dual-band-1"
POSITION = (
    43.1905,
    27.9120,
)  # synthetic, from the coastal scenario (services/simulator/scenarios/coastal.yaml)


@dataclass(frozen=True)
class Band:
    name: str  # "868" or "433"
    radio_port: int  # simulated radio (TCP on 127.0.0.1)
    health_port: int

    @property
    def external_id(self) -> str:
        return f"{NODE_NAME}-{self.name}"


BANDS = (Band("868", 47868, 8768), Band("433", 47433, 8769))


@dataclass
class Report:
    node_id: int
    records_made: int  # sequence numbers the node used in this run
    node: dict[str, int]  # the virtual node's own counts
    bridges: dict[str, dict[str, int]]  # per band: accepted, duplicate, rejected, acks sent
    stored_seqs: list[int]
    missing: list[int] = field(default_factory=list)
    all_sim: bool = True


def parse_node_summary(line: str) -> dict[str, int]:
    """`summary: next_seq=12 acks_applied=3 band_868 delivered=9 ...` -> flat counts, per band prefixed."""
    out: dict[str, int] = {}
    band = ""
    for tok in line.split()[1:]:
        if tok.startswith("band_"):
            band = tok[5:] + "_"
            continue
        k, _, v = tok.partition("=")
        if v.isdigit():
            out[band + k] = int(v)
    return out


def build_report(
    node_id: int,
    first_seq: int,
    node_summary: dict[str, int],
    bridge_health: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> Report:
    events = [e for e in events if e.get("node_id") == node_id]
    last = node_summary.get("next_seq", first_seq) - 1
    made = list(range(first_seq, last + 1))
    stored = sorted({e["seq"] for e in events if first_seq <= e["seq"] <= last})
    bridges = {}
    for band, h in bridge_health.items():
        up = h.get("uplink", {})
        bridges[band] = {
            "accepted": up.get("accepted", 0),
            "duplicate": up.get("duplicate", 0),
            "rejected": up.get("rejected", 0),
            "acks_sent": (h.get("acks") or {}).get("sent", 0),
        }
    return Report(
        node_id=node_id,
        records_made=len(made),
        node=node_summary,
        bridges=bridges,
        stored_seqs=stored,
        missing=sorted(set(made) - set(stored)),
        all_sim=all(e.get("sim") is True for e in events),
    )


def format_report(r: Report) -> str:
    lines = [
        f"SIM dual-band run, node {r.node_id}",
        f"records made        {r.records_made}",
        f"stored by the API   {len(r.stored_seqs)}   missing {len(r.missing)}"
        + (f" (seq {', '.join(map(str, r.missing))})" if r.missing else ""),
    ]
    for band in ("868", "433"):
        b = r.bridges.get(band, {})
        lines.append(
            f"band {band}: radio delivered {r.node.get(band + '_delivered', 0)}, lost on air {r.node.get(band + '_lost', 0)};"
            f" bridge accepted {b.get('accepted', 0)}, duplicate {b.get('duplicate', 0)},"
            f" rejected {b.get('rejected', 0)}, acks sent {b.get('acks_sent', 0)}"
        )
    lines.append(f"acks applied by the node {r.node.get('acks_applied', 0)}")
    lines.append("all events labelled SIM: " + ("yes" if r.all_sim else "NO"))
    return "\n".join(lines)


async def _ok(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        raise RuntimeError(
            f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:200]}"
        )
    return r.json()


async def ensure_node(http: httpx.AsyncClient, op: dict[str, str]) -> int:
    for n in await _ok(await http.get("/v1/nodes", headers=op)):
        if n["name"] == NODE_NAME and n["kind"] == "sim":
            return int(n["id"])
    body = {
        "name": NODE_NAME,
        "kind": "sim",
        "master_key_hex": TEST_MASTER_KEY,
        "surveyed_lat": POSITION[0],
        "surveyed_lon": POSITION[1],
        "transport_ids": [{"transport": "sim", "external_id": b.external_id} for b in BANDS],
    }
    return int((await _ok(await http.post("/v1/nodes", headers=op, json=body)))["id"])


def start_bridge(api: str, band: Band, system_key: str) -> subprocess.Popen[bytes]:
    env = {k: v for k, v in os.environ.items() if k not in ("STOZHER_RESPONDER_KEY", "MESH_PORT")}
    env["STOZHER_SYSTEM_KEY"] = system_key
    STATE.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "--package", "stozher-mesh-bridge", "python", "-m", "mesh_bridge",
        "--api", api, "--port", f"sim-tcp:127.0.0.1:{band.radio_port}",
        "run", "--name", f"sim-{band.name}", "--health-port", str(band.health_port),
        "--spool", str(STATE / f"spool-{band.name}.jsonl"),
    ]  # fmt: skip
    log = open(STATE / f"bridge-{band.name}.log", "ab")  # noqa: SIM115 - handed to the child process
    return subprocess.Popen(
        cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=ROOT
    )


async def bridge_health(band: Band) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=3) as h:
            return (await h.get(f"http://127.0.0.1:{band.health_port}/")).json()  # type: ignore[no-any-return]
    except (httpx.HTTPError, ValueError):
        return None


def stop(p: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)


async def run(
    api: str, operator_key: str, system_key: str, node_args: list[str], seconds: int, reset: bool
) -> int:
    if not VIRTUAL_NODE.exists():
        print(f"no {VIRTUAL_NODE}: run make fw-sim", file=sys.stderr)
        return 2
    op = {"X-API-Key": operator_key}
    async with httpx.AsyncClient(base_url=api, timeout=30) as http:
        if reset:
            print("reset:", await _ok(await http.post("/v1/sim/reset", headers=op)))
        cloud = await _ok(await http.get("/v1/control/public-key"))
        node_id = await ensure_node(http, op)
        state = STATE / f"node-{node_id}.seq"
        first_seq = 1
        if state.exists():
            m = re.match(r"\s*0\s+(\d+)", state.read_text())
            first_seq = int(m.group(1)) if m else 1

        started = datetime.now(UTC)
        bridges = {b.name: start_bridge(api, b, system_key) for b in BANDS}
        try:
            for _ in range(60):  # wait until both simulated radios listen
                hs = [await bridge_health(b) for b in BANDS]
                if all(h and h.get("radio", {}).get("connected") for h in hs):
                    break
                await asyncio.sleep(1)
            else:
                print("the simulated bridges did not start; see " + str(STATE), file=sys.stderr)
                return 1
            cmd = [
                str(VIRTUAL_NODE), "--node-id", str(node_id), "--state", str(state),
                "--cloud-key", cloud["public_key_hex"], "--cloud-key-id", str(cloud["cloud_key_id"]),
                "--lat-e7", str(round(POSITION[0] * 1e7)), "--lon-e7", str(round(POSITION[1] * 1e7)),
                "--seconds", str(seconds),
                *[x for b in BANDS for x in (f"--port-{b.name}", str(b.radio_port), f"--id-{b.name}", b.external_id)],
                *node_args,
            ]  # fmt: skip
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE)
            summary: dict[str, int] = {}
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode().rstrip()
                print("node |", line, flush=True)
                if line.startswith("summary:"):
                    summary = parse_node_summary(line)
            await proc.wait()
            await asyncio.sleep(20)  # let both bridges finish forwarding and sending acks
            health = {b.name: (await bridge_health(b)) or {} for b in BANDS}
            # /v1/events has no node filter: ask for this run's time window and keep this node's events
            since = (started - timedelta(minutes=1)).isoformat()
            events = await _ok(
                await http.get("/v1/events", headers=op, params={"from": since, "limit": 1000})
            )
            items = events.get("items", events) if isinstance(events, dict) else events
        finally:
            for p in bridges.values():
                stop(p)
    rep = build_report(node_id, first_seq, summary, health, items)
    print(format_report(rep))
    return 0 if not rep.missing and rep.all_sim else 1
