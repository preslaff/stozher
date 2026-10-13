# Road hazards and community reports (specification)

**Status: specification only. Nothing here is implemented.** It extends the platform with a second
module beside GNSS Trust: physical hazards that block or endanger movement on roads, reported by
people in the field and by other sources, shown with their source and verification level, and checked
against routes. It follows the `docs/api.md` conventions; where an API shape here becomes real, copy it
into `docs/api.md` in the same change (CLAUDE.md rule 6).

Users: first responders after natural disasters and after armed conflicts, plus police and journalists
who work in the same areas. The design must stay safe for all of them (section 3).

## 1. Goals and non-goals

**Goals**

- Let trusted field teams report a physical obstruction quickly, offline if needed.
- Show every hazard with **source, verification tier and age**, never as bare truth.
- Check watched routes against hazards and alert deterministically, next to the GNSS trust alerts.
- Combine sources in one record: community reports, operators, official feeds, Earth-observation
  findings (e.g. the flood layer of `docs/spikes/eo-flood.md`) and OpenStreetMap closures.

**Non-goals**

- Not a navigator and not a guarantee of passability. "No hazards known" is never "clear".
- Not an incident or intelligence system. It does not record people, vehicles, units, checkpoints or
  positions of any armed actor (section 3).
- Not a way to identify or track reporters (section 6).

## 2. Scope and rule changes this introduces

This module touches three existing constraints. All three are kept, and made explicit:

| Source | Constraint | How this module complies |
|---|---|---|
| CLAUDE.md rule 3, spec section 1 | Civil framing; no military features, targeting, weapon effects or tracking of individuals | Closed taxonomy of physical road conditions only; no weapons or explosives type; nothing about people or units |
| Spec section 12 (contest rules) | No content related to violence or firearms; public and synthetic data only; no real data from active conflict zones | The demo uses synthetic and public data only; photos and free text are off |
| Contest rules, Section 8.8 | Security-sensitive applications cannot be awarded a prize | Positioning stays "disaster response and humanitarian access". **Open question for the organisers** (section 11) |

## 3. Safety principles (do no harm)

These bind every change to this module.

1. **Report places, not people.** A hazard has a place and a time. It never has a person, vehicle,
   unit, group, checkpoint, or any description of who or what is nearby.
2. **Closed taxonomy.** Only the hazard kinds in section 4. A new kind needs a change to this document.
3. **No free text, no photos in v1.** Structured fields only. Free text and images leak identities,
   faces, equipment and positions, and carry hidden metadata.
4. **Reporters are pseudonymous.** The platform stores a key prefix, never a name, number or
   device location. The team that issued the key knows who holds it (section 6).
5. **Nothing is "clear" by default.** Absence of reports, an expired report or a failed check never
   marks a road safe. Only an explicit, attributed clear can close a hazard, and it is shown as such.
6. **Unverified is shown as unverified.** Everywhere, including alerts and the assistant.
7. **Humans decide.** Confirming, closing or dismissing is an operator action. No model and no
   automatic rule confirms a hazard or declares a road clear.
8. **Least visibility.** Each role sees the minimum it needs (section 8). Responders' own watched routes
   and reports are never visible to other keys.

## 4. Hazard taxonomy

A closed list. `kind` is one of:

| Kind | Meaning | Default validity |
|---|---|---|
| `flood` | Water on or across the road | 24 h |
| `landslide` | Earth, mud or rock on or threatening the road | 72 h |
| `debris` | Fallen trees, rubble or other material blocking the carriageway | 12 h |
| `road_collapse` | Road surface, embankment or culvert failed | 7 days |
| `bridge_damage` | A bridge is damaged or unsafe to cross | 7 days |
| `fire_near_road` | Fire or heavy smoke at or beside the road | 12 h |
| `snow_ice` | Snow or ice making the road impassable or dangerous | 12 h |
| `closed_by_authority` | An authority has closed the road (official source only) | until set |

`passability` is one of `blocked`, `restricted` (one lane, light vehicles only, slow), `passable_with_care`,
or `reported_clear` (a report that the hazard has ended).

**Deliberately absent:** weapons, explosives, unexploded or suspected munitions, combat damage,
checkpoints, crowds, vehicles, people, "security situation", and any "other" or free-form kind.
*Proposed decision D1:* a neutral "suspected explosive hazard: do not enter" kind is a legitimate
humanitarian need, but it is weapon-related content. It stays **out** until the team and a legal review
decide otherwise; if added, it is confirmed by an official source only, has no description, and is never
shown to the `public` role (section 11).

## 5. Data model

### Reports (append-only)

`hazard_reports`: `id`, `reporter_prefix` (the key's public prefix), `kind`, `passability`,
`geometry` (Point, or LineString up to 500 m; EPSG:4326), `observed_at` (reporter's claim),
`received_at`, `sim` (bool), `status` (`accepted`, `withdrawn`), `hazard_id`.
No other reporter data is stored (section 6).

### Hazards (derived, merged)

`road_hazards`: `id`, `kind`, `passability` (the most severe among current sources), `geometry`
(the union envelope of its members, at most 1 km), `tier`, `sources` (list: `community`, `operator`,
`official`, `eo`, `osm`), `first_seen`, `last_confirmed`, `valid_until`, `status` (`active`, `closed`,
`expired`, `dismissed`), `sim`, `evidence` (counts and scene dates, e.g. an EO finding id; never text from reporters).

Reports merge into a hazard when they have the same `kind`, lie within 300 m, and fall within 6 h of
each other. Merging is deterministic and recomputed; a hazard keeps its id while members change.

### Relation to `road_closures`

`road_closures` (spec) becomes the **confirmed or official** subset of hazards, still used by routing.
The API keeps `/v1/road-closures`; it reads hazards of tier `confirmed` with kind `closed_by_authority`
or any confirmed `blocked` hazard.

## 6. Identity, issuance and accountability

- **Issuance, not sign-up.** Reporter keys are created by an admin (the key management of
  `docs/api.md` section 3) with a new role `reporter`, a label naming the *team or organisation* (never a
  person), a rate limit and an expiry. There is no open self-registration: in a crisis, field teams are
  given keys in advance, so accountability sits with the issuing organisation.
- **Pseudonymous to the platform.** Only the key prefix is stored with a report. The platform keeps no
  name, phone number, IP address (beyond rate limiting, not logged with reports), device ID or location history.
- **Journalists and police** use the same `reporter` or `responder` keys as anyone else, with no special
  access. In particular, no endpoint lists reports by reporter, resolves a prefix to a person, or exports
  reporter data. An admin sees only counts per key, by design.
- **Revocation.** An admin can suspend a key that sends false or abusive reports; its past reports are
  kept but their weight drops (section 7).

## 7. Verification tiers

| Tier | Meaning | How reached |
|---|---|---|
| `unverified` | One source, not checked | A single report, or a single EO finding |
| `corroborated` | Independently reported | Two sources that are independent: two different reporter keys (and different issuing teams, if labels differ), or one report plus one EO finding or OSM closure. Within 300 m and 6 h |
| `confirmed` | Checked by a human or an authority | An operator confirms, or an `official` feed asserts it |
| `closed` | Ended, attributed | An operator closes it, or **two independent clear reports** (`reported_clear`) corroborate each other |
| `expired` | Past `valid_until` with no support | Automatic. **Expired is not clear**: it is hidden from alerts and shown only in history |
| `dismissed` | Judged false | An operator dismisses it with a reason; counts against the reporters' weight |

- A reporter's own repeated reports count once. A report never corroborates itself.
- New corroborating reports extend `valid_until` by the kind's default.
- Reporter weight (for ordering and for operator attention, never for auto-confirmation) rises when a
  reporter's reports are confirmed and falls when they are dismissed.
- EO findings enter as `unverified` with source `eo`; they cannot confirm anything by themselves
  (`docs/spikes/eo-flood.md`: high precision, low recall, days of latency).

## 8. API (proposed)

All under `/v1`, RFC 7807 errors, `sources`, `data_age_s` and the tier on every hazard.

| Method and path | Purpose | Role |
|---|---|---|
| `POST /v1/reports` | Submit a report. Body: `kind`, `passability`, `geometry`, `observed_at`. `202` with `{id, hazard_id, tier}`. Rate limit: 20 per hour and 120 per day per key by default | reporter and above |
| `POST /v1/reports/{id}/withdraw` | Withdraw one's own report | the reporting key |
| `GET /v1/reports?mine=true` | One's own reports only | reporter and above |
| `GET /v1/hazards?bbox&kind&min_tier&status` | List hazards | see below |
| `GET /v1/hazards/{id}` | One hazard: tier, sources, timeline of tier changes (no reporter prefixes) | see below |
| `POST /v1/hazards/{id}/confirm` · `/close` · `/dismiss` | Human decisions, with a `reason` code. Audited | operator |
| `POST /v1/hazards/along-route` | LineString plus `buffer_m` in; hazards that intersect, with position along the route, tier and age | responder |
| `GET /v1/stream/alerts` | gains `hazard_new`, `hazard_tier`, `hazard_closed` events | responder |

**What each role sees**

| Role | Hazards visible | Geometry |
|---|---|---|
| `public` | `confirmed` only, and only `closed_by_authority`, `flood`, `landslide`, `snow_ice`, `fire_near_road` | blurred to a 100 m grid |
| `reporter` | `confirmed` hazards, plus their own reports at any tier | blurred like public, own reports exact |
| `responder`, `operator`, `admin` | all tiers | exact |

Routes: `POST /v1/trust/route` is unchanged. A route check has **two separate results**, GNSS trust
(`overall`) and hazards (`along-route`); they are never combined into one score. Watched routes
(`docs/api.md` section 7) gain `hazard_min_tier` (default `corroborated`; `unverified` is opt-in) and two
alert kinds, `route_hazard` (a hazard of at least that tier appears on the route, or its passability
worsens) and `route_hazard_cleared` (it closes). Alerts say the tier and age, e.g. "debris, corroborated,
2 reports, 40 min old, km 6.1-6.3", and never "road blocked" as bare fact.

## 9. Privacy and security

### Data minimisation

| Collected | Not collected |
|---|---|
| kind, passability, a map position the reporter chose, time | name, phone, email, device ID, IP in the report record |
| the issuing key's prefix | device GPS fix, track, or location history |
| hazard tier changes and who (operator prefix) decided | photos, audio, free text, contacts |

Reporters place the hazard on a map (or snap to a road); the client never uploads its own position.
Reports from phones queue in encrypted local storage when offline and are deleted after upload.

### Retention

Reports and hazards are deleted 30 days after a hazard is closed, expired or dismissed (configurable);
aggregate counts without locations may be kept. The audit log keeps operator decisions with the
hazard id and reason code only. GDPR applies to operator accounts and any phone data; run a data
protection impact assessment before any real deployment (spec section 12).

### Threats and controls

| Threat | Control |
|---|---|
| Forged or fake reports to divert responders | Issued keys only; per-key rate limits; independence rule for corroboration; unverified never auto-alerts by default; operator confirmation; expiry; dismissal lowers weight |
| Coordinated false reports from several keys | Corroboration requires different issuing teams where labels differ; operator review for any hazard that closes a main road; admin can suspend keys |
| Leaking a reporter's position or identity | No device location, no identity, no photos or free text; blurred public geometry; no per-reporter queries or exports |
| Device seized | Encrypted offline queue; the platform holds nothing identifying; keys can be revoked at once |
| Police or other users tracking people | Same views for everyone; no person-level data exists to query; audit log of admin and operator actions |
| Journalists' sources exposed | Nothing about sources is stored; the module has no contact or interview features |
| Information hazards (positions of armed actors, critical sites) | Closed taxonomy; no free text or photos; `closed_by_authority` only from official feeds; the assistant may not add detail |
| Adversary learning where responders go | Watched routes and a key's reports are visible only to that key (and operators/admins); never published |
| Insider misuse | Role separation; every confirm, close, dismiss and key action is audited |
| Mesh eavesdropping | Private channel key (spec section 12); compact reports carry no identity |

## 10. Offline, mesh and Earth observation

- **Phones and the field kit** queue reports offline and sync later; the field kit keeps its own local
  hazards and uploads them like witness events (`docs/api.md` sync). Hazards are derived, so they are
  recomputed, never synced as state; only reports are.
- **Mesh.** A compact report is a short text, `STZR1:` plus a signed, fixed-size binary report (kind,
  passability, position with about 10 m precision, time, sequence), authenticated like witness
  records. Its exact format is a separate contract and needs golden vectors before it exists. Until
  then, reports reach the API only over IP.
- **EO findings** become `unverified` hazards (`kind=flood` for the Sentinel-1 flood layer), always
  labelled "minimum extent" with the scene dates. They can corroborate a community report; they never
  confirm one.
- **The assistant** may summarise and translate hazards using only API tool results. It may not create
  or change a hazard, judge passability, or describe anything not in the record.

## 11. Open decisions (proposed defaults)

| # | Question | Proposed default | Decides |
|---|---|---|---|
| D1 | Include a neutral "suspected explosive hazard" kind? | **No**, excluded; revisit with a legal review and official-only confirmation | the team, after legal advice |
| D2 | Does post-conflict humanitarian use count as "security-sensitive" under contest Section 8.8? | Positioning stays disaster response and humanitarian access; **ask the organisers** | the organisers |
| D3 | Pseudonymous keys or named reporters? | **Pseudonymous**, accountability through key issuance by the issuing team | the team |

Also to settle: which official closure feed to read (not checked for Bulgaria), and who acts as the
operator who confirms hazards during the demo.

## 12. Hackathon scope and acceptance

Synthetic and public data only. The simulator gets a hazard scenario: genuine reports, a duplicate,
a single unverified report, two independent reports that corroborate, a forged report from a key with a
bad history, a coordinated fake from two keys of the same team, an operator confirmation, a clear
report, and an expiry.

Done when:

- A reporter key can submit reports and cannot read other reporters' reports, identities or any tier above its role.
- A single report is `unverified`, shown as such, and does not alert a watched route by default.
- Two independent reports make `corroborated`; the same key twice does not; two keys of one team do not.
- Only an operator confirms or dismisses; expiry hides a hazard from alerts and does not mark it clear.
- A route check returns GNSS trust and hazards as separate results, and a watched route alerts once per transition.
- The public view shows only confirmed hazards, blurred; no response contains a reporter prefix.
- No photo, free text, device location, weapon or person data can be submitted (rejected with 422).
- The dashboard has a "report obstacle" tool, a hazard layer coloured by tier, and tiers in the route panel.

## 13. Build order

1. Migration, models and the report endpoint with validation, rate limits and the `reporter` role.
2. Merging and tiers in the worker; operator decisions; expiry.
3. Hazards read API with role views; `along-route`; watched-route alerts.
4. Simulator scenario and tests for every acceptance line above.
5. Dashboard: report tool, hazard layer, route panel.
6. EO findings and OSM closures as sources.
7. Mesh report contract with golden vectors.

## 14. References

Spec sections 1 and 12; CLAUDE.md rule 3; contest Rules of Contest Section 8.8; `docs/spikes/eo-flood.md`;
`docs/api.md` (key management, watched routes). For responsible-data practice, worth reading before any
real deployment (current versions not checked): the OCHA Data Responsibility Guidelines and the ICRC
Handbook on Data Protection in Humanitarian Action.
