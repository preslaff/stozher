# STOZHER (СТОЖЕР)

Situational awareness for degraded environments: trusted, evidence-based, and still working when networks fail.

The first module, **GNSS Trust**, answers one question: *can I trust satellite navigation here, right now, and what is the evidence?*
Witness nodes detect GNSS jamming and spoofing and sign every report. The reports travel over a one-way satellite link, a local LoRa
mesh or any available network. A backend and an offline-capable field kit turn them into a live map and a trust API.

Built for the 12th CASSINI Hackathon (Challenge #1). Civil use only: situation, evidence, resilience.

> **Status.** A hackathon prototype. Access is restricted to admitted organisations (`docs/access.md`, `docs/terms-of-use.md`, a draft
> that needs legal review). No licence has been chosen yet, so all rights are reserved until the team decides.

## What is in it

| Part | Where | Notes |
|---|---|---|
| Core library | `packages/stozher_core` | packet codec and crypto with golden vectors, trust model, signed acknowledgements |
| API | `services/api` | FastAPI under `/v1`: ingest, nodes, events, hotspots, trust grid and routes, alerts (SSE), hazards, keys, webhooks |
| Worker | `services/worker` | job queue, hotspots, source localization, H3 trust grid, alert rules |
| Mesh bridge | `services/mesh_bridge` | Meshtastic gateway (USB or Wi-Fi) to the API, signed acks back to nodes; reopens a link that went stale, quiet or whose gateway restarted |
| Simulator | `services/simulator` | data-only scenarios; everything it makes is labelled SIM |
| Assistant (optional) | `services/assistant` | text questions answered from read-only API tools; never in the alert path |
| Dashboard | `web/dashboard` | React, Vite, MapLibre; static build, works offline |
| Firmware | `firmware/node` | witness node (ESP32-S3), host-tested codec and detection rules, one or two mesh radios (one per LoRa band); records kept and resent until acked |
| Meshtastic fork | `firmware/tbeam` | STOZHER inside Meshtastic on a T-Beam (GPS tap, witness records) and a store-and-forward gateway that keeps records in flash for the next bridge |
| Field kit | `infra/kit`, `tools/kit` | one command to run the stack on a laptop, optionally with a second gateway on another band; holds a sleep lock while it runs; offline bundle and installer |

Contracts live in `docs/`: `packet.md`, `api.md`, `trust.md`, `control.md`, `mesh.md`, `hazards.md`, `access.md`, `assistant.md`,
`firmware.md`, `field-kit.md`, `scenario.md`. `decentralised-mesh.md` is a design note: steps 0, 0b and 3 are built (sleep lock and link
reopen, store-and-forward gateway, nodes resend until acked), the rest are proposals. Working rules and architecture are in `CLAUDE.md`; every library, tool, model and dataset is listed in `DECLARATIONS.md`.

## Principles

- Simulate, never transmit. Nothing here generates or drives a GNSS-like signal.
- Alerts are deterministic. The assistant answers from API tools and is never in the detection or alert path.
- Places, not people. Hazard reports use a closed taxonomy of road conditions; moving sources are a road segment and a time window.
- No evidence is never an all-clear. Thin evidence is labelled as thin; simulated data is labelled as simulated.
- Access is accountable: named staff accounts, keys labelled by organisation and purpose, no anonymous access.

## Quick start

Needs Linux, Python 3.12 with [`uv`](https://docs.astral.sh/uv/), Node with `pnpm`, and a **local PostgreSQL 16** with PostGIS
(no containers; pgvector is only needed once playbook retrieval is built). Details and the full procedure are in `docs/field-kit.md`.

```
git clone git@github.com:preslaff/stozher.git && cd stozher
make kit-init          # fills missing secrets in infra/.env (git-ignored, never printed)
make kit-build         # builds the dashboard
# optional: put MESH_PORT=/dev/serial/by-id/<gateway> in infra/kit/kit.env (see docs/field-kit.md);
#           MESH_PORT_2 (and MESH_NAME_2, default 433) adds a gateway on the other LoRa band
make kit               # API + worker + mesh bridge, dashboard at http://<this machine>:8000/
ASSISTANT=on make kit  # same, with the optional assistant
make kit-doctor        # checks the setup and prints what is missing
make kit-stop          # stops the kit (or Ctrl-C)
```

Sign in with an API key for your role. Create one with
`uv run python -m stozher_api.cli create-key --role operator --label "<team>, <purpose>"`. Boards are identified by USB serial under
`/dev/serial/by-id/`, never by `ttyUSB` number.

Run the pieces by hand with `make migrate`, `make api`, `make worker`, `make sim S=coastal`, `make bridge` and `pnpm dev` in
`web/dashboard` (`docs/scenario.md`, `docs/mesh.md`).

### Another laptop, offline

```
make kit-bundle        # kit-dist/stozher-kit-<version>-<arch>.tar.zst (+ .sha256); WITH_MAP=1 adds the offline map
```

Copy the files over, verify with `sha256sum -c`, unpack, and run `./install.sh`. It checks the machine, prints the exact `apt` lines for
anything missing, creates the database and runs the doctor. It never changes the network and never carries secrets. Each laptop makes its
own keys, so nodes must be registered there too.

### The assistant

Optional and off by default. Without an engine it still produces a deterministic situation report. For free-text questions run a local
[llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server --jinja` (default `http://127.0.0.1:8080`) or Ollama. A cloud model is
used only if the operator sets it up in `infra/.env`. See `docs/assistant.md`.

## Tests

```
make test              # ruff, mypy, pytest (including golden vectors and the assistant evaluation set), dashboard lint and tests
make fw-test           # firmware codec and detection tests on the host, no hardware needed
make sim-dualband      # a virtual dual-band node through two simulated bridges (needs a running API)
make assistant-eval    # the evaluation questions against a running local model (not part of make test)
```

## Repository layout

```
docs/          contracts and guides
packages/      stozher_core
services/      api, worker, mesh_bridge, simulator, assistant, kineis_adapter
web/dashboard/ the React dashboard
firmware/      node (witness firmware), tbeam (Meshtastic fork patches and boards), lotus_fallback
infra/         .env.example, field-kit scripts
tools/         bundle, basemap and other helpers
tests/vectors/ golden packet vectors shared by Python and C++
```

## On real hardware

Running on real data (Oct 2026): a LilyGO T-Beam (NEO-6M) with the fork as a witness node, and two LilyGO T3 gateways with the
store-and-forward build or stock Meshtastic, one on EU_868 and one on EU_433, each with its own bridge in the kit. Records arrive live,
signed acks reach the node and are verified there, and records held during a kit outage are handed over on reconnect. Gateways run with
Bluetooth off: Meshtastic 2.8.2 on the T3 restarted about hourly from a Bluetooth controller assert (`docs/mesh.md`). Only u-blox 6
receivers have been checked on hardware.

## Not built yet

Kinéis adapter, assistant retrieval over playbooks and voice, field-kit sync of operator changes and hazard reports, an Android witness
app, a dashboard screen for key management. Nothing has run against a real KIM1 satellite modem.
