import pytest
from simulator import physics
from simulator.detector import Observables, classify
from simulator.engine import generate, master_key
from simulator.run import encode_packet
from simulator.scenario import Scenario, load
from stozher_core.codec import decode
from stozher_core.crypto import derive_keys
from stozher_core.record import EventClass


def test_path_loss_grows_with_distance_and_exponent() -> None:
    assert physics.path_loss_db(1000, 3.2) > physics.path_loss_db(100, 3.2)
    assert physics.path_loss_db(1000, 3.5) > physics.path_loss_db(1000, 2.0)
    assert physics.path_loss_db(100, 3.2) == pytest.approx(76.4, abs=0.1)


def test_effective_cn0_drops_with_stronger_interference() -> None:
    assert physics.effective_cn0(44, None) == 44
    weak, strong = physics.effective_cn0(44, -115), physics.effective_cn0(44, -90)
    assert 44 > weak > strong and strong < 25
    assert physics.agc_level(None) == 100 < physics.agc_level(-90) < physics.agc_level(-60)


def obs(**kw: float) -> Observables:
    base = dict(
        cn0_mean=44,
        cn0_drop=0,
        cn0_spread=4,
        agc=100,
        satellites=12,
        pos_jump_m=0.0,
        clock_jump_ms=0.0,
    )
    return Observables(**{**base, **kw})  # type: ignore[arg-type]


def test_detector_rules() -> None:
    assert classify(obs()).event_class is EventClass.NORMAL
    assert classify(obs(cn0_drop=20, agc=180)).event_class is EventClass.JAMMING
    assert classify(obs(cn0_drop=9, agc=100)).event_class is EventClass.BENIGN  # no AGC rise
    assert classify(obs(pos_jump_m=400)).event_class is EventClass.SPOOFING
    big, small = classify(obs(cn0_drop=28, agc=200)), classify(obs(cn0_drop=9, agc=140))
    assert big.confidence > small.confidence and big.rx_jamming


def test_engine_is_deterministic_and_seed_sensitive() -> None:
    sc = load("coastal")
    a, b = generate(sc), generate(sc)
    assert [(p.node, p.sent_t, p.arrive_t, p.record) for p in a.packets] == [
        (p.node, p.sent_t, p.arrive_t, p.record) for p in b.packets
    ]
    other = generate(sc.model_copy(update={"seed": 8}))
    assert [p.arrive_t for p in other.packets] != [p.arrive_t for p in a.packets]


def test_packets_are_valid_records_and_forged_ones_are_not() -> None:
    sc = load("coastal")
    plan = generate(sc)
    ids = {n.name: i + 1 for i, n in enumerate(sc.nodes)}
    from stozher_core.errors import PacketError

    for p in plan.packets:
        raw = bytes.fromhex(encode_packet(p, ids[p.node], sc, base_ts=1_800_000_000))
        keys = derive_keys(master_key(sc.seed, p.node), ids[p.node], 0)
        assert len(raw) == (32 if next(n for n in sc.nodes if n.name == p.node).extended else 28)
        if p.mutation == "none":
            assert decode(raw, ids[p.node], keys).seq == p.record.seq
        else:
            with pytest.raises(PacketError):
                decode(raw, ids[p.node], keys)


def test_satellite_links_are_slow_and_reordered() -> None:
    plan = generate(load("coastal"))
    kineis = [p for p in plan.packets if p.node.startswith(("port-1", "port-4", "city-1"))]
    mesh = [p for p in plan.packets if p.node in ("port-2", "port-3")]
    assert (
        min(
            p.arrive_t - p.sent_t for p in kineis if p.expect == "accepted" and p.mutation == "none"
        )
        > 0.1
    )
    assert sum(p.arrive_t - p.sent_t for p in kineis) / len(kineis) > 20 * sum(
        p.arrive_t - p.sent_t for p in mesh
    ) / len(mesh)


def test_scenario_validation() -> None:
    with pytest.raises(ValueError):
        Scenario.model_validate(
            {
                "name": "x",
                "duration_s": 10,
                "nodes": [],
                "forged": [{"at": 1, "node": "nope", "mode": "bit_flip"}],
            }
        )
    with pytest.raises(ValueError):
        Scenario.model_validate(
            {
                "name": "x",
                "duration_s": 10,
                "nodes": [{"name": "a", "lat": 1, "lon": 1}],
                "jammers": [{"id": "j", "start": 0, "end": 1}],
            }
        )
