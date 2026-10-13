"""The coastal scenario against the real API and worker, scored against ground truth."""

from simulator.client import SimClient
from simulator.run import run_scenario
from simulator.scenario import load
from sqlalchemy import func, select
from stozher_api.models import Event
from stozher_worker.main import HANDLERS
from stozher_worker.queue import run_pending


async def test_coastal_scenario_end_to_end(client):  # type: ignore[no-untyped-def]
    sim = SimClient(client, client.keys["operator"], client.keys["system"])
    sc = load("coastal")

    async def worker() -> None:
        await run_pending(client.app.state.sessionmaker, HANDLERS)

    plan, rep, hotspots = await run_scenario(sc, sim, after_ingest=worker, settle_timeout_s=3)

    assert rep.mismatches == []
    assert rep.detection_rate == 1.0, [(s.id, s.detected) for s in rep.sources]
    assert rep.merged_sources == 0, "two real jammers must not share one hotspot"
    assert rep.false_alarm_hotspots == 0 and rep.false_alarm_reports == 0
    assert sorted(h["kind"] for h in hotspots) == ["jamming", "jamming", "spoofing"]
    assert rep.forged_total == rep.forged_correct == 7  # 3 wrong key + 2 bit flips + 2 resends
    assert rep.outcomes.get("rejected") == 5 and rep.outcomes.get("duplicate") == 2
    assert all(
        s.localization_error_m is not None and s.localization_error_m < 8000 for s in rep.sources
    )
    assert {h["kind"] for h in hotspots} == {"jamming", "spoofing"} and all(
        h["sim"] for h in hotspots
    )

    async with client.app.state.sessionmaker() as s:  # everything stored is labelled simulated
        assert (
            await s.execute(select(func.count()).select_from(Event).where(Event.sim.is_(False)))
        ).scalar_one() == 0


async def test_reset_removes_only_simulated_data(client):  # type: ignore[no-untyped-def]
    from .test_api import hdr, packet, post, rec, register

    live = await register(client, ext="live-1")
    await post(client, packet(rec(seq=1), live), ext="live-1")
    sim = SimClient(client, client.keys["operator"], client.keys["system"])
    await sim.register(load("coastal"))
    out = await sim.reset()
    assert out["deleted_nodes"] == len(load("coastal").nodes)
    events = (await client.get("/v1/events", headers=hdr(client, "responder"))).json()
    assert events["count"] == 1 and events["items"][0]["sim"] is False
    assert (await client.post("/v1/sim/reset", headers=hdr(client, "responder"))).status_code == 403
