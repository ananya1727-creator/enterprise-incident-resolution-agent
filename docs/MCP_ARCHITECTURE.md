# MCP Architecture

Companion to [TOOL_SPEC.md](TOOL_SPEC.md) and [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md). Describes how the simulated tool layer was converted into an MCP-compatible client/server split, and how each mock tool would later be backed by a real system.

## 1. Why this split exists

Up to this point, `agent/orchestrator.py` imported `agent.simulated_tools.SimulatedEnvironment` directly and called its methods in-process. That made the "tool layer" and the "agent" the same Python process with the same import graph — convenient for early development, but not what [TOOL_SPEC.md §8](TOOL_SPEC.md#8-interface-durability-for-future-real-integration) actually promised: tool *contracts* (name, input schema, output schema, risk tier) staying stable while the *implementation* behind them is swapped for a real system.

MCP (Model Context Protocol) is the mechanism that makes that swap real rather than aspirational: the agent talks to a tool server over a protocol boundary (stdio, in this case) instead of an import statement. Swapping the mock "Enterprise Integration Operations" server for a real SAP PO/CPI, ServiceNow, or observability-platform MCP server means pointing the client at a different process — nothing in the agent's code changes.

## 2. Components

```
┌──────────────────────────┐        MCP (stdio, JSON-RPC)        ┌────────────────────────────────┐
│ Agent process             │ ───────────────────────────────────▶│ mcp_server/server.py            │
│  agent/orchestrator.py    │                                      │ "Enterprise Integration Ops"    │
│  agent/reasoning.py       │ ◀───────────────────────────────────│ FastMCP server                  │
│  agent/guardrails.py      │        tool results (JSON)           │                                  │
│  agent/mcp_tool_client.py │                                      │ wraps: agent/simulated_tools.py  │
└──────────────────────────┘                                      │ reads: data/*.json               │
                                                                    └────────────────────────────────┘
```

- **`mcp_server/server.py`** — a `FastMCP` server exposing all 16 requested tools (plus the two High-risk remediation tools, `update_adapter_timeout` / `rotate_certificate`, kept for parity with `docs/TOOL_SPEC.md`). It is a thin transport wrapper: the actual simulation logic is the *same, unmodified* `SimulatedEnvironment` class from `agent/simulated_tools.py` that the pre-MCP orchestrator used directly. Converting to MCP did not require rewriting the simulation — only exposing it over a protocol boundary.
- **`agent/mcp_tool_client.py`** — `MCPToolClient`, a synchronous facade the `Orchestrator` uses as its `env` in place of `SimulatedEnvironment`. It launches the server as a subprocess (`python -m mcp_server.server`) over stdio, and proxies every tool call transparently via `__getattr__` — so `getattr(self.env, "inspect_credentials")(**params)` in the orchestrator becomes an MCP `call_tool` under the hood, with **zero changes to `agent/orchestrator.py`, `agent/reasoning.py`, or `agent/guardrails.py`**.
- **Separation guarantee**: nothing under `agent/` (other than `mcp_tool_client.py` itself) imports `mcp_server`, and nothing under `mcp_server/` imports `agent.orchestrator`, `agent.reasoning`, or `agent.guardrails`. The only shared code is `agent/simulated_tools.py` and `agent/data_store.py`, which the server wraps and the client never touches directly — the agent's reasoning process has no import-level access to the tool implementation at all when run via `--mcp`.

## 3. Running it

```bash
# Direct mode (in-process, no MCP) -- unchanged from before this conversion
python main.py INC-0099

# MCP mode -- agent discovers/calls tools only through the MCP server
python main.py INC-0099 --mcp

# Inspect the server standalone with any MCP client (e.g. an MCP inspector)
python -m mcp_server.server
```

`Orchestrator.__init__` already accepted an `env` override (added for the adversarial eval harness's `ScenarioEnvironment`); `--mcp` simply passes an `MCPToolClient` instance instead. The adversarial eval harness (`scripts/run_eval.py`) is unaffected — it still uses `ScenarioEnvironment` directly, since its job is precision-scripted testing, not integration-layer plumbing.

## 4. Tool coverage

All 16 tools requested, plus 2 kept for parity with the full `docs/TOOL_SPEC.md` catalog:

| Tool | Category |
|---|---|
| `get_incident`, `get_interface_metadata`, `search_logs`, `get_recent_changes` | Observation |
| `search_historical_incidents`, `retrieve_runbook`, `test_connectivity`, `inspect_credentials`, `inspect_certificate_status`, `compare_with_previous_successful_run`* | Diagnosis |
| `retry_message`*, `restart_interface`*, `refresh_connection`, `update_adapter_timeout`*, `rotate_certificate`*, `create_escalation`, `update_incident` | Action |
| `verify_interface_health`, `verify_message_processing` | Verification |

(*present in `docs/TOOL_SPEC.md` but not explicitly re-listed in the MCP conversion request — included so the server's coverage matches the full agent tool catalog rather than a subset.)

One simulation-only wart worth naming: `verify_interface_health`/`verify_message_processing` and the remediation tools carry a `will_fix_hint` / `_will_fix` field that only exists because the simulation needs a way to encode "this incident's ground truth says this action does/doesn't actually fix the symptom." A real backend would never expose this parameter — the real system's post-action state (reachable, degraded, error-free) *is* the signal, with no hint needed. This is called out explicitly here so it isn't mistaken for part of the durable tool contract.

## 5. Replacing the mock with a real backend

Because the MCP boundary is the only thing the agent depends on, replacing the mock server means writing a **new MCP server with the same tool names, input schemas, and output schemas**, backed by real system calls instead of fixture data. Four illustrative examples:

### ServiceNow (incident/ticketing tools)
- `get_incident` → ServiceNow Table API `GET /api/now/table/incident/{sys_id}`, mapped into the same output schema (`incident_id`, `reported_at`, `reporter`, `interface_id`, `symptom_description`, `reported_severity`, `business_context`).
- `update_incident` / `create_escalation` → `PATCH`/`POST` against the same table API, or an Incident/Problem record creation call.
- Risk/approval metadata (`docs/TOOL_SPEC.md` §0, §6) stays in the *agent's* static registry (`agent/registry.py`), not in ServiceNow — the ticketing system is a system of record, not a policy source.

### SAP PO / SAP CPI (the platform this whole project simulates)
- `search_logs` → SAP PO Message Monitoring API / CPI's Message Processing Log OData service, mapped into the same `{entries, total_matched}` shape.
- `test_connectivity`, `inspect_certificate_status`, `inspect_credentials` → SAP's Communication Channel Monitoring and NWA (NetWeaver Administrator) key-store APIs, or CPI's Security Material API for certificate/credential status — again, never returning secret values, matching the durable "no tool ever returns a real secret" constraint from `docs/TOOL_SPEC.md` §7.
- `retry_message`, `restart_interface`, `refresh_connection`, `update_adapter_timeout`, `rotate_certificate` → SAP PO's Adapter Engine / CPI's Runtime API calls for reprocessing messages, restarting channels, and managing keystores — the exact operations these tool names already describe.

### An observability platform (Datadog / Splunk / Dynatrace-style)
- `search_logs` → the platform's log search API (e.g., Splunk's `/services/search/jobs`), with the agent's `time_range`/`filter` params mapped to that platform's query DSL.
- `test_connectivity` → a synthetic-monitoring/APM check result lookup instead of a live probe.
- `get_recent_changes` → the platform's deployment/change-event stream (e.g., Datadog Events API) filtered to the interface's tagged resources.

### A generic integration platform (MuleSoft / Boomi / Azure Logic Apps)
- Most Action-category tools map directly to that platform's runtime management API (restart a flow/integration, resubmit a message, rotate a connector's credential) — the tool *names* in this project were deliberately chosen to be vendor-neutral (`docs/TOOL_SPEC.md` §8) specifically so this mapping doesn't require renaming anything on the agent side.

In every case, the migration path is the same three steps:
1. Write a new MCP server (or extend `mcp_server/server.py` with real adapters behind feature flags) exposing the identical tool names/schemas.
2. Point `MCPToolClient`'s `server_command` at the new server's entrypoint (or, for a remote server, swap the stdio transport for MCP's HTTP/SSE transport — `mcp_tool_client.py` isolates that change to one class).
3. Re-confirm the static risk-tier/approval-requirement registry (`agent/registry.py`) still matches reality for the new backend, per the durability principle in `docs/TOOL_SPEC.md` §8 — risk metadata is never re-derived from the implementation, only re-*validated* against it.

Nothing in `agent/reasoning.py`, `agent/guardrails.py`, or `agent/orchestrator.py` needs to change for any of this.
