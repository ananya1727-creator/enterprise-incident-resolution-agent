"""
Orchestrator: runs the OBSERVE -> INVESTIGATING -> DECIDING ->
(AWAITING_APPROVAL) -> ACTING -> VERIFYING -> RESOLVED/ESCALATED state
machine (architecture section 3), dispatching every proposed action through
the Guardrail Layer before Tool Execution, and applying the uniform
state-update rule (architecture section 4) after every tool call.
"""
import json
import time
from pathlib import Path
from typing import Callable, Optional

from . import guardrails, reasoning
from .data_store import DataStore
from .models import (
    IncidentState, Stage, Criticality, SLA_WINDOW_HOURS, Evidence,
    ApprovalRequest, ActionStatus, VerificationResult, VerificationOutcome,
    Escalation, ResolutionRecord, EscalationReason,
)
from . import risk_policy
from .memory import ResolutionMemory
from .simulated_tools import SimulatedEnvironment, REMEDIATION_TOOL_BY_CATEGORY
from datetime import datetime, timedelta

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


def auto_approve(approval_request: ApprovalRequest) -> bool:
    """Default simulated Human Approval Interface: always approves, but the
    approval is still a real recorded step in state, not skipped."""
    return True


class Orchestrator:
    def __init__(self, incident_id: str, store: Optional[DataStore] = None,
                 approval_callback: Callable[[ApprovalRequest], bool] = auto_approve,
                 verbose: bool = True, env=None, step_delay_seconds: float = 0.0):
        self.store = store or DataStore()
        # Purely cosmetic pacing knob, off by default (0.0 -- no behavior
        # change for main.py/scripts/tests). The agent's reasoning executes
        # in milliseconds; a dashboard operator watching a live run needs
        # something to actually watch, so dashboard/registry.py opts into a
        # small per-tool-call delay. This never affects decisions, ordering,
        # or outcomes -- it only slows down when each already-decided step
        # becomes visible.
        self.step_delay_seconds = step_delay_seconds
        # env override lets adversarial eval scenarios (scripts/run_eval.py)
        # supply a fully hand-authored ScenarioEnvironment instead of the
        # fixture-driven SimulatedEnvironment, without touching orchestration.
        self.env = env or SimulatedEnvironment(self.store, incident_id)
        self.state = IncidentState(incident_id=incident_id, interface_id=self.env.interface_id)
        # `self.state` IS investigation memory (agent/memory.py). `self.memory`
        # is the sole gateway to long-term resolution memory -- neither this
        # class nor agent/reasoning.py touches self.store's resolution-record
        # methods directly.
        self.memory = ResolutionMemory(self.store)
        self.approval_callback = approval_callback
        self.verbose = verbose

    # -----------------------------------------------------------------

    def _print(self, *args):
        if self.verbose:
            print(*args)

    def _execute_tool(self, tool_name: str, params: dict) -> dict:
        fn = getattr(self.env, tool_name)
        try:
            result = fn(**params)
            self.state.log_tool_call(tool_name, params, str(result)[:200], "success")
            self.state.advance_clock(3.0)  # simulated minutes per tool call
            if self.step_delay_seconds:
                time.sleep(self.step_delay_seconds)
            return result
        except Exception as e:
            self.state.log_tool_call(tool_name, params, str(e), "error")
            self.state.advance_clock(3.0)
            raise

    # -----------------------------------------------------------------
    # OBSERVE
    # -----------------------------------------------------------------

    def observe(self):
        incident = self._execute_tool("get_incident", {"incident_id": self.state.incident_id})
        ev = self.state.add_evidence(Evidence(source="get_incident", content=incident,
                                               summary=f"Incident intake: {incident['symptom_description']}"))
        self.state.reported_at = incident["reported_at"]

        iface = self._execute_tool("get_interface_metadata", {"interface_id": self.state.interface_id})
        self.state.add_evidence(Evidence(source="get_interface_metadata", content=iface,
                                          summary=f"Interface {iface['name']} ({iface['message_type']}), "
                                                  f"SLA tier {iface['sla_tier']}, owned by {iface['owning_team']}."))

        criticality = self._reconcile_criticality(incident["reported_severity"], iface["sla_tier"],
                                                    iface["decommissioned"])
        self.state.criticality = criticality
        hours = SLA_WINDOW_HOURS[criticality]
        reported_dt = datetime.fromisoformat(incident["reported_at"].rstrip("Z"))
        self.state.sla_deadline = (reported_dt + timedelta(hours=hours)).isoformat() + "Z"

        self.state.log_decision(
            decision=f"assess criticality: {criticality.value}",
            evidence_ids=[ev.evidence_id],
            alternatives_considered=[f"use reported_severity as-is ({incident['reported_severity']})"],
            confidence=None,
            reason=f"Reconciled reported severity '{incident['reported_severity']}' against interface "
                   f"SLA tier '{iface['sla_tier']}' (decommissioned={iface['decommissioned']}).",
            next_action="seed hypotheses and begin investigation" if not iface["decommissioned"]
                        else "escalate immediately -- interface is decommissioned",
        )

        if iface["decommissioned"]:
            self.state.stage = Stage.DECIDING
            return

        # broad initial log scan + recent-change check feed the hypothesis
        # seeding step (per the Incident Investigation skill's triage evidence
        # menu) -- logged as DiagnosticActions so they aren't re-proposed
        # during INVESTIGATING, but they don't count against the investigation
        # cycle budget since they're OBSERVE-stage triage, not discrimination.
        from .models import DiagnosticAction
        logs_action = DiagnosticAction(tool_name="search_logs",
                                        params={"interface_id": self.state.interface_id,
                                                "time_range": {"from": "2026-09-01T00:00:00Z",
                                                               "to": "2026-09-27T23:59:59Z"}},
                                        targets_hypothesis_ids=[], rationale="OBSERVE-stage broad triage scan.")
        logs = self._execute_tool("search_logs", logs_action.params)
        logs_action.status = ActionStatus.EXECUTED
        self.state.diagnostic_actions.append(logs_action)
        logs_ev = self.state.add_evidence(Evidence(source="search_logs", content=logs,
                                          produced_by_action_id=logs_action.action_id,
                                          summary=f"{logs['total_matched']} log entries found."))
        logs_action.result_evidence_id = logs_ev.evidence_id
        error_codes = [e.get("error_code") for e in logs["entries"] if e.get("error_code")]

        changes_action = DiagnosticAction(tool_name="get_recent_changes",
                                           params={"interface_id": self.state.interface_id, "lookback_window": "72h"},
                                           targets_hypothesis_ids=[], rationale="OBSERVE-stage recent-change triage.")
        changes = self._execute_tool("get_recent_changes", changes_action.params)
        changes_action.status = ActionStatus.EXECUTED
        self.state.diagnostic_actions.append(changes_action)
        changes_ev = self.state.add_evidence(Evidence(source="get_recent_changes", content=changes,
                                             produced_by_action_id=changes_action.action_id,
                                             summary=f"{len(changes['changes'])} recent change(s) found."))
        changes_action.result_evidence_id = changes_ev.evidence_id

        reasoning.seed_hypotheses(self.state, error_codes, iface.get("known_fragility_flags", []),
                                   seed_evidence_id=logs_ev.evidence_id)
        reasoning.interpret_and_revise(self.state, "search_logs", logs, logs_ev.evidence_id)
        reasoning.interpret_and_revise(self.state, "get_recent_changes", changes, changes_ev.evidence_id)

        self.state.stage = Stage.INVESTIGATING

    def _reconcile_criticality(self, reported_severity, sla_tier, decommissioned) -> Criticality:
        if decommissioned:
            return Criticality.P4
        try:
            reported = Criticality(reported_severity)
        except ValueError:
            reported = Criticality.P3
        try:
            tier = Criticality(sla_tier)
        except ValueError:
            tier = Criticality.P3
        order = [Criticality.P1, Criticality.P2, Criticality.P3, Criticality.P4]
        return order[min(order.index(reported), order.index(tier))]

    # -----------------------------------------------------------------
    # INVESTIGATING
    # -----------------------------------------------------------------

    def investigate_step(self):
        guard = guardrails.check_diagnostic_proposal(self.state)
        if not guard.allowed:
            self._print(f"[guardrail] {guard.reason} -> forcing {guard.forced_transition}")
            self.state.stage = Stage(guard.forced_transition)
            return

        proposal = reasoning.propose_next_diagnostic(self.state)
        if proposal is None:
            self.state.stage = Stage.DECIDING
            return

        result = self._execute_tool(proposal.tool_name, proposal.params)
        proposal.status = ActionStatus.EXECUTED
        self.state.diagnostic_actions.append(proposal)
        self.state.investigation_cycle_count += 1

        evidence = self.state.add_evidence(Evidence(
            source=proposal.tool_name, content=result,
            produced_by_action_id=proposal.action_id,
            summary=f"{proposal.tool_name} -> {str(result)[:120]}",
        ))
        proposal.result_evidence_id = evidence.evidence_id
        reasoning.interpret_and_revise(self.state, proposal.tool_name, result, evidence.evidence_id)
        self._print(f"  [investigate] {proposal.tool_name} -> {evidence.summary}")

    # -----------------------------------------------------------------
    # DECIDING
    # -----------------------------------------------------------------

    def decide_step(self):
        iface = self.store.get_interface(self.state.interface_id)
        if iface and iface["decommissioned"]:
            self._escalate(EscalationReason.CONTRADICTORY_EVIDENCE,
                            "Incident references a decommissioned interface; ticket-data-quality issue, "
                            "not a technical fault.")
            return

        outcome = reasoning.decide_act_or_escalate(self.state)
        if outcome[0] == "escalate":
            self._escalate(outcome[1], outcome[2])
            return

        _, remediation = outcome
        guard = guardrails.check_remediation_proposal(
            self.state, remediation.tool_name, remediation.targets_hypothesis_id,
            remediation.hypothesis_confidence_at_decision,
        )
        if not guard.allowed:
            self._print(f"[guardrail] remediation rejected: {guard.reason}")
            remediation.status = ActionStatus.REJECTED_BY_GUARDRAIL
            self.state.remediation_actions.append(remediation)
            if guard.forced_transition == "ESCALATED":
                self._escalate(EscalationReason.RETRIES_EXHAUSTED, guard.reason)
            else:
                # Guarantee forward progress: a "" forced_transition means
                # "stay in DECIDING," which would otherwise re-propose the
                # exact same rejected action forever. Demoting the targeted
                # hypothesis forces the next decide_step() to consider a
                # different one (or escalate) instead of looping.
                reasoning.demote_for_insufficient_direct_evidence(
                    self.state, remediation.targets_hypothesis_id, guard.reason)
            return

        self.state.remediation_actions.append(remediation)
        if guard.forced_transition == "AWAITING_APPROVAL":
            self._route_to_approval(remediation)
        else:
            self.state.stage = Stage.ACTING
            self._act(remediation)

    def _route_to_approval(self, remediation):
        req = ApprovalRequest(
            remediation_action_id=remediation.action_id,
            risk_tier=remediation.risk_tier,
            justification=remediation.rationale,
        )
        self.state.approval_requests.append(req)
        remediation.approval_request_id = req.approval_request_id
        remediation.status = ActionStatus.AWAITING_APPROVAL
        self.state.stage = Stage.AWAITING_APPROVAL
        self._print(f"  [approval] requesting approval for {remediation.tool_name} (risk={remediation.risk_tier})")

        approved = self.approval_callback(req)
        req.decision = "approved" if approved else "rejected"
        req.decided_at = "now"
        self.state.log_decision(
            decision=f"human approval {'granted' if approved else 'denied'} for {remediation.tool_name}",
            evidence_ids=[], alternatives_considered=[], confidence=None,
            reason=req.justification, next_action="execute remediation" if approved else "return to DECIDING",
        )
        if approved:
            remediation.status = ActionStatus.APPROVED
            self.state.stage = Stage.ACTING
            self._act(remediation)
        else:
            remediation.status = ActionStatus.REJECTED
            # Record the declined tool so reasoning.decide_act_or_escalate
            # never silently re-proposes it -- without this, the next
            # decide_step() sees the same top hypothesis with nothing
            # changed and immediately re-requests approval for the exact
            # same action, which looks to an operator like Reject did nothing.
            self.state.rejected_tool_names.add(remediation.tool_name)

            # A rejection is a reason to look for more evidence, not just a
            # reason to re-evaluate the same evidence again: if investigation
            # budget/SLA allow it, go back to INVESTIGATING first (per
            # docs/AGENT_ARCHITECTURE.md's DECIDING<->INVESTIGATING back-edge)
            # so the agent can pursue an alternative explanation
            # (reasoning.propose_next_diagnostic now specifically allows
            # supplementary evidence-gathering for a confirmed-but-blocked
            # hypothesis). If no budget remains or nothing new is found, the
            # very next INVESTIGATING/DECIDING pass still reaches a clean
            # escalation -- this never risks looping.
            diag_guard = guardrails.check_diagnostic_proposal(self.state)
            next_stage = Stage.INVESTIGATING if diag_guard.allowed else Stage.DECIDING
            self.state.log_decision(
                decision=f"human rejected '{remediation.tool_name}'",
                evidence_ids=[], alternatives_considered=[], confidence=None,
                reason=f"'{remediation.tool_name}' will not be re-proposed for this incident. "
                       + (f"Returning to investigation to look for an alternative explanation."
                          if next_stage == Stage.INVESTIGATING else
                          f"Investigation budget exhausted ({diag_guard.reason}); re-evaluating remaining "
                          f"hypotheses directly."),
                next_action="resume investigation" if next_stage == Stage.INVESTIGATING
                            else "re-evaluate remaining hypotheses in DECIDING",
            )
            self.state.stage = next_stage

    # -----------------------------------------------------------------
    # ACTING + VERIFYING
    # -----------------------------------------------------------------

    def _act(self, remediation):
        # Hard invariant, independent of and in addition to the routing logic
        # in decide_step()/_route_to_approval(): a gated tool may never
        # execute without a granted approval record on file. This is the
        # structural guarantee that the agent (or a future bug in the
        # routing code above) cannot bypass approval -- it is checked here,
        # immediately before the tool call, not just at proposal time.
        approval_decision = None
        if remediation.approval_request_id:
            req = next((r for r in self.state.approval_requests
                        if r.approval_request_id == remediation.approval_request_id), None)
            approval_decision = req.decision if req else None
        risk_policy.assert_approved_if_required(remediation.tool_name, approval_decision)

        result = self._execute_tool(remediation.tool_name, remediation.params)
        remediation.status = ActionStatus.EXECUTED
        self.state.remediation_attempt_count += 1
        will_fix = result.pop("_will_fix", False)

        evidence = self.state.add_evidence(Evidence(
            source=remediation.tool_name, content=result,
            produced_by_action_id=remediation.action_id,
            summary=f"Executed {remediation.tool_name}: {str(result)[:120]}",
        ))
        self._print(f"  [act] {remediation.tool_name} executed.")

        self.state.stage = Stage.VERIFYING
        self._verify(remediation, will_fix)

    def _verify(self, remediation, will_fix_hint: bool):
        verification_tool = "verify_message_processing" if remediation.tool_name in (
            "retry_message",) else "verify_interface_health"
        params = {"interface_id": self.state.interface_id, "baseline_snapshot_id": "baseline-1",
                  "will_fix_hint": will_fix_hint}
        if verification_tool == "verify_message_processing":
            params = {"interface_id": self.state.interface_id,
                      "message_id": remediation.params.get("message_id", "unknown"),
                      "expected_end_state": "processed", "will_fix_hint": will_fix_hint}

        diag = None
        from .models import DiagnosticAction
        diag = DiagnosticAction(tool_name=verification_tool, params=params,
                                 targets_hypothesis_ids=[remediation.targets_hypothesis_id],
                                 rationale=f"Mandatory verification of {remediation.tool_name}",
                                 is_verification=True, verifies_remediation_id=remediation.action_id)
        result = self._execute_tool(verification_tool, params)
        diag.status = ActionStatus.EXECUTED
        self.state.diagnostic_actions.append(diag)

        succeeded = result.get("error_state_cleared", result.get("matches_expected", False))
        outcome = VerificationOutcome.PASSED if succeeded else (
            VerificationOutcome.INCONCLUSIVE if result.get("channel_status") == "degraded"
            else VerificationOutcome.FAILED
        )

        evidence = self.state.add_evidence(Evidence(
            source=verification_tool, content=result, produced_by_action_id=diag.action_id,
            summary=f"Verification outcome: {outcome.value}",
        ))
        diag.result_evidence_id = evidence.evidence_id

        vr = VerificationResult(
            remediation_action_id=remediation.action_id, diagnostic_action_id=diag.action_id,
            outcome=outcome, comparison=result,
        )
        self.state.verification_results.append(vr)
        self._print(f"  [verify] {verification_tool} -> {outcome.value}")

        if outcome == VerificationOutcome.PASSED:
            self._resolve(remediation, vr)
            return

        vguard = guardrails.check_verification_retry(self.state)
        self.state.verification_retry_count += 1
        if not vguard.allowed:
            self._escalate(EscalationReason.RETRIES_EXHAUSTED,
                            f"Verification of '{remediation.tool_name}' outcome={outcome.value}; "
                            f"retry/attempt budget exhausted.")
            return

        reasoning.decide_after_verification_failure(self.state, remediation.targets_hypothesis_id)
        self.state.stage = Stage.DECIDING

    # -----------------------------------------------------------------
    # RESOLVE / ESCALATE / LEARN
    # -----------------------------------------------------------------

    def _resolve(self, remediation, verification: VerificationResult):
        hyp = self.state.get_hypothesis(remediation.targets_hypothesis_id)
        from .models import HypothesisStatus
        hyp.status = HypothesisStatus.CONFIRMED
        self.state.stage = Stage.RESOLVED

        self._execute_tool("update_incident", {
            "incident_id": self.state.incident_id, "status": "Resolved",
            "diagnosis_summary": hyp.statement, "evidence_refs": hyp.supporting_evidence_ids,
            "actions_taken": [a.tool_name for a in self.state.remediation_actions], "outcome": "Resolved",
        })

        iface = self.store.get_interface(self.state.interface_id) or getattr(self.env, "interface", {})
        record = ResolutionRecord(
            symptom_signature={
                "interface_type": iface.get("message_type") if iface else None, "error_category": hyp.category,
                "log_pattern_tags": [], "affected_component": "adapter",
            },
            root_cause=hyp.category, root_cause_statement=hyp.statement,
            remediation_tool=remediation.tool_name, remediation_params_template={"interface_id": "{{interface_id}}"},
            verification_signature={"check": "verify_interface_health", "expected_state": "healthy"},
            outcome="Resolved", tool_call_count=len(self.state.diagnostic_actions) + len(self.state.remediation_actions),
            human_feedback="Unreviewed", source_incident_id=self.state.incident_id,
        )
        self.state.resolution = record
        # The only path into long-term resolution memory: promote() itself
        # re-checks that `verification` actually PASSED before writing
        # anything, independent of this record's own outcome="Resolved" field.
        self.memory.promote(record, verification)
        self._print(f"[RESOLVED] root cause: {hyp.category} (confidence={hyp.confidence:.2f})")

    def _escalate(self, reason: EscalationReason, note: str):
        active = self.state.active_hypotheses()
        leading = [{"category": h.category, "confidence": h.confidence, "statement": h.statement}
                   for h in active[:3]]
        actions = [a.action_id for a in self.state.diagnostic_actions] + \
                  [a.action_id for a in self.state.remediation_actions]
        unresolved = [f"Why does '{h.category}' remain unconfirmed?" for h in active[:2]] or \
                     ["Root cause not conclusively determined."]

        summary = (f"Escalation reason: {reason.value}. {note} "
                   f"Leading hypotheses: {', '.join(h['category'] for h in leading) or 'none'}. "
                   f"{len(actions)} actions attempted.")

        esc = Escalation(reason=reason, summary_for_human=summary, leading_hypotheses=leading,
                          actions_attempted=actions, unresolved_questions=unresolved)
        self.state.escalation = esc
        self.state.stage = Stage.ESCALATED

        self._execute_tool("create_escalation", {
            "incident_id": self.state.incident_id, "reason": reason.value,
            "summary_for_human": summary, "leading_hypothesis_ids": [h["category"] for h in leading],
            "unresolved_questions": unresolved,
        })
        self._execute_tool("update_incident", {
            "incident_id": self.state.incident_id, "status": "Escalated",
            "diagnosis_summary": summary, "evidence_refs": [], "actions_taken": actions, "outcome": "Escalated",
        })

        # In-memory record for reporting (main.py reads state.resolution for
        # both outcomes) -- deliberately NOT written to long-term resolution
        # memory. An escalated incident was never verified as fixed, so it
        # must never resurface as case-memory precedent for a future
        # incident's hypothesis generation (agent/memory.py).
        record = ResolutionRecord(
            symptom_signature={"interface_type": None, "error_category": leading[0]["category"] if leading else None,
                                "log_pattern_tags": [], "affected_component": None},
            root_cause=leading[0]["category"] if leading else "unknown",
            root_cause_statement=summary, remediation_tool=None, remediation_params_template=None,
            verification_signature={}, outcome="Escalated",
            tool_call_count=len(self.state.diagnostic_actions) + len(self.state.remediation_actions),
            human_feedback="Unreviewed", source_incident_id=self.state.incident_id,
        )
        self.state.resolution = record
        self.memory.archive_escalation({
            "incident_id": self.state.incident_id, "reason": reason.value, "summary": summary,
            "leading_hypotheses": leading, "actions_attempted": actions,
            "unresolved_questions": unresolved, "created_at": record.created_at,
        })
        self._print(f"[ESCALATED] reason={reason.value}: {note}")

    # -----------------------------------------------------------------
    # main loop
    # -----------------------------------------------------------------

    def run(self) -> IncidentState:
        self._print(f"=== Incident {self.state.incident_id} ===")
        self.observe()
        while self.state.stage not in (Stage.RESOLVED, Stage.ESCALATED):
            if self.state.stage == Stage.INVESTIGATING:
                self.investigate_step()
            elif self.state.stage == Stage.DECIDING:
                self.decide_step()
            else:
                # AWAITING_APPROVAL / ACTING are handled synchronously inline
                # above; reaching here with no progress would be a bug guard.
                break
        self._dump_run()
        return self.state

    def _dump_run(self):
        RUNS_DIR.mkdir(exist_ok=True)
        path = RUNS_DIR / f"{self.state.incident_id}.json"
        path.write_text(json.dumps(_state_to_dict(self.state), indent=2, default=str), encoding="utf-8")

        from .observability import render_timeline
        timeline_path = RUNS_DIR / f"{self.state.incident_id}.timeline.txt"
        timeline_path.write_text(render_timeline(self.state), encoding="utf-8")

        self._print(f"[log] full trace written to {path}")
        self._print(f"[log] observability timeline written to {timeline_path}")


def _state_to_dict(state: IncidentState) -> dict:
    import dataclasses

    def conv(obj):
        if dataclasses.is_dataclass(obj):
            return {k: conv(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, list):
            return [conv(v) for v in obj]
        if isinstance(obj, dict):
            return {k: conv(v) for k, v in obj.items()}
        if isinstance(obj, set):
            return sorted(conv(v) for v in obj)
        if hasattr(obj, "value"):  # Enum
            return obj.value
        return obj

    return conv(state)
