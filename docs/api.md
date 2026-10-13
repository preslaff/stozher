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
| `responder` | Emergency and operations users | Public + events, situation, hotspots, playbooks, restricted POIs, access/nearest, alert stream, assistant |
| `operator` | Platform operators | Responder + nodes, evidence packs, EO changes, POI and closure writes, simulator, webhooks |
| `admin` | Platform owners | Operator + API key management, audit log |
| `system` | Machine components (adapters, simulator, field kit) | Ingest, sync, acks only |

Roles are not strictly hierarchical for `system`: it cannot read situational data and
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
| `allowed_origins` | Optional list for browser CORS |
| `created_at`, `created_by` | Audit |
| `expires_at` | Optional; expired keys are rejected |
| `revoked_at` | Set on revoke; takes effect immediately |
| `last_used_at` | Updated at most once per minute per key |

Schema changes go through an Alembic migration.

### Endpoints (role: admin)

| Method and path | Purpose |
|---|---|
| `POST /v1/admin/api-keys` | Create. Body: `label`, `role`, optional `rate_limit_per_min`, `expires_at`, `allowed_origins`, `owner_contact`. Response `201` includes the full `key` once. |
| `GET /v1/admin/api-keys` | List keys (never the secret): `id`, `prefix`, `label`, `role`, limits, `last_used_at`, `expires_at`, `revoked_at`. |
| `GET /v1/admin/api-keys/{id}` | One key, plus usage summary (requests last 24 h, last error). |
| `PATCH /v1/admin/api-keys/{id}` | Change `label`, `rate_limit_per_min`, `expires_at`, `allowed_origins`, `owner_contact`. Role changes require rotate-and-reissue, not patch. |
| `POST /v1/admin/api-keys/{id}/rotate` | Issue a new secret for the same key record. The old secret stays valid for `grace_s` (default 3600, max 86400) so consumers can switch. Response includes the new `key` once. |
| `DELETE /v1/admin/api-keys/{id}` | Revoke immediately (soft delete: the record stays for audit). |
| `GET /v1/admin/audit-log?actor&action&from&to` | Audit log, including all key operations. |

An admin cannot revoke or downgrade the last active admin key. `GET /v1/me` (any valid
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

Limits are per key. Defaults when `rate_limit_per_min` is null: `public` 60, `responder`
300, `operator` 600, `admin` 600, `system` 6000. Every response includes
`X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`. The `stream/alerts`
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
| Nodes | `POST /v1/nodes`, `GET /v1/nodes`, `GET /v1/nodes/{id}` | Register (surveyed position, kind, transport IDs); list; status and reliability | operator |
| Events | `GET /v1/events?bbox&from&to&class&min_confidence` | Filtered events | responder |
| Events | `GET /v1/events/{id}` | One event with checks, classifier output, agreeing sources | responder |
| Events | `GET /v1/events/{id}/evidence-pack` | Report for regulators and NIS2 (JSON; PDF optional) | operator |
| Situation | `GET /v1/situation?bbox` | Active hotspots, affected area, trust summary | responder |
| Hotspots | `GET /v1/hotspots`, `GET /v1/hotspots/{id}` | Hotspots with source ellipse, timeline, track | responder |
| Hotspots | `GET /v1/hotspots/{id}/eo-changes` | Copernicus areas of interest | operator |
| Hotspots | `GET /v1/hotspots/{id}/playbook?asset_type=` | Recommended actions and fallbacks | responder |
| Trust | `GET /v1/trust?lat&lon` | Trust score for a point | public |
| Trust | `GET /v1/trust/grid?bbox` | H3 (res 8) trust cells as GeoJSON | public |
| Trust | `POST /v1/trust/route` | LineString in, segments with trust scores out | public |
| POI | `GET /v1/poi?bbox&type&min_verification` | Points of interest; restricted ones need responder | public / responder |
| POI | `POST /v1/poi`, `PATCH /v1/poi/{id}` | Create and update points and status | operator |
| POI | `GET /v1/poi/{id}/access?from=lat,lon` | Route with per-segment trust and nearby damage | responder |
| POI | `GET /v1/poi/nearest?type&from&avoid=spoofed,jammed` | Nearest reachable point avoiding interference zones | responder |
| Roads | `POST /v1/road-closures`, `GET /v1/road-closures` | Closures used by routing | operator |
| Routes | `POST /v1/watched-routes`, `GET /v1/watched-routes`, `GET/PATCH/DELETE /v1/watched-routes/{id}` **(new)** | Watch a route: current trust per segment, alerts when it is affected (section 7) | responder |
| Live | `GET /v1/stream/alerts` | SSE: alerts, hotspot and trust changes, new events | responder |
| Webhooks | `POST/GET/DELETE /v1/webhooks` **(new)** | Register, list, remove push subscriptions (section 6) | operator |
| Sync | `GET /v1/sync/events?since=<cursor>` | Field kit catch-up | system |
| Sync | `POST /v1/sync/upload` | Field kit uploads local events and operator changes | system |
| Control | `GET /v1/acks?node_id` | Latest signed acknowledgement per node | system |
| Assistant | `POST /v1/assistant/query` | Question in, answer with tool calls and sources | responder |
| Simulator | `POST /v1/sim/scenarios`, `POST /v1/sim/scenarios/{id}/run`, `GET /v1/sim/runs/{id}` | Define, run, read ground truth and metrics | operator |
| Simulator | `POST /v1/sim/reset` | Delete all simulated nodes and their events, raw messages and keys; live data is never touched. Returns `{deleted_nodes, deleted_events}` | operator |
| Admin | `/v1/admin/api-keys…`, `GET /v1/admin/audit-log` | Key management (section 3) | admin |

### Situational list responses

`GET /v1/events` (filters `bbox`, `from`, `to`, `class`, `min_confidence`, `limit`) and
`GET /v1/hotspots` (filters `state`, `bbox`, `limit`) return
`{"items": [...], "count": n, "sources": [...], "data_age_s": s}`. Geometry is GeoJSON. A
hotspot has `centroid`, `source_ellipse` (Polygon), `ellipse` (`semi_major_m`, `semi_minor_m`,
`bearing_deg`), `severity`, `confidence`, `independent_sources`, `state` (`active|closed`) and
`sim`; `GET /v1/hotspots/{id}` adds the event `timeline`. `GET /v1/trust/grid?bbox=` returns a
GeoJSON FeatureCollection of H3 res-8 cells (at most 5000; larger boxes get `422
bbox-too-large`). `GET /health` reports `worker` as `present` when a job finished in the last
3 minutes.

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

- **SSE**: `GET /v1/stream/alerts` (responder and above). Supports `Last-Event-ID` to
  resume; events carry an increasing `id`.
- **Webhooks**: `POST /v1/webhooks` with `url` (https), `events` (list of `alert`,
  `hotspot`, `trust_change`, `route`), optional `bbox`, and `min_level`. The response returns a
  `secret` once. Deliveries are JSON `POST`s with headers `X-Stozher-Event`,
  `X-Stozher-Delivery` (unique id) and `X-Stozher-Signature` (HMAC-SHA256 of the raw body
  with the secret, hex). Consumers verify the signature and deduplicate by delivery id.
  Failed deliveries retry with exponential backoff for up to 24 h; the outcome per
  channel is recorded in `alerts.delivered`. Webhook targets must be https and must not
  resolve to private address ranges.

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
  "notify": {"sse": true, "webhook_ids": ["..."], "mesh": false},
  "expires_at": "2026-11-29T18:00:00Z",
  "asset_type": "road_vehicle"
}
```

| Field | Rules |
|---|---|
| `geometry` | GeoJSON LineString, EPSG:4326, at most 500 vertices |
| `buffer_m` | Corridor half-width, default 200, range 50–2000. Distances use geodesics |
| `min_level` | Lowest trust state that alerts: `degraded` (default), `jammed`, `spoofed` |
| `notify` | Channels. `sse` is on by default; `webhook_ids` must belong to the caller's key; `mesh` sends a short text alert via the mesh bridge when present, otherwise it is skipped and logged once |
| `expires_at` | Default now + 24 h, maximum now + 7 days. Expired watches are deleted, not archived |
| `asset_type` | Optional; selects playbook text for alerts |

`201` returns the watch `id` plus the same body as the status view below.

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

The stream accepts `GET /v1/stream/alerts?route_id=<id>` to receive only that route's
alerts. Mesh text alerts use the compact `STZ1:` convention and carry route name, state and
segment index only.

### Other operations

| Method and path | Purpose |
|---|---|
| `GET /v1/watched-routes` | Caller's watches (operator and above: all, `?owner=` filter) |
| `PATCH /v1/watched-routes/{id}` | Change `name`, `buffer_m`, `min_level`, `notify`, `expires_at`; changing `geometry` creates a new evaluation |
| `DELETE /v1/watched-routes/{id}` | Stop watching |

### Rules

- Watches belong to the API key that created them; other keys get `404`, not `403`.
- Limits: at most 20 active watches per key; the status endpoint counts against the
  normal rate limit.
- Privacy: the stored data is the geometry the caller sent, a name, and settings. No
  position, device or person identifier is stored, and the audit log records
  create/delete with the key prefix only. Do not use watches to follow moving individuals.
- Watched routes evaluated against simulated data carry `"sim": true`.

## 8. Open points

- Whether PDF evidence packs ship this weekend (spec: optional).
- Final rate-limit defaults; tune after a load run.
- Whether `public` keys should be optional (anonymous access to `trust` with an IP-based
  limit) or always required. Default here: always required.
- Which routing engine (if any) supplies `alternatives`: OSRM or Valhalla with an offline
  OSM extract for the field kit.
- Whether `mesh` alerts need a per-route throttle on top of the change-only rule.
