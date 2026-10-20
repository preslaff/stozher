# Mesh path (Meshtastic)

Witness records travel over the local LoRa mesh as plain Meshtastic text, `STZ1:` followed by
the record hex (56 or 64 hex characters, see `docs/packet.md`). The T-Beams run stock Meshtastic
firmware (region EU_868, private channel); we do not write mesh firmware. A gateway T-Beam
connected to a computer by USB feeds `services/mesh_bridge`, which forwards records to
`POST /v1/ingest/mesh`.

```
witness T-Beam ~~LoRa mesh~~ gateway T-Beam --USB--> mesh_bridge --HTTP--> API
                                      ^                                    |
                                      +------- alert text (optional) <-----+  GET /v1/stream/alerts
```

## Transport ID

A node's mesh transport ID is its Meshtastic node ID, `!` plus 8 lowercase hex digits (for
example `!a1b2c3d4`). Register the witness node with transport `mesh` and that `external_id`
(`POST /v1/nodes`). `python -m mesh_bridge info` prints the gateway's own ID and the nodes it has heard.
The bridge forwards the sender's ID; the API resolves the node and the 4-byte tag proves the sender,
so a stranger relaying the same bytes is rejected (`unknown_transport_id` or `bad_tag`).

### After reflashing a device

Flashing a board can give it a **new Meshtastic ID** (the a4b0 test board went from `!bdf0a4b0` to `!0b533538`). Its records are then
rejected as `unknown_transport_id` until the node is attached to the new ID. There is no database edit and no free
registration for this: the node's key is the proof.

1. Let the device send a packet that reaches the API (the self-test after boot, an event or a heartbeat). It is rejected, but stored.
2. `GET /v1/nodes/unrecognised-senders` (operator) lists the new ID and, as `candidate_node_id`, the node whose key the packet's tag
   verifies under. The dashboard shows the same list with a **Bind** button.
3. `POST /v1/nodes/{id}/transport-ids/rebind` with `{"transport": "mesh", "external_id": "!0b533538"}` replaces the node's old
   mesh ID, writes an audit entry, and re-ingests the packets that proved it, so they are not lost.

What the API checks, all server side: a named operator (or an operator-role key); a stored packet from that ID whose tag verifies for this
node's key (a device without the key cannot make one); the packet arrived within the replay window and within 24 hours; its sequence number was not
already delivered (a copy of an old packet is no proof); the ID does not belong to another node; the node is not simulated.
Limit: someone who captured a packet that never reached us could replay it from their own radio within the window and make
that ID a candidate. That only lets them repeat that packet (the sequence rules still apply), and an operator confirms the bind, but check
that the candidate is what you expect before pressing Bind.

## Run

```
# serial access (once per login, or add yourself to the dialout group)
sudo setfacl -m u:$USER:rw /dev/ttyUSB0
export STOZHER_SYSTEM_KEY=...        # a key with the system role
uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge --port /dev/ttyUSB0 run
```

`make bridge` does the same (`MESH_PORT` selects the device). `--port fake` runs with no radio.
Options: `--health-port N` (JSON health on 127.0.0.1), `--spool FILE` (default
`~/.local/state/stozher/mesh-spool.jsonl`), `--no-spool`, `--relay-sim`, `--relay-interval S`.
Setting `STOZHER_RESPONDER_KEY` also relays alerts to the mesh (below).

## Behaviour

- **Only records are forwarded.** Chat and other apps on the channel are ignored.
- **Mesh repeats** (same sender, same bytes within 10 minutes) are dropped by the bridge; the API
  would also answer `duplicate`.
- **Store and forward.** A record waits in a bounded queue (1000), mirrored to the spool file and
  replayed after a restart. Network errors, 5xx and 429 retry with backoff (2 s up to 60 s). A
  refused key (401/403) keeps the record and keeps retrying so a corrected key loses nothing. The
  backend's answer (`accepted`, `duplicate` or `rejected`) is final and never retried.
- **A missing radio never stops the bridge.** It retries the port every 5 s and keeps serving health.
- **A stale link is reopened.** A serial link can die without the radio library noticing: after the machine
  suspended, the bridge reported "connected" for hours while the gateway held the node's messages. The bridge reopens the
  link when the machine resumes from sleep (the monotonic clock stops during suspend, the boot clock does not) and after
  `--stale-after` seconds with no text from the radio (default 3 h, 0 = never). Health shows `reconnects`,
  `last_reconnect_reason` and `last_text_age_s`. Records the gateway did not keep while the link was dead are lost.
- **Alerts to the mesh (optional).** With a responder key the bridge listens to the alert stream and
  sends `STOZHER <LEVEL>: <message>` (at most 200 bytes) on channel 0, warnings and critical only,
  at most one per `--relay-interval` (default 60 s) because LoRa airtime is shared. Simulated
  alerts stay off the air unless `--relay-sim` is set, and are then prefixed `[SIM]`.
  Signed node commands are not implemented yet.
- **Acknowledgements.** After the API has accepted (or recognised as duplicate) records from a node, the
  bridge fetches that node's latest cloud-signed ack (`GET /v1/acks`, system key) and sends it back as a
  direct `STZA1:` message (docs/control.md). Records are coalesced (15 s) and each node gets at most one
  ack per 120 s. A duplicate arrival means the node missed the last ack, so it earns another within the
  same limit. Acks are sent as direct messages with Meshtastic's link-layer ack on, so the radio retransmits
  until the node confirms: on the bench, single-shot direct messages were lost about half the time on the
  shared LoRa channel, and with the link-layer ack 4 of 4 arrived. The bridge only relays: it cannot sign, and nodes verify with the cloud public key. Without
  `STOZHER_ED25519_SEED` on the API the pusher turns itself off with one warning; `--no-acks` disables it.

## Health

`GET http://127.0.0.1:<health-port>/`: `status` (`ok` only while the radio is connected), `radio`
(`connected`, `node_id`, `last_error`), `uplink` (`queued`, counters, `rejections` by reason,
`last_error`) and `alert_relay` and `acks` (`sent`, `waiting`, `fetch_errors`, `disabled`).

## Test it with two T-Beams

1. Gateway on `/dev/ttyUSB0`, second T-Beam on `/dev/ttyUSB1`. Get the second one's ID:
   `uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge --port /dev/ttyUSB1 info`
2. Register it as a node (operator key) with that ID, test master key `000102…0f` and kind `tbeam`;
   note the returned `id`.
3. Start the bridge on `/dev/ttyUSB0` (`run`), then make and send a record from the second radio:
   ```
   TEXT=$(uv run python -m simulator packet --node-id <id> --seq 1 --cls jamming)
   uv run --package stozher-mesh-bridge --extra radio python -m mesh_bridge --port /dev/ttyUSB1 send "$TEXT"
   ```
4. The bridge logs and its health shows `accepted: 1`; `GET /v1/events` shows the event with
   `received_via: mesh`. Use a new `--seq` for every send: a repeated sequence number is a duplicate.

Test records use the well-known test key; use real per-node keys for anything else.
The Meshtastic library is GPL-3.0, which is why the bridge is a separate GPL-3.0-or-later package
that talks to the platform only over HTTP (`services/mesh_bridge/LICENSE-NOTE.md`).

See also `docs/decentralised-mesh.md` (proposal, not built): several receivers, peer sync and retransmission on the mesh path.
