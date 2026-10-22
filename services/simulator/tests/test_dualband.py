"""Dual-band run: the report logic, and the virtual node against two simulated radios (no API, no hardware)."""

import shutil
import subprocess
import time

import pytest
from simulator.dualband import ROOT, VIRTUAL_NODE, build_report, format_report, parse_node_summary

SUMMARY = (
    "summary: next_seq=9 acks_applied=4 band_868 delivered=9 lost=0 ack_lines=2 "
    "band_433 delivered=5 lost=4 ack_lines=2"
)


def test_the_node_summary_is_read_per_band() -> None:
    s = parse_node_summary(SUMMARY)
    assert s["next_seq"] == 9 and s["acks_applied"] == 4
    assert (s["868_delivered"], s["868_lost"], s["433_delivered"], s["433_lost"]) == (9, 0, 5, 4)


def test_the_report_counts_only_this_node_and_this_run() -> None:
    events = [
        *({"node_id": 7, "seq": s, "sim": True} for s in (3, 4, 5, 6, 8)),
        {"node_id": 7, "seq": 2, "sim": True},  # an earlier run: not counted
        {"node_id": 4243, "seq": 5, "sim": False},  # a real node in the same time window: ignored
    ]
    health = {
        "868": {"uplink": {"accepted": 3, "duplicate": 1}, "acks": {"sent": 2}},
        "433": {"uplink": {"accepted": 2, "duplicate": 2}, "acks": {"sent": 1}},
    }
    r = build_report(7, 3, parse_node_summary(SUMMARY), health, events)
    assert r.records_made == 6 and r.stored_seqs == [3, 4, 5, 6, 8] and r.missing == [7]
    assert r.all_sim
    text = format_report(r)
    assert "missing 1 (seq 7)" in text and "band 433: radio delivered 5, lost on air 4" in text


def test_a_live_event_for_the_sim_node_would_be_flagged() -> None:
    r = build_report(7, 1, {"next_seq": 2}, {}, [{"node_id": 7, "seq": 1, "sim": False}])
    assert not r.all_sim and "NO" in format_report(r)


@pytest.mark.skipif(
    shutil.which("g++") is None or shutil.which("gcc") is None, reason="needs gcc and g++"
)
def test_the_virtual_node_sends_every_record_on_both_bands(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from mesh_bridge.sim_radio import SimTcpRadio

    subprocess.run(["make", "-s", "fw-sim"], cwd=ROOT, check=True, capture_output=True)
    got: dict[str, list[str]] = {"868": [], "433": []}
    radios = {band: SimTcpRadio("127.0.0.1", 0) for band in got}
    for band, radio in radios.items():
        radio.start(lambda m, b=band: got[b].append(m.text))
    try:
        cmd = [str(VIRTUAL_NODE), "--node-id", "77", "--state", str(tmp_path / "seq"),
               "--cloud-key", "00" * 32, "--lat-e7", "431905000", "--lon-e7", "279120000",
               "--seconds", "8", "--heartbeat-s", "3", "--drop-433", "1"]  # fmt: skip
        for band, radio in radios.items():
            cmd += [f"--port-{band}", str(radio.bound_port), f"--id-{band}", f"t-{band}"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        time.sleep(0.2)
    finally:
        for radio in radios.values():
            radio.close()
    summary = parse_node_summary(
        next(line for line in out.splitlines() if line.startswith("summary:"))
    )
    assert summary["next_seq"] >= 3  # boot heartbeat and at least one more
    assert len(got["868"]) == summary["868_delivered"] >= 2
    assert all(t.startswith("STZ1:") for t in got["868"])
    assert (
        got["433"] == [] and summary["433_lost"] == summary["868_delivered"]
    )  # every 433 copy lost on air
    assert (tmp_path / "seq").read_text().split() == ["0", str(summary["next_seq"])]
