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
