# Licence note

`stozher-mesh-bridge` links the Meshtastic Python library (GPL-3.0) and is therefore
distributed under GPL-3.0-or-later. It is a separate program: it reads text messages from a
gateway radio and talks to the STOZHER platform only through the public HTTP API
(`POST /v1/ingest/mesh`, `GET /v1/stream/alerts`). No other STOZHER package imports it, so the
rest of the platform is not affected. Encryption products may fall under EU dual-use export
rules outside the EU (Technical Specification, section on licences).
