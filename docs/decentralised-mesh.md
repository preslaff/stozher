# Decentralised operation: design note (proposal)

> **Status: proposal for team review.** Only step 0 is built; steps 1 to 5 are proposals. The note changes no contract; where a
> step would change one (`docs/control.md`, `docs/api.md`), the step says so and needs a team decision first.

STOZHER should keep working when parts of it are missing: a receiver switched off, a relay node gone, the cloud unreachable,
two kits cut off from each other for a day. This note records what already tolerates that, where the system is still
centralised, and the steps to remove each single point of failure, in order of cost.

It was prompted by a real failure (19 to 20 October 2026). The only receiving machine, with the only gateway radio on it,
suspended overnight. After it woke, the bridge still reported its radio as connected, but the serial link was dead, so for
another nine hours nothing arrived either. When the bridge was restarted, the gateway handed over the 8 messages it was still
holding; the 43 records before them were lost. The gateway kept only those last 8 for its disconnected client, and the node
does not resend on the mesh path.

## What already tolerates missing parts (built)

- **Records merge without conflicts.** A witness record is immutable and unique on `(node_id, key_id, seq)`
  (`services/api/.../models.py`). A second copy is answered `duplicate` and kept once; every arrival, duplicates included,
  is audited in `raw_messages`. The same sequence number with different content is rejected (`seq_reuse`). Records therefore form
  a grow-only set: any number of receivers can hear the same record, in any order, and end up with the same data.
- **Derived data is recomputed, never synced.** Hotspots, the trust grid and alerts are rebuilt from the records
  (`docs/trust.md`). Two databases holding the same records show the same picture.
- **The radio mesh is already decentralised.** Meshtastic relays between nodes; a missing relay does not stop the others.
- **Every record is verified where it lands.** Each side checks the tag with the node's key before storing it, so a peer
  cannot invent evidence (`docs/api.md` section 8).
- **Gaps are visible.** `/v1/nodes` reports per node the records held, the highest and contiguous sequence numbers, the
  missing count and whether the last ack is current (`docs/mesh.md`, Delivery).
- **The bridge survives API outages.** It queues up to 1000 records, spools them to disk, replays the spool at start and
  retries with backoff (`services/mesh_bridge/.../forwarder.py`).

## Where it is centralised today

```
witness nodes ~~LoRa mesh~~ ONE gateway --USB--> ONE machine: bridge -> API -> ONE database
                                                                 |
                                         sync (kit-driven) <-> ONE cloud URL
```

- **One receiver.** One gateway radio on one machine. If it sleeps, crashes or loses USB, records sent meanwhile are lost
  beyond the few the gateway holds for its client.
- **No retransmission on the mesh path.** The node's mesh queue holds 4 records; an event is sent twice 30 s apart,
  a heartbeat once, then the slot is freed whether or not an ack came (`firmware/node/lib/stozher/stz_mesh.*`, used unchanged
  by the T-Beam module in `firmware/tbeam`). The satellite path does retry (`stz_tx.*`). The heartbeat interval is 3600 s,
  a build-time default.
- **One ack signer.** Acks are Ed25519-signed with key id 1 by default (`config.py` `cloud_key_id`). The T-Beam module trusts
  exactly one public key (`StozherModule.cpp`, `cloud[1]`). `infra/kit/init.sh` generates a new signing seed on every kit, still
  under id 1, so a node provisioned with one kit's or the cloud's key rejects any other kit's ack as a bad signature.
- **Sync has one peer.** The worker's `sync` job pulls from and pushes to a single `STOZHER_CLOUD_URL` every 60 s. The
  endpoints (`GET /v1/sync/events`, `POST /v1/sync/upload`, system role) are not cloud-specific, but no code talks to
  more than one peer. Two kits only meet through the cloud.
- **Only witness records sync.** Operator changes (node edits, closures, confirmations), hazard reports, API keys and the
  node registry are not synced. Node master keys are sealed with each machine's KEK and registered per machine.
- **Accounts are central.** Organisations, named staff, the two-person rule and key revocation live in one database.
  Short-lived kit credentials and a signed deny-list are specified as future work (`docs/access.md`), not built.

## Design principle

Every database is an equal peer holding a subset of one grow-only set of signed records. Peers exchange what the other
lacks; trust is recomputed locally; anything unsigned or unverifiable is ignored. No peer is required for another to work,
and when peers meet again they converge without conflict resolution. Operator decisions are different: they are not a
grow-only set, so they come last and only with explicit rules.

## Steps

Each step is useful on its own. Order is by cost and by how much it removes.

### Step 0. Keep the one receiver awake and its link alive (built, 20 October 2026)

- **Sleep lock:** the kit holds a `systemd-inhibit --what=sleep` lock while it runs (`infra/kit/run.sh`, `docs/field-kit.md`).
- **Stale link:** the bridge reopens the radio link after the machine resumes from sleep and after `--stale-after` seconds
  (default 3 h) with no text from the radio; health shows the reconnects and their reasons (`docs/mesh.md`, Behaviour).
- **What it does not fix:** a crash, a pulled cable or a dead gateway still lose records. That is what steps 1 to 3 are for.

### Step 1. Several receivers on one mesh

- **What:** run the kit on two or more machines, each with its own gateway radio hearing the same mesh (for example the
  desktop and the laptop). Each forwards to its own database; peer sync (step 2) or the cloud merges them.
- **Change:** record which gateway received a record: the bridge's system key label (or a configured gateway name) in
  `raw_messages.transport_meta`. Add a bridge flag so only one receiver per mesh sends acks: the 120 s ack rate limit
  (`services/mesh_bridge/.../acks.py`) is per bridge process, so two acking bridges double the ack airtime.
  Acks from two kits also need step 4 to verify.
- **Contracts:** none (`transport_meta` is free-form).
- **Test:** two bridges with `MESH_PORT=fake` feeding one and two APIs; the second copy is `duplicate`, delivery counts unchanged.

### Step 2. Peer sync between kits

- **What:** generalise the worker `sync` job from one cloud URL to a configured list of peers, each with its own system key
  and its own cursors in `sync_cursors`. Same endpoints, same verification, same first-copy-wins rule.
- **Change:** configuration (`STOZHER_SYNC_PEERS`, one URL and key reference per peer, keys only from the environment);
  the job loops over reachable peers and skips unreachable ones without blocking. Peer discovery is out of scope:
  a configured list first.
- **Contracts:** none for the endpoints; `docs/api.md` section 8 gains a paragraph on multiple peers.
- **Prerequisite:** every peer knows the same node ids and master keys (registration over a trusted channel, as today).
- **Test:** three API instances against three test databases; a record ingested at A reaches C through B; partition and
  rejoin converge to the same `(node_id, key_id, seq)` set and the same trust grid.

### Step 3. Nodes keep records until acknowledged (firmware)

- **What:** on the mesh path, keep a record in the queue until an ack covers it, and resend unacknowledged records with the
  next heartbeat, as the satellite path already does. Bounded by the existing limits (5 s between messages, 40 per hour)
  and the queue size; when the queue is full, drop heartbeats before events.
- **Contracts:** none on the wire; `docs/firmware.md` behaviour changes. Host tests (`make fw-test`) for queue, ack and
  budget behaviour first.
- **Hardware:** flashing a node needs the owner's go-ahead. No change to radio settings.
- **Depends on:** acks that move past a lost record. **Built** (`docs/control.md`, "Giving up on old gaps"): the ack
  gives up a gap 17 behind the newest record, and the node's mesh queue does the same, so one lost record no longer
  keeps every later one unacknowledged.
- **Limit:** a queue of 4 covers minutes to hours of outage, not ten hours of hourly heartbeats; a larger queue or a
  shorter heartbeat interval is a separate, measured decision (airtime and duty cycle).

### Step 4. More than one ack signer

Nodes must verify acks from whichever receiver hears them. Options:

| Option | How | For | Against |
|---|---|---|---|
| a. Shared seed | every kit and the cloud sign with the same key | no firmware change | one stolen kit compromises every node's acks; no per-site revocation |
| b. Key per site | each kit gets its own key id; nodes hold a short list of trusted public keys | per-site revocation; `verify_ack` already takes an array | nodes must be provisioned with the list; firmware holds one key today |
| c. Cloud only | kits forward but never ack | simplest | no acks while the cloud is unreachable, which is when they matter |

**Recommendation:** (b), with key ids assigned per site and a small fixed list on the node (for example 4 keys).
Revocation is a key-list update at the node's next service. This changes `docs/control.md` (key ids per signer, provisioning)
and the T-Beam module, so it is a team decision.

### Step 5. Operator state (later)

Hazard reports, confirmations, closures and node edits are decisions, not observations. Syncing them needs conflict rules
(who wins, what an expired decision means) and must keep "only a human confirms, closes or dismisses" (`docs/hazards.md`).
Do not start before the credential work below.

## Credentials while disconnected

A kit cut off from the cloud cannot learn that a key was revoked. Until short-lived kit credentials renewed at each sync and
a signed deny-list exist (`docs/access.md`), issue each kit's keys with a short expiry and treat a lost kit as a lost key set:
revoke its system key and its ack key id (step 4). Relevant open decisions: D7 (default lifetimes) and D10 (who runs a
deployment). Peer kits authenticate each other with system keys labelled by organisation and purpose, never by a person.

## Limits

- **Airtime.** LoRa at 868 MHz has duty-cycle limits and small payloads. Records are 28 or 32 bytes; nodes do not gossip
  histories to each other over the radio, and database sync happens over IP between kits, not over LoRa.
- **Keys.** Every receiver needs every node's master key to verify its records. Losing a kit exposes those keys; the KEK
  seals them at rest, nothing more.
- **Simulated data never syncs** (already enforced by the sync endpoint).
- **No positions of people.** Nothing here adds tracking: receivers record which gateway heard a record, not where anyone is.

## Acceptance drills

| Drill | Expected outcome |
|---|---|
| Switch off the acking receiver for an hour | the second receiver keeps ingesting; after step 4 it acks; no gap in `/v1/nodes` delivery |
| Try to suspend the receiver while the kit runs | refused by the sleep lock (step 0) |
| Suspend every receiver for an hour (lock off) | the link is reopened on resume (step 0); the gateway hands over what it held; with step 3 the node resends the rest |
| Remove a relay node | records still arrive by another path, or the gap is visible in delivery |
| Partition two kits for a day, then reconnect | after step 2 both hold the same record set and show the same trust grid |
| Lose a kit | its system key and ack key id are revoked; other kits and nodes keep working |

## Decisions for the team

1. Which receiver acks while there is more than one (step 1): a fixed one per site, or coalescing across peers. *Recommendation: fixed, by configuration.*
2. Ack signer model (step 4). *Recommendation: (b), key per site.*
3. Node queue size and heartbeat interval on the mesh path (step 3), against the airtime budget. *Recommendation: measure on the bench first.*
4. Whether operator state syncs at all (step 5), and on what rules.
5. Kit credential lifetime while disconnected (`docs/access.md` D7).

**Out of scope:** new radio hardware, the Kinéis adapter, the Android witness app, peer discovery, synchronising over LoRa.
