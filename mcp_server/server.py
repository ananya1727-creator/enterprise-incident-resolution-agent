"""
Mock MCP server: "Enterprise Integration Operations Platform".

This is the ONLY place the agent's tool implementations are allowed to live.
It exposes exactly the tool surface defined in docs/TOOL_SPEC.md over the
Model Context Protocol (stdio transport), backed entirely by the simulated
fixture dataset under data/ (via agent.simulated_tools.SimulatedEnvironment,
reused unchanged -- this server is a thin MCP transport wrapper around the
same simulation logic, not a reimplementation of it).

Run standalone (for inspection/debugging with any MCP client):
    python -m mcp_server.server

The agent never imports this module's internals; it talks to it exclusively
through an MCP ClientSession (see agent/mcp_tool_client.py), the same way it
would talk to a real SAP PO/CPI, ServiceNow, or observability MCP server.
See docs/MCP_ARCHITECTURE.md for how a real backend would replace this.
"""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from agent.data_store import DataStore
from agent.simulated_tools import SimulatedEnvironment, ToolError

mcp = FastMCP(
    name="enterprise-integration-ops",
    instructions=(
        "Simulated Enterprise Integration Operations platform (SAP PO/CPI-style). "
        "Exposes incident, interface-metadata, log, diagnostic, remediation, and "
        "verification tools for integration-incident investigation. All tools "
        "operate on incident_id-scoped simulated state; call get_incident first "
        "to establish which incident/interface is under investigation."
    ),
)

_store = DataStore()
# One SimulatedEnvironment per incident under active investigation. A real
# backend wouldn't need this -- it would just be stateless calls against a
# live system -- but the fixture oracle needs to remember which incident's
# ground truth a session is currently probing.
_envs: dict[str, SimulatedEnvironment] = {}


def _env_for(incident_id: str) -> SimulatedEnvironment:
    if incident_id not in _envs:
        _envs[incident_id] = SimulatedEnvironment(_store, incident_id)
    return _envs[incident_id]


def _iface_env(interface_id: str) -> Optional[SimulatedEnvironment]:
    """Find (or lazily create) the env whose interface_id matches, used by
    tools that take interface_id rather than incident_id."""
    for env in _envs.values():
        if env.interface_id == interface_id:
            return env
    # No incident context established yet for this interface -- resolve via
    # the store's incident list so a diagnostic call can still be answered.
    for inc in _store.incidents:
        if inc["interface_id"] == interface_id:
            return _env_for(inc["incident_id"])
    return None


# ---------------------------------------------------------------------
# OBSERVATION
# ---------------------------------------------------------------------

@mcp.tool()
def get_incident(incident_id: str) -> dict:
    """Retrieve the full incident record as originally raised."""
    env = _env_for(incident_id)
    return env.get_incident(incident_id)


@mcp.tool()
def get_interface_metadata(interface_id: str) -> dict:
    """Retrieve interface topology, criticality, ownership, and fragility flags."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.get_interface_metadata(interface_id)


@mcp.tool()
def search_logs(interface_id: str, time_range: dict, filter: Optional[str] = None,
                max_results: int = 50) -> dict:
    """Query simulated message/error logs for an interface within a time window."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.search_logs(interface_id, time_range, filter, max_results)


@mcp.tool()
def get_recent_changes(interface_id: str, lookback_window: str = "72h") -> dict:
    """Retrieve recent configuration/deployment changes for an interface."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.get_recent_changes(interface_id, lookback_window)


# ---------------------------------------------------------------------
# DIAGNOSIS
# ---------------------------------------------------------------------

@mcp.tool()
def search_historical_incidents(symptom_signature: dict, max_results: int = 5) -> dict:
    """Search case memory for prior incidents with a similar symptom signature."""
    return {"matches": _store.search_historical_incidents(symptom_signature, max_results)}


@mcp.tool()
def retrieve_runbook(root_cause_category: Optional[str] = None,
                      interface_id: Optional[str] = None) -> dict:
    """Retrieve a static runbook document matching a root-cause category, if one exists."""
    rb = _store.runbook_for_category(root_cause_category)
    if not rb:
        return {"runbook_found": False, "runbook_id": None, "title": None, "steps": [], "last_reviewed_at": None}
    return {"runbook_found": True, "runbook_id": rb["runbook_id"], "title": rb["title"],
             "steps": rb["steps"], "last_reviewed_at": rb.get("last_reviewed_at")}


@mcp.tool()
def test_connectivity(interface_id: str, endpoint: str = "both") -> dict:
    """Simulated reachability/latency probe between source and target endpoints."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.test_connectivity(interface_id, endpoint)


@mcp.tool()
def inspect_credentials(interface_id: str) -> dict:
    """Check adapter credential status (expiry/lock/revocation) -- never returns secret values."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.inspect_credentials(interface_id)


@mcp.tool()
def inspect_certificate_status(interface_id: str) -> dict:
    """Check TLS/client certificate validity and expiry for the interface's adapter."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.inspect_certificate_status(interface_id)


@mcp.tool()
def compare_with_previous_successful_run(interface_id: str, failing_message_id: str) -> dict:
    """Diff the failing message/run against the interface's last known-good run."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.compare_with_previous_successful_run(interface_id, failing_message_id)


# ---------------------------------------------------------------------
# ACTION (remediation + incident bookkeeping)
# ---------------------------------------------------------------------

@mcp.tool()
def retry_message(interface_id: str, message_id: str) -> dict:
    """Resend/reprocess a single specific failed message. Risk: Low."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.retry_message(interface_id, message_id)


@mcp.tool()
def restart_interface(interface_id: str, channel_id: str) -> dict:
    """Restart the messaging channel/adapter instance. Risk: Medium."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.restart_interface(interface_id, channel_id)


@mcp.tool()
def refresh_connection(interface_id: str) -> dict:
    """Force re-authentication/refresh of the adapter's connection pool or token. Risk: Medium."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.refresh_connection(interface_id)


@mcp.tool()
def update_adapter_timeout(interface_id: str, new_timeout_ms: int) -> dict:
    """Modify the adapter's configured timeout value. Risk: High -- requires approval upstream of this call."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.update_adapter_timeout(interface_id, new_timeout_ms)


@mcp.tool()
def rotate_certificate(interface_id: str) -> dict:
    """Trigger certificate renewal/rotation. Risk: High -- requires approval upstream of this call."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.rotate_certificate(interface_id)


@mcp.tool()
def reprocess_message_batch(interface_id: str, batch_id: str, message_count: int) -> dict:
    """Bulk delete/reprocess a message batch. Risk: High -- requires approval upstream of this call."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.reprocess_message_batch(interface_id, batch_id, message_count)


@mcp.tool()
def update_routing_rule(interface_id: str, rule_id: str, new_target: str) -> dict:
    """Change message routing for an interface. Risk: High -- requires approval upstream of this call."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.update_routing_rule(interface_id, rule_id, new_target)


@mcp.tool()
def create_escalation(incident_id: str, reason: str, summary_for_human: str,
                       leading_hypothesis_ids: list, unresolved_questions: list) -> dict:
    """Formally hand off the incident to a human with a structured brief."""
    env = _env_for(incident_id)
    return env.create_escalation(incident_id, reason, summary_for_human,
                                  leading_hypothesis_ids, unresolved_questions)


@mcp.tool()
def update_incident(incident_id: str, status: str, diagnosis_summary: str,
                     evidence_refs: list, actions_taken: list,
                     outcome: Optional[str] = None) -> dict:
    """Write diagnosis, evidence summary, and outcome back to the incident record."""
    env = _env_for(incident_id)
    return env.update_incident(incident_id, status, diagnosis_summary, evidence_refs,
                                actions_taken, outcome)


# ---------------------------------------------------------------------
# VERIFICATION
# ---------------------------------------------------------------------

@mcp.tool()
def verify_interface_health(interface_id: str, baseline_snapshot_id: str,
                             will_fix_hint: Optional[bool] = None) -> dict:
    """Re-check overall interface/channel operational status after a remediation."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.verify_interface_health(interface_id, baseline_snapshot_id, will_fix_hint)


@mcp.tool()
def verify_message_processing(interface_id: str, message_id: str, expected_end_state: str,
                               will_fix_hint: Optional[bool] = None) -> dict:
    """Check whether a specific message actually completed processing end-to-end."""
    env = _iface_env(interface_id)
    if env is None:
        raise ToolError(f"no known context for interface {interface_id}")
    return env.verify_message_processing(interface_id, message_id, expected_end_state, will_fix_hint)


if __name__ == "__main__":
    mcp.run(transport="stdio")
