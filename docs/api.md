# STOZHER API v1

Contract for the public `/v1` API. When this file and the code disagree, fix the code or
change this file in the same change (CLAUDE.md rule 6). Derived from §9 of the Technical
Specification; sections marked **(new)** extend it.

The API is a product surface. The dashboard, assistant and field kit are clients of the
same endpoints as third-party systems; there are no private endpoints.

## 1. Conventions

- FastAPI, JSON over HTTPS. Interactive docs at `/docs`, schema at `/openapi.json`.
- Base path `/v1`. Breaking changes go to `/v2`; `/v1` stays backward compatible
  (adding optional fields and endpoints is not breaking).
- Authentication: `X-API-Key: <key>` header, mapped to a role (section 2).
- Geometry is GeoJSON in EPSG:4326 (`[lon, lat]`). Times are UTC, ISO 8601.
- Every situational response carries `sources` and `data_age_s`, and `confidence` where
  the value is derived.
- Ingest endpoints return `202` with `{status: accepted | duplicate | rejected, reason?,
  event_id?}`. Rejections are stored in `raw_messages`.
- Errors are RFC 7807 problem details: `type`, `title`, `status`, `detail`.
- Simulated data (`kind = sim`) is always flagged `"sim": true` in responses.

## 2. Roles

| Role | Intended for | Access |
|---|---|---|
| `public` | Third-party systems, anonymous consumers | Coarse trust data only: `trust`, `trust/grid`, `trust/route`, public POIs, `health` |
| `reporter` | Field teams issued a key to report road hazards (docs/hazards.md) | Public + `POST /v1/reports`, withdraw and list **their own** reports; nothing else. Cannot read events, hotspots, routes or any other reporter's reports |
| `responder` | Emergency and operations users | Public + events, situation, hotspots, playbooks, restricted POIs, access/nearest, alert stream, assistant |
| `operator` | Platform operators | Responder + nodes, evidence packs, EO changes, POI and closure writes, simulator, webhooks |
| `admin` | Platform owners | Operator + API key management, audit log |
| `system` | Machine components (adapters, simulator, field kit) | Ingest, sync, acks and heartbeats only |

Who may call each endpoint is in the generated access matrix of `docs/access.md` (checked by a test), together
with the access model: organisations, named staff accounts, credential kinds and the issuing policy.

The hierarchy is `public < reporter < responder < operator < admin`. Roles are not strictly hierarchical for `system`: it cannot read situational data and
other roles cannot call ingest or sync. The `public` role never sees raw witness records,
node IDs or node locations.

## 3. Key management **(new)**

Keys identify a consumer, carry a role and a rate limit, and can be rotated and revoked
without redeploying.

### Key format and storage

- Format: `stz_<env>_<prefix>_<secret>`, e.g. `stz_live_a1b2c3d4_<32 random bytes, base64url>`.
  The 8-character `prefix` is stored in clear and used for lookup and display.
- Only a hash of the full key is stored (SHA-256 is sufficient for high-entropy random
  keys). The full key is returned **once**, at creation or rotation, and never again.
- Keys are never logged. Logs and the audit log show `prefix` only.
- Bootstrap: the first admin key is created from the environment variable
  `STOZHER_BOOTSTRAP_ADMIN_KEY` (in `infra/.env`) on first start if no admin key exists.

### `api_keys` table (extends the spec)

| Column | Notes |
|---|---|
| `id` | UUID |
| `prefix` | Unique, clear text |
| `hash` | Hash of the full key |
| `role` | `admin`, `operator`, `responder`, `public`, `system` |
| `label` | Human name of the consumer, e.g. "Port authority – routing" |
| `owner_contact` | Optional contact for the consumer |
| `rate_limit_per_min` | Requests per minute; role default if null |
| `allowed_origins` | Optional list (at most 20) of browser origins. When set, a request that carries an `Origin` header not in the list is refused (`403 origin-not-allowed`); clients that send no `Origin` (servers, scripts) are unaffected. CORS preflights cannot carry the key, so this is a per-key check, not a replacement for the server-wide CORS list |
| `created_at`, `created_by` | Audit |
| `expires_at` | Optional; expired keys are rejected |
| `revoked_at` | Set on revoke; takes effect immediately |
| `last_used_at` | Updated at most once per minute per key |
| `prev_hash`, `prev_hash_expires_at` | The previous secret's hash while a rotation's grace period lasts; never returned |

Schema changes go through an Alembic migration.

### Endpoints (role: admin)

| Method and path | Purpose |
|---|---|
| `POST /v1/admin/api-keys` | Create. Body: `label`, `role`, optional `rate_limit_per_min`, `expires_at`, `allowed_origins`, `owner_contact`. Response `201` includes the full `key` once. |
| `GET /v1/admin/api-keys?role=&include_revoked=` | List keys (never the secret or hash): `id`, `prefix`, `label`, `role`, `rate_limit_per_min` and `effective_rate_limit`, `allowed_origins`, `last_used_at`, `expires_at`, `revoked_at`, `active`, `rotating_until`. Revoked keys are hidden unless asked for. |
| `GET /v1/admin/api-keys/{id}` | One key, plus `usage`: `requests` and `rate_limited` counted by the API process since it started (`since: "process start"`) and `last_used_at`. A durable per-request history is not recorded. |
| `PATCH /v1/admin/api-keys/{id}` | Change `label`, `rate_limit_per_min`, `expires_at`, `allowed_origins`, `owner_contact`; an explicit `null` clears an optional field (`rate_limit_per_min: null` returns to the role default). A `role` field is rejected (422): create a new key and revoke the old one. |
| `POST /v1/admin/api-keys/{id}/rotate` | Body `{"grace_s": 3600}` (0-86400). Issues a new secret under the **same prefix**; the old secret stays valid for `grace_s` so consumers can switch (0 = cut over at once). Response includes the new `key` once. A revoked key cannot be rotated (409). |
| `DELETE /v1/admin/api-keys/{id}` | Revoke immediately (soft delete: the record stays for audit; also ends any rotation grace). Returns the key's view with `revoked_at` (200); repeating it is harmless. |
| `GET /v1/admin/audit-log?actor&action&from&to` | Audit log, including all key operations. |

The last active admin key cannot be revoked (`409 last-admin`); roles cannot be changed in place, so there is no downgrade. `GET /v1/me` (any valid
key) returns the caller's own `prefix`, `label`, `role`, rate limit and expiry, so
consumers can self-check their access.

### Rules

- Every create, rotate, patch and revoke writes to `audit_log` (actor key prefix, action,
  target prefix, time). Secrets are never written.
- Principle of least privilege: third parties get `public` unless there is a reason.
  Browser-delivered builds (e.g. a Vercel-hosted dashboard) get a `public` key only.
- Keys with role `system` should be bound to the component that uses them; use one key
  per component so one can be revoked alone.

### Errors

| Status | `type` suffix | When |
|---|---|---|
| 401 | `missing-key` | No `X-API-Key` header |
| 401 | `invalid-key` | Unknown, expired or revoked key (same response for all three) |
| 403 | `insufficient-role` | Valid key, role not allowed for this endpoint |
| 429 | `rate-limited` | Over the key's limit; `Retry-After` header set |

## 4. Rate limits and response headers **(new)**

Limits are per key, in fixed one-minute windows. Defaults when `rate_limit_per_min` is null:
`public` 60, `reporter` 120, `responder` 300, `operator` 600, `admin` 600, `system` 6000. Every response to an
authenticated request includes `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
`X-RateLimit-Reset` (unix seconds when the window ends). Over the limit the answer is `429
rate-limited` with `Retry-After`; a rejected request still counts. `/health` and the public key
endpoint are not limited. The counters live in the API process: with several processes each counts
separately, so the effective limit is the configured one times the process count (a shared store
comes with multi-process deployments). The `stream/alerts`
connection counts as one request on open. Ingest from `system` keys must not be dropped
when the limit is hit for normal operation; size the `system` limit above the expected
traffic.

## 5. Endpoints

Role means the minimum role; `system` endpoints accept only `system` keys.

| Area | Method and path | Purpose | Role |
|---|---|---|---|
| Health | `GET /health` | Service status; optional modules listed as present/absent | public |
| Self | `GET /v1/me` | Caller's key info | any |
| Ingest | `POST /v1/ingest/kineis` | Kinéis messages: hex payload, device ID, metadata (Doppler position if any) | system |
| Ingest | `POST /v1/ingest/mesh` | Mesh records: hex payload, Meshtastic origin node, hops | system |
| Ingest | `POST /v1/ingest/direct` | Records over USB, Wi-Fi or mobile | system |
| Ingest | `POST /v1/ingest/sim` | Simulator records (stored with `kind = sim`) | system |
| System | `POST /v1/system/heartbeat` | An optional module (`mesh_bridge`, `kineis_adapter`, `assistant`) says it is alive, with small status facts (`radio_connected`). `/health` shows the module present while the beat is under 2 minutes old (the mesh bridge also needs a connected radio). The worker beats through the database | system |
| Nodes | `POST /v1/nodes`, `GET /v1/nodes`, `GET /v1/nodes/{id}` | Register (surveyed position, kind, transport IDs); list; status and reliability | operator |
| Nodes | `PATCH /v1/nodes/{id}` | Change `name`, `status` (`active`, `retired`: a retired node's records are rejected as `node_inactive`, its events stay), `mode`, and the surveyed position (`surveyed_lat` and `surveyed_lon` together; both `null` clears it). Kind, keys, IDs and transport IDs are not patchable (422; transport IDs through the re-bind route). Audited as `node.update` | operator |
| Nodes | `GET /v1/nodes/unrecognised-senders?transport=mesh` | Radio IDs that sent something we could not place (for example after a reflash), with the one node each proves to be, if any. No payloads | operator |
| Nodes | `POST /v1/nodes/{id}/transport-ids/rebind` | Attach a new transport ID to the node, only when a fresh packet from that ID proves, by its tag, that the sender holds the node's key; recovers the packets that proved it. Errors: `no-proof` (409), `id-in-use` (409), `sim-node` (409). Audited (`node.rebind_transport`) | operator |
| Events | `GET /v1/events?bbox&from&to&class&min_confidence` | Filtered events | responder |
| Events | `GET /v1/events/{id}` | One event with checks, classifier output, agreeing sources | responder |
| Events | `GET /v1/events/{id}/evidence-pack` | Report for regulators and NIS2 (JSON; PDF optional) | operator |
| Situation | `GET /v1/situation?bbox` | The current picture: `hotspots` (active count, by kind, the top 10 with severity, confidence, independent sources, centroid), `affected_area` (union of active hotspot source ellipses, GeoJSON, null if none), `trust` (evidence cells counted by state), plus `sources`, `data_age_s` (null with no evidence), `confidence` (mean of active hotspots, else of evidence cells) and `sim`. Derived data only: no node identities or records. No evidence is not an all-clear | responder |
| Hotspots | `GET /v1/hotspots`, `GET /v1/hotspots/{id}` | Hotspots with source ellipse, timeline, track | responder |
| Hotspots | `GET /v1/hotspots/{id}/eo-changes` | Copernicus areas of interest | operator |
| Hotspots | `GET /v1/hotspots/{id}/playbook?asset_type=` | Recommended actions and fallbacks | responder |
| Trust | `GET /v1/trust?lat&lon` | Trust score for a point | public |
| Trust | `GET /v1/trust/grid?bbox` | H3 (res 8) trust cells as GeoJSON | public |
| Trust | `POST /v1/trust/route?buffer_m=` | LineString in (2-500 positions, optional corridor 0-2000 m), segments with trust out: per-segment `state`, `score`, `confidence`, `data_age_s`, `independent_sources`, and the worst segment as `overall` (UNKNOWN ranks above TRUSTED, SPOOFED above JAMMED). Samples every 150 m. See docs/trust.md | public |
| POI | `GET /v1/poi?bbox&type&min_verification` | Points of interest; restricted ones need responder | public / responder |
| POI | `POST /v1/poi`, `PATCH /v1/poi/{id}` | Create and update points and status | operator |
| POI | `GET /v1/poi/{id}/access?from=lat,lon` | Route with per-segment trust and nearby damage | responder |
| POI | `GET /v1/poi/nearest?type&from&avoid=spoofed,jammed` | Nearest reachable point avoiding interference zones | responder |
| Roads | `POST /v1/road-closures`, `GET /v1/road-closures` | Closures used by routing | operator |
| Routes | `POST /v1/watched-routes`, `GET /v1/watched-routes`, `GET/PATCH/DELETE /v1/watched-routes/{id}` **(new)** | Watch a route: current trust per segment, alerts when it is affected (section 7) | responder |
| Live | `GET /v1/stream/alerts` | SSE: alerts, hotspot and trust changes, new events | responder |
| Live | `GET /v1/alerts?limit&after` **(new)** | Recent alerts, newest first (or after a cursor, oldest first) | responder |
| Webhooks | `POST/GET/DELETE /v1/webhooks` **(new)** | Register, list, remove push subscriptions (section 6) | operator |
| Sync | `GET /v1/sync/events?since=<cursor>&limit` **(built)** | Field kit catch-up: signed witness records after a cursor (section 8) | system |
| Sync | `POST /v1/sync/upload` **(built)** | Field kit uploads the records it holds, each verified again (section 8) | system |
| Control | `GET /v1/acks?node_id=` or `?transport=&external_id=` (`&key_id=`) | Latest cloud-signed acknowledgement per key id of one node, for gateways to relay (docs/control.md). `503 signing-not-configured` without a signing key | system |
| Control | `GET /v1/control/public-key` | The cloud's Ed25519 public key and key id, for provisioning nodes (open; nodes must be provisioned with it, not fetch it at runtime) | none |
| Assistant | `POST /v1/assistant/query` | Question in, answer with tool calls and sources | responder |
| Simulator | `POST /v1/sim/scenarios`, `POST /v1/sim/scenarios/{id}/run`, `GET /v1/sim/runs/{id}` | Define, run, read ground truth and metrics | operator |
| Simulator | `POST /v1/sim/reset` | Delete all simulated nodes and their events, raw messages and keys; live data is never touched. Returns `{deleted_nodes, deleted_events}` | operator |
| Admin | `/v1/admin/api-keys…`, `GET /v1/admin/audit-log` | Key management (section 3). `POST /api-keys` requires `kind`, `organisation_id`, `purpose` (and `user_id` for `staff`); admin, operator, staff and break-glass keys answer `202` with a pending request unless in setup mode | admin |
| Admin | `/v1/admin/organisations…` (`POST`, `GET`, `PATCH`, `/agreement`, `/activate`, `/suspend`, `/offboard`) | Organisation registry (`docs/access.md` section 4) | admin |
| Admin | `/v1/admin/users…` (`POST`, `GET`, `/role`, `/disable`) | Named staff accounts | admin |
| Admin | `/v1/admin/requests…` (`GET`, `/approve`, `/reject`, `/cancel`) | Two-person approvals; the secret of an approved key is returned once, to the approver | admin |
| Admin | `GET /v1/admin/inventory` | Every active credential with organisation, purpose, kind, account, last use, expiry | admin |

### Situational list responses

`GET /v1/events` (filters `bbox`, `from`, `to`, `class`, `min_confidence`, `limit`) and
`GET /v1/hotspots` (filters `state`, `bbox`, `limit`) return
`{"items": [...], "count": n, "sources": [...], "data_age_s": s}`. Geometry is GeoJSON. A
hotspot has `centroid`, `source_ellipse` (Polygon), `ellipse` (`semi_major_m`, `semi_minor_m`,
`bearing_deg`), `severity`, `confidence`, `independent_sources`, `state` (`active|closed`) and
`sim`; `GET /v1/hotspots/{id}` adds the event `timeline`. `GET /v1/trust/grid?bbox=` returns a
GeoJSON FeatureCollection of H3 res-8 cells (at most 5000; larger boxes get `422
bbox-too-large`). `GET /health` reports `ack_signing` as `present` when a signing key is configured, and `worker` as `present` when a job finished in the last
3 minutes.

### Hazards and reports

Specified in `docs/hazards.md`, which is the contract. **Implemented so far (build steps 1-2):** the
`reporter` role, `POST /v1/reports`, `POST /v1/reports/{id}/withdraw`, `GET /v1/reports?mine=true`,
`GET /v1/hazards` and `GET /v1/hazards/{id}` (responder and above, all tiers, exact geometry), and the
operator decisions `POST /v1/hazards/{id}/confirm|close|dismiss`.

`POST /v1/reports` (reporter and above) takes exactly `kind`, `passability`, `geometry` (a Point, or a
LineString of at most 50 positions and 500 m, `[lon, lat]` only) and `observed_at` (with a time zone,
within the last 48 hours and not in the future). Any other field (note, photo, device location,
contact, ...) is refused with 422; `closed_by_authority` is refused too (official sources only).
Coordinates are rounded to 5 decimals. It returns `202 {id, hazard_id, tier, status}`; until reports are
merged into hazards (the next build step) `hazard_id` is `null` and `tier` is `unverified`. Limits per key,
counted in the database and including withdrawn reports: 20 per hour and 120 per day (`429
report-rate-limited` with `Retry-After`). No response ever contains a reporter's key prefix, and
`GET /v1/reports` works only with `mine=true`: there is no way to list anyone else's reports. `GET /v1/hazards` takes `bbox`, `kind`, `min_tier`, `status` (`active` by default, or `closed`, `expired`,
`dismissed`, `all`) and `limit`; a hazard carries `tier` (`unverified`, `corroborated`, `confirmed`), `status`,
`passability`, `sources`, `evidence` (counts only), `first_seen`, `last_confirmed`, `valid_until`, `data_age_s`
and, in the detail view, a `timeline` of tier and status changes. Decisions take `{"reason": <code>}` from a
closed list (docs/hazards.md, implementation notes), are operator-only and audited, and answer `409
not-open` once a hazard is closed or dismissed. No hazard response contains a reporter prefix or team label.
Not yet implemented: the `public` and `reporter` views, `along-route`, hazard alerts.

### Trust response (shape)

```json
{
  "state": "JAMMED",
  "score": 12,
  "confidence": 0.82,
  "data_age_s": 94,
  "independent_sources": 3,
  "sources": ["node:expert", "node:mesh"],
  "sim": false,
  "location": {"type": "Point", "coordinates": [27.91, 43.20]}
}
```

`state` is one of `TRUSTED`, `DEGRADED`, `JAMMED`, `SPOOFED`, `UNKNOWN`. With no recent
evidence the response is `UNKNOWN` with `score: null`, `confidence: 0` and `data_age_s: null`.
The model and constants are in `docs/trust.md`. Definition lives in
`stozher_core.trust`. For the `public` role, `sources` lists transport classes only, not
node identities.

## 6. Pushing data to other systems **(new)**

Two ways, both deterministic and independent of the assistant:

- **SSE**: `GET /v1/stream/alerts` (responder and above). Event `hello` (server time, last alert
  id), `alert` (the alert JSON; `id` is the cursor, resume with `Last-Event-ID` or `?after=`) and
  `refresh` (analytics finished a run: reload hotspots, trust and events). Browsers' `EventSource`
  cannot send `X-API-Key`; use `fetch` streaming. Alerts are raised by deterministic rules in
  `services/worker/alerts.py`: `hotspot_new`, `hotspot_escalated` (severity crosses 0.7) and
  `hotspot_cleared`, each once per hotspot.
- **Webhooks**: `POST /v1/webhooks` with `url` (https), `events` (list of `alert`,
  `hotspot`, `trust_change`, `route`), optional `bbox`, and `min_level`. The response returns a
  `secret` once. Deliveries are JSON `POST`s with headers `X-Stozher-Event`,
  `X-Stozher-Delivery` (unique id) and `X-Stozher-Signature` (HMAC-SHA256 of the raw body
  with the secret, hex). Consumers verify the signature and deduplicate by delivery id.
  Failed deliveries retry with exponential backoff for up to 24 h; the outcome per
  channel is recorded in `alerts.delivered` (`webhook:<id>`: `pending`, `ok`, `failed`). Webhook targets must be https and must not
  resolve to private address ranges.

  Details of the implementation (`webhook_logic.py`, `webhook_service.py`, run by the worker every 10 s):
  - **Registration** (`POST /v1/webhooks`, operator, audited): `url`, `events` (`alert` = every alert; `hotspot` = `hotspot_*` alerts;
    `route` = route and route-hazard alerts; `trust_change` is reserved, nothing raises it yet), optional `bbox` (an alert is sent if its
    area touches the box), `min_level` (`info`, `warning`, `critical`), `include_sim` (default false: simulated alerts are not sent;
    when sent they carry `sim: true`). The `secret` (`whsec_...`) is shown once; only a sealed copy is stored. Limit 50 webhooks.
    `GET` lists them (never the secret) with delivery counts; `DELETE` removes one and its pending deliveries.
  - **Only new alerts:** a webhook gets alerts raised after it was registered, not a backlog.
  - **Delivery:** body `{"delivery_id", "event", "alert"}` (the alert as in `GET /v1/alerts`), 5 s timeout, no redirects. The signature is
    HMAC-SHA256 over the raw body with the secret string as the key. The delivery id is the same on every retry, so consumers deduplicate on it.
  - **Retries:** after a failure (a non-2xx answer, a timeout or a connection error) at +10 s, +30 s, +2 min, +10 min, +1 h, +4 h, +12 h;
    then the delivery is `failed`. About 17 hours in all, inside the 24 h limit.
  - **Address rules:** `https` only, no credentials, no `localhost` or `.local` names, and every resolved address must be globally
    routable (no loopback, private, link-local, multicast or reserved ranges). Checked at registration and again right before each
    delivery. Limit: between that check and the connection a name could still change; connecting by the checked address is not done yet.

Webhook payloads for `public`-visible changes (`trust_change`) contain the same coarse
fields as the trust endpoints.

## 7. Watched routes **(new)**

A watched route turns "is my route trustworthy?" into a standing question. The user (or a
consuming system) submits a route; STOZHER reports its trust per segment now and raises an
alert when a hotspot or trust change affects it. STOZHER does **not** navigate, and it
never receives or stores a live position: matching a vehicle to the route is done on the
client's device.

### Create

`POST /v1/watched-routes` (responder and above)

```json
{
  "name": "Port to ring road",
  "geometry": {"type": "LineString", "coordinates": [[27.91, 43.20], [27.95, 43.22]]},
  "buffer_m": 300,
  "min_level": "degraded",
  "expires_at": "2026-11-29T18:00:00Z",
  "asset_type": "road_vehicle"
}
```

| Field | Rules |
|---|---|
| `geometry` | GeoJSON LineString, EPSG:4326, 2 to 500 positions |
| `buffer_m` | Corridor half-width, 50-2000 (default 200). A corridor of k cells (`round(buffer_m / 920)`) means the worst cell within it counts |
| `hazard_min_tier` | Lowest road-hazard tier that alerts (`unverified` < `corroborated` default < `confirmed`); see `docs/hazards.md` section 8 |
| `min_level` | Lowest state that alerts: `degraded` (default) < `jammed` < `spoofed`; `UNKNOWN` never alerts |
| `notify` | Only `{"sse": true}` today (the alert stream). Webhook ids and mesh delivery are refused with 422: webhooks are owned by operators and receive route alerts by subscribing to the `route` event (section 6); mesh gateways relay route alerts by the global relay rules |
| `expires_at` | Needs a time zone; default now + 24 h, at most now + 7 days, must be in the future. Expired watches are deleted by the worker |
| `asset_type` | Optional, at most 32 characters; reserved for playbook text |

`201` returns the watch `id` plus the status view below. The state at creation is the baseline:
only changes after it raise alerts. At most 20 active watches per key (`409 limit-reached`).

### Status

`GET /v1/watched-routes/{id}` returns the current evaluation:

```json
{
  "id": "...",
  "overall": {"state": "DEGRADED", "score": 41, "confidence": 0.74,
              "data_age_s": 120, "independent_sources": 2},
  "segments": [
    {"index": 0, "geometry": {"type": "LineString", "coordinates": []},
     "state": "TRUSTED", "score": 91, "confidence": 0.8},
    {"index": 1, "geometry": {"type": "LineString", "coordinates": []},
     "state": "JAMMED", "score": 9, "confidence": 0.7, "hotspot_ids": ["..."]}
  ],
  "alternatives": [],
  "sources": ["node:expert", "node:mesh"],
  "sim": false,
  "evaluated_at": "2026-11-28T10:00:00Z"
}
```

Segments are the route cut at H3 (res 8) cell boundaries and merged where the state is
equal; the corridor is the buffered geometry. `overall` is the worst segment state, with
`score` and `confidence` computed by `stozher_core.trust`. `alternatives` lists up to 3
routes that avoid affected cells, each with its own per-segment trust; it is empty when no
routing engine is configured (optional module, degrades silently).

### Alerts

When the worker recomputes hotspots or trust cells and a watched corridor crosses an
affected cell, it creates an alert (`kind = route_affected`) through the normal alert
pipeline. Evaluation is deterministic, with no assistant in the path. An alert is sent when:
the worst state on the route first reaches `min_level`; the worst state gets worse; or
the route returns below `min_level` (`kind = route_cleared`). Repeated recomputes with no
change do not repeat alerts. Alert payload:

```json
{
  "kind": "route_affected",
  "route_id": "...",
  "level": "warning",
  "segments": [1],
  "state": "JAMMED",
  "hotspot_ids": ["..."],
  "message": "Route 'Port to ring road': segment 1 likely JAMMED (3 sources, data 2 min old).",
  "evidence": "/v1/hotspots/...",
  "sim": false
}
```

The stream accepts `GET /v1/stream/alerts?route_id=<id>` and the list `GET /v1/alerts?route_id=<id>`
to receive only that route's alerts; alerts carry `route_id`. Mesh text alerts use the compact `STZ1:` convention and carry route name, state and
segment index only.

### Other operations

| Method and path | Purpose |
|---|---|
| `GET /v1/watched-routes` | Caller's watches (operator and above: all, `?owner=` filter) |
| `PATCH /v1/watched-routes/{id}` | Change `name`, `buffer_m`, `min_level`, `hazard_min_tier`, `expires_at`. The geometry cannot change (422): create a new watch |
| `DELETE /v1/watched-routes/{id}` | Stop watching |

### Rules

- Watches belong to the API key that created them; other keys get `404`, not `403`.
- Limits: at most 20 active watches per key; the status endpoint counts against the
  normal rate limit.
- Privacy: the stored data is the geometry the caller sent, a name, and settings. No
  position, device or person identifier is stored, and the audit log records
  create/delete with the key prefix only. Do not use watches to follow moving individuals.
- Watched routes evaluated against simulated data carry `"sim": true`.

## 8. Field-kit sync **(new)**

A field kit and the cloud run the same API. Sync moves **witness records only**; hazards, hotspots and the trust grid are
derived and recomputed on each side (`docs/trust.md`). Both ends verify every record again under the node's key, so a peer
cannot invent evidence, only pass on what a node signed. Both endpoints are `system` role.

**Prerequisite:** the cloud and the kit know the same nodes: same node ID, same master keys (and so the same KEK-sealed key
rows). A record for a node the receiver does not know is rejected (`unknown_transport_id`) and not retried.

- `GET /v1/sync/events?since=<cursor>&limit=<1..200>` returns `{items, next_cursor, has_more}`. An item is
  `{cursor, node_id, payload_hex, received_via, ingested_at}`. The cursor is `events.sync_seq`, allocated by the database in
  insert order. Simulated events are never served. Events younger than 5 s are held back, because a row with a lower number
  may still be committing; the next call returns them.
- `POST /v1/sync/upload` takes `{records: [{node_id, payload_hex, received_via?}]}` (at most 200) and returns one result
  `{status, reason}` per record, in order (`accepted`, `duplicate`, `rejected`). Old records are accepted (the live replay
  window does not apply, since a kit may have been offline for days); a record dated in the future beyond that window, a bad
  tag and a simulated node are rejected. Stored with `origin = "synced"` and the transport it first arrived by.
- **The kit's loop** (worker job `sync`, every 60 s, only if `STOZHER_CLOUD_URL` and `STOZHER_CLOUD_KEY` are set): pull from the
  `download` cursor, ingest each record locally as `synced`, then push local events that are not `synced` from the `upload`
  cursor. Records that came from the cloud are not sent back. The cursors and the last error live in `sync_cursors`. An
  unreachable cloud is recorded and retried next round; nothing is skipped. A *rejected* record (for example an unknown node on
  the other side) is counted and not retried, so provision the node and reset the cursor if that was a mistake.
- Duplicates are harmless: the unique `(node_id, key_id, seq)` keeps the first.

**Not built:** syncing operator changes (nodes, closures) and hazard reports; short-lived kit credentials and the signed
deny-list (`docs/access.md`, row 8). Until then, issue the kit's cloud key with a short expiry.

## 9. Open points

- Whether PDF evidence packs ship this weekend (spec: optional).
- Final rate-limit defaults; tune after a load run.
- Whether `public` keys should be optional (anonymous access to `trust` with an IP-based
  limit) or always required. Default here: always required.
- Which routing engine (if any) supplies `alternatives`: OSRM or Valhalla with an offline
  OSM extract for the field kit.
- Whether `mesh` alerts need a per-route throttle on top of the change-only rule.
