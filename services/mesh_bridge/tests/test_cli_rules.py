"""Rules the bridge shares with the API: checked against the API's source text (the GPL bridge never imports it), and
the sim/live spool separation."""

import json
import re
from pathlib import Path

import httpx
from mesh_bridge.cli import BRIDGE_NAME_PATTERN, DEFAULT_BRIDGE_NAME, _bridge_name
from mesh_bridge.forwarder import Forwarder
from mesh_bridge.protocol import MeshText

API = Path(__file__).resolve().parents[2] / "api" / "src" / "stozher_api"


def test_the_bridge_name_rule_is_the_apis() -> None:
    system = (API / "routes" / "system.py").read_text()
    m = re.search(r"instance: str \| None = Field\((.*?)\n    \)", system, re.S)
    assert m, "HeartbeatIn.instance not found in the API source"
    assert f'pattern=r"^{BRIDGE_NAME_PATTERN}$"' in m.group(1)
    heartbeat = (API / "heartbeat.py").read_text()
    assert re.search(rf'^DEFAULT_INSTANCE = "{DEFAULT_BRIDGE_NAME}"', heartbeat, re.M)
    assert _bridge_name("433") == "433" and _bridge_name("") is None


def _forwarder(spool: Path, sim: bool, seen: list[str]) -> Forwarder:
    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r.url.path)
        return httpx.Response(202, json={"status": "accepted"})

    return Forwarder(
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api"),
        "K",
        spool=spool,
        sim=sim,
    )


def test_a_spool_line_from_the_other_mode_is_set_aside_not_sent(tmp_path: Path) -> None:
    spool = tmp_path / "spool.jsonl"
    rec = "ab" * 28
    sim = _forwarder(spool, True, [])
    assert sim.submit(MeshText("!a1b2c3d4", "STZ1:" + rec, hops=0))
    assert json.loads(spool.read_text())["sim"] is True
    spool.write_text(
        spool.read_text()
        + json.dumps({"from_id": "!0b533538", "payload_hex": "cd" * 28, "hops": 1})
        + "\n"
    )
    live = _forwarder(
        spool, False, []
    )  # the untagged line is an old live one: it stays; the sim one goes aside
    assert [i["payload_hex"] for i in live.queue] == ["cd" * 28]
    assert json.loads((tmp_path / "spool.jsonl.sim").read_text())["payload_hex"] == rec
    assert len(spool.read_text().splitlines()) == 1
