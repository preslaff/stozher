# Trust model and analytics (v1)

Defines how stored events become hotspots and the GNSS trust grid. Code: the score is
`stozher_core.trust` (one definition); clustering, localization and grid building are in
`services/worker`. All of it is deterministic. The assistant is never in this path.

## Pipeline

Ingest enqueues one pending `recompute` job (bursts collapse into one). The worker also
enqueues one every 60 s so evidence ages out. A recompute reads events of types heartbeat,
event and summary (test records are ignored) and rebuilds the derived tables
`hotspots`, `hotspot_events` and `trust_cells` from scratch. Derived tables are never synced.

## Evidence weight

`weight = (0.25 + 0.75 · confidence/31) · 0.5^(age / 30 min)`: node confidence 0–31 maps to
0.25–1, and evidence halves every 30 minutes.

| Event class | Argues for |
|---|---|
| 0 normal | supporting evidence (TRUSTED) |
| 1 jamming | JAMMED |
| 2 spoofing | SPOOFED |
| 3 benign degradation, 5 time anomaly | DEGRADED |
| 4 tamper, 7 unknown | nothing (node integrity, not GNSS trust) |

## Trust score (per H3 res-8 cell)

Each event adds its weight to every cell within 4 rings of its own cell (61 cells; the outer cell centres are
3.3-3.9 km away). Two kinds of record are left out. A normal record sent without a GNSS fix (`no fix` flag,
docs/packet.md) says nothing about GNSS where the node is, so it is not support; an adverse record without a fix is
placed at the node's last place, which it carries. And a node's own normal record from a real fix ends its earlier
adverse records in the live grid: the node reports the condition over where it is, so a 30-second event no longer
holds 61 cells for an hour after it (a4b0, 5 Nov 2026). Those records stay in the events and in
`/v1/trust/history`; reports from other nodes are not affected.
For one cell, with `adv` the adverse weight, `sup` the supporting weight and `ratio = adv/(adv+sup)`:

- no evidence: `UNKNOWN`, score `null`, confidence 0 (no honest number exists);
- `adv = 0`: `TRUSTED`, score `70 + 30·min(1, sup)`;
- `ratio >= 0.5`: the heaviest adverse state (ties: JAMMED over SPOOFED over DEGRADED),
  score `cap · 2 · (1 - ratio)` with cap 30 (JAMMED, SPOOFED) or 60 (DEGRADED);
- otherwise (contested): `DEGRADED`, score `70 · (1 - ratio)`.

`independent_sources` is the number of distinct witness nodes. `confidence = min(1, 0.3 +
0.25·(sources - 1)) · min(1, adv + sup)`. `data_age_s` is the age of the newest contributing
event, computed when the response is built. Evidence older than 90 minutes is dropped, so a
cell with no recent evidence reverts to `UNKNOWN`. A cell fed by any simulated event is
flagged `sim`.

## Hotspots

Adverse events (jamming, spoofing) are grouped per kind. Strong reports (confidence >= 22, a
calibration parameter tuned on the coastal scenario) define hotspots: spatial DBSCAN on
great-circle distance (eps 5 km, min 1 event), then a split where consecutive events are more
than 1 h apart. Weak reports come from witnesses far from a source: each attaches to the nearest
strong group within 7.5 km and 10 minutes of its time span but never links two groups, and weak reports with no
strong group nearby form their own hotspots. Without this rule a node that hears two sources
faintly chains them into one hotspot (the simulator caught exactly that). (The specification
names HDBSCAN; DBSCAN is the deterministic first version and can be swapped without changing the
API.) Known limit: sources close in space *and* time cannot be told apart from witness reports
alone, and a moving source is one elongated hotspot until road-segment tracks exist.

- **Source estimate:** centroid weighted by `event weight · (1 + C/N0 drop / 10)` in a local
  azimuthal-equidistant projection. The ellipse is 2σ of the weighted spread, never smaller
  than 1500 m, with the major axis as a bearing. It expresses uncertainty from the
  witnesses' positions, not the transmitter's range.
- **State:** `active` while the newest event is under 30 min old, then `closed`. Hotspots are
  built from the last 6 h of events.
- **Id:** UUIDv5 of the earliest event id in the group, so it stays stable while the group grows.
- **Severity** `min(1, 0.15·events + 0.2·nodes + 0.3·maxConfidence/31)`; **confidence**
  `min(1, 0.3 + 0.25·(nodes - 1)) · (0.5 + 0.5·maxConfidence/31)`.
- `track` (moving sources as a road segment and time window) is reserved and `null` for now.

## Route trust

`POST /v1/trust/route` and watched routes (docs/api.md section 7) evaluate a LineString against the
grid. The route is sampled every 150 m; each sample takes the trust of its H3 res-8 cell, or with a
corridor of `buffer_m` the worst cell within `round(buffer_m / 920)` rings (cell centres are about
920 m apart). Consecutive samples with the same state merge into a segment; a segment's score is its
lowest, its confidence its lowest, its sources its highest, its data age that of its freshest
evidence. The overall result is the worst segment, ranked TRUSTED < UNKNOWN < DEGRADED < JAMMED <
SPOOFED: no evidence cannot vouch for a route, and spoofing outranks jamming because a receiver can
notice jamming but not spoofing. Code: `services/api/src/stozher_api/route_trust.py` (pure).

Watched-route alerts (`services/worker/route_alerts.py`): `route_affected` when the worst state first
reaches the watch's level (degraded < jammed < spoofed) or worsens while at it; `route_cleared` when it
falls back below; each transition once. UNKNOWN never alerts.

## Records without a position

A witness record whose latitude and longitude are both 0 has no GNSS fix. It is stored and shown as an event, but the worker
never localises it: it makes no hotspot, no trust cell and no alert with coordinates (`stozher_worker.points.has_fix`).
The count is in the recompute summary (`no_fix`).

## History is not trust

The live grid holds the last 90 minutes of evidence and nothing else: older evidence has under 12% weight and is dropped, so a quiet network
(a healthy node sends one heartbeat an hour and an event only on an anomaly) gives an empty grid. An empty grid is "no recent evidence", never "clear".

`GET /v1/trust/history` shows what was last seen, for responders and above, over 6 hours, 24 hours or 7 days. It is a different thing from the grid:
- each cell is scored at the moment of its **newest** evidence (weights relative to that moment), then labelled `expired` and given the age it has today;
- it uses the same evidence rules as the grid (types 0, 1, 2; no-fix records and classes 4 and 7 are left out) and the same 4-ring spread;
- evidence whose weight at that moment is under 0.05 (about 4 half-lives, 2 hours or more older than the newest) is ignored, so a cell is not turned
  DEGRADED by a stray old reading next to a fresh heartbeat; the newest evidence always counts (its weight is at least 0.25);
- clients must draw it as expired (grey, hatched, with the age), never with the trust colours, and must say it is not current;
- the public role does not get it, because a map of where witnesses reported is location history. The public sees only `newest_evidence_age_s` on the grid.

The dashboard shows it by default for responders and above (a legend checkbox turns it off), so a quiet hour or a lost heartbeat
does not blank the map: under the live grid, grey hatched with a dashed outline, each cell labelled with its last state and its
age now ("was TRUSTED · 47 min ago", from zoom 12), and a banner saying the grey cells are not the current state. A live cell
replaces the expired one at the same place, and ages move on every minute and with every new reading. Route trust is unchanged:
an expired cell is still UNKNOWN there.

## Reception is not trust

`GET /v1/nodes` gives each node a `reception` condition (`stozher_core.reception`), for operators: how well **that witness's own receiver**
hears satellites, from its records of the last 6 hours. It describes the node, not the place, and it never feeds the grid, hotspots or alerts;
jamming, spoofing and time events are ignored by it and stay the detector's business.
- `no_reception`: the newest record has no fix (the `no fix` flag, or a loss-of-reception event the node has not yet ended).
- `limited_sky`: a lost fix in the last 3 hours, or the median mean C/N0 of the newest 3 normal records with a fix under 23 dB-Hz, or (extended
  records only) under 6 satellites. Indoors, by a window, under cover and local noise all look like this: one node cannot tell which.
- `good`: none of these, with at least 3 normal records with a fix.
- `unknown`: nothing for 3 hours, or too few records. Never "good" by absence.

The 23 dB-Hz level comes from a4b0 (NEO-6M): four heartbeats under open sky on 7 Nov 2026 gave 23-28 dB-Hz (median 24), against 16-21 on
most days indoors next to a PC and by a window. One day (4 Nov, where it stood is not recorded) gave a median of 27, so the signal level alone does not always tell
indoors from outdoors; the lost-fix rule is the other half. Other receivers may need their own level.

## The bench is not the field

A witness next to a laptop or indoors reports true things about the desk: weak signals, lost fixes and the receiver's own
interference warnings from the laptop's and USB's noise (a4b0, 8-9 Nov 2026: jamming events, hourly no-fix jamming
heartbeats, 61 cells JAMMED and a hotspot reopening every hour). That is not evidence about the area. An operator puts such
a node on the bench (`PATCH /v1/nodes/{id}` `{"status": "bench"}`, docs/api.md); it is a human decision, like the SIM label,
never guessed by the platform.
- A record is bench when its observed time falls in one of the node's bench periods (`node_bench_periods`), so records made
  on the desk stay out after the node is back in the field, and store-and-forward records arriving late are judged by when
  they were made.
- Bench records are stored and shown (`bench: true` on events) and still count for the node's reception status and
  delivery, but are left out of the trust grid, hotspots, alerts and `/v1/trust/history`, normal records too: the desk vouches
  for nothing either way. One SQL test (`stozher_api.bench.on_bench`) serves every reader.
- A bench period ends when the node is set `active` (or `retired`) or at `bench_until` (default 24 h, at most 7 days), so a
  forgotten switch never silences a witness for long.
- The field kit does not yet sync bench periods (operator-change sync is not built): set the bench where the records are analysed.
