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

Each event adds its weight to every cell within 4 rings of its own cell (about 4 km).
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
strong group within 7.5 km and a 1 h window but never links two groups, and weak reports with no
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
