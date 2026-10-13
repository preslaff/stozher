# Network simulator and scenarios

`services/simulator` replays a scenario through the real stack. It works on data only: it
computes receiver observables (numbers) for virtual nodes, runs a stand-in for the node's
detection rules, encodes the result with the real codec and keys, and posts it to
`/v1/ingest/sim`. It never generates, transmits or drives anything that emits a signal
(CLAUDE.md rule 2). Everything it creates is stored with `kind = sim` and shown as SIM.

## Run

```
make migrate && make api        # terminal 1 (uvicorn, port 8000)
make worker                     # terminal 2 (analytics)
STOZHER_OPERATOR_KEY=... STOZHER_SYSTEM_KEY=... make sim S=coastal
```

Create the two keys with `uv run python -m stozher_api.cli create-key --role operator|system --label ...`.
`--speed N` paces arrivals in real time at N times (timestamps then start now); without it the
run is as fast as possible with timestamps ending now. Each run first calls `POST /v1/sim/reset`
(`--keep` skips it) and registers its nodes. `python -m simulator reset` only resets.
`STOZHER_API_URL` sets the target (default `http://localhost:8000`). The exit code is 0 only if
every source is found, none are merged, and every forged or replayed record is handled correctly.

## Scenario file (YAML)

`name`, `seed`, `duration_s`, `epoch_s` (30), `heartbeat_s` (600), `event_period_s` (120), then:

- `nodes`: `name`, `lat`, `lon`, `kind` (expert, tbeam, phone), `link` (kineis, mesh, direct),
  `extended` (32-byte record). Fixed nodes report their surveyed position.
- `jammers`: `id`, `start`, `end`, `eirp_dbm`, `kind` (wideband, chirp, tone), `duty`,
  `path_loss_exp`, and either `lat`/`lon` or a `track` of `[t, lat, lon]` (a vehicle on a road).
- `spoofers`: `id`, `start`, `end`, `lat`, `lon`, `radius_m`, `push_m`, `push_bearing_deg`,
  `time_push_ms`, `drag_off_s`. Nodes inside the radius see the position/time offset ramp up.
- `benign`: non-hostile degradations (e.g. scintillation) at named nodes; they must not raise hotspots.
- `forged`: `wrong_key`, `bit_flip` (both must be rejected) and `resend` (must return `duplicate`).

Links add realistic delay, jitter and loss (Kinéis about 3 min ± 2, mesh seconds, direct sub-second),
so records arrive late and out of order. Sequence numbers burn on lost packets.

## Model assumptions (not RF design)

Log-distance path loss from a 100 m free-space reference (default exponent 3.2); effective
C/N0 from the standard broadband-interferer formula, jammer type as an effective-bandwidth
factor; AGC indicator rising with interference; spoofed nodes keep a strong signal while
position or time moves. The virtual node's rules: spoofing if position jump >= 150 m or clock
jump >= 200 ms with little C/N0 loss; jamming if C/N0 drop >= 8 dB and AGC >= 135; benign if
C/N0 drops but AGC stays low. Real thresholds are calibrated during the weekend.

## Metrics

Per source: found (a hotspot of the right kind within 8 km), time from start to the first
adverse report (sent, and arrived including link delay), localization error (nearest point of
its track to the hotspot centroid). Overall: detection rate, **merged sources** (distinct
sources answered by one hotspot), false-alarm hotspots and reports, forged and replayed
records handled correctly, and the outcome counts.

## The coastal scenario

Varna, one hour, 19 nodes. A static jammer at the port (600–1800 s), a vehicle-borne jammer
driving north along the coast road (2000–3200 s), a spoofing zone at the western harbour
(2400–3000 s), a scintillation episode at three southern nodes (1200–1500 s), 3 wrong-key and 2
bit-flipped records, and 2 resent records. Reference result: all 3 sources found, 0 merged,
0 false alarms, 7/7 forged or replayed records handled correctly, 364 packets.
