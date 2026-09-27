"""
The two memory layers required by the task, made explicit as first-class
objects rather than left implicit in orchestrator plumbing.

1. Investigation memory -- the CURRENT incident's observations, hypotheses,
   tool results, and actions. This is exactly `agent.models.IncidentState`;
   it already carries evidence, hypotheses (with confidence_history),
   diagnostic/remediation actions, verification results, and the decision
   log. Nothing new is needed to satisfy this half of the requirement --
   `InvestigationMemory` below is a documented alias, not a duplicate class,
   so there is only ever one place investigation-scoped state lives.

2. Resolution memory -- validated historical incident resolutions. This is
   the part that needs real enforcement: the agent may READ this freely to
   generate/prioritize hypotheses (agent/reasoning.py's
   `search_historical_incidents` interpretation), but a historical match is
   corroborating evidence, never ground truth (see
   `docs/RISK_POLICY.md`-style guardrail in agent/guardrails.py:
   `check_hypothesis_has_direct_evidence`, and the "already directly
   refuted" skip in `agent/reasoning.py::interpret_and_revise`). Writing TO
   this store is gated by `ResolutionMemory.promote()`, the sole path that
   accepts records, and it structurally refuses anything not backed by a
   Passed VerificationResult -- an escalated or unverified outcome can never
   land here, no matter what the orchestrator or a future caller claims.
"""
from typing import Optional

from .data_store import DataStore
from .models import IncidentState, ResolutionRecord, VerificationResult, VerificationOutcome

# Canonical alias -- see module docstring point 1. Code and docs may refer to
# "investigation memory" and mean this.
InvestigationMemory = IncidentState


class ResolutionMemoryError(Exception):
    pass


def _record_to_dict(record: ResolutionRecord) -> dict:
    return {
        "record_id": record.record_id, "symptom_signature": record.symptom_signature,
        "root_cause": record.root_cause, "root_cause_statement": record.root_cause_statement,
        "remediation_tool": record.remediation_tool, "remediation_params_template": record.remediation_params_template,
        "verification_signature": record.verification_signature, "outcome": record.outcome,
        "tool_call_count": record.tool_call_count, "human_feedback": record.human_feedback,
        "source_incident_id": record.source_incident_id, "created_at": record.created_at,
    }


class ResolutionMemory:
    """
    Sole read/write gateway to long-term resolution memory.
    agent/orchestrator.py and agent/reasoning.py must go through this class,
    never call agent.data_store.DataStore's resolution-record methods
    directly, so "only verified successful resolutions may be promoted" has
    exactly one enforcement point to audit.
    """

    def __init__(self, store: DataStore):
        self.store = store

    def retrieve(self, symptom_signature: dict, max_results: int = 5) -> list:
        """
        Read path used by the `search_historical_incidents` tool. Returned
        matches are candidate evidence ONLY -- see agent/reasoning.py's
        interpret_and_revise for how a match's confidence contribution is
        deliberately capped (weighted by similarity, further discounted
        unless human_feedback=="Correct", and skipped entirely if the
        matched category has already been directly refuted by fresh
        diagnostic evidence this incident) so it can never, by itself, push
        a hypothesis to the acting threshold.
        """
        return self.store.search_historical_incidents(symptom_signature, max_results)

    def promote(self, record: ResolutionRecord, verification: VerificationResult):
        """
        The ONLY path by which a resolution can be written to long-term,
        retrievable resolution memory. Raises ResolutionMemoryError if the
        supplied verification did not PASS -- this is checked here,
        independent of what `record.outcome` claims, so a caller cannot
        promote an unverified or failed-verification record by constructing
        a ResolutionRecord with outcome="Resolved" and skipping this check.
        """
        if verification.outcome != VerificationOutcome.PASSED:
            raise ResolutionMemoryError(
                f"Refusing to promote resolution for incident {record.source_incident_id} into "
                f"long-term resolution memory: verification outcome was "
                f"'{verification.outcome.value}', not 'Passed'."
            )
        if verification.remediation_action_id is None:
            raise ResolutionMemoryError(
                f"Refusing to promote resolution for incident {record.source_incident_id}: "
                f"no remediation_action_id on the supplied verification result."
            )
        self.store.append_learned_resolution(_record_to_dict(record))

    def archive_escalation(self, summary: dict):
        """
        Escalations are recorded for audit/analytics but are explicitly kept
        OUT of resolution memory: they were never verified as a fix, so they
        must never be retrievable as case-memory precedent for a future
        incident's hypothesis generation.
        """
        self.store.append_escalation_archive(summary)
