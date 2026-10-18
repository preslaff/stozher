# Assistant (optional module)

A text assistant that answers questions about the situation and writes situation reports. It is **never in the detection or alert path**
(CLAUDE.md rule 5): alerts are deterministic, and the platform runs with the assistant absent. It is a client of the public `/v1` API like the dashboard.

## How a question is answered

```
dashboard / API client ──POST /v1/assistant/query (responder key)──► API ──► assistant service (loopback, port 8100)
                                                                               │  tool calls with the SAME key
                                                                               ▼
                                                                        /v1 situation, trust, hotspots, events, alerts, hazards…
                                                                               │
                                                                    engine: cloud model (if switched on) or local Ollama
```

- The API forwards the caller's own key, so the assistant can read exactly what the caller can read and nothing more. A tool the key may not call returns an error text.
- The question is not stored. The audit log records `assistant.query` with the key and the mode (`ask` or `report`).
- Tools are read-only and compacted before the model sees them: no node IDs, no raw records, no geometry. `recent_events` reports counts and how many readings had no GNSS position.
  Tools: `situation`, `trust_point`, `trust_history` (expired), `hotspots`, `hotspot_detail`, `recent_events`, `alerts`, `hazards`, `route_trust`.

## What code guarantees (not the model)

| Rule | Where |
|---|---|
| Questions about people, vehicles, units, checkpoints or weapons are refused before the model sees them | `guard.refusal` |
| An answer without a tool call is replaced by "I could not ground an answer"; the model gets one reminder first | `agent.ask` |
| Every answer carries `data_age_s`, `sources`, `sim` and the tools used, computed from the tool results | `agent.Answer` |
| No recent evidence is stated in the answer text ("not an all-clear"), whatever the model wrote | `guard.caveats` |
| Expired history is marked as not current; unverified hazards stay unverified; simulated data prefixes the answer with `[SIMULATED]` | `guard.caveats`, `agent.compose` |
| A failed read is reported as an incomplete answer | `guard.caveats` |

The system prompt repeats these rules, but a prompt is not a guarantee, so the rules above are enforced after the model has spoken.

## Reports

`mode: report` with a `bbox` gathers the facts by code (situation, readings of the last 24 h, expired history, hazards, alerts) and writes them as sentences.
A model, if one is available, only adds a short summary on top, from those facts. With no engine the report is still complete (`engine: "none"`).

## Engines and data leaving the machine

| Engine | When | Data |
|---|---|---|
| Local: Ollama, `qwen2.5:7b-instruct` by default | always available if Ollama runs (offline too) | stays on this machine |
| Cloud: Anthropic Messages API, `claude-sonnet-5-5` | only when `STOZHER_ASSISTANT_CLOUD=on` **and** `ANTHROPIC_API_KEY` are set in the environment | tool results (compacted situation data, role-limited by the key) are sent to the provider |

`STOZHER_ASSISTANT_ENGINE=auto` (default) tries the cloud first when it is on, then falls back to the local engine; `local` and `cloud` force one. The cloud is off by default
and the kit never switches it on. Switching it on is a decision about where situation data goes, so it is the operator's and is made in `infra/.env`.

## Running it

`ASSISTANT=on MESH_PORT=... make kit` starts it with the kit (log `~/.local/state/stozher/assistant.log`), or `make assistant` by hand with `STOZHER_ASSISTANT_URL=http://127.0.0.1:8100` set for the API.
Install Ollama and `ollama pull qwen2.5:7b-instruct` once for the local engine. `/health` lists `assistant` as present while its heartbeat (30 s) is recent.

## Not built

Retrieval over playbooks (pgvector `kb_chunks`), voice, and streaming answers. The dashboard panel is the only client so far.
