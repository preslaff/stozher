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
| Live | `GET /v1/stream/alerts` | SSE: alerts, hotspot and trust changes, new events | responder |
| Webhooks | `POST/GET/DELETE /v1/webhooks` **(new)** | Register, list, remove push subscriptions (section 6) | operator |
| Sync | `GET /v1/sync/events?since=<cursor>` | Field kit catch-up | system |
| Sync | `POST /v1/sync/upload` | Field kit uploads local events and operator changes | system |
| Control | `GET /v1/acks?node_id` | Latest signed acknowledgement per node | system |
| Assistant | `POST /v1/assistant/query` | Question in, answer with tool calls and sources | responder |
| Simulator | `POST /v1/sim/scenarios`, `POST /v1/sim/scenarios/{id}/run`, `GET /v1/sim/runs/{id}` | Define, run, read ground truth and metrics | operator |
| Admin | `/v1/admin/api-keys…`, `GET /v1/admin/audit-log` | Key management (section 3) | admin |

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

`state` is one of `TRUSTED`, `DEGRADED`, `JAMMED`, `SPOOFED`, `UNKNOWN`. Definition lives in
`stozher_core.trust`. For the `public` role, `sources` lists transport classes only, not
node identities.

## 6. Pushing data to other systems **(new)**

Two ways, both deterministic and independent of the assistant:

- **SSE**: `GET /v1/stream/alerts` (responder and above). Supports `Last-Event-ID` to
  resume; events carry an increasing `id`.
- **Webhooks**: `POST /v1/webhooks` with `url` (https), `events` (list of `alert`,
  `hotspot`, `trust_change`), optional `bbox`, and `min_level`. The response returns a
  `secret` once. Deliveries are JSON `POST`s with headers `X-Stozher-Event`,
  `X-Stozher-Delivery` (unique id) and `X-Stozher-Signature` (HMAC-SHA256 of the raw body
  with the secret, hex). Consumers verify the signature and deduplicate by delivery id.
  Failed deliveries retry with exponential backoff for up to 24 h; the outcome per
  channel is recorded in `alerts.delivered`. Webhook targets must be https and must not
  resolve to private address ranges.

Webhook payloads for `public`-visible changes (`trust_change`) contain the same coarse
fields as the trust endpoints.

## 7. Open points

- Whether PDF evidence packs ship this weekend (spec: optional).
- Final rate-limit defaults; tune after a load run.
- Whether `public` keys should be optional (anonymous access to `trust` with an IP-based
  limit) or always required. Default here: always required.
