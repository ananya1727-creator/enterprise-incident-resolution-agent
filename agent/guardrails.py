"""
Guardrail Layer (architecture 1.6). Deterministic, non-LLM checks the
orchestrator runs on every proposed action before it reaches Tool Execution.
The agent proposes; this module disposes. Ceilings mirror
docs/AGENT_ARCHITECTURE.md section 7.
"""
from dataclasses import dataclass

from .models import IncidentState
from . import risk_policy

MAX_INVESTIGATION_CYCLES = 8
MAX_REMEDIATION_ATTEMPTS = 2
MAX_VERIFICATION_RETRIES_PER_REMEDIATION = 1
SLA_REMAINING_FORCE_DECIDE_THRESHOLD = 0.20
ACT_CONFIDENCE_THRESHOLD = 0.70


@dataclass
class GuardrailDecision:
    allowed: bool
    reason: str = ""
    forced_transition: str = ""  # "" | "DECIDING" | "ESCALATED"


def check_diagnostic_proposal(state: IncidentState) -> GuardrailDecision:
    if state.investigation_cycle_count >= MAX_INVESTIGATION_CYCLES:
        return GuardrailDecision(False, "investigation cycle ceiling reached", "DECIDING")
    if state.sla_remaining_fraction() < SLA_REMAINING_FORCE_DECIDE_THRESHOLD:
        return GuardrailDecision(False, "SLA remaining below force-decide threshold", "DECIDING")
    return GuardrailDecision(True)


def check_hypothesis_has_direct_evidence(state: IncidentState, targets_hypothesis_id: str) -> GuardrailDecision:
    """
    Structural enforcement of "historical resolutions must never be treated
    as guaranteed truth": a hypothesis may not be acted on unless at least
    one of its supporting evidence items came from a DIRECT diagnostic tool
    call against THIS incident, not solely from search_historical_incidents
    corroboration. This does not depend on the confidence-weighting math in
    agent/reasoning.py being correct -- it is a second, independent check the
    orchestrator applies before any remediation proposal is allowed through,
    the same layered-enforcement pattern used for risk-tier approval
    (agent/risk_policy.py).
    """
    hyp = state.get_hypothesis(targets_hypothesis_id)
    if hyp is None:
        return GuardrailDecision(False, "targeted hypothesis not found in investigation memory", "ESCALATED")
    direct_evidence_ids = {e.evidence_id for e in state.evidence if e.source != "search_historical_incidents"}
    if not any(eid in direct_evidence_ids for eid in hyp.supporting_evidence_ids):
        return GuardrailDecision(
            False,
            f"hypothesis '{hyp.category}' is supported only by historical/case-memory precedent, with no "
            f"direct diagnostic evidence from this incident -- refusing to act on unvalidated precedent",
            "",
        )
    return GuardrailDecision(True)


def check_remediation_proposal(state: IncidentState, tool_name: str,
                                targets_hypothesis_id: str, confidence: float) -> GuardrailDecision:
    if state.remediation_attempt_count >= MAX_REMEDIATION_ATTEMPTS:
        return GuardrailDecision(False, "remediation attempt ceiling reached", "ESCALATED")
    if confidence < ACT_CONFIDENCE_THRESHOLD:
        return GuardrailDecision(False, "confidence below acting threshold; remediation must cite a "
                                        "hypothesis above threshold", "")
    direct_evidence_check = check_hypothesis_has_direct_evidence(state, targets_hypothesis_id)
    if not direct_evidence_check.allowed:
        return direct_evidence_check
    if risk_policy.requires_approval(tool_name):
        return GuardrailDecision(
            True,
            f"'{tool_name}' is risk tier '{risk_policy.risk_tier(tool_name)}' and requires approval "
            f"under the current risk policy (config/risk_policy.json) -- routed to Human Approval Interface.",
            "AWAITING_APPROVAL",
        )
    return GuardrailDecision(True)


def check_verification_retry(state: IncidentState) -> GuardrailDecision:
    if state.verification_retry_count >= MAX_VERIFICATION_RETRIES_PER_REMEDIATION:
        return GuardrailDecision(False, "verification retry ceiling reached for this remediation", "ESCALATED")
    if state.remediation_attempt_count >= MAX_REMEDIATION_ATTEMPTS:
        return GuardrailDecision(False, "remediation attempt ceiling reached", "ESCALATED")
    return GuardrailDecision(True)
