"""
Agent Reasoning / Orchestration component (architecture 1.1). This is the
"brain": given the current IncidentState, it PROPOSES exactly one next
action -- it never calls a tool or mutates state directly (the orchestrator
does that). Tool/action choice is driven by which hypotheses are currently
uncertain and which tools are still untried for them, so different incidents
take different tool paths -- there is no fixed classify->search->recommend
chain.
"""
from typing import Optional

from .models import (
    Hypothesis, HypothesisStatus, DiagnosticAction, RemediationAction,
    IncidentState, EscalationReason,
)
from .registry import spec_for
from .guardrails import ACT_CONFIDENCE_THRESHOLD
from .skills import (
    SKILL_TOOL_PRIORITY, ALL_CATEGORIES, SUPPLEMENTARY_TOOLS,
    ERROR_CODE_TO_CATEGORY, CATEGORY_STATEMENTS,
)
from .simulated_tools import REMEDIATION_TOOL_BY_CATEGORY


def seed_hypotheses(state: IncidentState, initial_error_codes: list, fragility_flags: list,
                     seed_evidence_id: str = "seed"):
    """
    OBSERVE-stage: seed one Hypothesis per known category at a low uniform
    base rate, then bias priors using whatever coarse signal is already
    available (log error codes seen in the intake logs, interface fragility
    flags) -- mirrors the Incident Investigation skill's triage step. Biases
    are applied via revise() (not a raw attribute set) so they show up in
    confidence_history like any other evidence-driven update -- the seed step
    is itself a form of evidence, not a silent default.
    """
    for category in ALL_CATEGORIES:
        state.hypotheses.append(Hypothesis(category=category, statement=CATEGORY_STATEMENTS[category],
                                            confidence=0.05))

    biased = set()
    for code in initial_error_codes:
        cat = ERROR_CODE_TO_CATEGORY.get(code)
        if cat:
            biased.add(cat)
    if "cert_expires_soon" in fragility_flags:
        biased.add("cert_expiry")
    if "deprecated_auth_scheme" in fragility_flags:
        biased.add("auth_failure")

    for h in state.hypotheses:
        if h.category in biased:
            h.revise(seed_evidence_id, 0.25, "Coarse triage: error code / fragility flag present in intake logs.",
                      supports=True)


def _called_tool_names(state: IncidentState, exclude_verification: bool = True) -> set:
    return {a.tool_name for a in state.diagnostic_actions
            if not (exclude_verification and a.is_verification)}


def propose_next_diagnostic(state: IncidentState) -> Optional[DiagnosticAction]:
    """
    Returns the next DiagnosticAction to propose, or None if the agent judges
    investigation is no longer productive (time to move to DECIDING).
    Selection logic: look at the top-ranked ACTIVE hypotheses, and for each
    (in confidence order) find the highest-priority tool from its skill list
    that hasn't been called yet in this incident. This means two incidents
    with different symptom profiles call different tools in different orders.
    """
    already_called = _called_tool_names(state)
    active = state.active_hypotheses()

    alternatives = []
    for h in active:
        # Deliberately NOT truncated to a fixed top-N: a mid-confidence
        # hypothesis with no untried discriminating tools left would otherwise
        # permanently occupy a slot ahead of a lower-ranked, still-untested
        # hypothesis and starve it of ever being investigated. Iterating all
        # active hypotheses in confidence order still means higher-confidence
        # ones are asked for their next tool first -- this only changes which
        # hypotheses are ever *reachable*, not the priority order.
        priority_tools = SKILL_TOOL_PRIORITY.get(h.category, [])
        for tool in priority_tools:
            if tool not in already_called:
                alternatives.append((h, tool))

    if alternatives:
        target_hyp, chosen_tool = alternatives[0]
        rationale = (
            f"Top active hypothesis '{target_hyp.category}' (confidence={target_hyp.confidence:.2f}) "
            f"has not yet been tested with '{chosen_tool}', which the corresponding skill lists as "
            f"discriminating for this category."
        )
        other_considered = [f"{t} (for {h.category}, conf={h.confidence:.2f})" for h, t in alternatives[1:4]]
        state.log_decision(
            decision=f"propose diagnostic action: {chosen_tool}",
            evidence_ids=[e.evidence_id for e in state.evidence[-3:]],
            alternatives_considered=other_considered or ["none -- only one discriminating tool remained"],
            confidence=target_hyp.confidence,
            reason=rationale,
            next_action=f"execute {chosen_tool}",
        )
        return _build_diagnostic_action(chosen_tool, target_hyp, state)

    # No priority tool remains for top hypotheses -- consider supplementary
    # tools once each, if either:
    #   (a) the top hypothesis is still in the ambiguous confidence band
    #       (not confident enough to act, not low enough to abandon), or
    #   (b) the top hypothesis IS confirmed but its only remediation was
    #       already rejected by a human (state.rejected_tool_names) -- this
    #       is exactly the case where the agent should go looking for
    #       corroboration of a DIFFERENT explanation rather than simply
    #       re-escalating on the same confirmed-but-blocked diagnosis.
    top = state.top_hypothesis()
    top_tool = REMEDIATION_TOOL_BY_CATEGORY.get(top.category) if top else None
    top_blocked = bool(top_tool and top_tool in state.rejected_tool_names)
    if top and (0.15 <= top.confidence < ACT_CONFIDENCE_THRESHOLD or top_blocked):
        for tool in SUPPLEMENTARY_TOOLS:
            if tool not in already_called:
                reason = (
                    f"The only remediation for confirmed hypothesis '{top.category}' ('{top_tool}') was "
                    f"rejected by a human; seeking evidence for an alternative explanation via '{tool}' "
                    f"before escalating." if top_blocked else
                    f"Leading hypothesis '{top.category}' is in the ambiguous confidence band; "
                    f"seeking corroborating evidence via '{tool}' before deciding."
                )
                state.log_decision(
                    decision=f"propose diagnostic action: {tool}",
                    evidence_ids=[e.evidence_id for e in state.evidence[-3:]],
                    alternatives_considered=["move directly to DECIDING without corroboration"],
                    confidence=top.confidence,
                    reason=reason,
                    next_action=f"execute {tool}",
                )
                return _build_diagnostic_action(tool, top, state)

    return None  # nothing productive left to try -> move to DECIDING


def _build_diagnostic_action(tool_name: str, target_hypothesis: Hypothesis, state: IncidentState) -> DiagnosticAction:
    interface_id = state.interface_id
    params = {"interface_id": interface_id}
    if tool_name == "search_logs":
        params.update({"time_range": {"from": "2026-09-01T00:00:00Z", "to": "2026-09-27T23:59:59Z"}})
    elif tool_name == "test_connectivity":
        params.update({"endpoint": "both"})
    elif tool_name == "compare_with_previous_successful_run":
        params.update({"failing_message_id": f"MSG-{state.incident_id[-4:]}-001"})
    elif tool_name == "search_historical_incidents":
        top = state.top_hypothesis()
        params = {"symptom_signature": {
            "interface_type": None, "error_category": top.category if top else None,
            "log_pattern_tags": [], "affected_component": None,
        }}
    elif tool_name == "retrieve_runbook":
        top = state.top_hypothesis()
        params = {"root_cause_category": top.category if top else None, "interface_id": interface_id}
    elif tool_name == "get_recent_changes":
        params.update({"lookback_window": "72h"})
    return DiagnosticAction(tool_name=tool_name, params=params,
                             targets_hypothesis_ids=[target_hypothesis.hypothesis_id],
                             rationale=f"Discriminating for hypothesis {target_hypothesis.category}")


def decide_act_or_escalate(state: IncidentState):
    """
    DECIDING-stage reasoning: choose between proposing a RemediationAction or
    escalating, based on current top hypothesis confidence and whether a
    remediation tool exists for it at all.
    Returns one of: ("act", RemediationAction), ("escalate", EscalationReason, str)
    """
    active = state.active_hypotheses()
    top = active[0] if active else None
    second = active[1] if len(active) > 1 else None

    alternatives = [f"{h.category} (conf={h.confidence:.2f})" for h in active[1:4]]

    if top is None:
        state.log_decision("escalate", [], [], None, "no active hypotheses remain", "create_escalation")
        return ("escalate", EscalationReason.LOW_CONFIDENCE, "No viable hypothesis remains active.")

    raw_contradiction = detect_raw_contradiction(state)
    if raw_contradiction:
        state.log_decision(
            decision="escalate (contradictory evidence -- raw source conflict)",
            evidence_ids=raw_contradiction[1],
            alternatives_considered=alternatives,
            confidence=top.confidence,
            reason=raw_contradiction[0],
            next_action="create_escalation",
        )
        return ("escalate", EscalationReason.CONTRADICTORY_EVIDENCE, raw_contradiction[0])

    # Contradictory evidence: top two hypotheses are both moderately confident
    # and close together -- genuine ambiguity, not just low information. Only
    # checked on the FIRST decision point (no remediation attempted yet):
    # after a failed remediation demotes the hypothesis it targeted, picking
    # the next-best (even if close to a third) is the intended pivot behavior,
    # not a new contradiction.
    if second and state.remediation_attempt_count == 0 and top.confidence < ACT_CONFIDENCE_THRESHOLD \
            and abs(top.confidence - second.confidence) < 0.1 and top.confidence > 0.3:
        state.log_decision(
            decision="escalate (contradictory evidence)",
            evidence_ids=top.supporting_evidence_ids + top.contradicting_evidence_ids,
            alternatives_considered=alternatives,
            confidence=top.confidence,
            reason=f"Top two hypotheses ('{top.category}'={top.confidence:.2f}, "
                   f"'{second.category}'={second.confidence:.2f}) remain too close to safely act on either.",
            next_action="create_escalation",
        )
        return ("escalate", EscalationReason.CONTRADICTORY_EVIDENCE,
                f"Hypotheses '{top.category}' and '{second.category}' remain contradictory/too close in confidence.")

    if top.confidence < ACT_CONFIDENCE_THRESHOLD:
        state.log_decision(
            decision="escalate (low confidence)",
            evidence_ids=top.supporting_evidence_ids,
            alternatives_considered=alternatives,
            confidence=top.confidence,
            reason=f"Leading hypothesis '{top.category}' confidence {top.confidence:.2f} is below the "
                   f"acting threshold {ACT_CONFIDENCE_THRESHOLD}, and no further discriminating evidence remains.",
            next_action="create_escalation",
        )
        return ("escalate", EscalationReason.LOW_CONFIDENCE,
                f"No hypothesis reached sufficient confidence (top: '{top.category}'={top.confidence:.2f}).")

    # Walk hypotheses in confidence order (not just `top`): a hypothesis
    # whose only remediation tool was already declined by a human this
    # incident (state.rejected_tool_names, set in
    # agent/orchestrator.py::_route_to_approval on rejection) must never be
    # re-proposed, but a DIFFERENT, still-sufficiently-confident hypothesis
    # may still have a legitimate, not-yet-tried action.
    any_actionable_but_rejected = False
    for h in active:
        if h.confidence < ACT_CONFIDENCE_THRESHOLD:
            break  # active is sorted descending; nothing further can qualify
        remediation_tool = REMEDIATION_TOOL_BY_CATEGORY.get(h.category)
        if remediation_tool is None:
            if h is top:
                state.log_decision(
                    decision="escalate (no autonomous remediation)",
                    evidence_ids=h.supporting_evidence_ids,
                    alternatives_considered=alternatives,
                    confidence=h.confidence,
                    reason=f"Hypothesis '{h.category}' is confirmed with confidence {h.confidence:.2f}, but no "
                           f"tool in the catalog can remediate this category autonomously or with approval.",
                    next_action="create_escalation",
                )
                return ("escalate", EscalationReason.NO_AUTONOMOUS_REMEDIATION,
                        f"Root cause '{h.category}' confirmed (confidence={h.confidence:.2f}) but no remediation "
                        f"tool exists for this category; requires human/owning-team action.")
            continue
        if remediation_tool in state.rejected_tool_names:
            any_actionable_but_rejected = True
            continue

        spec = spec_for(remediation_tool)
        action = RemediationAction(
            tool_name=remediation_tool,
            params=_remediation_params(remediation_tool, state),
            targets_hypothesis_id=h.hypothesis_id,
            hypothesis_confidence_at_decision=h.confidence,
            risk_tier=spec.risk_tier,
            rationale=f"Hypothesis '{h.category}' confirmed at confidence {h.confidence:.2f}; "
                      f"'{remediation_tool}' is the mapped remediation for this category (risk={spec.risk_tier}).",
        )
        state.log_decision(
            decision=f"act: propose remediation '{remediation_tool}'",
            evidence_ids=h.supporting_evidence_ids,
            alternatives_considered=alternatives + ["escalate instead of acting"],
            confidence=h.confidence,
            reason=action.rationale,
            next_action="guardrail check -> execute or route to approval",
        )
        return ("act", action)

    if any_actionable_but_rejected:
        state.log_decision(
            decision="escalate (remediation declined by human)",
            evidence_ids=top.supporting_evidence_ids,
            alternatives_considered=alternatives,
            confidence=top.confidence,
            reason=f"Every sufficiently-confident hypothesis maps only to a remediation tool a human already "
                   f"rejected for this incident (rejected: {sorted(state.rejected_tool_names)}). Not "
                   f"re-proposing a declined action.",
            next_action="create_escalation",
        )
        return ("escalate", EscalationReason.HUMAN_REJECTED_REMEDIATION,
                f"The proposed remediation was rejected by a human reviewer, and no alternative "
                f"sufficiently-confident hypothesis has a different available action.")

    # Nothing above the threshold at all (shouldn't normally be reached given
    # the top.confidence check above, but kept as a safe fallback).
    state.log_decision(
        decision="escalate (low confidence)",
        evidence_ids=top.supporting_evidence_ids,
        alternatives_considered=alternatives,
        confidence=top.confidence,
        reason=f"No hypothesis with an available, not-yet-rejected remediation reached the acting threshold.",
        next_action="create_escalation",
    )
    return ("escalate", EscalationReason.LOW_CONFIDENCE,
            f"No actionable hypothesis remains (top: '{top.category}'={top.confidence:.2f}).")


def _remediation_params(tool_name: str, state: IncidentState) -> dict:
    interface_id = state.interface_id
    message_id = f"MSG-{state.incident_id[-4:]}-001"
    if tool_name == "retry_message":
        return {"interface_id": interface_id, "message_id": message_id}
    if tool_name == "restart_interface":
        return {"interface_id": interface_id, "channel_id": f"CH-{interface_id}"}
    if tool_name == "refresh_connection":
        return {"interface_id": interface_id}
    if tool_name == "update_adapter_timeout":
        return {"interface_id": interface_id, "new_timeout_ms": 30000}
    if tool_name == "rotate_certificate":
        return {"interface_id": interface_id}
    return {"interface_id": interface_id}


def interpret_and_revise(state: IncidentState, tool_name: str, result: dict, evidence_id: str):
    """
    Evidence-interpretation rules (the machine-readable form of each skill's
    "evidence that confirms/refutes each hypothesis" section). Every
    hypothesis touched here gets a confidence_history entry citing
    evidence_id -- this is what makes hypothesis revision auditable rather
    than decorative (spec: "confidence theater" failure mode).
    """
    def bump(category, new_conf, reason, supports=True):
        h = next((x for x in state.hypotheses if x.category == category), None)
        if h and h.status == HypothesisStatus.ACTIVE:
            h.revise(evidence_id, new_conf, reason, supports)

    if tool_name == "inspect_certificate_status":
        if result.get("is_expired"):
            bump("cert_expiry", 0.9, "Certificate confirmed expired.")
        elif result.get("is_expiring_soon"):
            bump("cert_expiry", 0.35, "Certificate expiring soon but not yet expired -- partial support.")
        else:
            bump("cert_expiry", 0.05, "Certificate confirmed valid, not expiring.", supports=False)

    elif tool_name == "inspect_credentials":
        status = result.get("credential_status")
        iface = state.interface_id
        # both auth_failure and sftp_credential_failure map to credential-state evidence;
        # bump whichever is currently more active as the intended target.
        target_cat = "sftp_credential_failure" if _sftp_context(state) else "auth_failure"
        if status in ("expired", "locked", "revoked"):
            bump(target_cat, 0.9, f"Credential status confirmed '{status}'.")
        elif status == "valid":
            current = _current(state, target_cat)
            if current >= 0.15:
                # Already-elevated confidence (e.g. from a recent credential_rotation
                # change) plus a *valid* credential is the specific signature for a
                # stale cached session, not a refutation of the auth hypothesis --
                # a flat demotion here would incorrectly rule out auth_failure
                # entirely just because the credential itself checks out.
                bump(target_cat, max(current, 0.8),
                     "Credential confirmed valid, but combined with a recent credential/session-affecting "
                     "change already in evidence, this supports a stale cached session needing refresh "
                     "rather than ruling out auth entirely.")
            else:
                bump(target_cat, 0.05, "Credential confirmed valid with no corroborating change signal.",
                     supports=False)
        elif status == "unknown":
            bump(target_cat, 0.3, "Credential status indeterminate -- weak, ambiguous signal.")

    elif tool_name == "test_connectivity":
        for r in result.get("results", []):
            if r["endpoint"] == "target":
                if r["reachable"] is False:
                    bump("connectivity_failure", 0.7, "Target endpoint unreachable.")
                    bump("endpoint_unavailable", 0.5, "Target endpoint unreachable (possible service-down variant).")
                    bump("database_connection_failure", 0.4,
                         "Target unreachable; relevant if this interface is DB-bound.")
                elif r["reachable"] is True and r.get("latency_ms", 0) and r["latency_ms"] > 5000:
                    bump("timeout", 0.75, f"Elevated latency observed ({r['latency_ms']}ms).")
                else:
                    bump("connectivity_failure", 0.05, "Target endpoint reachable with normal latency.", supports=False)
                    bump("endpoint_unavailable", 0.05, "Target endpoint reachable.", supports=False)

    elif tool_name == "compare_with_previous_successful_run":
        diffs = result.get("diffs", [])
        fields = {d["field"] for d in diffs}
        superficial_fields = {"batch_size", "timestamp_only"}
        if any("encoding" in f for f in fields):
            bump("malformed_payload", 0.8, f"Diff shows encoding-related field change: {fields}.")
        elif diffs and not result.get("previous_success_found") is False:
            if fields and fields.issubset(superficial_fields):
                bump("mapping_error", 0.3, f"Diff present but looks superficial/non-causal: {fields}.")
            else:
                # Any non-superficial structural diff is treated as strong support --
                # deliberately not gated on specific field-name keywords, since a
                # keyword filter here would itself be exactly the kind of
                # surface-matching shortcut this agent is meant to avoid.
                bump("mapping_error", 0.8, f"Diff shows structural field change: {fields}.")
        elif not result.get("previous_success_found", True):
            bump("duplicate_message", 0.3, "No previous successful run found -- weak signal for novel/duplicate handling issue.")
        else:
            bump("mapping_error", 0.05, "No meaningful diff found versus last successful run.", supports=False)
            bump("malformed_payload", 0.05, "No meaningful diff found versus last successful run.", supports=False)

    elif tool_name == "search_logs":
        texts = " ".join(e.get("raw_text", "") for e in result.get("entries", [])).lower()
        if "stuck" in texts or "queue_blocked" in texts.lower():
            bump("message_processing_failure", 0.6, "Log text indicates stuck/blocked processing.")
        if "duplicate" in texts:
            bump("duplicate_message", 0.8, "Log text indicates duplicate message handling.")

    elif tool_name == "get_recent_changes":
        for change in result.get("changes", []):
            ct = change["change_type"]
            if ct == "certificate":
                bump("cert_expiry", max(_current(state, "cert_expiry"), 0.3), "Recent certificate change correlates with onset.")
            elif ct == "credential_rotation":
                bump("auth_failure", max(_current(state, "auth_failure"), 0.3), "Recent credential rotation correlates with onset.")
            elif ct == "deployment":
                bump("mapping_error", max(_current(state, "mapping_error"), 0.25), "Recent deployment correlates with onset.")
                bump("timeout", max(_current(state, "timeout"), 0.2), "Recent deployment may have affected performance.")

    elif tool_name == "search_historical_incidents":
        for match in result.get("matches", []):
            if match.get("human_feedback") == "Incorrect":
                continue  # do not let a known-bad precedent inflate confidence
            cat = match["root_cause"]
            target = next((x for x in state.hypotheses if x.category == cat), None)
            if target and target.contradicting_evidence_ids:
                # This category has already been directly, freshly refuted by a
                # diagnostic tool in THIS incident. A historical precedent -- even
                # a similar, positively-reviewed one -- must not resurrect a
                # hypothesis that direct evidence has already ruled out; case
                # memory corroborates, it does not override.
                continue
            weight = match["similarity_score"] * (0.3 if match.get("human_feedback") == "Correct" else 0.15)
            bump(cat, max(_current(state, cat), _current(state, cat) + weight),
                 f"Corroborated by historical match {match['record_id']} "
                 f"(similarity={match['similarity_score']}, feedback={match.get('human_feedback')}).")


def detect_raw_contradiction(state: IncidentState):
    """
    Detects a specific, well-defined raw-evidence conflict: a connectivity
    probe reporting the target fully reachable at the same time a broad log
    scan shows total silence (zero matched entries) for this incident. Both
    signals cannot be simultaneously true of a healthy interface, and neither
    can be dismissed by hypothesis-confidence reasoning alone -- this is a
    source-level inconsistency, not an uncertain hypothesis.
    Returns (reason_text, [evidence_ids]) or None.
    """
    connectivity_ev = [e for e in state.evidence if e.source == "test_connectivity"]
    logs_ev = [e for e in state.evidence if e.source == "search_logs"]
    for c in connectivity_ev:
        target_reachable = any(r["endpoint"] == "target" and r.get("reachable") is True
                                for r in c.content.get("results", []))
        if not target_reachable:
            continue
        for l in logs_ev:
            if l.content.get("total_matched", 0) == 0:
                return (
                    f"test_connectivity ({c.evidence_id}) reports the target endpoint fully reachable, "
                    f"but search_logs ({l.evidence_id}) shows zero matched log entries for this interface "
                    f"in the same window -- these two sources cannot both be describing a healthy interface.",
                    [c.evidence_id, l.evidence_id],
                )
    return None


def _current(state: IncidentState, category: str) -> float:
    h = next((x for x in state.hypotheses if x.category == category), None)
    return h.confidence if h else 0.0


def _sftp_context(state: IncidentState) -> bool:
    h = next((x for x in state.hypotheses if x.category == "sftp_credential_failure"), None)
    return bool(h and h.confidence >= 0.2)


def demote_for_insufficient_direct_evidence(state: IncidentState, hypothesis_id: str, reason: str):
    """
    Called when the Guardrail Layer rejects a remediation proposal because
    the targeted hypothesis is supported only by historical/case-memory
    precedent with no direct diagnostic evidence from this incident
    (agent/guardrails.py::check_hypothesis_has_direct_evidence). Given the
    current confidence-weighting caps in interpret_and_revise(), historical
    corroboration alone cannot actually reach the acting threshold, so this
    path is not expected to fire today -- it exists so that IF it ever does
    (e.g. after a future retuning of those weights), the decision loop makes
    forward progress instead of re-proposing the same unvalidated action
    forever: demoting here forces decide_act_or_escalate() to pick a
    different hypothesis (or escalate) on its next pass.
    """
    hyp = state.get_hypothesis(hypothesis_id)
    if hyp:
        hyp.status = HypothesisStatus.DEMOTED
        hyp.revise(evidence_id=state.evidence[-1].evidence_id if state.evidence else "none",
                   new_confidence=min(hyp.confidence, 0.2),
                   reason=f"Demoted: {reason}", supports=False)
        state.log_decision(
            decision=f"demote hypothesis '{hyp.category}' -- insufficient direct evidence",
            evidence_ids=hyp.contradicting_evidence_ids,
            alternatives_considered=["act on historical precedent alone"],
            confidence=hyp.confidence,
            reason=reason,
            next_action="re-evaluate remaining hypotheses in DECIDING",
        )


def decide_after_verification_failure(state: IncidentState, failed_hypothesis_id: str):
    """
    VERIFYING -> DECIDING reasoning on Failed/Inconclusive: demote the
    hypothesis the failed remediation targeted (evidence that acting on it
    didn't fix the symptom) rather than blindly retrying, then let
    decide_act_or_escalate() pick the next best path on the next turn.
    """
    hyp = state.get_hypothesis(failed_hypothesis_id)
    if hyp:
        hyp.status = HypothesisStatus.REFUTED
        hyp.revise(evidence_id=state.evidence[-1].evidence_id if state.evidence else "none",
                   new_confidence=0.05,
                   reason="Targeted remediation executed but verification did not confirm resolution.",
                   supports=False)
        state.log_decision(
            decision=f"demote hypothesis '{hyp.category}' after failed verification",
            evidence_ids=hyp.contradicting_evidence_ids,
            alternatives_considered=["retry same remediation again"],
            confidence=hyp.confidence,
            reason="Verification failed/inconclusive after acting on this hypothesis; treating it as refuted "
                   "rather than retrying blindly, per verification-blindness guardrail.",
            next_action="re-evaluate remaining hypotheses in DECIDING",
        )
