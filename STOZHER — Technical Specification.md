# STOZHER — Technical Specification

Oct 13, 2026 · @Georgi

The single reference for building **STOZHER** (СТОЖЕР), a Situational Awareness Platform for Degraded Environments, at the 12th CASSINI Hackathon (27–29 November 2026, Challenge #1). Its first module, **GNSS Trust**, detects GNSS jamming and spoofing. This spec supersedes the earlier concept doc.

## 1. Overview and design principles

**Platform:** STOZHER, named after the central pole of a Bulgarian threshing floor that everything turns around: trusted, evidence-based situational awareness that keeps working when networks fail. **First module, GNSS Trust:** witnesses that detect GNSS jamming and spoofing and keep reporting when every network fails, so operators and responders always know where satellite navigation can be believed.

The core question every module serves: **"Can I trust GNSS here, right now, and what is the evidence?"**

### Challenge mapping

Registered under **Challenge #1**; the other challenges are served as secondary benefits only.

| Challenge | Scenario | How the platform covers it | Depth |
| --- | --- | --- | --- |
| #1 | Detect and locate Galileo spoofing and jamming in real time | Witness nodes, GNSS-independent checks, source localization | Core |
| #1 | Early-warning dashboards | Live map, trust layer, alert stream | Core |
| #1 | Copernicus imagery for physical damage | Change detection around hotspots | Supporting |
| #1 | Contingency drill and recovery plan | Crisis scenario simulator, response playbooks | Supporting |
| #2 | Galileo routing in disrupted transport networks | GNSS trust layer for routes | Supporting |
| #2 | Direct civilians to water, shelter, aid points | Points of interest with verification levels | Supporting |
| #3 | Change detection for suspicious activity; OSINT fusion | Investigation support in the source area; ADS-B and AIS context | Light |

### Design principles

1. **Witnesses, not receivers.** Nodes observe, sign and report. They never need to receive anything; the one-way satellite link is a feature.
2. **Satellite path is the backbone.** The core (node + KIM1 + cloud + dashboard) works alone. Mesh, phones, field kit, Copernicus, OSINT and the assistant are optional layers that depend only on the layers below them.
3. **Local-first, independent under heavy interference.** Every layer keeps working when the layer above disappears: the node without a network, the field kit without the cloud, the evidence without GNSS.
4. **Modular with fixed contracts.** Modules talk only through the packet format, the event schema and the API. Each module stands alone and fails alone.
5. **Evidence over alarms.** Every report is signed, timestamped and checked against references GNSS cannot fake; every output states its sources and data age.
6. **Civil framing.** Critical infrastructure, maritime safety, humanitarian logistics, regulators. No military functions, no targeting, no tracking of individuals.
7. **Simulate, never transmit.** Attacks are modelled in software; no jamming or spoofing signal is ever generated on air.

## 2. System architecture

&#91;embedded content: system architecture · witnesses, transports, cloud and field kit, clients\]

Witnesses send the same signed packet over whatever transport exists; the satellite reaches the cloud anywhere, the mesh feeds the field kit in seconds, and the two sync whenever a connection appears. A mesh node with internet also forwards to the cloud (section 6).

| Layer | Component | Role | Required for the core? |
| --- | --- | --- | --- |
| Witness | Expert node (ESP32 + u-blox M10 + KIM1) | Full detection, signs and reports by satellite | **Yes** |
| Witness | T-Beam (NEO-6M, Meshtastic) | GPS-only reference receiver, mesh radio | No |
| Witness | Android phone | Mobile expert witness (raw GNSS, dual frequency, sensors) | No |
| Witness | Simulated nodes | Scale for demo, evaluation and drills | No |
| Transport | Kinéis satellite | Global one-way uplink | **Yes** |
| Transport | LoRa mesh (Meshtastic) | Local real-time sharing, opportunistic gateways | No |
| Transport | USB, Wi-Fi, mobile | Fast path when available | No |
| Analysis | Cloud | Central analysis, Copernicus, OSINT, distribution | **Yes** |
| Analysis | Field kit | Same stack offline, syncs with the cloud | No |
| Client | Dashboard | Live map and timeline | **Yes** |
| Client | Trust API, phone terminal, assistant | Integrations, pocket alerts, natural-language queries | No |

## 3. Hardware

The provided kit is the Grove Beginner Kit (Seeeduino Lotus, ATmega328P) plus the Kinéis KIM1. All additional hardware must be listed in the submission.

### Bill of materials

| Item | Status | Role | Notes |
| --- | --- | --- | --- |
| Kinéis KIM1 + adapter + 401 MHz antenna | Provided | Satellite uplink | One-way. 5 V ±0.3 V supply, UART 3.3 V TTL at 4800 baud, TX power 250–1500 mW, up to 31/32 bytes per message, about 2 KB per day |
| Grove Beginner Kit (Lotus) | Provided | Fallback controller; sensors | Accelerometer, OLED, buzzer, LED, button, light, pressure, temperature/humidity modules detach and reconnect by Grove cable |
| Seeed XIAO ESP32-S3 + Grove expansion board | To buy | **Primary node controller** | 3.3 V logic (matches KIM1, no level shifter), native USB, 3 UARTs, hardware AES/SHA, Wi-Fi/BLE off during measurement |
| Matek M10Q-5883 (u-blox SAM-M10Q) | To buy | Main node GNSS | Galileo, EGNOS, UBX, built-in jamming/spoofing indicators; 9600 baud default; QMC5883L compass on I2C |
| HGLRC M100-5883 (u-blox M10) | To buy | Second expert witness | 115200 baud default; use with Leonardo `Serial1` or a second ESP32 |
| 2 × LilyGO T-Beam, 868 MHz, NEO-6M, battery | **Owned** | Mesh radios + GPS-only reference witnesses | Meshtastic firmware, Bluetooth, 18650 battery |
| Arduino Leonardo | **Owned** | Optional second node | ATmega32U4, `Serial1` free |
| RTL-SDR stick (FC0012 tuner) | **Owned** | Debugging: verify KIM1 (401 MHz) and mesh (868 MHz) bursts | Range up to about 950 MHz: no GNSS band, no ADS-B |
| Android phone with raw GNSS | Owned | Optional mobile witness | Check support with GnssLogger |
| 5 V supply with bulk capacitor | To buy | Powers KIM1 during transmission | Arduino/ESP32 5 V pin is not enough |
| Level shifter | To buy | Only if the Lotus fallback drives the KIM1 | 5 V to 3.3 V on the KIM1 RX line |

### Main node wiring (XIAO ESP32-S3)

| Function | Interface | Notes |
| --- | --- | --- |
| GNSS (Matek M10Q) | UART1 | Reconfigure at every boot (no flash on the module) |
| KIM1 | UART2, 4800 8N1 | 5 V power from the external supply, common ground |
| KIM1 TX\_STATUS (pin 11) | GPIO input | Blanking of GNSS anomaly detection during own transmissions |
| T-Beam A (Meshtastic Serial module) | UART0 | Optional; firmware detects presence at boot |
| Accelerometer, OLED, compass, light | I2C (Grove) | Kit modules via Grove cables |
| Buzzer, button | GPIO | Local alarm and demo trigger |

The XIAO has 11 usable GPIOs; the plan above uses 10. If more are needed, switch to an ESP32-S3 DevKit.

### Radio placement

Three transmitters share the node with a GNSS receiver: KIM1 (401 MHz, up to 1.5 W), LoRa (868 MHz) and the ESP32 radio (2.4 GHz). Keep the GNSS antenna as far as practical from the KIM1 and LoRa antennas, disable Wi-Fi/BLE on witness nodes during measurement, and blank detection during known transmissions (section 4).

## 4. Node firmware

Target: XIAO ESP32-S3, PlatformIO with the Arduino framework, FreeRTOS tasks. The Lotus fallback runs a reduced build (rules and satellite only, no encryption, MAC optional).

### Boot sequence

1. Load configuration from NVS: node ID, key ID and keys, surveyed position (fixed nodes), thresholds, transmit settings.
2. Configure the GNSS module over UBX: enable navigation (position, velocity, time), satellite signal summary, RF/jamming status and spoofing status messages; set rate and baud. The module has no flash, so this runs at every boot.
3. Probe optional modules with timeouts: mesh radio on UART0, I2C sensors, OLED. Record each as present or absent; never block on an absent module.
4. Start tasks; send a heartbeat packet.

### Tasks

| Task | Responsibility |
| --- | --- |
| `gnss` | Parse UBX into a per-epoch feature record |
| `sensors` | Accelerometer (stationary/moving, shock), light (enclosure opened), compass |
| `detect` | Apply the rules below, maintain baselines, raise events |
| `tx` | Priority queue, rate limiting, repeats, KIM1 `AT+TX`, `+TX_INFO` handling, TX\_STATUS blanking flag |
| `mesh` | Optional: send the same hex packet to the Meshtastic Serial module, non-blocking |
| `ui` | OLED state (NORMAL / JAMMING / SPOOFING / TAMPER), buzzer, LED, demo button |

### Detection rules (initial values, calibrated during the weekend)

| Rule | Condition | Event class |
| --- | --- | --- |
| J1 | Mean C/N0 drop versus the rolling baseline across most tracked satellites, or receiver jamming indicator raised | Jamming |
| S1 | Fixed node: GNSS position deviates from the surveyed position beyond a threshold for several seconds while the accelerometer says stationary | Spoofing |
| S2 | Unusually uniform C/N0 across many satellites | Spoofing (suspicion) |
| S3 | Receiver spoofing indicator raised | Spoofing |
| S4 | Clock or time jump beyond threshold | Time anomaly |
| S5 | Mobile node: position or velocity change physically implausible, or GNSS motion disagrees with the accelerometer | Spoofing |
| T1 | Light sensor shows enclosure opened, or shock/relocation on the accelerometer | Tamper |

Cross-receiver checks (GNSS vs Kinéis Doppler position, M10 vs T-Beam GPS-only, terrain context) run in the cloud, not on the node.

### Self-interference blanking

While the KIM1 `TX_STATUS` line is high, and for a short guard interval afterwards, anomaly detection is suspended and epochs are marked `blanked`. The same applies after the node's own mesh transmissions. Meshtastic periodic traffic is reduced in its settings because the node cannot see its timing.

### Transmission strategy

| Message | Policy |
| --- | --- |
| Confirmed event | Send immediately, repeat 2–3 times spread over time to hit different satellite passes |
| Status change | Send once; repeat at the next heartbeat |
| Heartbeat | Fixed interval (about hourly) |
| Budget | Rate limiter keeps the daily total within the Kinéis allowance; events take priority over heartbeats |

Transmit power 500–1000 mW for the demo. Band and frequency values for `AT+TX` come from the organisers.

### Store and forward

A ring buffer keeps the most recent events. Unacknowledged events are retransmitted by policy; an internet-connected mesh gateway can clear them with a cloud-signed acknowledgement (section 6). Sequence numbers are persisted in NVS so they never repeat after a restart.

### Demo mode (hardware-in-the-loop)

A build flag switches the GNSS input from the module to a serial replay stream from the laptop, so the real firmware processes scenario data. The kit button triggers a scenario step.

## 5. Packet format, encryption and keys

One binary packet, the **witness record**, travels unchanged over every transport. It is sent as a hex string (KIM1 `AT+TX` data field and Meshtastic text alike). Two sizes, both multiples of 4 bytes so the KIM1 adds no padding:

- **Core, 28 bytes:** fits either Kinéis ID length.
- **Extended, 32 bytes:** only with a 7-digit (28-bit) Kinéis ID.

Multi-byte integers are big-endian.

### Layout

| Bytes | Field | Encoding | Protection |
| --- | --- | --- | --- |
| 0 | Version and message type | 4 bits version (`1`) + 4 bits type: 0 heartbeat, 1 event, 2 summary, 3 test | Authenticated, clear |
| 1–2 | Sequence number | uint16, persisted, never reused under one key | Authenticated, clear |
| 3 | Key ID and flags | 4 bits key ID + 1 bit extended + 3 bits reserved | Authenticated, clear |
| 4 | Event class and confidence | 3 bits class: 0 normal, 1 jamming, 2 spoofing, 3 benign degradation, 4 tamper, 5 time anomaly, 7 unknown; 5 bits confidence (0–31) | Encrypted |
| 5 | Status flags | OSNMA (2 bits: n/a, ok, fail, unknown), stationary, receiver jamming flag, receiver spoofing flag, tamper, mesh present | Encrypted |
| 6–9 | Latitude | int32, 1e-7 degrees | Encrypted |
| 10–13 | Longitude | int32, 1e-7 degrees | Encrypted |
| 14–17 | Timestamp | uint32, Unix seconds (node clock) | Encrypted |
| 18–19 | Event duration | uint16, seconds | Encrypted |
| 20 | Mean C/N0 | uint8, dB-Hz | Encrypted |
| 21 | C/N0 drop | uint8, dB versus baseline | Encrypted |
| 22 | C/N0 spread | uint8, dB across satellites | Encrypted |
| 23 | Jamming indicator / AGC | uint8, receiver scale | Encrypted |
| 24–27 | *Extended only:* satellites in view, position jump (log-scaled), clock jump (scaled), battery | 4 × uint8 | Encrypted |
| last 4 | Authentication tag | Truncated HMAC | — |

Core: header (0–3) + body (4–23) + tag (24–27). Extended: header + body (4–27) + tag (28–31).

### Encryption and authentication

- **Encryption:** AES-128 in counter mode over the body. The counter block is built from node ID + sequence number, so no nonce bytes are transmitted and the body keeps its size.
- **Authentication:** HMAC-SHA256 over node ID + header + ciphertext, truncated to 4 bytes (encrypt-then-MAC). The node ID is not in the packet; the receiver resolves it from the transport identifier (Kinéis device ID, Meshtastic node number) and the tag proves it.
- **Replay protection:** sequence number must be new for the node and key; timestamp within an acceptance window.
- **Tag length:** 4 bytes is a deliberate prototype trade-off for the 31-byte budget; production uses a longer tag.

### Keys

- A unique master key per node, provisioned at installation; encryption and MAC keys derived from it (HKDF), identified by key ID.
- Rotate the key before the sequence number wraps.
- ESP32 stores keys with flash encryption and secure boot in production (roadmap); the Lotus cannot protect keys.
- The cloud signs acknowledgements and mesh summaries with its Ed25519 key; nodes and field kits verify with the cloud public key.

### Golden test vectors

The repository holds fixed test vectors (input fields, key, sequence number → expected hex). The C++ firmware codec and the Python codec must both pass them; this is the contract between the embedded and backend work.

## 6. Transports and opportunistic gateways

All transports carry the same witness record and enter the backend through an ingest endpoint that records how it arrived. Duplicates are removed by node ID + sequence number.

| Transport | Path into the backend | Latency | Direction |
| --- | --- | --- | --- |
| Kinéis satellite | KIM1 → satellite → Kinéis platform → `POST /v1/ingest/kineis` (webhook or poller, to confirm) | Minutes | Uplink only |
| LoRa mesh | Node → T-Beam (Serial module) → mesh → T-Beam at a gateway → mesh bridge → `POST /v1/ingest/mesh` | Seconds | Both, locally |
| USB / Wi-Fi / mobile | Node or phone → `POST /v1/ingest/direct` | Seconds | Both, when available |
| Simulator | Scenario engine → `POST /v1/ingest/sim` | Configurable | — |

### Mesh message

A Meshtastic text message on the private channel: a short prefix plus the witness record in hex, for example `STZ1:` followed by 56 or 64 hex characters. Meshtastic provides hop handling and the origin node number; our record provides integrity end to end, so relays cannot alter testimony.

### Opportunistic gateways (delay- and disruption-tolerant)

1. **Store and forward:** every node keeps a bounded buffer of its own and relayed records.
2. **Connectivity beacons:** nodes announce in the mesh whether they currently have internet, satellite or nothing.
3. **Exit selection:** internet first (fast, free), satellite second (budgeted, high-priority records only). A node that hears another node forward a record suppresses its own forward.
4. **Acknowledgements:** when the cloud receives records over the internet it returns a signed acknowledgement (per node: highest contiguous sequence number). The gateway broadcasts it in the mesh; nodes drop acknowledged records and satellite nodes skip them.
5. **Downlink summaries:** an internet gateway relays short, cloud-signed situation summaries into the mesh for field kits without internet.
6. **Signed control traffic:** acknowledgements, summaries and any command to nodes are Ed25519-signed by the cloud; unsigned or invalid control traffic is ignored, so a rogue node cannot make others delete evidence.

### Priorities

| Priority | Content | Allowed exits |
| --- | --- | --- |
| High | Confirmed jamming, spoofing, tamper | Any, immediately, including satellite |
| Medium | Status changes | Internet when available; satellite in batches |
| Low | Heartbeats and health | Internet; satellite summaries only |

### Human-readable alerts in the mesh

The field kit or gateway also broadcasts short text alerts on the channel (for example `ALERT GNSS SPOOFING · Port zone · 14:32 · conf 90% · Use radar`) so responders with a T-Beam and the stock Meshtastic phone app receive them over Bluetooth without a custom app. The stock app cannot show our signature check; that is stated in the pitch.

### Meshtastic configuration

Region EU\_868, private channel with its own key, changed Bluetooth PIN, position interval 30–60 s at full precision on the private channel, reduced telemetry and node-info intervals, Serial module enabled on T-Beam A, Bluetooth off on unattended witnesses.

## 7. Backend services and analytics

### Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async, asyncpg) + GeoAlchemy2 · Alembic · PostgreSQL 16 + PostGIS + pgvector · h3 · scikit-learn (HDBSCAN, Isolation Forest) and LightGBM · shapely, pyproj · cryptography · meshtastic · sentinelhub and openeo clients · httpx · sse-starlette. A job queue in PostgreSQL (`jobs` table with `FOR UPDATE SKIP LOCKED`) instead of Redis, so the field kit has one fewer dependency.

### Services

| Service | Responsibility | Depends on |
| --- | --- | --- |
| `api` | REST API, ingest endpoints, live stream, auth | Database |
| `worker` | Analytics jobs: decode, classify, cluster, localize, trust grid, Copernicus, playbooks | Database |
| `mesh-bridge` | Meshtastic Python library on a USB-connected T-Beam; posts mesh records to ingest; broadcasts alerts and signed control traffic | API |
| `kineis-adapter` | Receives or polls Kinéis messages, including Doppler position if offered | API |
| `simulator` | Virtual nodes, attackers and the crisis scenario timeline | API |
| `assistant` | LLM tool-calling over the API and pgvector retrieval; optional voice | API, LLM runtime |
| `web` | Dashboard (React, Vite, MapLibre GL) | API |

The shared Python package `stozher_core` holds the packet codec, crypto, schemas and trust model; every service imports it.

### Pipeline

1. **Ingest:** store the raw message with transport and arrival time; resolve the node from the transport ID.
2. **Verify and decode:** check tag, sequence and timestamp window; decrypt; reject with a reason (shown on the map as "signature invalid").
3. **Enrich:** node context (surveyed position, terrain sky mask from Copernicus DEM, land cover), Kinéis Doppler position, co-located receivers.
4. **Classify:** LightGBM over packet features plus context → jamming / spoofing / benign / normal with probability; Isolation Forest per node for unusual behaviour.
5. **Cross-checks:** GNSS vs Doppler distance (gross spoofing); expert node vs GPS-only T-Beam (GPS-only spoofing); phone dual-frequency disagreement.
6. **Hotspots:** HDBSCAN over anomalous events in position and time; a hotspot carries members, start, last seen and severity.
7. **Source localization:** weighted centroid of C/N0 drop across at least three nodes, uncertainty ellipse from the weighted covariance; a centroid moving over successive windows gives speed and heading for vehicle-borne sources.
8. **Trust grid:** H3 cells (resolution 8) get a state, score 0–100, confidence, data age and number of independent sources; scores decay with time.
9. **Route trust:** routes from OSRM or GraphHopper are split by cell and scored per segment.
10. **Alerts and playbooks:** state changes push to the live stream, webhooks and mesh text alerts; playbooks per asset type (port, telecom, energy, aviation, humanitarian logistics) give actions and fallbacks.
11. **Copernicus jobs (optional):** for persistent hotspots, Sentinel-1/2 change detection inside the source ellipse and around critical sites; results become areas of interest for human review.

### Trust score

| Field | Meaning |
| --- | --- |
| `state` | TRUSTED, DEGRADED, JAMMED, SPOOFED, UNKNOWN |
| `score` | 0–100 |
| `confidence` | 0–1 |
| `data_age_s` | Seconds since the newest contributing observation |
| `independent_sources` | Count of distinct witnesses and source types that agree |

### Training data

TEXBAT spoofing recordings, Google Smartphone Decimeter Challenge and own GnssLogger recordings as the normal baseline, Jammertest recordings if released, simulator output. Models are trained during the weekend.

## 8. Data model

PostgreSQL with PostGIS (geometry in EPSG:4326) and pgvector. **Events are immutable**: every record has a global identity (node ID + key ID + sequence number), which makes deduplication and field-kit sync conflict-free. Derived tables (hotspots, trust cells) are recomputed from events and never synced.

| Table | Key columns | Notes |
| --- | --- | --- |
| `nodes` | id, name, kind (expert, tbeam, phone, sim), mode (fixed, mobile), surveyed\_position, sky\_mask, reliability\_score, status, last\_seen | `kind = sim` rows are always labelled SIM in the UI |
| `node_keys` | node\_id, key\_id, master\_key (encrypted), valid\_from, valid\_to | Never returned by the API |
| `transport_ids` | node\_id, transport (kineis, mesh, direct), external\_id | Maps Kinéis device ID or Meshtastic node number to a node |
| `raw_messages` | id, transport, external\_id, payload\_hex, received\_at, transport\_meta (jsonb, e.g. Doppler position), verify\_status, reject\_reason | Append-only audit trail |
| `events` | id, node\_id, key\_id, seq, msg\_type, event\_class, confidence, flags, position, observed\_at, duration\_s, features (jsonb), classifier (jsonb), checks (jsonb), origin (cloud, local), received\_via, ingested\_at | Unique on (node\_id, key\_id, seq) |
| `hotspots` | id, kind, state, centroid, source\_ellipse, track (jsonb, for moving sources), severity, first\_seen, last\_seen | Derived |
| `hotspot_events` | hotspot\_id, event\_id | Derived |
| `trust_cells` | h3\_index, state, score, confidence, data\_age\_s, independent\_sources, updated\_at | Derived; H3 resolution 8 |
| `pois` | id, type, name, position, status, capacity, verification (verified, official, community, unverified), source, last\_verified, visibility (public, restricted) | Restricted POIs only for authorised roles |
| `poi_status_history` | poi\_id, status, capacity, changed\_at, source | Append-only |
| `road_closures` | id, geometry, reason, source, valid\_from, valid\_to | Used by route trust |
| `eo_findings` | id, hotspot\_id, kind (damage, change), geometry, sensor, scene\_ids, score, created\_at | Areas of interest for human review |
| `alerts` | id, level, kind, area, message, hotspot\_id, created\_at, delivered (jsonb per channel) | Feeds the stream, webhooks and mesh text |
| `acks` | node\_id, key\_id, max\_seq, signed\_at, signature | Cloud-signed acknowledgements |
| `playbooks` | asset\_type, version, body (jsonb) | Authored content |
| `kb_chunks` | id, source, title, text, embedding (vector) | Playbooks, regulations, incident summaries for retrieval |
| `sync_cursors` | peer, last\_event\_id, updated\_at | Field kit sync state |
| `scenarios`, `scenario_runs` | id, name, timeline (jsonb); run id, started\_at, speed, ground\_truth (jsonb), metrics (jsonb) | Simulator |
| `jobs` | id, kind, payload (jsonb), state, run\_after, attempts | Postgres job queue |
| `api_keys` | id, hash, role (admin, operator, responder, public), label | Role-based access |
| `audit_log` | id, actor, action, target, at | Who changed POIs, closures, nodes |

The embedding dimension of `kb_chunks` follows the chosen local embedding model and is fixed in the first migration.

## 9. API specification (v1)

FastAPI, JSON over HTTPS, interactive documentation at `/docs`. Authentication by API key (header `X-API-Key`), mapped to a role. Geographic responses use GeoJSON. Every situational response carries `sources`, `data_age_s` and, for derived values, `confidence`.

| Area | Method and path | Purpose | Role |
| --- | --- | --- | --- |
| Health | `GET /health` | Service and module status (which optional modules are present) | public |
| Ingest | `POST /v1/ingest/kineis` | Kinéis messages: hex payload + device ID + metadata (Doppler position if available) | system |
| Ingest | `POST /v1/ingest/mesh` | Mesh records: hex payload + Meshtastic origin node + hops | system |
| Ingest | `POST /v1/ingest/direct` | Records over USB, Wi-Fi or mobile | system |
| Ingest | `POST /v1/ingest/sim` | Simulator records | system |
| Nodes | `POST /v1/nodes`, `GET /v1/nodes`, `GET /v1/nodes/{id}` | Register (surveyed position, kind, transport IDs); list; status and reliability | operator |
| Events | `GET /v1/events?bbox&from&to&class&min_confidence` | Filtered events | responder |
| Events | `GET /v1/events/{id}` | One event with checks, classifier output, agreeing sources | responder |
| Events | `GET /v1/events/{id}/evidence-pack` | Report for regulators and NIS2 reporting (JSON; PDF optional) | operator |
| Situation | `GET /v1/situation?bbox` | Current picture: active hotspots, affected area, trust summary | responder |
| Hotspots | `GET /v1/hotspots`, `GET /v1/hotspots/{id}` | Hotspots with source ellipse, timeline, track | responder |
| Hotspots | `GET /v1/hotspots/{id}/eo-changes` | Copernicus areas of interest | operator |
| Hotspots | `GET /v1/hotspots/{id}/playbook?asset_type=` | Recommended actions and fallbacks | responder |
| Trust | `GET /v1/trust?lat&lon` | Trust score for a point | public |
| Trust | `GET /v1/trust/grid?bbox` | H3 trust cells as GeoJSON | public |
| Trust | `POST /v1/trust/route` | GeoJSON LineString in → segments with trust scores out | public |
| POI | `GET /v1/poi?bbox&type&min_verification` | Points of interest (restricted ones only for authorised roles) | public / responder |
| POI | `POST /v1/poi`, `PATCH /v1/poi/{id}` | Create and update points and status | operator |
| POI | `GET /v1/poi/{id}/access?from=lat,lon` | Route to the point with per-segment trust and nearby damage | responder |
| POI | `GET /v1/poi/nearest?type&from&avoid=spoofed,jammed` | Nearest reachable point avoiding interference zones | responder |
| Roads | `POST /v1/road-closures`, `GET /v1/road-closures` | Closures used by routing | operator |
| Live | `GET /v1/stream/alerts` | Server-sent events: alerts, hotspot and trust changes, new events | responder |
| Sync | `GET /v1/sync/events?since=<cursor>` | Field kit catch-up | system |
| Sync | `POST /v1/sync/upload` | Field kit uploads locally collected events and operator changes | system |
| Control | `GET /v1/acks?node_id` | Latest signed acknowledgement per node (for mesh gateways) | system |
| Assistant | `POST /v1/assistant/query` | Natural-language question → answer with tool calls and sources | responder |
| Simulator | `POST /v1/sim/scenarios`, `POST /v1/sim/scenarios/{id}/run`, `GET /v1/sim/runs/{id}` | Define, run (speed, pause), read ground truth and metrics | operator |

### Ingest response

`202 Accepted` with `{status: accepted | duplicate | rejected, reason?, event_id?}`. Rejections are stored in `raw_messages` and surfaced on the dashboard.

### Errors

RFC 7807 problem details (`type`, `title`, `status`, `detail`).

## 10. Field kit, sync, dashboard and assistant

### Field kit

One codebase, two deployments: the cloud and the field kit run the same Docker Compose services. The field kit adds offline assets and a local LLM.

| Field kit asset | Source |
| --- | --- |
| Offline map tiles for the region | PMTiles extract (OpenStreetMap) |
| Terrain and land cover | Copernicus DEM GLO-30, CORINE, Urban Atlas (Varna, Burgas) |
| Recent imagery | Pre-fetched Sentinel-1/2 scenes for the demo area |
| POIs | OpenStreetMap (Overpass) export for the region |
| Local LLM and embeddings | Ollama with an open-weight 7–8B instruct model and an embedding model |
| Offline voice (optional) | sherpa-onnx speech recognition, Piper speech synthesis, public models |

### Degradation levels

| Level | Situation | What works |
| --- | --- | --- |
| 0 | Fully connected | Everything |
| 1 | Internet intermittent | Field kit with local LLM and cached layers, sync when possible |
| 2 | No internet | Field kit with mesh and USB witnesses; satellite still informs the cloud |
| 3 | Node only | Local alarm and satellite uplink |

### Sync

1. Online: the field kit subscribes to `/v1/stream/alerts` and writes every event into its local database.
2. It stores the last cloud event ID as its cursor.
3. Reconnect: `GET /v1/sync/events?since=<cursor>` fetches the backlog; `POST /v1/sync/upload` sends local events and operator changes.
4. Derived data is recomputed locally. Every event shows `origin` and `received_via`.

The field kit disk is encrypted and the UI requires login.

### Dashboard

React + Vite + MapLibre GL, consuming only the public API and the live stream.

- Layers: nodes (state colour, LIVE · SATELLITE / LIVE · MESH / LIVE · LOCAL / SIM badge), H3 trust cells, hotspots with source ellipses and tracks, routes coloured by trust, POIs with verification badges and status, road closures, Copernicus areas of interest.
- Event timeline panel with time, source, transport and verification.
- Offline banner and catch-up indicator.
- Rejected messages listed with their reason (for example "signature invalid").
- Evaluation panel for simulator runs (detection time, localization error, false alarms).

### Assistant

- **Interface, not source of truth:** answers only through tool calls to the API and retrieval from `kb_chunks`; every answer lists its sources and data age.
- **Never in the alert path:** detection and alerts are deterministic code.
- **Two engines, one tool interface:** a cloud model when connected, the local Ollama model offline; the service switches automatically.
- **Fixed answer shape for critical questions:** state, area, data age, source, recommended action.
- **Human confirmation** before any action (for example broadcasting a warning).
- **Voice (optional):** push-to-talk, a small fixed command set for critical queries ("trust here", "trust route", "repeat", "details"), short standard spoken alerts, read-back before actions; free conversation for analysts.

## 11. Crisis scenario simulator and demo

### Simulator

Software only. It never drives a transmitter.

| Level | What it produces |
| --- | --- |
| Measurement | Receiver observables for each virtual node from attacker models. Jammer: position, power, type (tone, chirp, wideband noise), duty cycle. Spoofer: position push, drag-off, time push, meaconing, GPS-only vs multi-constellation. Calibrated against TEXBAT and Jammertest. |
| Device | Hardware-in-the-loop replay stream into the real node firmware (section 4) |
| Network | 20–30 virtual nodes (Varna port, coast, main road), moving attackers, civil events (outages, closures, POI changes, fake reports), all encoded with the real codec and keys and posted to `/v1/ingest/sim` |

The simulator keeps **ground truth** for every run, which yields detection rate, false-alarm rate, time to detect and localization error. Scenario files are timelines in JSON or YAML, written during the weekend.

### Coastal crisis scenario (6 hours, compressed to about 4 minutes)

| Scenario time | Event | On the map |
| --- | --- | --- |
| T+0:00 | Jamming starts near the port | Port nodes turn red, jamming cells appear, the source ellipse narrows |
| T+0:20 | Mobile internet down in the district | **Laptop Wi-Fi switched off for real**; offline banner; map continues from mesh and USB witnesses; simulated nodes fall back to satellite delays |
| T+0:45 | Bridge on the main road closed | Closure drawn; route recalculated |
| T+1:10 | Water point A closes, B opens | A grey, B green (official) |
| T+1:30 | Fake report of a new aid point X | X outlined, UNVERIFIED; a forged witness record is rejected with "signature invalid" |
| T+2:00 | Spoofing zone on the evacuation route (GPS-only spoofer) | Route segments red; GPS-only witnesses shifted, Galileo receivers unaffected; spoken alert |
| T+3:00 | Shelter reaches capacity | Shelter shows full; assistant proposes the nearest shelter reachable without crossing interference |
| End | Wi-Fi back on | Catch-up: events received by the cloud via satellite while offline appear in order; evaluation panel |

### Live versus simulated

| Source | Label |
| --- | --- |
| Expert node via KIM1 | LIVE · SATELLITE |
| Expert node via T-Beam mesh | LIVE · MESH |
| Second node or phone via USB/Wi-Fi | LIVE · LOCAL |
| Virtual nodes and civil events | SIM |

The satellite link cannot be compressed: trigger the real node shortly before the pitch so its message is in flight; its local alarm fires instantly; the satellite record appears with its true timestamps. Fallback: the message received during the morning rehearsal, labelled as such.

### Rehearsal and evidence

- At least two full runs on Sunday morning, one of them **with mesh and Bluetooth off** to prove the core alone.
- Record the full run as the submission video.
- Statement in the pitch: one node is live by satellite, one live locally, the rest simulated with the same format through the same pipeline.

## 12. Security, privacy and rules compliance

### Security

| Layer | Threat | Control |
| --- | --- | --- |
| Mesh radio | Eavesdropping | Meshtastic private channel key (AES) |
| Mesh radio | Forgery by anyone holding the channel key | End-to-end witness record tag (section 5) |
| Satellite | Payload visible to the operator | End-to-end encryption inside the record |
| Control traffic | Fake acknowledgements making nodes delete evidence | Cloud Ed25519 signatures, verified before use |
| Node | Key extraction, tampering | Per-node keys; tamper flags; production flash encryption and secure boot |
| API | Interception, unauthorised access | HTTPS, API keys with roles, rate limits, audit log |
| Field kit | Device loss | Disk encryption, login |
| Bluetooth | Unauthorised pairing | Non-default PIN; Bluetooth off on unattended witnesses |

Encryption protects content, not the existence of a transmission: radio emissions reveal that a node exists and roughly where.

### Privacy and "do no harm"

- No tracking of individuals; vehicle-borne sources are reported as a road segment and time window for authorities.
- Phone witnesses only with consent; keep events and areas, not continuous personal tracks.
- Shelters and distribution points are restricted POIs; optional location blurring on public views.
- Copernicus change detection produces areas of interest for human review, never automatic accusations.
- GDPR applies to phone data and operator accounts.

### Rules compliance

| Rule | How the project complies |
| --- | --- |
| All code created by the team during the hackathon weekend | No project code before Friday 27 November 18:00; repository created at the start with frequent commits; this spec, scenario texts, playbook texts and datasets are prepared in advance |
| Public, openly licensed libraries allowed | Only public libraries and tools; listed in `DECLARATIONS.md` and in the submission |
| Unique hardware listed | XIAO ESP32-S3, u-blox modules, T-Beams, Leonardo, RTL-SDR, phone listed in the submission |
| No content related to violence or firearms | Civil framing only; the simulator models crisis effects, not combat |
| Security-sensitive applications cannot be awarded (Section 8.8) | Public and synthetic data only; no real interference locations in active conflict zones; source estimates shown for synthetic or historical public cases |
| No jamming or spoofing on air | Simulation in software; the RTL-SDR is receive-only; no flowgraph or tool drives a transmitter |
| Team of 3–8, same location tag | Bulgaria location, all members registered there |
| Submission by Sunday 15:00 | Target 14:00 |

### Licensing for later

Meshtastic and its Python library are GPL-3. Keep the mesh bridge a separate component so the platform code is not affected, or replace the mesh stack in the product. Encryption products may fall under EU dual-use export rules outside the EU.

## 13. Repository, deployment, scope and build plan

### Repository layout

```
stozher/
  CLAUDE.md                  agent guide
  DECLARATIONS.md            libraries, tools, hardware, datasets (for the submission)
  docs/                      packet.md, api.md, scenario.md, playbooks/
  packages/stozher_core/     Python: codec, crypto, schemas, trust model
  services/api/              FastAPI app, Alembic migrations
  services/worker/           analytics jobs
  services/mesh_bridge/      Meshtastic bridge
  services/kineis_adapter/   Kinéis ingest adapter
  services/simulator/        attacker models, virtual nodes, scenarios
  services/assistant/        LLM tools, retrieval, optional voice
  web/dashboard/             React + Vite + MapLibre
  firmware/node/             PlatformIO, XIAO ESP32-S3
  firmware/lotus_fallback/   reduced build for the kit board
  tests/vectors/             golden packet vectors (shared by Python and C++)
  infra/                     compose.yml, compose.field.yml, .env.example
  data/                      git-ignored: datasets, tiles, cached layers
```

### Deployment

- `infra/compose.yml`: `db` (PostGIS + pgvector), `api`, `worker`, `web`, `simulator`, `kineis-adapter`.
- `infra/compose.field.yml` adds `mesh-bridge` (USB device passthrough), `ollama`, `assistant` and offline assets.
- The same images run in the cloud and on the field kit; configuration only through environment variables.
- Cloud deployed on Friday evening, not Sunday.

### Scope lock

| Priority | Items |
| --- | --- |
| **Must** | Expert node with detection and KIM1 uplink; codec and crypto with golden vectors; ingest, events, hotspots, trust grid; live dashboard; network simulator |
| **Should** | Mesh path with two T-Beams; offline mode and catch-up sync; coastal crisis scenario with forged-record rejection; route trust; GPS-only cross-check; evaluation metrics |
| **Could** | POIs with verification; Copernicus change detection; playbooks and evidence pack; text assistant; Android witness app |
| **Won't this weekend** | Voice; ADS-B and AIS fusion; own mesh firmware; secure boot; on-device OSNMA; mobile node checks beyond basics |

Rule: if a Must is not solid by Saturday evening, no Could item starts.

### Build plan

**Before Friday 18:00 (no project code):** register on TAIKAI; recruit; email organisers; order hardware; register for OSNMA keys and Copernicus Data Space accounts; download datasets and regional layers; configure T-Beams; write scenario and playbook texts.

**Friday evening**

1. Repository, compose stack and cloud deployment up.
2. `stozher_core` codec and crypto passing golden vectors in Python.
3. Firmware: GNSS parsing, sensors, OLED; codec in C++ passing the same vectors; first `AT+TX` packet sent.
4. Ingest endpoint and the first record visible on a bare map.

**Saturday**

1. Node detection rules and blanking; classifier trained; hotspots and localization.
2. Trust grid and route trust; mesh bridge; simulator with virtual nodes.
3. Offline mode and sync; scenario engine; evaluation panel.
4. Could items only if all Musts are solid.

**Sunday**

1. Two full rehearsals (one core-only); record the video.
2. Submission on TAIKAI by 14:00; pitch at 15:05.

## 14. Open questions

| # | Question | Ask | Affects |
| --- | --- | --- | --- |
| 1 | What is "ATT" in the Friday technical briefing? | hello@hackathons.cassini.eu | Possibly the whole hardware design |
| 2 | Is additional hardware allowed alongside the kit? | prizes@euspa.europa.eu | GNSS modules, ESP32, T-Beams |
| 3 | May hardware be tested before the weekend with unmodified official library examples? | prizes@euspa.europa.eu | Preparation |
| 4 | Does self-recorded GNSS and spectrum data count like downloaded datasets? | prizes@euspa.europa.eu | Baseline data |
| 5 | Would a civil GNSS interference monitor with public and synthetic data raise Section 8.8 concerns? | prizes@euspa.europa.eu | Framing |
| 6 | Is Meshtastic acceptable as an openly licensed framework? | prizes@euspa.europa.eu | Mesh layer |
| 7 | Kinéis device ID length (5 or 7 hex digits)? | Organisers / Kinéis | 28 vs 32-byte record |
| 8 | Band and frequency values for `AT+TX`? | Organisers / Kinéis | Firmware |
| 9 | How are received messages delivered (platform, API, webhook)? | Organisers / Kinéis | `kineis-adapter` |
| 10 | Is the Doppler position of each message available to teams? | Organisers / Kinéis | Cloud cross-check |
| 11 | Current constellation, passes over Bulgaria, latency, daily limit? | Organisers / Kinéis | Transmission strategy, demo timing |
| 12 | KIM1 adapter: connector, 3.3 V logic, 5 V supply, antenna included? | Organisers | ESP32 wiring |
| 13 | Venue in Bulgaria, on-site or hybrid, sky view for the demo? | Risk Space Transfer | Demo |
| 14 | Is SIGNALSTATE registered at the Bulgarian location; merge? | Team | Team, decision layer |

Deadline for clarification requests to EUSPA: six working days before the submission deadline (about 20 November).
