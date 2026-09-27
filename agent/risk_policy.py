"""
Risk-based autonomy framework (enforceable tool-layer policy, not a prompt
instruction). This module is the single place that decides whether a tool
call may execute autonomously or must be routed through the Human Approval
Interface -- the Guardrail Layer (agent/guardrails.py) and the Orchestrator
(agent/orchestrator.py) both call into this module rather than each
re-implementing the rule, and neither the agent's reasoning code
(agent/reasoning.py) nor any tool implementation can see or influence it.

Rules (fixed, not configurable per incident or by the agent):
  LOW    -> always autonomous, never gated.
  MEDIUM -> requires approval UNLESS explicitly configured safe for this
            environment in config/risk_policy.json. The override list is
            read from disk once at import time; the agent has no tool that
            can read or write that file.
  HIGH   -> ALWAYS requires human approval. There is no override mechanism
            for HIGH at all -- see requires_approval() below, which does not
            even consult the config file for this tier.
"""
import json
from pathlib import Path
from typing import Optional

from .registry import spec_for

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "risk_policy.json"


def _load_medium_overrides() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return data.get("medium_risk_safe_overrides", {})


# Loaded once at import time -- a deliberate choice: the policy is fixed for
# the lifetime of the process, not something a running incident investigation
# (or the agent) can alter mid-run.
_MEDIUM_OVERRIDES = _load_medium_overrides()


class ApprovalBypassError(Exception):
    """Raised if code anywhere attempts to execute a gated tool without a
    valid, granted approval record. This should be structurally unreachable
    -- it exists as a hard, fail-loud invariant check, not a normal control
    path."""


def risk_tier(tool_name: str) -> str:
    return spec_for(tool_name).risk_tier


def requires_approval(tool_name: str) -> bool:
    """
    The single source of truth for "does this tool call need a human
    approval record before it may execute." Pure function of (a) the static
    registry's risk tier and (b) the read-only override config -- never of
    anything the agent said, proposed, or claimed.
    """
    tier = risk_tier(tool_name)
    if tier == "High":
        return True  # no override path exists for HIGH, by design
    if tier == "Medium":
        return not _MEDIUM_OVERRIDES.get(tool_name, False)
    return False  # Low / None


def is_autonomous(tool_name: str) -> bool:
    return not requires_approval(tool_name)


def assert_approved_if_required(tool_name: str, approval_decision: Optional[str]):
    """
    Hard invariant check called immediately before ANY remediation tool
    executes (agent/orchestrator.py `_act`), independent of and in addition
    to the guardrail routing that should already have prevented reaching
    this point. This is the "the agent must never bypass approval"
    guarantee made structurally enforceable rather than merely documented:
    even a future bug that let a proposal skip the guardrail's routing logic
    would still be caught here before any tool actually runs.
    """
    if requires_approval(tool_name) and approval_decision != "approved":
        raise ApprovalBypassError(
            f"Refusing to execute '{tool_name}': risk tier '{risk_tier(tool_name)}' requires a "
            f"granted human approval, but approval_decision={approval_decision!r}."
        )
