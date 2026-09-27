"""
Static tool registry: name -> (category, read/write, risk tier). This is
metadata, never derived from the LLM/agent, per docs/TOOL_SPEC.md section 8
and docs/AGENT_ARCHITECTURE.md section 1.6. Swapping a simulated tool
implementation for a real one later means changing only
agent/simulated_tools.py, never this registry's risk tier without a
deliberate, separate review.

Risk tier here is the ONLY thing this module decides. Whether a given risk
tier actually requires human approval for a given tool is a separate,
explicit policy decision -- see agent/risk_policy.py -- because MEDIUM-risk
tools require approval by default but may be explicitly configured safe for
a given environment; that override is data (config/risk_policy.json), not
something this static registry or the agent itself can toggle.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: str  # observation | diagnosis | action | verification
    read_write: str  # READ | WRITE
    risk_tier: str  # None | Low | Medium | High


TOOL_REGISTRY = {
    t.name: t for t in [
        # --- LOW: read-only observation/diagnosis tools. No side effects on
        # any system -- always autonomous, never gated. ---------------------
        ToolSpec("get_incident", "observation", "READ", "Low"),
        ToolSpec("get_interface_metadata", "observation", "READ", "Low"),
        ToolSpec("search_logs", "observation", "READ", "Low"),
        ToolSpec("get_recent_changes", "observation", "READ", "Low"),
        ToolSpec("test_connectivity", "diagnosis", "READ", "Low"),
        ToolSpec("inspect_certificate_status", "diagnosis", "READ", "Low"),
        ToolSpec("inspect_credentials", "diagnosis", "READ", "Low"),
        ToolSpec("compare_with_previous_successful_run", "diagnosis", "READ", "Low"),
        ToolSpec("search_historical_incidents", "diagnosis", "READ", "Low"),
        ToolSpec("retrieve_runbook", "diagnosis", "READ", "Low"),
        ToolSpec("verify_interface_health", "verification", "READ", "Low"),
        ToolSpec("verify_message_processing", "verification", "READ", "Low"),
        # Bookkeeping/handoff actions: they write to the incident record, not
        # to the interface/system under investigation -- no technical side
        # effect on the thing being diagnosed, so LOW like the read tools.
        ToolSpec("create_escalation", "action", "WRITE", "Low"),
        ToolSpec("update_incident", "action", "WRITE", "Low"),

        # --- MEDIUM: reversible but affects live system/channel state.
        # Requires approval BY DEFAULT; may be overridden per tool via
        # config/risk_policy.json for a given (simulated) environment. -------
        ToolSpec("retry_message", "action", "WRITE", "Medium"),
        ToolSpec("restart_interface", "action", "WRITE", "Medium"),
        ToolSpec("refresh_connection", "action", "WRITE", "Medium"),

        # --- HIGH: persistent config/security change, destructive, or
        # affects routing/credentials -- ALWAYS requires approval, no
        # override is possible for this tier (see risk_policy.py). ----------
        ToolSpec("update_adapter_timeout", "action", "WRITE", "High"),
        ToolSpec("rotate_certificate", "action", "WRITE", "High"),
        ToolSpec("reprocess_message_batch", "action", "WRITE", "High"),
        ToolSpec("update_routing_rule", "action", "WRITE", "High"),
    ]
}


def spec_for(tool_name: str) -> ToolSpec:
    return TOOL_REGISTRY[tool_name]
