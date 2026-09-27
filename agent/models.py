"""
State model for the Incident Resolution Agent.
Mirrors docs/AGENT_ARCHITECTURE.md section 2 exactly: IncidentState, Evidence,
Hypothesis, DiagnosticAction, RemediationAction, VerificationResult, Escalation,
ResolutionRecord. Append-only history, mutable "current" pointers only.
"""
from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Stage(str, Enum):
    OBSERVE = "OBSERVE"
    INVESTIGATING = "INVESTIGATING"
    DECIDING = "DECIDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class Criticality(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


SLA_WINDOW_HOURS = {
    Criticality.P1: 4,
    Criticality.P2: 8,
    Criticality.P3: 24,
    Criticality.P4: 72,
}

RISK_TIERS = ("None", "Low", "Medium", "High")


class HypothesisStatus(str, Enum):
    ACTIVE = "Active"
    DEMOTED = "Demoted"
    CONFIRMED = "Confirmed"
    REFUTED = "Refuted"


class ActionStatus(str, Enum):
    PROPOSED = "Proposed"
    EXECUTED = "Executed"
    REJECTED_BY_GUARDRAIL = "RejectedByGuardrail"
    AWAITING_APPROVAL = "AwaitingApproval"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class VerificationOutcome(str, Enum):
    PASSED = "Passed"
    FAILED = "Failed"
    INCONCLUSIVE = "Inconclusive"


class EscalationReason(str, Enum):
    LOW_CONFIDENCE = "LowConfidence"
    RISK_TOO_HIGH = "RiskTooHigh"
    CYCLES_EXHAUSTED = "CyclesExhausted"
    RETRIES_EXHAUSTED = "RetriesExhausted"
    APPROVAL_TIMEOUT = "ApprovalTimeout"
    CONTRADICTORY_EVIDENCE = "ContradictoryEvidence"
    # Extension beyond the architecture doc's base enum: some root-cause
    # categories (spec: mapping_error, malformed_payload, sftp_credential_failure,
    # duplicate_message) have NO autonomous or approvable remediation tool at all
    # (see docs/TOOL_SPEC.md). Forcing those into LOW_CONFIDENCE/RISK_TOO_HIGH would
    # mislabel a confidently-diagnosed-but-untreatable case as an uncertain one.
    NO_AUTONOMOUS_REMEDIATION = "NoAutonomousRemediation"
    # A human explicitly rejected the only available remediation for every
    # sufficiently-confident hypothesis. Distinct from LOW_CONFIDENCE (the
    # agent wasn't uncertain -- a person overrode it) and from
    # NO_AUTONOMOUS_REMEDIATION (a tool existed; it was declined, not absent).
    HUMAN_REJECTED_REMEDIATION = "HumanRejectedRemediation"


@dataclass
class Evidence:
    source: str
    content: dict
    summary: str
    produced_by_action_id: Optional[str] = None
    evidence_id: str = field(default_factory=lambda: new_id("EVID"))
    timestamp: str = field(default_factory=now_iso)


@dataclass
class ConfidenceDelta:
    evidence_id: str
    prior_confidence: float
    new_confidence: float
    reason: str
    timestamp: str = field(default_factory=now_iso)


@dataclass
class Hypothesis:
    category: str
    statement: str
    confidence: float = 0.05
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    supporting_evidence_ids: list = field(default_factory=list)
    contradicting_evidence_ids: list = field(default_factory=list)
    confidence_history: list = field(default_factory=list)
    hypothesis_id: str = field(default_factory=lambda: new_id("HYP"))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def revise(self, evidence_id: str, new_confidence: float, reason: str, supports: bool):
        new_confidence = max(0.0, min(1.0, new_confidence))
        self.confidence_history.append(
            ConfidenceDelta(evidence_id, self.confidence, new_confidence, reason)
        )
        self.confidence = new_confidence
        if supports and evidence_id not in self.supporting_evidence_ids:
            self.supporting_evidence_ids.append(evidence_id)
        elif not supports and evidence_id not in self.contradicting_evidence_ids:
            self.contradicting_evidence_ids.append(evidence_id)
        self.updated_at = now_iso()


@dataclass
class DiagnosticAction:
    tool_name: str
    params: dict
    targets_hypothesis_ids: list
    rationale: str
    is_verification: bool = False
    verifies_remediation_id: Optional[str] = None
    status: ActionStatus = ActionStatus.PROPOSED
    rejection_reason: Optional[str] = None
    result_evidence_id: Optional[str] = None
    action_id: str = field(default_factory=lambda: new_id("DIAG"))
    timestamp: str = field(default_factory=now_iso)


@dataclass
class RemediationAction:
    tool_name: str
    params: dict
    targets_hypothesis_id: str
    hypothesis_confidence_at_decision: float
    risk_tier: str
    rationale: str
    status: ActionStatus = ActionStatus.PROPOSED
    approval_request_id: Optional[str] = None
    action_id: str = field(default_factory=lambda: new_id("REM"))
    timestamp: str = field(default_factory=now_iso)


@dataclass
class ApprovalRequest:
    remediation_action_id: str
    risk_tier: str
    justification: str
    decision: Optional[str] = None  # "approved" | "rejected" | None (pending)
    note: Optional[str] = None
    approval_request_id: str = field(default_factory=lambda: new_id("APR"))
    created_at: str = field(default_factory=now_iso)
    decided_at: Optional[str] = None


@dataclass
class VerificationResult:
    remediation_action_id: str
    diagnostic_action_id: str
    outcome: VerificationOutcome
    comparison: dict
    verification_id: str = field(default_factory=lambda: new_id("VER"))
    timestamp: str = field(default_factory=now_iso)


@dataclass
class Escalation:
    reason: EscalationReason
    summary_for_human: str
    leading_hypotheses: list
    actions_attempted: list
    unresolved_questions: list
    escalation_id: str = field(default_factory=lambda: new_id("ESC"))
    timestamp: str = field(default_factory=now_iso)


@dataclass
class ResolutionRecord:
    symptom_signature: dict
    root_cause: str
    root_cause_statement: str
    remediation_tool: Optional[str]
    remediation_params_template: Optional[dict]
    verification_signature: dict
    outcome: str  # "Resolved" | "Escalated"
    tool_call_count: int
    human_feedback: str  # always "Unreviewed" for freshly-created records
    source_incident_id: str
    record_id: str = field(default_factory=lambda: new_id("RES"))
    created_at: str = field(default_factory=now_iso)


@dataclass
class DecisionLogEntry:
    """
    Every MAJOR decision (not every tool call) is logged with these six
    required fields, per the implementation task: decision, evidence,
    alternatives considered, confidence, reason, next action.
    """
    decision: str
    evidence_ids: list
    alternatives_considered: list
    confidence: Optional[float]
    reason: str
    next_action: str
    timestamp: str = field(default_factory=now_iso)


@dataclass
class ToolCallLogEntry:
    """Every tool call, without exception, is logged here (architecture 1.9)."""
    tool_name: str
    params: dict
    result_summary: str
    status: str
    timestamp: str = field(default_factory=now_iso)


@dataclass
class IncidentState:
    incident_id: str
    interface_id: str
    stage: Stage = Stage.OBSERVE
    criticality: Optional[Criticality] = None
    sla_deadline: Optional[str] = None
    reported_at: Optional[str] = None

    evidence: list = field(default_factory=list)
    hypotheses: list = field(default_factory=list)
    diagnostic_actions: list = field(default_factory=list)
    remediation_actions: list = field(default_factory=list)
    verification_results: list = field(default_factory=list)
    approval_requests: list = field(default_factory=list)

    investigation_cycle_count: int = 0
    remediation_attempt_count: int = 0
    verification_retry_count: int = 0
    # Tool names a human has explicitly rejected for THIS incident via the
    # Human Approval Center. Checked by reasoning.decide_act_or_escalate so a
    # rejected action is never silently re-proposed -- see agent/orchestrator.py
    # `_route_to_approval`'s rejection branch, the only writer of this set.
    rejected_tool_names: set = field(default_factory=set)

    escalation: Optional[Escalation] = None
    resolution: Optional[ResolutionRecord] = None

    decision_log: list = field(default_factory=list)
    tool_call_log: list = field(default_factory=list)

    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # Simulated wall clock, minutes elapsed since the incident was reported.
    # Fixture incidents are historical (reported_at may be months in the
    # past), so SLA-remaining must be judged relative to a simulated
    # investigation clock advancing per tool call, not real datetime.utcnow().
    simulated_elapsed_minutes: float = 0.0

    # --- helpers -----------------------------------------------------

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Hypothesis]:
        return next((h for h in self.hypotheses if h.hypothesis_id == hypothesis_id), None)

    def active_hypotheses(self) -> list:
        return sorted(
            (h for h in self.hypotheses if h.status == HypothesisStatus.ACTIVE),
            key=lambda h: h.confidence,
            reverse=True,
        )

    def top_hypothesis(self) -> Optional[Hypothesis]:
        active = self.active_hypotheses()
        return active[0] if active else None

    def add_evidence(self, evidence: Evidence) -> Evidence:
        self.evidence.append(evidence)
        self.updated_at = now_iso()
        return evidence

    def log_decision(self, decision: str, evidence_ids: list, alternatives_considered: list,
                      confidence: Optional[float], reason: str, next_action: str):
        self.decision_log.append(
            DecisionLogEntry(decision, evidence_ids, alternatives_considered, confidence, reason, next_action)
        )

    def log_tool_call(self, tool_name: str, params: dict, result_summary: str, status: str):
        self.tool_call_log.append(ToolCallLogEntry(tool_name, params, result_summary, status))

    def advance_clock(self, minutes: float):
        self.simulated_elapsed_minutes += minutes

    def sla_remaining_fraction(self) -> float:
        if not self.sla_deadline or not self.reported_at:
            return 1.0
        total_minutes = (datetime.fromisoformat(self.sla_deadline.rstrip("Z"))
                          - datetime.fromisoformat(self.reported_at.rstrip("Z"))).total_seconds() / 60.0
        if total_minutes <= 0:
            return 0.0
        remaining = total_minutes - self.simulated_elapsed_minutes
        return max(0.0, min(1.0, remaining / total_minutes))
