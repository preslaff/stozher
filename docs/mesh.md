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

A STOZHER witness board (T-Beam or WROOM fork, `firmware/tbeam`) can also serve as the gateway: plug it into the kit and
point `MESH_PORT` at it. It forwards other nodes' records, and while the bridge is connected it hands over its own records
too (Meshtastic would not show them otherwise), so one board is witness and gateway at once. Not yet checked on hardware.

### A second band (433 MHz)

Radios on different bands never hear each other, so a mesh on 433 MHz has its own gateway and bridge
(`MESH_PORT_2`, `docs/field-kit.md`). Set up in October 2026: a LilyGO T3 V1.6.1 (ESP32-PICO-D4, 4 MB) on stock
Meshtastic 2.8.2 built from the same checkout, region `EU_433`, the same private channel and key as the 868 MHz mesh
(copied board to board, never printed) and the same modem preset (LONG_FAST, 3 hops). Its Meshtastic ID is
`!06e4365b` (it showed `!605757d0` on first boot and changed after setup), name `STZ gateway 433`. The factory flash
was backed up first (`data/firmware-backups/`, git-ignored). A T3 has no GNSS receiver, so it is a gateway, not a
witness; a STOZHER node on 433 MHz needs a board with GNSS and our firmware. In Bulgaria use `EU_433` or `EU_868`
only; 470 MHz is China's band.

### A gateway on the network (Wi-Fi)

An ESP32 gateway (T3, T-Beam, the WROOM board) can join a Wi-Fi network and offer Meshtastic's client API there (TCP port
4403) instead of USB: `--port tcp:<host>[:<port>]`, or `MESH_PORT=tcp:192.168.4.20` in the kit. The bridge then works as
over USB (link supervisor, acks, the witness-as-gateway copy of its own records). The board's Wi-Fi name and password are
set on the board by its owner (`meshtastic --set network.wifi_ssid ... --set network.wifi_psk ...`, a secret: not in this
repository). **Meshtastic's network API has no authentication**: anyone on that network can read the channel, already
decrypted, and change the radio's settings. Use it only on the kit's own private WPA2 network, never a shared one; the
bridge logs a warning and `make kit-doctor` checks that the gateway answers and repeats the warning. Wi-Fi costs about
100 mA (mains or USB power, not a battery witness) and does not run together with Bluetooth on the ESP32. On a T-Beam the
Wi-Fi antenna is centimetres from the GPS antenna: compare C/N0 and noise with Wi-Fi on and off (`STOZHER_EPOCH_LOG_S`)
before trusting a witness that uses it. Built and unit-tested; not yet run against a board.

### Store and forward on the gateway

A stock gateway keeps only **8** messages for a client that is not connected (`MAX_RX_TOPHONE` on a classic ESP32), so a
bridge that is down for a night loses the rest (22-23 October 2026: the kit computer slept 7 hours and a4b0's records 335-355
were lost). A gateway running our fork keeps them (`firmware/tbeam`, `STOZHER_STORE`; the T3 build is
`tlora-v2-1-1_6-stozher-gw`, and every witness board has it too):

- **Keep.** Every `STZ1:` record heard on the primary channel, and the board's own records if it is a witness, is written
  to a file in the board's flash (1024 slots of 48 bytes) with the sender's Meshtastic ID and the time it was heard. The
  same sender, key id and sequence number again (a mesh repeat) is kept once. The records are the opaque signed bytes: the
  gateway holds no node keys and no signing key, so it cannot read, change or invent one, and a stolen gateway shows only
  sender IDs and counts.
- **Room.** At most a quarter of the slots per sender, so one noisy or bogus channel member cannot push the others out.
  When a sender is at its quota (or the store is full, then the sender holding the most), its oldest heartbeat goes first,
  then its oldest record. Records older than 7 days go (when the gateway knows the time). The store survives a reboot; a
  slot damaged by a power cut is dropped.
- **Hand over.** When a bridge connects over USB, Wi-Fi or Ethernet (not a phone over Bluetooth), the gateway hands it every
  stored record, oldest first, as a packet from the original sender, a few at a time so the client queue keeps room for live
  traffic. **Any** kit's bridge gets them: a field kit or a command kit, whichever connects first. Copies the API already
  holds are answered `duplicate`, so nothing is counted twice. Records that arrive during the hand-over come live.
- **Forget.** A stored record is deleted only when an ack the connected bridge sends (`STZA1:`) verifies under the trusted
  cloud key and shows the backend has it. `max_seq` means "received or given up" (`docs/control.md`), so the gateway deletes
  only what is known to be received: sequence numbers above the give-up point (17 behind the newest) up to `max_seq`, and
  those marked in the mask. A gap the cloud gave up stays in the store, so a later hand-over can still fill it. Unsigned or
  invalid acks change nothing; acks other gateways transmit are usually encrypted for their node and are not used.
- **Trusted key.** The board trusts one cloud public key, set at build time (`STOZHER_CLOUD_PUBLIC_HEX`, default the test
  key). A kit signs with its own key (`infra/kit/init.sh`), so build with that kit's `GET /v1/control/public-key`; with the
  wrong key nothing is ever deleted by acks (records then go by age or for room, and every hand-over repeats them). Every
  STOZHER board prints `acks trusted from key <id> <first 8 hex digits>` at boot and `make kit-doctor` prints the kit's
  fingerprint the same way, so a mismatch shows at a glance; the test key is named as such.
- **Log.** Counts only (held, senders, kept, repeats, deleted by ack, pushed out, expired, handed over), never record
  contents or positions.

Built and host-tested (`make fw-test`, `test_store`); the T3, T-Beam and WROOM images build. Not yet run on a board.

### Simulated radios (sim mode)

`--port sim-tcp:127.0.0.1:<port>` gives the bridge a simulated gateway radio (`mesh_bridge/sim_radio.py`) instead of a USB
board: a TCP server on loopback only, one connection per simulated node radio, which first sends `SIMID:<id>` (the
transport ID its radio on that band would have) and then one text message per line, as the Serial module does. A bridge
with a simulated radio is in **sim mode**: records go to `/v1/ingest/sim` (never live ingest; the API also rejects a SIM
node on live mesh ingest, `sim_mismatch`), acks are looked up as transport `sim`, alert texts carry `[SIM]`, and it sends
no heartbeat, so it never shows up in `/health` or the dashboard next to the real gateways. Used by the dual-band run
(`docs/scenario.md`).

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
  `last_reconnect_reason` and `last_text_age_s`. Records the gateway did not keep while the link was dead are lost (a stock
  gateway keeps 8; a store-and-forward gateway keeps them, above).
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
