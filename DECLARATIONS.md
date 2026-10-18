# DECLARATIONS

Every library, tool, model and dataset used, with licence. Add a row when you add one.

| Name | Kind | Licence | Used in | Notes |
|---|---|---|---|---|
| FastAPI | library | MIT | services/api | planned |
| Pydantic | library | MIT | stozher_core | |
| cryptography | library | Apache-2.0 / BSD | stozher_core | |
| uv, ruff, mypy, pytest | tooling | MIT / Apache-2.0 | all Python | |
| PostgreSQL, PostGIS (postgis/postgis image) | tool | PostgreSQL / GPL-2 | infra | database |
| Hypothesis | library | MPL-2.0 | stozher_core tests | property tests |
| OpenSSL CLI | tool | Apache-2.0 | tests/vectors/make_vectors.py | independent generator for golden vectors |
| hatchling | tooling | MIT | stozher_core build | |
| FastAPI, uvicorn, SQLAlchemy, asyncpg, GeoAlchemy2, Alembic, pydantic-settings | libraries | MIT / BSD / Apache-2.0 | services/api | |
| h3 (Uber H3 bindings) | library | Apache-2.0 | api, worker | trust grid |
| shapely, pyproj | libraries | BSD-3 / MIT | worker | ellipse polygon, projections |
| scikit-learn, numpy | libraries | BSD-3 | worker | DBSCAN, weighted statistics |
| pytest-asyncio, httpx | libraries | Apache-2.0 / BSD-3 | tests | |
| PostgreSQL 16 + PostGIS (local server) | tool | PostgreSQL / GPL-2 | database | pgvector to follow |
| PyYAML | library | MIT | services/simulator | scenario files |
| sse-starlette | library | BSD-3 | services/api | server-sent events |
| React, React DOM | libraries | MIT | web/dashboard | |
| MapLibre GL JS | library | BSD-3 | web/dashboard | map rendering |
| Vite, Vitest, TypeScript, ESLint (typescript-eslint, react-hooks), Prettier | tooling | MIT / Apache-2.0 | web/dashboard | |
| OpenStreetMap raster tiles | dataset | ODbL (© OpenStreetMap contributors) | web/dashboard | explicit online option only (`VITE_TILE_URL`), subject to the OSM tile usage policy; the default base map is the local extract below |
| pmtiles (JavaScript) | library | BSD-3 | web/dashboard | reads the offline base map from one file with HTTP range requests |
| @types/node | library (types only) | MIT | web/dashboard (dev) | type-checks `vite.config.ts`, which copies MapLibre's worker files into the production build |
| @protomaps/basemaps | library | BSD-3 | web/dashboard | style layers (dark flavor) for the Protomaps vector tiles |
| Protomaps basemap build, Bulgaria extract | dataset | ODbL (© OpenStreetMap contributors, Protomaps) | data/basemap (git-ignored), tools/basemap/fetch.sh | vector tiles, zoom 0-14, extracted once from the public daily build with the pmtiles CLI |
| go-pmtiles CLI 1.31.2 | tool | BSD-3 | tools/basemap/fetch.sh | cuts the extract; used on the developer machine only |
| protomaps/basemaps-assets: Noto Sans (Regular, Medium, Italic) glyphs, dark sprites | dataset | SIL OFL 1.1 (fonts), BSD-3 (sprites) | web/dashboard/public/basemap (git-ignored) | map labels and icons served locally, so no font or sprite is fetched online |
| Meshtastic Python library (+ pypubsub) | library | GPL-3.0 | services/mesh_bridge (optional `radio` extra) | bridge is GPL-3.0-or-later and isolated, HTTP only |
| httpx | library | BSD-3 | simulator, mesh_bridge, assistant, tests | |
| uvicorn | library | BSD-3 | services/api, services/assistant | serves the assistant on loopback |
| Ollama (local LLM server) | tool | MIT | services/assistant (optional, not bundled) | local engine; HTTP `/api/chat` with tools; installed by the user, nothing is downloaded by the kit |
| qwen2.5:7b-instruct (Qwen2.5 7B Instruct, Alibaba Cloud) | model | Apache-2.0 | services/assistant (default local model) | open-weight instruct model with tool calling; pulled by the user with `ollama pull`; replaceable with `STOZHER_ASSISTANT_LOCAL_MODEL` |
| Anthropic Messages API, model `claude-sonnet-5-5` | service / model | Anthropic terms (commercial API) | services/assistant (optional cloud engine) | off by default; used only when `STOZHER_ASSISTANT_CLOUD=on` and `ANTHROPIC_API_KEY` are both set in the environment; called with plain httpx, no SDK |
| Ed25519 via `cryptography` | library feature | Apache-2.0 / BSD | stozher_core.control, services/api | acknowledgement signatures; golden vectors cross-checked with the OpenSSL CLI |
| Sentinel-1 GRD (processed on CDSE through openEO), Copernicus DEM GLO-30 | datasets | Copernicus Sentinel data terms (free, attribution required) | spikes/eo_flood | flood-detection spike; attribution in docs/spikes/eo-flood.md |
| Copernicus EMS rapid-mapping products (EMSR692 AOI 05) | dataset | Copernicus EMS terms (free, attribution) | spikes/eo_flood | validation reference only, not redistributed |
| OpenStreetMap roads via the Overpass API | dataset | ODbL | spikes/eo_flood | road overlay |
| openeo (Python client), rasterio, scipy, shapely, pyproj, geopandas, pyogrio, matplotlib, pillow | libraries | Apache-2.0 / BSD / MIT | spikes/eo_flood (isolated script environments) | |
| PlatformIO Core, Espressif 32 platform, Arduino-ESP32 framework | tooling | Apache-2.0 / LGPL-2.1 | firmware/node | build and flash; the Arduino core is linked into the node image |
| Unity (ThrowTheSwitch) | library | MIT | firmware/node tests | host unit tests (`pio test -e native`) |
| Own C++ SHA-256, HMAC, HKDF, AES-128 | project code | project | firmware/node/lib/stozher | written here from FIPS 180-4, RFC 2104/5869 and FIPS 197; checked against the standard test vectors and the golden packet vectors; a hardware mbedTLS backend may replace it later |
| TweetNaCl 20140427 (Bernstein, Lange, Schwabe et al.) | library | Public domain | firmware/node/lib/tweetnacl | vendored unmodified; only `crypto_sign_open` is used, to verify Ed25519-signed acknowledgements (docs/control.md); checked against tests/vectors/ack_messages.json |
| Meshtastic firmware (checkout of github.com/meshtastic/firmware, 2.8.x) | third-party firmware, built in a separate git worktree | GPL-3.0 | firmware/tbeam (patch and tap files only) | experiment: STOZHER logic inside the T-Beam's own firmware; the checkout is not vendored; any image built from it is GPL-3.0 |
| pyubx2 | library (dev tool only) | BSD-3-Clause | firmware/node/tools, tests | builds the u-blox UBX frames used as independent test vectors for the C++ parser; not part of the firmware |
| Own C++ UBX reader, epoch builder and detection rules | project code | project | firmware/node/lib/stozher | written here from the u-blox M10 interface description as reflected by pyubx2's message definitions; thresholds are initial values |
| Crespo, Chamorro, Azanza, González (GMV), "New Approach for Jamming and Spoofing Detection Mechanisms for High Accuracy Solutions", Eng. Proc. 126, 8 (2026), CC BY 4.0; Ghanbarzadeh, Soleimani, Soleimani, "GNSS/GPS Spoofing and Jamming Identification Using Machine Learning and Deep Learning", arXiv:2501.02352 | papers (reading only) | CC BY 4.0 / arXiv | docs/papers, firmware/node/lib/stozher/stz_detect | ideas only, no code or data taken: the first (several indicators, three levels nominal / off-nominal / anomaly, so one metric alone does not decide) shaped the corroboration rule in J1; the second is background for the worker-side classifier |
| u-blox UBX-CFG-RST and CFG-MSG frame layouts, resetMode and navBbrMask values (read from pyubx2's message definitions and community driver documentation: ROS `ublox_msgs` CfgRST, Zephyr `ubx_cfg_rst`) | protocol reference | BSD / Apache-2.0 documentation | firmware/node/lib/stozher/stz_ubx, stz_recover | frame bytes checked against pyubx2; the reset mode values still to be confirmed against the u-blox 6 receiver description on the real receiver |
