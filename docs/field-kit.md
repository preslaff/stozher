# Field kit

One laptop at a command post runs the whole STOZHER stack with no internet, receives witness records over LoRa through a
gateway radio, and serves the dashboard (with the offline map) to phones and laptops on its own network.

```
GPS witness T-Beams ~~LoRa~~ gateway T3 --USB--> laptop: bridge -> API + worker + local PostgreSQL
                                                          |
phones / laptops ----- Wi-Fi (kit's network) ------ http://<laptop>:8000/  (dashboard + map + API)
```

## What you need
- A Linux laptop with the repository, `uv`, `pnpm`, the local PostgreSQL 16 (PostGIS, pgvector) with the `stozher` database. **No containers.**
- A gateway radio on the private channel with stock Meshtastic (the TTGO T3 in our kit), connected by USB. Find its path with
  `ls /dev/serial/by-id/` (identify a board by its USB serial, never by `ttyUSB` number).
- The offline map, built once with internet: `tools/basemap/fetch.sh` (`docs/basemap.md`). After that nothing needs a network.

## First run (once)
```
make kit-init        # fills only the missing secrets in infra/.env (never printed, never overwritten), creates the bridge and relay keys
make kit-build       # the dashboard with a relative API URL (web/dashboard/dist), served by the API itself
```
`kit-init` will not create a new `STOZHER_KEK` if one exists: a new one would orphan every node key already stored.

## Start and stop
```
MESH_PORT=/dev/serial/by-id/usb-Silicon_Labs_CP2104_..._02066B17-if00-port0 make kit
make kit-stop        # or Ctrl-C in the terminal that runs it
```
`MESH_PORT=none` starts the kit without a radio (for example to look at stored data). The script migrates the database,
starts the API (bound to all interfaces, port 8000, override with `STOZHER_API_HOST` and `STOZHER_API_PORT`), the worker and the
bridge, prints the addresses, and stops everything on exit. Logs: `~/.local/state/stozher/{api,worker,bridge}.log`.

The top bar of the dashboard shows the state of each part: the **mesh bridge** chip turns green when the bridge is alive and its
radio is connected, **worker** when the worker is alive. They come from heartbeats (`POST /v1/system/heartbeat`, `docs/api.md`).

## Joining from a phone or another laptop
1. Put the device on the kit's network. If the laptop has no other network, you can make it an access point with
   `infra/kit/hotspot.sh up <name> <password>` (a private WPA2 network). **That script changes the laptop's network
   settings: run it yourself and on purpose; nothing in the kit runs it.**
2. Open `http://<laptop address>:8000/` and enter an API key for your role. Issue each team its own key (a named
   operator or admin creates it through `POST /v1/admin/api-keys`, labelled by organisation and purpose, short-lived): never share
   one key. `responder` shows the situation, `operator` can also bind reflashed devices (`docs/mesh.md`).
3. The map comes from the same address, so it works with no internet. (The build must include MapLibre's worker files, `assets/maplibre-gl-worker.mjs` and `maplibre-gl-shared.mjs`; `vite.config.ts` emits them and `make kit` rebuilds if they are missing.)
4. On a phone the sidebar sits above the map. Phone-specific layout is basic.

## Limits, said plainly
- The kit speaks plain **HTTP** on its own network. A key typed on a phone travels unencrypted over that Wi-Fi. Use a private
  WPA2 network, short-lived keys, and revoke a key when a device is lost. TLS for the kit is on the backlog.
- Alert text goes back into the mesh only if `STOZHER_RESPONDER_KEY` is set (it is, after `kit-init`) and the radio is connected.
  A phone with the stock Meshtastic app on a radio receives it, but that app cannot verify our signatures.
- Sync with the cloud covers witness records only (`docs/api.md` section 8). Set `STOZHER_CLOUD_URL` and `STOZHER_CLOUD_KEY` (a `system` key
  issued by the cloud, short expiry) in `infra/.env`; without them the kit holds only what it received itself. Nodes and their keys must exist on both
  sides. Operator changes and hazard reports are not synced; short-lived credentials and the signed deny-list are not built.
- Real GNSS jamming has not been recorded by our hardware: thresholds are calibrated on synthetic data and quiet baselines
  (`docs/firmware.md`).
