"""
Observability layer: renders an IncidentState (investigation memory) into a
concise, human-readable timeline covering every section required for
oversight -- incident, current state, selected skill(s), tools called and
their results, hypotheses, confidence changes, decisions, actions,
verification, escalation, and final resolution.

Deliberately does NOT expose private chain-of-thought, because there isn't
any to hide: the agent's reasoning (agent/reasoning.py) is deterministic
Python producing structured, evidence-cited DecisionLogEntry objects, not an
LLM transcript. This renderer's job is to surface exactly those structured
rationales -- concise, tied to specific evidence_ids and confidence values --
and nothing else. If agent/reasoning.py is later replaced with an
LLM-backed component (README.md's noted future direction), this module's
render_timeline() is where any raw model output must be summarized down to
the same DecisionLogEntry shape before it reaches here; this file should
never need to change to enforce that boundary.
"""
from .models import IncidentState, Stage
from .skills import CATEGORY_TO_SKILL


def _fmt_conf(c):
    return f"{c:.2f}" if c is not None else "n/a"


def _section(title: str) -> str:
    bar = "=" * len(title)
    return f"\n{title}\n{bar}\n"


def render_timeline(state: IncidentState) -> str:
    out = []

    # --- Incident -------------------------------------------------------
    out.append(_section(f"INCIDENT: {state.incident_id}"))
    out.append(f"Interface:     {state.interface_id}")
    out.append(f"Criticality:   {state.criticality.value if state.criticality else 'n/a'}")
    out.append(f"SLA deadline:  {state.sla_deadline or 'n/a'}")
    out.append(f"Reported at:   {state.reported_at or 'n/a'}")

    # --- Current state ----------------------------------------------------
    out.append(_section("CURRENT STATE"))
    out.append(f"Stage: {state.stage.value}")
    out.append(f"Investigation cycles used: {state.investigation_cycle_count}")
    out.append(f"Remediation attempts used: {state.remediation_attempt_count}")
    out.append(f"Simulated elapsed time: {state.simulated_elapsed_minutes:.0f} min "
               f"(SLA remaining: {state.sla_remaining_fraction() * 100:.0f}%)")

    # --- Selected skill(s) --------------------------------------------------
    out.append(_section("SKILLS ENGAGED"))
    skill_to_categories = {}
    for h in state.hypotheses:
        if h.confidence_history:
            skill = CATEGORY_TO_SKILL.get(h.category, "Incident Investigation")
            skill_to_categories.setdefault(skill, []).append(h.category)
    if skill_to_categories:
        for skill, cats in skill_to_categories.items():
            out.append(f"- {skill}  (categories: {', '.join(cats)})")
    else:
        out.append("- Incident Investigation (triage only; no specialist skill reached)")

    # --- Tools called + results --------------------------------------------
    out.append(_section("TOOLS CALLED"))
    for i, call in enumerate(state.tool_call_log, 1):
        status_flag = "OK" if call.status == "success" else "ERROR"
        out.append(f"{i:>2}. [{status_flag}] {call.tool_name}({_fmt_params(call.params)})")
        out.append(f"      -> {call.result_summary}")

    # --- Hypotheses (final ranked view) -------------------------------------
    out.append(_section("HYPOTHESES (final ranking)"))
    ranked = sorted(state.hypotheses, key=lambda h: h.confidence, reverse=True)
    for h in ranked:
        if not h.confidence_history and h.confidence <= 0.05:
            continue  # never touched by any evidence -- not worth listing
        out.append(f"- [{h.status.value:<9}] {h.category:<28} confidence={_fmt_conf(h.confidence)}  "
                   f"\"{h.statement}\"")

    # --- Confidence changes --------------------------------------------------
    out.append(_section("CONFIDENCE CHANGES"))
    any_deltas = False
    for h in ranked:
        if not h.confidence_history:
            continue
        any_deltas = True
        out.append(f"{h.category}:")
        for d in h.confidence_history:
            out.append(f"    {_fmt_conf(d.prior_confidence)} -> {_fmt_conf(d.new_confidence)}   "
                       f"[{d.evidence_id}] {d.reason}")
    if not any_deltas:
        out.append("(no confidence-affecting evidence recorded)")

    # --- Decisions (concise, evidence-based rationale only) ------------------
    out.append(_section("DECISIONS"))
    for i, d in enumerate(state.decision_log, 1):
        out.append(f"{i:>2}. {d.decision}")
        out.append(f"      reason: {d.reason}")
        if d.confidence is not None:
            out.append(f"      confidence: {_fmt_conf(d.confidence)}")
        if d.alternatives_considered:
            out.append(f"      alternatives considered: {', '.join(d.alternatives_considered)}")
        out.append(f"      next action: {d.next_action}")

    # --- Actions (diagnostic + remediation) -----------------------------------
    out.append(_section("ACTIONS"))
    out.append("Diagnostic:")
    for a in state.diagnostic_actions:
        kind = "VERIFY" if a.is_verification else "DIAG"
        out.append(f"  [{kind}] {a.tool_name} -- {a.status.value} -- {a.rationale}")
    out.append("Remediation:")
    if not state.remediation_actions:
        out.append("  (none proposed)")
    for a in state.remediation_actions:
        out.append(f"  {a.tool_name} (risk={a.risk_tier}) -- {a.status.value} -- {a.rationale}")

    # --- Verification ------------------------------------------------------
    out.append(_section("VERIFICATION"))
    if not state.verification_results:
        out.append("(no verification performed -- no remediation was executed)")
    for v in state.verification_results:
        out.append(f"- remediation {v.remediation_action_id}: outcome={v.outcome.value}")
        diff = v.comparison.get("diff_summary") or v.comparison.get("comparison_to_baseline") \
            or v.comparison.get("processing_errors")
        if diff:
            out.append(f"    {diff}")

    # --- Escalation ----------------------------------------------------------
    out.append(_section("ESCALATION"))
    if state.escalation:
        e = state.escalation
        out.append(f"Reason: {e.reason.value}")
        out.append(f"Summary: {e.summary_for_human}")
        out.append("Leading hypotheses at escalation:")
        for h in e.leading_hypotheses:
            out.append(f"  - {h['category']} (confidence={_fmt_conf(h['confidence'])})")
        if e.unresolved_questions:
            out.append("Unresolved questions:")
            for q in e.unresolved_questions:
                out.append(f"  - {q}")
    else:
        out.append("(not escalated)")

    # --- Final resolution -------------------------------------------------
    out.append(_section("FINAL RESOLUTION"))
    if state.resolution:
        r = state.resolution
        out.append(f"Outcome: {r.outcome}")
        out.append(f"Root cause: {r.root_cause}")
        out.append(f"Statement: {r.root_cause_statement}")
        if r.remediation_tool:
            out.append(f"Remediation applied: {r.remediation_tool}")
        out.append(f"Total tool calls: {r.tool_call_count}")
    else:
        out.append("(incident still in progress -- no terminal outcome yet)")

    return "\n".join(out)


def _fmt_params(params: dict) -> str:
    parts = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)
