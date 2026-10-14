# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# STOZHER (СТОЖЕР)

> **Current state (Oct 2026), built and tested:**
> - `stozher_core`: packet codec and crypto with golden vectors, trust model, Ed25519 signed
>   acknowledgements (`docs/control.md`).
> - API: ingest (kineis, mesh, direct, sim), nodes, events, hotspots, trust grid and route trust,
>   alerts + SSE, watched routes, signed acks, sim reset, **API key management** (create, rotate with
>   grace, revoke, audit log) and per-key rate limits (`docs/api.md` sections 3-4).
> - Worker: job queue, hotspots, source localization, H3 trust grid, alert rules (hotspots, routes).
> - Network simulator (coastal scenario), mesh bridge (verified on two T-Beams over LoRa, including
>   signed acks back to the node), and the React dashboard (live map, alerts, route planner, watches).
>
> **Partly built:** community hazard reports (`docs/hazards.md`): the `reporter` role, `POST /v1/reports`, worker
> merging into hazards with tiers (independence by team label), operator confirm/close/dismiss, expiry and
> retention, public/reporter coarse views, `along-route`, watched-route hazard alerts. The simulator hazard scenario and the dashboard (report tool, hazard layer, route panel) are built. Still to do: EO/OSM hazard sources and the mesh report contract. Access phase A (organisations, named staff, two-person rule) is built.
>
> **Firmware (`docs/firmware.md`):** codec, Ed25519 ack verification, UBX reader and detection rules are built and
> tested on the host (`make fw-test`), plus the KIM1 driver, sequence numbers and retransmission against a scripted modem;
> ESP32-S3 and Leonardo builds compile. Not built: the mesh uplink. Nothing has run against a real KIM1 (waiting for the
> KIM1), nor against a real GNSS receiver.
>
> **Not built yet:** `situation`, webhooks, Kinéis adapter, assistant,
> offline field-kit sync, a dashboard UI for key management (use the admin API or
> `python -m stozher_api.cli create-key`).
>
> Run it: `make migrate`, `make api`, `make worker`, `make sim S=coastal`, `make bridge`, and
> `pnpm dev` in `web/dashboard` (see `docs/scenario.md`, `docs/mesh.md`). The dev database is the
> user's local PostgreSQL 16, not a container. Read the Technical Specification `.md` for detail
> beyond this file.

Guide for the Claude Code agent building STOZHER, a **Situational Awareness Platform
for Degraded Environments**, at the 12th CASSINI Hackathon (Challenge #1, 27–29
November 2026). The full technical specification lives in the team's "STOZHER —
Technical Specification" doc; `docs/` holds the excerpts the code depends on. When this
file and `docs/` disagree, `docs/` wins for contracts (packet, API), this file wins for
working rules.

## 1. What we are building

**STOZHER** is the platform: trusted, evidence-based situational awareness that keeps
working when networks fail. Its **first module, GNSS Trust**, is what we build and demo
this weekend.

Trusted **witness nodes** detect GNSS jamming and spoofing, sign every report, and send
it over a one-way satellite link (Kinéis KIM1), a local LoRa mesh (Meshtastic) or any
available network. A cloud backend and an offline-capable **field kit** turn the reports
into a live picture and a **GNSS trust API**: "Can I trust GNSS here, right now, and what
is the evidence?"

Keep platform code generic (witness records, transports, events, map layers, POIs,
sync) and GNSS-specific logic (detection rules, classifier, interference localization,
GNSS trust score) in clearly named modules, so later modules can reuse the platform.
In UI text, docs and code comments use civil terms: "situational awareness",
"evidence", "resilience". Never "reconnaissance", "targets" or "battle".

## 2. Non-negotiable rules

1. **Fresh code only.** All project code is written during the hackathon weekend.
   Use only public, openly licensed libraries and tools. Record every library, tool,
   model and dataset in `DECLARATIONS.md` when you add it.
2. **Simulate, never transmit.** Never write code that generates, transmits or drives
   a transmitter with jamming, spoofing or any GNSS-like signal. The simulator works on
   data only. No GNU Radio or SDR sink that can transmit.
3. **Civil framing.** No military features, targeting, weapon effects or tracking of
   individuals. Moving sources are reported as a road segment and time window.
4. **The core never depends on optional modules.** Core = expert node + KIM1 +
   backend + dashboard. Mesh, phones, field-kit sync, Copernicus, OSINT, assistant and
   voice must degrade gracefully when absent. Never block on an optional module.
5. **Alerts are deterministic.** The LLM assistant is never in the detection or alert
   path; it answers only through API tools and retrieval, with sources.
6. **Contracts are sacred.** Do not change the packet format, the golden test vectors
   or the public API shape without updating `docs/` and all affected modules in the
   same change.
7. **Secrets** only through environment variables (`infra/.env`, never committed).
   Never log keys, decrypted payloads at INFO level, or API keys.
8. **Label simulated data.** Anything from the simulator is stored with `kind = sim`
   and shown with a SIM badge. Never present simulated data as live.
9. **Hazard reports: places, not people.** (`docs/hazards.md`; reports, merging, tiers and operator decisions exist.) Closed
   taxonomy of physical road conditions only. Never add weapons, explosives, combat damage,
   checkpoints, or anything about people, vehicles or units; no photos or free text in reports; no
   reporter identity, device location or per-reporter queries or exports. Unverified is always shown as
   unverified; nothing is "clear" by default or by absence; only a human confirms, closes or dismisses.
   Demo data is synthetic or public only. Do not extend the taxonomy or add real conflict-zone data
   without updating `docs/hazards.md` and getting the team's decision (open decisions D1-D3 there).
10. **Access is restricted and accountable.** (`docs/access.md`.) No anonymous access and no self-registration;
    every credential belongs to an organisation, has a purpose and expires. Platform staff (admin, operator)
    act through **named accounts**; field teams and machines use keys labelled by organisation, team or
    system and purpose, never by a person. No shared staff credentials. Give every new endpoint an explicit
    role and update the generated access matrix (`uv run python -m stozher_api.access_matrix --write`); a
    test fails otherwise. Sensitive admin actions go through the two-person request queue (setup mode while fewer
    than two named admins exist). Open decisions D4-D10 are listed there.

## 3. Architecture in one screen

```
Witnesses (expert node, T-Beam GPS-only, Android phone, simulated nodes)
   │  same 28/32-byte signed witness record, hex-encoded
   ├─ Kinéis KIM1 satellite (one-way, minutes) ──► kineis_adapter ─┐
   ├─ LoRa mesh / Meshtastic (seconds, local) ──► mesh_bridge ─────┤
   ├─ USB / Wi-Fi / mobile (when available) ─────────────────────► │
   └─ simulator ──────────────────────────────────────────────────►│
                                                                    ▼
            api (FastAPI) ── PostgreSQL + PostGIS + pgvector ── worker (analytics)
                 │                                                   
     live stream, trust API, webhooks, mesh text alerts, sync         
                 │                                                   
     dashboard (React + MapLibre) · assistant (LLM tools) · field kit (same stack offline)
```

Pipeline in `worker`: verify + decode → enrich (node context, terrain, Doppler) →
classify (LightGBM, Isolation Forest) → cross-checks → hotspots (HDBSCAN) → source
localization (weighted centroid + ellipse, moving track) → H3 trust grid (res 8) →
route trust → alerts + playbooks → optional Copernicus change detection.

## 4. Repository layout

```
CLAUDE.md, DECLARATIONS.md
docs/                      packet.md, api.md, scenario.md, playbooks/
packages/stozher_core/    codec, crypto, schemas, trust model (imported by every service)
services/api/              FastAPI app + Alembic migrations
services/worker/           analytics jobs (Postgres job queue)
services/mesh_bridge/      Meshtastic Python library bridge (GPL-3: keep it isolated)
services/kineis_adapter/   Kinéis ingest
services/simulator/        attacker models, virtual nodes, scenario engine
services/assistant/        LLM tool-calling, retrieval, optional voice
web/dashboard/             React + Vite + TypeScript + MapLibre GL
firmware/node/             PlatformIO, Seeed XIAO ESP32-S3, Arduino framework
firmware/lotus_fallback/   reduced build for the kit's Seeeduino Lotus
tests/vectors/             golden packet vectors (JSON) shared by Python and C++
infra/                     compose.yml, compose.field.yml, .env.example
data/                      git-ignored datasets, tiles, cached Copernicus layers
```

## 5. Stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg,
  GeoAlchemy2, Alembic, PostgreSQL 16 + PostGIS + pgvector, h3, scikit-learn,
  LightGBM, shapely, pyproj, cryptography, httpx, sse-starlette, sentinelhub, openeo.
- **Job queue:** a `jobs` table with `SELECT … FOR UPDATE SKIP LOCKED`. No Redis.
- **Frontend:** React, Vite, TypeScript, MapLibre GL, h3-js; PMTiles for offline tiles.
  The dashboard is a static build (no SSR) so the field kit can serve it offline. The API
  base URL comes from the build-time variable `VITE_API_URL` (never hard-coded), so one
  build works against localhost, the field kit and the cloud; the API must allow CORS for
  the dashboard origin. Vercel is a possible later host for the cloud dashboard (static
  deploy, rewrite to `index.html`); it is optional and the core must not depend on it. A
  public deployment gets a `public`-role key only, because anything in the build is
  visible in the browser.
- **Firmware:** PlatformIO, Arduino framework on ESP32-S3, FreeRTOS tasks, mbedTLS
  (AES, HMAC), UBX parsing for u-blox M10.
- **LLM:** provider interface with two backends: cloud model when online, Ollama
  (open-weight 7–8B instruct + embedding model) offline. Same tool set for both.
- **Tooling:** uv for Python, ruff (lint + format), mypy (strict on `stozher_core`),
  pytest; pnpm, eslint, prettier for the web; `pio test` for firmware.

## 6. Contracts

### Witness record (`docs/packet.md`)

Big-endian. Core 28 bytes; extended 32 bytes only with a 7-digit Kinéis ID.

| Bytes | Field |
|---|---|
| 0 | version (4 bits, = 1) · type (4 bits: 0 heartbeat, 1 event, 2 summary, 3 test) |
| 1–2 | sequence number (uint16, persisted, never reused per key) |
| 3 | key ID (4 bits) · extended flag (1) · reserved (3) |
| 4 | event class (3 bits: 0 normal, 1 jamming, 2 spoofing, 3 benign, 4 tamper, 5 time anomaly, 7 unknown) · confidence (5 bits) |
| 5 | flags: OSNMA (2), stationary, rx jamming flag, rx spoofing flag, tamper, mesh present |
| 6–9 | latitude int32 ×1e-7 |
| 10–13 | longitude int32 ×1e-7 |
| 14–17 | timestamp uint32 Unix seconds |
| 18–19 | duration uint16 s |
| 20–23 | mean C/N0, C/N0 drop, C/N0 spread, jamming indicator/AGC (uint8 each) |
| 24–27 | extended only: satellites, position jump (log), clock jump (scaled), battery |
| last 4 | tag |

- Bytes 0–3 clear and authenticated; body encrypted with AES-128-CTR, counter block
  derived from node ID + sequence number (no nonce on the wire).
- Tag = HMAC-SHA256(k_mac, node_id ‖ header ‖ ciphertext) truncated to 4 bytes.
- Keys derived from a per-node master key with HKDF; selected by key ID.
- Node ID is resolved from the transport ID (Kinéis device ID, Meshtastic node number).
- Wire format: hex string. Mesh text: `STZ1:` + hex.
- **Golden vectors in `tests/vectors/` must pass in both Python and C++. Add a vector
  for every new edge case.**

### Event identity and sync

Events are immutable and unique on `(node_id, key_id, seq)`. Duplicates from multiple
transports are expected: return `duplicate`, keep the first, record the extra arrival.
Derived tables (hotspots, trust cells) are recomputed, never synced.

### API (`docs/api.md`)

Versioned under `/v1`, API key in `X-API-Key`, roles: admin, operator, responder,
public, system. GeoJSON for geometry. RFC 7807 errors. Every situational response
includes `sources`, `data_age_s` and `confidence` where derived. Ingest endpoints
return `202` with `accepted | duplicate | rejected` and a reason. Key groups: ingest
(`kineis`, `mesh`, `direct`, `sim`), nodes, events (+ evidence pack), situation,
hotspots (+ eo-changes, playbook), trust (point, grid, route), poi (+ access, nearest),
road-closures, stream/alerts (SSE), webhooks, sync, acks, assistant/query, sim, me, watched-routes, control (`public-key`; `acks` is system-only), admin
(`api-keys` create/list/patch/rotate/revoke, `audit-log`). Key management, rate limits
and webhook signing are specified in `docs/api.md` §3–6, watched routes in §7. Watched
routes store only caller-supplied geometry; STOZHER never receives live positions.

**The API is a product surface for other systems**, not only the dashboard's backend:
the dashboard, assistant and field kit are just clients of the same public `/v1` API
(no private endpoints). Third-party systems (navigation, logistics, emergency
management, other platforms) integrate through it, so: publish the OpenAPI schema and
docs from FastAPI, issue per-consumer API keys with a role and rate limit, push events
through webhooks and SSE (`stream/alerts`) in addition to polling, keep `/v1` backward
compatible (breaking changes go to `/v2`), and make responses self-describing
(`sources`, `data_age_s`, `confidence`). The `public` role exposes only coarse trust
data, never raw witness records or node locations.

### Trust score

`state` (TRUSTED, DEGRADED, JAMMED, SPOOFED, UNKNOWN), `score` 0–100, `confidence`
0–1, `data_age_s`, `independent_sources`. One definition in `stozher_core.trust`.

### Signed control traffic

Acknowledgements, mesh summaries and node commands are Ed25519-signed by the cloud.
Anything unsigned or invalid is ignored.

## 7. Coding conventions

- Small, focused modules; one responsibility per file. Prefer pure functions in
  `stozher_core` and the worker so they are testable without a database.
- Type everything. Pydantic models for every request, response and packet field.
- Async I/O in services; no blocking calls in request handlers.
- Every module exposes health (`/health` lists optional modules as present/absent).
- Optional integrations behind configuration flags; absent → log once, continue.
- Database changes only through Alembic migrations.
- Geometry in EPSG:4326; distances computed with pyproj geodesics, not degrees.
- Time in UTC everywhere; store both node `observed_at` and server `ingested_at`.
- Firmware: no dynamic allocation in loops, no blocking waits on optional peripherals
  (timeouts everywhere), configuration in NVS, sequence numbers persisted before use.
- Firmware: suspend anomaly detection while KIM1 `TX_STATUS` is high plus a guard
  interval, and after own mesh transmissions; mark those epochs `blanked`.
- Firmware: reconfigure the u-blox module over UBX at every boot (no flash on module).
- Commit early and often with clear messages; the commit history is evidence for the
  fresh-code rule.

## 8. Testing

- `stozher_core`: unit tests for codec, crypto, replay checks, trust scoring; golden
  vectors; property tests for encode/decode round trips.
- API: pytest with a PostGIS test database; ingest → event → hotspot → trust path.
- Worker: deterministic tests from simulator scenarios with known ground truth.
- Firmware: `pio test` for codec vectors and detection rules on recorded epochs.
- Before demo: one full scenario run with mesh, Bluetooth and assistant **disabled**
  to prove the core alone.

## 9. Common commands

Create these as Makefile targets early on Friday and keep them working:

```
make up            # docker compose -f infra/compose.yml up -d
make field         # adds infra/compose.field.yml (mesh bridge, ollama, assistant)
make migrate       # alembic upgrade head
make test          # python tests + golden vectors + web lint
make sim S=coastal # run the coastal crisis scenario against the local API
make fw            # pio run -d firmware/node
make fw-upload     # pio run -d firmware/node -t upload
```

## 10. Priorities (scope lock)

- **Must:** expert node detection + KIM1 uplink; codec/crypto with golden vectors;
  ingest, events, hotspots, trust grid; live dashboard; network simulator.
- **Should:** mesh path (2 T-Beams); offline mode + catch-up sync; coastal scenario
  with forged-record rejection; route trust; GPS-only cross-check; evaluation metrics.
- **Could:** POIs with verification; Copernicus change detection; playbooks + evidence
  pack; text assistant; Android witness app.
- **Won't this weekend:** voice, ADS-B/AIS fusion, own mesh firmware, secure boot,
  on-device OSNMA.

If a Must is not solid, do not start a Could. When unsure what to build next, pick the
highest unfinished item in this list.

## 11. Definition of done

A change is done when: tests pass (`make test`), golden vectors pass in both codecs if
the packet was touched, the dashboard still runs with all optional modules disabled,
`DECLARATIONS.md` lists any new library, tool, model or dataset, and the change is
committed.

## 12. Hardware facts the code relies on

- KIM1: `AT+TX=<band>,<freq>,<pwr>,<hex>`; reply `+TX_INFO: 1|0` means local transmit
  done or failed, **not** satellite reception. UART 4800 8N1, 3.3 V TTL, 5 V supply.
  Band/frequency values come from the organisers (placeholder in config).
- GNSS: Matek M10Q-5883 (u-blox SAM-M10Q, 9600 default) on the main node;
  HGLRC M100-5883 (u-blox M10, 115200 default) on the second node.
- T-Beams: LilyGO, 868 MHz, NEO-6M (GPS only, no Galileo), Meshtastic firmware,
  Bluetooth, battery. Region EU_868, private channel.
- Kit board: Seeeduino Lotus (ATmega328P, 2 KB RAM) is the fallback only.
- RTL-SDR (FC0012, up to about 950 MHz) is a debugging tool, not part of the product.
