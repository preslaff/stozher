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
