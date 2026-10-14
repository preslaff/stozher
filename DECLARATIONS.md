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
| OpenStreetMap raster tiles | dataset | ODbL (© OpenStreetMap contributors) | web/dashboard | dev default only, subject to the OSM tile usage policy; the field kit uses a local PMTiles extract |
| Meshtastic Python library (+ pypubsub) | library | GPL-3.0 | services/mesh_bridge (optional `radio` extra) | bridge is GPL-3.0-or-later and isolated, HTTP only |
| httpx | library | BSD-3 | simulator, mesh_bridge, tests | |
| Ed25519 via `cryptography` | library feature | Apache-2.0 / BSD | stozher_core.control, services/api | acknowledgement signatures; golden vectors cross-checked with the OpenSSL CLI |
| Sentinel-1 GRD (processed on CDSE through openEO), Copernicus DEM GLO-30 | datasets | Copernicus Sentinel data terms (free, attribution required) | spikes/eo_flood | flood-detection spike; attribution in docs/spikes/eo-flood.md |
| Copernicus EMS rapid-mapping products (EMSR692 AOI 05) | dataset | Copernicus EMS terms (free, attribution) | spikes/eo_flood | validation reference only, not redistributed |
| OpenStreetMap roads via the Overpass API | dataset | ODbL | spikes/eo_flood | road overlay |
| openeo (Python client), rasterio, scipy, shapely, pyproj, geopandas, pyogrio, matplotlib, pillow | libraries | Apache-2.0 / BSD / MIT | spikes/eo_flood (isolated script environments) | |
| PlatformIO Core, Espressif 32 platform, Arduino-ESP32 framework | tooling | Apache-2.0 / LGPL-2.1 | firmware/node | build and flash; the Arduino core is linked into the node image |
| Unity (ThrowTheSwitch) | library | MIT | firmware/node tests | host unit tests (`pio test -e native`) |
| Own C++ SHA-256, HMAC, HKDF, AES-128 | project code | project | firmware/node/lib/stozher | written here from FIPS 180-4, RFC 2104/5869 and FIPS 197; checked against the standard test vectors and the golden packet vectors; a hardware mbedTLS backend may replace it later |
| TweetNaCl 20140427 (Bernstein, Lange, Schwabe et al.) | library | Public domain | firmware/node/lib/tweetnacl | vendored unmodified; only `crypto_sign_open` is used, to verify Ed25519-signed acknowledgements (docs/control.md); checked against tests/vectors/ack_messages.json |
| pyubx2 | library (dev tool only) | BSD-3-Clause | firmware/node/tools, tests | builds the u-blox UBX frames used as independent test vectors for the C++ parser; not part of the firmware |
| Own C++ UBX reader, epoch builder and detection rules | project code | project | firmware/node/lib/stozher | written here from the u-blox M10 interface description as reflected by pyubx2's message definitions; thresholds are initial values |
