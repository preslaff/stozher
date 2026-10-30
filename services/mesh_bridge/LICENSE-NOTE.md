# Licence note

`stozher-mesh-bridge` links the Meshtastic Python library (GPL-3.0) and is therefore
distributed under GPL-3.0-or-later. It is a separate program: it reads text messages from a
gateway radio and talks to the STOZHER platform only through the public HTTP API
(`POST /v1/ingest/mesh`, `GET /v1/stream/alerts`). No other STOZHER package imports it at run time, so
the rest of the platform is not affected. Only the API's test suite imports two of its modules
(`forwarder`, `protocol`; neither imports the Meshtastic library) to run a bridge against the real API
in `services/api/tests/test_mesh_bridge.py`; tests are not distributed with the platform. Encryption products may fall under EU dual-use export
rules outside the EU (Technical Specification, section on licences).
