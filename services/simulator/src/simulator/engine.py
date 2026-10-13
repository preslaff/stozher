"""Deterministic scenario engine: turns a Scenario into timed packets plus ground truth.

Pure given the seed. Times are virtual seconds from the scenario start; `sent_t` is when the
node emits a record and `arrive_t` when it reaches the backend after link delay, jitter and
loss, so satellite latency and out-of-order delivery are part of the picture.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from stozher_core.record import EventClass, Extended, MsgType, Osnma, WitnessRecord
from stozher_core.scaling import encode_jump

from .detector import Observables, classify
from .geo import distance_m, interpolate
from .physics import CN0_NOMINAL_DBHZ, agc_level, effective_cn0, interferer_dbm
from .scenario import JammerSpec, NodeSpec, Scenario

# (mean delay s, jitter s, loss probability) per link; Kinéis is minutes, mesh seconds.
LINKS = {"kineis": (180.0, 120.0, 0.02), "mesh": (4.0, 3.0, 0.01), "direct": (0.5, 0.5, 0.0)}
Expect = Literal["accepted", "duplicate", "rejected"]


@dataclass(frozen=True)
class Packet:
    node: str
    sent_t: float
    arrive_t: float
    record: WitnessRecord  # timestamp is relative; the runner adds the wall-clock base
    mutation: Literal["none", "wrong_key", "bit_flip"] = "none"
    expect: Expect = "accepted"


@dataclass(frozen=True)
class TruthSource:
    id: str
    kind: Literal["jamming", "spoofing"]
    start: float
    end: float
    track: list[tuple[float, float, float]]  # (t, lat, lon)


@dataclass
class Plan:
    scenario: Scenario
    packets: list[Packet] = field(default_factory=list)
    truth: list[TruthSource] = field(default_factory=list)
    lost: int = 0


def master_key(seed: int, name: str) -> bytes:
    return hashlib.sha256(f"stozher-sim:{seed}:{name}".encode()).digest()[:16]


def _truth(sc: Scenario) -> list[TruthSource]:
    out: list[TruthSource] = []
    for j in sc.jammers:
        track = list(j.track) if j.track else [(j.start, j.lat or 0.0, j.lon or 0.0)]
        out.append(TruthSource(j.id, "jamming", j.start, j.end, track))
    for s in sc.spoofers:
        out.append(TruthSource(s.id, "spoofing", s.start, s.end, [(s.start, s.lat, s.lon)]))
    return out


def _jammer_pos(j: JammerSpec, t: float) -> tuple[float, float]:
    return interpolate(list(j.track), t) if j.track else (j.lat or 0.0, j.lon or 0.0)


def _observe(node: NodeSpec, t: float, sc: Scenario, rng: np.random.Generator) -> Observables:
    base = CN0_NOMINAL_DBHZ + rng.normal(0, 1.0)
    cn0, jam_total_dbm, kind = base, None, "wideband"
    for j in sc.jammers:
        if not (j.start <= t <= j.end) or rng.random() > j.duty:
            continue
        lat, lon = _jammer_pos(j, t)
        p = interferer_dbm(j.eirp_dbm, distance_m(node.lat, node.lon, lat, lon), j.path_loss_exp)
        jam_total_dbm = (
            p
            if jam_total_dbm is None
            else 10 * np.log10(10 ** (jam_total_dbm / 10) + 10 ** (p / 10))
        )
        kind = j.kind
    cn0 = effective_cn0(base, None if jam_total_dbm is None else float(jam_total_dbm), kind)
    pos_jump, clock_jump = 0.0, 0.0
    for sp in sc.spoofers:
        if (
            sp.start <= t <= sp.end
            and distance_m(node.lat, node.lon, sp.lat, sp.lon) <= sp.radius_m
        ):
            ramp = min(1.0, (t - sp.start) / max(sp.drag_off_s, 1.0))
            pos_jump = max(pos_jump, sp.push_m * ramp)
            clock_jump = max(clock_jump, sp.time_push_ms * ramp)
            cn0 += 1.5  # a spoofer's signal is typically a little stronger than the real ones
    for b in sc.benign:
        if b.start <= t <= b.end and node.name in b.nodes:
            cn0 -= b.drop_db * (0.7 + 0.6 * rng.random())
    drop = max(0.0, base - cn0) if pos_jump == 0 else max(0.0, CN0_NOMINAL_DBHZ - cn0)
    sats = max(0, round(12 - drop / 4 + rng.normal(0, 0.5)))
    return Observables(
        cn0_mean=int(max(0, round(cn0))),
        cn0_drop=int(round(drop)),
        cn0_spread=int(max(0, round((4.0 if drop < 8 else 2.5) + rng.normal(0, 0.7)))),
        agc=agc_level(None if jam_total_dbm is None else float(jam_total_dbm))
        + int(rng.integers(-3, 4)),
        satellites=sats,
        pos_jump_m=pos_jump,
        clock_jump_ms=clock_jump,
    )


def _record(
    node: NodeSpec,
    sent_t: float,
    obs: Observables,
    det_class: EventClass,
    conf: int,
    rx_jam: bool,
    rx_spoof: bool,
    duration: int,
    seq: int,
    battery: int,
) -> WitnessRecord:
    ext = (
        Extended(
            satellites=obs.satellites,
            pos_jump_code=encode_jump(obs.pos_jump_m),
            clock_jump_code=encode_jump(obs.clock_jump_ms),
            battery=battery,
        )
        if node.extended
        else None
    )
    return WitnessRecord(
        type=MsgType.HEARTBEAT if det_class is EventClass.NORMAL else MsgType.EVENT,
        seq=seq,
        key_id=0,
        event_class=det_class,
        confidence=conf,
        osnma=Osnma.UNKNOWN if node.kind == "expert" else Osnma.NOT_AVAILABLE,
        stationary=True,
        rx_jamming=rx_jam,
        rx_spoofing=rx_spoof,
        tamper=False,
        mesh=node.link == "mesh",
        lat_e7=round(node.lat * 1e7),
        lon_e7=round(node.lon * 1e7),  # surveyed position of a fixed node
        timestamp=int(sent_t),
        duration_s=duration,
        cn0_mean=min(255, obs.cn0_mean),
        cn0_drop=min(255, obs.cn0_drop),
        cn0_spread=min(255, obs.cn0_spread),
        agc=max(0, min(255, obs.agc)),
        extended=ext,
    )


def generate(sc: Scenario) -> Plan:
    rng = np.random.default_rng(sc.seed)
    plan = Plan(sc, truth=_truth(sc))
    seqs = {n.name: 0 for n in sc.nodes}
    sent: dict[str, list[Packet]] = {n.name: [] for n in sc.nodes}

    def deliver(
        node: NodeSpec,
        t: float,
        obs: Observables,
        cls: EventClass,
        conf: int,
        rx_jam: bool,
        rx_spoof: bool,
        duration: int,
        battery: int,
    ) -> None:
        delay, jitter, loss = LINKS[node.link]
        seqs[node.name] += 1
        rec = _record(node, t, obs, cls, conf, rx_jam, rx_spoof, duration, seqs[node.name], battery)
        if rng.random() < loss:
            plan.lost += 1
            return
        arrive = t + max(0.1, delay + rng.normal(0, jitter / 2))
        pkt = Packet(node.name, t, arrive, rec)
        plan.packets.append(pkt)
        sent[node.name].append(pkt)

    for i, node in enumerate(sc.nodes):
        alarm_since: float | None = None
        alarm_class = EventClass.NORMAL
        last_event_t = -1e9
        last_hb = -sc.heartbeat_s + (i * 37.0) % sc.heartbeat_s  # staggered first heartbeat
        t = 0.0
        while t <= sc.duration_s:
            obs = _observe(node, t, sc, rng)
            det = classify(obs)
            battery = max(5, 100 - int(t / 3600 * 2))
            if det.event_class is not EventClass.NORMAL:
                changed = det.event_class is not alarm_class
                if changed or t - last_event_t >= sc.event_period_s:
                    if alarm_since is None or changed:
                        alarm_since = t
                    deliver(
                        node,
                        t,
                        obs,
                        det.event_class,
                        det.confidence,
                        det.rx_jamming,
                        det.rx_spoofing,
                        int(t - alarm_since),
                        battery,
                    )
                    last_event_t = t
                alarm_class = det.event_class
            else:
                if alarm_class is not EventClass.NORMAL or t - last_hb >= sc.heartbeat_s:
                    deliver(node, t, obs, EventClass.NORMAL, 0, False, False, 0, battery)
                    last_hb = t
                alarm_class, alarm_since = EventClass.NORMAL, None
            t += sc.epoch_s

    for f in sc.forged:
        node = next(n for n in sc.nodes if n.name == f.node)
        base_t = min(f.at, sc.duration_s)
        for k in range(f.count):
            t = base_t + k
            if f.mode == "resend":
                origs = [p for p in sent[node.name] if p.sent_t <= f.at]
                if origs:
                    o = origs[-1]
                    plan.packets.append(
                        Packet(
                            node.name, o.sent_t, o.arrive_t + 30.0 + k, o.record, expect="duplicate"
                        )
                    )
                continue
            seqs[node.name] += 1
            obs = _observe(node, t, sc, rng)
            rec = _record(
                node, t, obs, EventClass.JAMMING, 31, True, False, 600, seqs[node.name], 90
            )
            plan.packets.append(
                Packet(node.name, t, t + 5.0, rec, mutation=f.mode, expect="rejected")
            )

    plan.packets.sort(key=lambda p: (p.arrive_t, p.node, p.record.seq))
    return plan
