# MeshCore as a second mesh (option for later)

Status: **evaluation, nothing built.** Written 30 Oct 2026 from the MeshCore repository at v1.18.0 (commit `81dbae59`),
the `meshcore` Python library 2.3.17 and public sources. Meshtastic stays the primary mesh; MeshCore would be an optional
module (CLAUDE.md rule 4: the core never depends on it). Not before the hackathon: the core Musts (expert node on real
hardware, KIM1) come first, and "own mesh firmware" is on the Won't list for that weekend.

## 1. What MeshCore is

- MIT-licensed LoRa mesh firmware and library (C++, RadioLib), release every 1–2 months, about half Meshtastic's
  community. Official site after the April 2026 project split: meshcore.io (meshcore.co.uk is no longer the core team's).
- One role per board: **companion** (radio driven by a phone or PC over BLE, USB serial or Wi-Fi TCP), **repeater**,
  **room server** (message board with catch-up on login), **sensor** (Cayenne LPP telemetry), **KISS modem**, terminal chat.
- **Routing:** the first message to a contact floods; the reply returns the path taken (a list of 1–3-byte repeater
  hashes) and later messages are source-routed along it ("direct"). **Only repeaters forward;** companions never relay.
  Up to 64 hops (1-byte hashes), 32 (2-byte) or 21 (3-byte); Meshtastic defaults to 3, at most 7.
- **Packet:** header 1 byte, optional transport code 4, path ≤ 64, payload ≤ 184 bytes (255-byte LoRa frame).
- **Not compatible with Meshtastic on air** (sync word 0x12 against 0x2B, different frame, crypto and presets). Two meshes,
  bridged only by software. A board runs one firmware at a time.
- **Boards in the kit are supported:** LilyGO T-Beam (SX1276 and SX1262), T3 LoRa32 V2.1-1.6 (SX1276), Seeed XIAO
  ESP32-S3 with Wio-SX1262 (including the sensor and USB companion roles), also Heltec V3/V4 and RAK4631.

## 2. What fits our contracts

| STOZHER item | Size | MeshCore carrier | Fits |
|---|---|---|---|
| Witness record (`docs/packet.md`) | 28/32 bytes (61/69 chars as `STZ1:` text) | text (133 chars documented, `MAX_TEXT_LEN` 160) or binary | yes, either |
| Signed ack (`docs/control.md`) | 79 bytes (164 chars as `STZA1:` text) | **binary only**: channel data (`GRP_DATA`, ≤ 165 bytes, 2-byte `data_type`) or a binary request to the node | not as text |
| Mesh alert text (`docs/mesh.md`) | ≤ 200 bytes today | text | cut to ≤ 130 bytes or split |
| Hazard mesh report `STZR1:` (`docs/hazards.md`, not built) | fixed report + 64-byte signature | binary channel data | yes, binary |

Binary framing proposal: `data_type` from the developer range 0xFF00–0xFFFF (MeshCore `docs/number_allocations.md`;
register one if this goes ahead), first byte a STOZHER message type (record, ack, report), then the same bytes as today's
hex payload. The records and acks keep their own crypto (AES-CTR + HMAC tag, Ed25519 signature), so MeshCore only
carries bytes.

## 3. What would change in STOZHER

| Part | Today | For MeshCore | Effort |
|---|---|---|---|
| API | transport `mesh`, any text ID up to 64 chars | nothing; either keep `mesh` with IDs prefixed `mc:` (public-key prefix, hex) or add transport `meshcore` to the literals in `routes/nodes.py`, `routes/control.py`, `rebind_service.py` | 0–½ day |
| Bridge | `Radio` protocol; `MeshtasticRadio` is the only Meshtastic file | `meshcore_radio.py` on the MIT `meshcore` library (async; serial, BLE, TCP): binary records in, binary acks out, `last_packet_mono`/`restarted_mono` for the supervisor; a `meshcore:` port prefix and a `meshcore` extra | 2–3 days |
| Kit (`infra/kit/`) | `--extra radio`, TCP port 4403 check | `MESH_TECH` / `MESH_TECH_2`; doctor checks duty cycle and advert settings | ½ day |
| Node uplink (`stz_mesh`) | plain `STZ1:` lines to a stock Meshtastic radio in TEXTMSG mode | a `Port` speaking the companion frame protocol (`<`/`>` + length, frames ≤ 176 bytes); `Link`/`Fanout` unchanged | 3–5 days |
| Gateway store, GPS tap (fork) | Meshtastic modules, GPL-3 | a MeshCore build reusing `lib/stozher`; `stz_store` keeps the sender as `uint32_t`, so store a 4-byte key prefix or widen it | 1–2 weeks |
| Simulator | `sim-tcp` radios, mesh link model | nothing; `make sim-dualband` already covers a second band | none |
| On-air tests | — | collisions with Meshtastic, duty cycle, a repeater chain | 2–3 days |

## 4. Rules and risks

- **Duty cycle:** repeaters default to 50 %, far above EU limits. Every build and every repeater we set up gets
  `set dutycycle 10` in 869.4–869.65 MHz (1 % in the other 868 sub-bands) and `set cad on`; the kit doctor checks it.
- **Frequencies:** EU_433 / EU_868 only. MeshCore's EU narrow preset (869.618 MHz, SF8, 62.5 kHz) overlaps Meshtastic
  EU_868 (869.525 MHz, 250 kHz): no decoding across, but collisions. Prefer 433.650 MHz (EU 433 narrow, SF8, 62.5 kHz),
  away from our 433 Meshtastic channel at 433.125 MHz, and measure before deciding.
- **Positions:** adverts can carry the node's position in clear. Witness and gateway builds leave it out (node locations
  are never public, CLAUDE.md API rules).
- **MeshCore's own crypto is weak:** AES-128-ECB, a 2-byte truncated HMAC, one secret for both, no forward secrecy; issue
  #259 open since May 2025, no advisories, no audit. Acceptable only because STOZHER's evidence carries its own crypto.
  Never rely on a MeshCore channel for anything confidential; channel messages have no authenticated sender.
- **Metadata in clear:** the repeater path and 1-byte sender/receiver hashes. About equipment, not people, but noted.
- **Moving target:** protocol changes between versions (multibyte path hashes in v1.14 are dropped by older repeaters);
  pin a version and test upgrades. Governance dispute (April 2026) may split the ecosystem.
- **Licence:** firmware and Python library are MIT, so a MeshCore radio adapter or firmware needs no GPL isolation
  (unlike `services/mesh_bridge` with Meshtastic). Still declared in `DECLARATIONS.md` when added.

## 5. Where it would help

1. **Witnesses that never relay:** a companion transmits only its own records, so the detector blanks fewer epochs for
   our own bursts.
2. **Road corridors and route trust:** a repeater chain along a road with source-routed delivery reaches far beyond
   Meshtastic's 3–7 hops.
3. **Acks back to the node** travel along the learned path instead of flooding (decentralised-mesh step 3).
4. **Several receivers, kit-to-kit catch-up** (decentralised-mesh steps 1–2): the room server's login-and-sync pattern;
   its storage is too weak to replace ours (32 posts, RAM only), so our flash store stays.
5. **Hazard reports over the mesh** (`STZR1:`): fits binary channel data, not text.
6. **Android witness app** (not built): MIT client libraries (meshcore.js, meshcore_py) and an open Flutter client over
   BLE, a licence-friendly base.
7. **Node health telemetry** (battery and similar) through the sensor role, apart from witness records.
8. **Existing community MeshCore networks** could carry our encrypted records: a team decision first (public repeaters
   carry our traffic).

## 6. Suggested order (after the hackathon)

1. Bridge proof: one spare board as a USB companion on 433.650 MHz, `MeshCoreRadio`, binary records and acks, a node
   simulated over the same path. Host tests with a fake library, as `tests/test_quiet_link.py` does for Meshtastic.
2. A repeater chain on the bench and outdoors: delivery, duty cycle, collisions with the Meshtastic channels.
3. Only if both pay off: the node `Port`, then a MeshCore build of the gateway store and GPS tap.

## Sources

- MeshCore repository: `docs/packet_format.md`, `docs/payloads.md`, `docs/companion_protocol.md`, `docs/cli_commands.md`,
  `docs/faq.md`, `docs/number_allocations.md`, `src/Mesh.h`, `src/Dispatcher.cpp`, `src/Utils.cpp`,
  `src/helpers/BaseChatMesh.h`, `variants/`. https://github.com/meshcore-dev/MeshCore
- Python library: https://pypi.org/project/meshcore/ , https://github.com/meshcore-dev/meshcore_py
- Radio presets (served, not compiled in): https://api.meshcore.nz/api/v1/config
- Project split: https://blog.meshcore.io/2026/04/23/the-split
- Security: https://github.com/meshcore-dev/MeshCore/issues/259 , https://alainx277.com/posts/meshcores-problem-with-security/
- Review: https://hackaday.com/2025/12/06/lessons-learned-after-trying-meshcore-for-off-grid-text-messaging
