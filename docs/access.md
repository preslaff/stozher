# Access: who and what uses STOZHER

**Status: specification.** Sections 1-7 and 10 describe the intended model; section 9 says exactly what is
implemented today and what is not. Access to the platform is **restricted**: nobody gets in without a
credential that an administrator issued to an identified organisation for a stated purpose. This document
is the contract for that, next to `docs/api.md` (the endpoints) and `docs/hazards.md` (what reporters may
and may not provide).

Two decisions already taken by the project: **organisations are an open-ended set, including other
systems that use the API; and staff accounts are named** (every action by platform staff names a person).

## 1. Principles

1. **Restricted by default.** No self-registration, no anonymous access to any `/v1` endpoint. Even the
   coarse `public` role needs an issued key.
2. **Every credential has an owner and a purpose.** An organisation owns it, a purpose says why it exists,
   and it expires. A credential without those three does not get issued.
3. **Name the people who can change things; do not name the people who observe.** Staff who confirm
   hazards, issue keys or manage the platform act through **named accounts**. Field teams that report
   or read are identified **as an organisation and a purpose**, not as individuals, so a report can never
   expose the person who made it (`docs/hazards.md` sections 3 and 6).
4. **No shared staff credentials.** One person, one account. Shared team keys are fine for field teams and
   machines, and are labelled as such.
5. **Least privilege.** Each credential has the lowest role that does its job; each machine component has
   its own key, so one can be revoked alone.
6. **Separate identity from activity.** Activity records (reports, hazards, audit entries) carry a credential
   or account reference, never a name. The mapping from a team key to a person stays with the issuing
   organisation. Resolving it is a deliberate, logged step (section 8).
7. **Everything is auditable and reviewable.** Issuing, changing and revoking access is logged, and the
   set of active credentials can be listed and reviewed at any time.

## 2. Who and what uses it

| Principal | Examples | Identified as | Credential today | Intended credential |
|---|---|---|---|---|
| **Platform staff** | administrators; operators who confirm hazards, register nodes, manage keys | a named person in an organisation | an API key (possibly shared) | **named staff account** with MFA (section 5) |
| **Field responders** | rescue, civil protection, municipal, utility and NGO teams reading hazards, routes, alerts | organisation + team + purpose | team API key | team key (`responder`) |
| **Field reporters** | the same teams submitting hazard reports | organisation + team + purpose | team API key | team key (`reporter`), pseudonymous |
| **Journalists, police, other users** | same hazard and trust views as everyone else, no special features | organisation + purpose | team API key | team key at the lowest fitting role |
| **Other systems** | a logistics planner reading trust data, a navigation service, a national dashboard, a partner platform that pushes or pulls data | organisation + system name + purpose | API key | **integration key** (section 6) |
| **Our own components** | mesh bridge, simulator, field-kit sync, Kinéis adapter, the assistant | component name | `system` or other keys | **service key** per component |
| **Witness nodes** | T-Beams, expert node, phones | node ID + per-node cryptographic key | *not API principals*: they reach the API only through an adapter's `system` key; each record is authenticated by its own tag (`docs/packet.md`) | unchanged |
| **The worker** | analytics | internal | direct database access, not an API client | unchanged |

An organisation is any legal or institutional entity that is admitted: a rescue service, municipality,
police force, media organisation, NGO, public authority, research body, **or an operator of another
system**. The list of types is open (decision D4); what is fixed is that each one is registered, has a
contact, and has been onboarded (section 4).

## 3. Roles

Roles describe what a credential may do. Hierarchy: `public < reporter < responder < operator < admin`;
`system` is a separate lane (machine ingest only).

| Role | For | May |
|---|---|---|
| `public` | integrations that only need coarse trust data | trust at a point, trust grid, route trust, own key info |
| `reporter` | field teams that report | public + submit, withdraw and list **their own** hazard reports |
| `responder` | field and operations teams | reporter + hazards, events, hotspots, alerts and stream, own watched routes |
| `operator` | platform staff | responder + register nodes, confirm / close / dismiss hazards, see all watched routes, reset simulated data |
| `admin` | platform administrators | operator + issue, rotate and revoke credentials, read the audit log |
| `system` | machine components | ingest records, fetch acknowledgements; nothing else |

### 3.1 Access matrix (generated from the code)

Every `/v1` operation and who may call it. Generated by `uv run python -m stozher_api.access_matrix --write`
and checked by a test, so it cannot drift from the behaviour: adding or changing an endpoint without
updating this table fails the build.

<!-- access-matrix:start -->
| Operation | public | reporter | responder | operator | admin | system |
|---|---|---|---|---|---|---|
| `GET /health` (no key) | x | x | x | x | x | x |
| `GET /v1/acks` |  |  |  |  |  | x |
| `GET /v1/admin/api-keys` |  |  |  |  | x |  |
| `POST /v1/admin/api-keys` |  |  |  |  | x |  |
| `DELETE /v1/admin/api-keys/{key_id}` |  |  |  |  | x |  |
| `GET /v1/admin/api-keys/{key_id}` |  |  |  |  | x |  |
| `PATCH /v1/admin/api-keys/{key_id}` |  |  |  |  | x |  |
| `POST /v1/admin/api-keys/{key_id}/rotate` |  |  |  |  | x |  |
| `GET /v1/admin/audit-log` |  |  |  |  | x |  |
| `GET /v1/admin/inventory` |  |  |  |  | x |  |
| `GET /v1/admin/organisations` |  |  |  |  | x |  |
| `POST /v1/admin/organisations` |  |  |  |  | x |  |
| `GET /v1/admin/organisations/{org_id}` |  |  |  |  | x |  |
| `PATCH /v1/admin/organisations/{org_id}` |  |  |  |  | x |  |
| `POST /v1/admin/organisations/{org_id}/activate` |  |  |  |  | x |  |
| `POST /v1/admin/organisations/{org_id}/agreement` |  |  |  |  | x |  |
| `POST /v1/admin/organisations/{org_id}/offboard` |  |  |  |  | x |  |
| `POST /v1/admin/organisations/{org_id}/suspend` |  |  |  |  | x |  |
| `GET /v1/admin/requests` |  |  |  |  | x |  |
| `POST /v1/admin/requests/{request_id}/approve` |  |  |  |  | x |  |
| `POST /v1/admin/requests/{request_id}/cancel` |  |  |  |  | x |  |
| `POST /v1/admin/requests/{request_id}/reject` |  |  |  |  | x |  |
| `GET /v1/admin/users` |  |  |  |  | x |  |
| `POST /v1/admin/users` |  |  |  |  | x |  |
| `POST /v1/admin/users/{user_id}/disable` |  |  |  |  | x |  |
| `POST /v1/admin/users/{user_id}/role` |  |  |  |  | x |  |
| `GET /v1/alerts` |  |  | x | x | x |  |
| `GET /v1/control/public-key` (no key) | x | x | x | x | x | x |
| `GET /v1/events` |  |  | x | x | x |  |
| `GET /v1/events/{event_id}` |  |  | x | x | x |  |
| `GET /v1/hazards` | x | x | x | x | x |  |
| `POST /v1/hazards/along-route` |  |  | x | x | x |  |
| `GET /v1/hazards/{hazard_id}` | x | x | x | x | x |  |
| `POST /v1/hazards/{hazard_id}/close` |  |  |  | x | x |  |
| `POST /v1/hazards/{hazard_id}/confirm` |  |  |  | x | x |  |
| `POST /v1/hazards/{hazard_id}/dismiss` |  |  |  | x | x |  |
| `GET /v1/hotspots` |  |  | x | x | x |  |
| `GET /v1/hotspots/{hotspot_id}` |  |  | x | x | x |  |
| `POST /v1/ingest/direct` |  |  |  |  |  | x |
| `POST /v1/ingest/kineis` |  |  |  |  |  | x |
| `POST /v1/ingest/mesh` |  |  |  |  |  | x |
| `POST /v1/ingest/sim` |  |  |  |  |  | x |
| `GET /v1/me` | x | x | x | x | x | x |
| `GET /v1/nodes` |  |  |  | x | x |  |
| `POST /v1/nodes` |  |  |  | x | x |  |
| `GET /v1/nodes/{node_id}` |  |  |  | x | x |  |
| `GET /v1/reports` |  | x | x | x | x |  |
| `POST /v1/reports` |  | x | x | x | x |  |
| `POST /v1/reports/{report_id}/withdraw` |  | x | x | x | x |  |
| `POST /v1/sim/reset` |  |  |  | x | x |  |
| `GET /v1/stream/alerts` |  |  | x | x | x |  |
| `GET /v1/trust` | x | x | x | x | x |  |
| `GET /v1/trust/grid` | x | x | x | x | x |  |
| `POST /v1/trust/route` | x | x | x | x | x |  |
| `GET /v1/watched-routes` |  |  | x | x | x |  |
| `POST /v1/watched-routes` |  |  | x | x | x |  |
| `DELETE /v1/watched-routes/{watch_id}` |  |  | x | x | x |  |
| `GET /v1/watched-routes/{watch_id}` |  |  | x | x | x |  |
| `PATCH /v1/watched-routes/{watch_id}` |  |  | x | x | x |  |
<!-- access-matrix:end -->

Not in the matrix because they are not built yet: the `public` and `reporter` hazard views, the along-route
hazard check, offline sync, webhooks, the assistant (`docs/api.md`, `docs/hazards.md`).

## 4. Organisations

Every credential belongs to an **organisation record**:

| Field | Notes |
|---|---|
| name, type | type from an open list (rescue service, municipality, police, media, NGO, authority, research, system operator, other) |
| contact | a named role or person responsible for the organisation's credentials, with a work address; reachable for abuse and revocation |
| status | `proposed`, `active`, `suspended`, `offboarded` |
| onboarded on, by | the admin pair who approved it (two-person rule, section 7) |
| agreement | the terms of use accepted (version, date); see below |
| roles permitted | the highest role this organisation may be issued (an NGO may be capped at `responder`) |
| review date | when its access is next reviewed |

**Onboarding.** An organisation is proposed, checked (who they are, why they need access, what role is
enough), approved by two administrators, and given the agreement to accept. Only then may credentials be
issued. **Offboarding** revokes every credential at once and, for named accounts, disables the accounts.

**Terms of use** (to be written; the minimum content): permitted purposes; the data-handling rules of
`docs/hazards.md` (places not people, no attempt to identify reporters, no use for tracking individuals,
no weapons or combat content); no re-publication of restricted data; Copernicus and OpenStreetMap
attribution; rate limits; the right of the platform to suspend access; the contact and incident duties.

### 4.1 Other systems through the API

A system that consumes or feeds the API is an organisation of type `system operator` (or belongs to a
larger organisation) and gets an **integration key** that names the system. To be admitted it states:

- the purpose and the data it needs (this decides `public` or `responder`; never more);
- how it will call (rate, schedule, the `Origin` if from a browser, fixed source addresses if any);
- push endpoints for webhooks, when those exist (https, a signing secret, no private addresses);
- who is responsible for it, and a contact for incidents.

It receives only what its role allows: **never** node identities, reporter data or raw witness records
(`docs/api.md` section 2). Its usage is visible to admins (last used, request counts, rate-limited
requests), and a misbehaving integration can be suspended without touching anyone else.

## 5. Named staff accounts

**Decision: platform staff (admin and operator) act through named accounts.** The aim is that every
confirm, close, dismiss, key issuance, rotation, revocation and role change is attributable to a person.

- **Accounts.** One per person: a stable account id, display name, work email, organisation, role, status,
  who created it. Personal data is limited to that (section 11).
- **Sign-in.** Through an OpenID Connect identity provider with **multi-factor authentication**: either the
  organisation's own, or a self-hosted open-source one operated for the platform (decision D5). The
  dashboard uses the authorization-code flow with PKCE; the API accepts the resulting short-lived bearer
  token. Roles come from group membership, and an admin changes them, not the user.
- **Service and field credentials stay keys.** Named accounts replace keys for staff only; field teams and
  machines keep API keys (sections 6 and 1).
- **Dev and break-glass.** A sealed `admin` key exists for emergencies and recovery (section 10), stored
  offline, never used for routine work, and every use raises an alert.
- **Audit.** Entries record the actor type (`user`, `key`, `system`), the account id or key prefix, the
  action, the target, and the time. Display names are resolved only when an admin reads the log.

A pragmatic first step toward this, usable before the identity provider exists, is a **named staff key**:
an API key that must be bound to one named account record, so the audit trail already names a person.
The provider and MFA then replace the key for sign-in without changing the audit model (section 9, phase A).

## 6. Credential kinds

| Kind | For | Identity | Binding | Default life | Issued by |
|---|---|---|---|---|---|
| **Staff account** | admin, operator | a named person | MFA, short sessions | review every 90 days | two admins (section 7) |
| **Field key** | responder, reporter teams | organisation + team + purpose | `Origin` for browser use; rate limit | **90 days**, or the length of a deployment | an admin |
| **Service key** | our components | the component (`mesh-bridge-gateway-1`) | source address or client certificate where possible | 1 year, rotated every 90 days | an admin |
| **Integration key** | other systems | system + organisation | source address or client certificate, rate limit, `Origin` | 180 days | an admin |
| **Break-glass key** | recovery only | sealed | offline storage | until used or rotated yearly | two admins |

Naming: a key's `label` is **organisation, team or system, and purpose**, never a person ("Municipal rescue
team 3, field laptop"; "Logistics Co. route planner, production"). Several people of one team share a label
and may share a key; independent teams must have different labels, because corroboration of hazard
reports counts distinct labels (`docs/hazards.md` section 14).

## 7. Issuing, changing and revoking

| Action | Who | Rule |
|---|---|---|
| Register an organisation | two admins | both record approval |
| Create a field, service or integration key | one admin | for an `active` organisation, with a purpose, an expiry and a role within the organisation's cap |
| Create or change a **staff account**, or an `admin` or `operator` credential | **two admins** | four eyes |
| Confirm a hazard from an official feed (`closed_by_authority`) | an operator, with a second operator's approval | when that source exists |
| Rotate a key | an admin | the old secret works for a grace period (`docs/api.md` section 3) |
| Revoke a credential | any one admin | takes effect at once; suspending an organisation revokes all of its credentials |
| Review access | admins, quarterly | section 10 |

**Revocation and the offline field kit.** An API key is revoked instantly online. A field kit without a
network cannot learn that. Therefore field kits hold **short-lived credentials** renewed at each sync and a
signed **deny-list** delivered through sync; the expiry is the backstop. The design and the signing are a
separate specification when the field-kit sync is built.

## 8. Identity, accountability and privacy

- **What the platform holds about people:** staff accounts (name, work email, organisation, role) and the
  organisation contacts. **It holds no name, phone number or device identity for field reporters**; the
  team key and its label are the identity. Reports carry only the key prefix and are never shown with it.
- **Who knows who holds a field key:** the issuing organisation, outside the platform. The platform's admin
  sees the organisation, team label, purpose and contact, not the individual.
- **Investigating abuse.** If a key sends false or harmful reports: an admin **suspends** the key
  (immediate), notifies the organisation's contact, and the organisation determines who held it. The
  platform logs the suspension and the request. It never exports reporter data, and there is no endpoint
  that resolves a key to a person.
- **Journalists and police** are organisations like any other and use the same roles. Nothing in the
  platform lets one party query another party's activity, and no feature is added for either.
- **GDPR** applies to staff accounts and organisation contacts: a legal basis, a data-protection impact
  assessment before any real deployment, retention limits (accounts removed after offboarding, audit entries
  kept as policy requires), and access to the registry limited to admins. This document is not legal advice.

## 9. Implemented today, and the gaps

**Implemented (as of this document):**

| Capability | Where |
|---|---|
| API keys with a role, label, optional contact, expiry, revocation, rotation with a grace period, a per-key rate limit, optional allowed browser origins, last-used time | `docs/api.md` sections 3 and 4 |
| Roles `public`, `reporter`, `responder`, `operator`, `admin`, `system`, enforced on every endpoint | section 3 and the generated matrix |
| Keys created only by admins; a bootstrap admin from the environment | `docs/api.md` section 3 |
| A local command, `python -m stozher_api.cli create-key`, also creates keys. It needs direct database access, bypasses the API rules, and is for development and bootstrap only; it must be removed or restricted in a real deployment | `services/api/src/stozher_api/cli.py` |
| Audit log of key operations, node registration, route watches and hazard decisions, by key prefix | `GET /v1/admin/audit-log` |
| Reporters pseudonymous by design; no endpoint lists reports by reporter; schema-guard test | `docs/hazards.md`, `test_reports.py` |
| The access matrix generated from code and checked by a test | this document, `access_matrix.py` |
| **Phase A:** organisation registry (type, contact, role cap, agreement version, status), every key belongs to an active organisation, has a purpose, a kind and an expiry (default and maximum by kind) | `access_policy.py`, `access_service.py`, `routes/access.py`, migration 0009 |
| **Phase A:** named staff accounts; staff keys bound to an account; audit entries carry the account (`account` in the audit log, `account` in `/v1/me`) | same |
| **Phase A:** two-person rule: activating an organisation, creating or re-roling an account, issuing an admin, operator, staff or break-glass key become requests a different named admin approves (`/v1/admin/requests`); fewer than two named admins means *setup mode*, where one admin completes the action and the audit entry is flagged `setup_mode` | same |
| **Phase A:** access inventory, admin only (`GET /v1/admin/inventory`), legacy keys without organisation or purpose flagged | same |
| **Phase A:** suspending or offboarding an organisation revokes all its credentials and disables its accounts at once; disabling an account revokes its keys | same |

**Gaps, in order, with what I would do for the hackathon (phase A) and after:**

| # | Gap | Phase |
|---|---|---|
| 1 | ~~Organisation registry~~ done (phase A) | A |
| 2 | ~~Named staff accounts~~ done (phase A) | A |
| 3 | ~~Two-person rule~~ done (phase A) | A |
| 4 | ~~Access inventory report~~ done (phase A) | A |
| 5 | Agreement version per organisation done; the written terms are a draft in `docs/terms-of-use.md` that needs legal review | A (text) |
| 6 | **OIDC sign-in with MFA** for staff; the dashboard login instead of pasting a key | B |
| 7 | **Source-address or client-certificate binding** for service and integration keys | B |
| 8 | **Offline revocation** for field kits (short-lived credentials, signed deny-list) | B, with field-kit sync |
| 9 | Break-glass key procedure and alerting on its use; quarterly review as a routine | B |
| 10 | Alerts on anomalies (unused keys, bursts of rejected requests, admin actions out of hours) | B |

## 10. Review and monitoring

- **Quarterly access review** by two admins from the inventory report: every credential still needed, role
  still right, organisation still active, no credential past its purpose. Unused keys (no use in 60 days)
  are suspended unless renewed.
- **Revoke on trigger:** an organisation leaves or is suspended; a device is lost; abuse is suspected; a staff
  member changes role or leaves; a secret may have leaked.
- **Network.** TLS everywhere. Admin endpoints are reachable only from an administrative network or
  through the identity provider's session; service and integration keys from their declared addresses.
  Deployment (VPN, allow-lists, client certificates) is an infrastructure decision (phase B).

## 11. Open decisions (proposed defaults)

| # | Question | Proposed default | Decides |
|---|---|---|---|
| D4 | Which organisation types, and which highest role may each be issued? | the open list in section 4; police, media and NGOs capped at `responder` unless justified; only the platform's own staff are `operator` or `admin` | the team |
| D5 | Identity provider for staff: the organisation's own, or one operated for the platform? | self-hosted open-source OpenID Connect provider for the weekend demo, the organisation's own later | the team |
| D6 | Who are the administrators, and who is the second admin for the two-person rule? | two named people from the project team | the team |
| D7 | Default lifetimes (section 6) | as in the table | the team |
| D8 | Any access that is not keyed (anonymous)? | **none**, including the trust API | the team |
| D9 | Legal basis, data-protection owner and impact assessment for staff accounts and contacts | before any real deployment; a named data-protection contact | the team, with legal advice |
| D10 | Who runs the platform in a real deployment (the project, a civil-protection body, a trusted third party)? | out of scope for the hackathon; a prerequisite for real use | the organisations involved |

## 12. Acceptance for phase A

- A key cannot be created without an organisation, a purpose and an expiry (except break-glass), and the
  organisation must be `active` and permit the role.
- An organisation cannot be activated, nor an admin, operator or staff credential created, by a single admin.
- A staff action in the audit log resolves to a named account; a field key's action resolves to an
  organisation and a purpose, and never to a person.
- The inventory report lists every active credential with organisation, purpose, role, created, last used and
  expiry, and is readable by admins only.
- Suspending an organisation revokes all its credentials at once.
- The matrix test fails when an endpoint's role requirement changes without this document changing.
- No endpoint returns a reporter's key prefix, a team label to a non-admin, or resolves a key to a person.

## 13. References

`docs/api.md` (roles, key management, rate limits); `docs/hazards.md` (what reporters provide, section 6 on
identity); `CLAUDE.md` rules 3, 7 and 9; spec section 12 (security, privacy and "do no harm").
