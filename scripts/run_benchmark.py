"""
Runs the agent against every case in data/benchmark_incidents.json and
computes the 12 metrics defined in docs/EVALUATION.md, writing a generated
report to reports/EVALUATION_REPORT.md (and the raw per-case results to
reports/evaluation_results.json for anyone who wants to re-slice the numbers).

Usage:
    python scripts/run_benchmark.py
"""
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.data_store import DataStore
from agent.eval_environment import ScenarioEnvironment
from agent.orchestrator import Orchestrator
from agent.models import VerificationOutcome

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

# The two "OBSERVE-stage" tool calls the orchestrator always makes before
# INVESTIGATING begins -- excluded when judging the agent's OWN tool choices
# (first-action accuracy, tool-selection accuracy), since they're fixed
# triage, not a decision.
OBSERVE_STAGE_TOOLS = {"search_logs", "get_recent_changes"}


def is_agent_chosen(action) -> bool:
    return not (action.rationale.startswith("OBSERVE-stage"))


def run_case(case: dict, store: DataStore):
    if case["run_mode"] == "scenario":
        scenarios = json.loads((DATA_DIR / "eval_scenarios.json").read_text(encoding="utf-8"))
        scn = next(s for s in scenarios if s["incident_stub"]["incident_id"] == case["incident_id"])
        env = ScenarioEnvironment(scn)
        orch = Orchestrator(incident_id=case["incident_id"], store=store, env=env, verbose=False)
    else:
        orch = Orchestrator(incident_id=case["incident_id"], store=store, verbose=False)
    state = orch.run()
    return state


def score_case(case: dict, state) -> dict:
    agent_diag_actions = [a for a in state.diagnostic_actions if is_agent_chosen(a) and not a.is_verification]
    first_action = agent_diag_actions[0].tool_name if agent_diag_actions else None

    executed_remediations = [a for a in state.remediation_actions if a.status.value == "Executed"]
    acceptable_remediation = case["acceptable_remediation"]
    unacceptable = set(case["unacceptable_actions"])

    # 1. Root-cause identification: does the agent's final belief (confirmed
    # hypothesis if resolved, else top leading hypothesis if escalated)
    # match ground truth. None ground truth (genuinely unknown/ambiguous
    # adversarial cases) is excluded from this metric's denominator.
    root_cause_correct = None
    if case["ground_truth_root_cause"] is not None:
        if state.resolution and state.resolution.outcome == "Resolved":
            root_cause_correct = (state.resolution.root_cause == case["ground_truth_root_cause"])
        elif state.escalation and state.escalation.leading_hypotheses:
            root_cause_correct = (state.escalation.leading_hypotheses[0]["category"] == case["ground_truth_root_cause"])
        else:
            root_cause_correct = False

    # 2. First-action accuracy
    first_action_correct = (first_action in case["acceptable_diagnostic_tools"]) if first_action else False

    # 3. Successful autonomous resolution (resolved AND no approval needed)
    autonomous_resolution = bool(state.resolution and state.resolution.outcome == "Resolved"
                                  and len(state.approval_requests) == 0)

    # 4. Verification outcomes (aggregated at report level from raw counts)
    verification_outcomes = [v.outcome for v in state.verification_results]

    # 5. Escalation appropriateness
    actual_escalated = (state.stage.value == "ESCALATED")
    expected = case["expected_escalation"]
    if expected["should_escalate"] is None:
        escalation_appropriate = True  # legitimately either way (ambiguous_evidence bucket)
    elif expected["should_escalate"]:
        escalation_appropriate = actual_escalated and (
            not expected["acceptable_reasons"] or
            (state.escalation and state.escalation.reason.value in expected["acceptable_reasons"])
        )
    else:
        escalation_appropriate = not actual_escalated

    # 6. Diagnostic steps (agent-chosen only, excludes OBSERVE + verification)
    diagnostic_steps = len(agent_diag_actions)

    # 7. Time to resolution (simulated clock)
    time_to_resolution = state.simulated_elapsed_minutes

    # 8. Human intervention needed
    human_intervention = bool(state.approval_requests) or actual_escalated

    # 9. False remediation: any EXECUTED remediation tool not the acceptable one
    false_remediation = any(a.tool_name in unacceptable for a in executed_remediations) if \
        acceptable_remediation is not None else \
        any(True for a in executed_remediations)  # no acceptable tool exists at all -> any execution is false

    # 10. Tool selection accuracy (fraction of agent-chosen diagnostic calls
    # that are in the acceptable set for this incident's category)
    if agent_diag_actions:
        acceptable_set = set(case["acceptable_diagnostic_tools"])
        on_target = sum(1 for a in agent_diag_actions if a.tool_name in acceptable_set)
        tool_selection_accuracy = on_target / len(agent_diag_actions)
    else:
        tool_selection_accuracy = None

    # 11. Evidence utilization: fraction of confidence_history entries citing
    # a real evidence_id present in this incident's evidence log
    evidence_ids = {e.evidence_id for e in state.evidence}
    all_deltas = [d for h in state.hypotheses for d in h.confidence_history]
    if all_deltas:
        cited_real = sum(1 for d in all_deltas if d.evidence_id in evidence_ids)
        evidence_utilization = cited_real / len(all_deltas)
    else:
        evidence_utilization = None

    # 12. SLA breach
    sla_breached = state.sla_remaining_fraction() <= 0.0

    return {
        "benchmark_id": case["benchmark_id"],
        "incident_id": case["incident_id"],
        "ground_truth_root_cause": case["ground_truth_root_cause"],
        "final_stage": state.stage.value,
        "root_cause_correct": root_cause_correct,
        "first_action": first_action,
        "first_action_correct": first_action_correct,
        "autonomous_resolution": autonomous_resolution,
        "verification_outcomes": [v.value for v in verification_outcomes],
        "escalation_appropriate": escalation_appropriate,
        "diagnostic_steps": diagnostic_steps,
        "time_to_resolution_minutes": time_to_resolution,
        "human_intervention": human_intervention,
        "false_remediation": false_remediation,
        "tool_selection_accuracy": tool_selection_accuracy,
        "evidence_utilization": evidence_utilization,
        "sla_breached": sla_breached,
    }


def aggregate(results: list) -> dict:
    n = len(results)

    def rate(pred):
        vals = [pred(r) for r in results]
        vals = [v for v in vals if v is not None]
        return (sum(1 for v in vals if v) / len(vals)) if vals else None

    def mean(key):
        vals = [r[key] for r in results if r[key] is not None]
        return statistics.mean(vals) if vals else None

    def mean_of_optional(pred):
        vals = [pred(r) for r in results]
        vals = [v for v in vals if v is not None]
        return statistics.mean(vals) if vals else None

    all_verification_outcomes = [o for r in results for o in r["verification_outcomes"]]
    verification_success_rate = (
        all_verification_outcomes.count("Passed") / len(all_verification_outcomes)
        if all_verification_outcomes else None
    )

    return {
        "n_cases": n,
        "1_root_cause_identification_accuracy": rate(lambda r: r["root_cause_correct"]),
        "2_first_action_accuracy": rate(lambda r: r["first_action_correct"]),
        "3_successful_autonomous_resolution_rate": rate(lambda r: r["autonomous_resolution"]),
        "4_verification_success_rate": verification_success_rate,
        "5_escalation_appropriateness": rate(lambda r: r["escalation_appropriate"]),
        "6_mean_diagnostic_steps": mean("diagnostic_steps"),
        "7_mean_time_to_resolution_minutes": mean("time_to_resolution_minutes"),
        "8_human_intervention_rate": rate(lambda r: r["human_intervention"]),
        "9_false_remediation_rate": rate(lambda r: r["false_remediation"]),
        "10_tool_selection_accuracy": mean_of_optional(lambda r: r["tool_selection_accuracy"]),
        "11_evidence_utilization": mean_of_optional(lambda r: r["evidence_utilization"]),
        "12_sla_breach_rate": rate(lambda r: r["sla_breached"]),
    }


METRIC_LABELS = {
    "1_root_cause_identification_accuracy": "1. Root-cause identification accuracy",
    "2_first_action_accuracy": "2. First-action accuracy",
    "3_successful_autonomous_resolution_rate": "3. Successful autonomous resolution rate",
    "4_verification_success_rate": "4. Verification success rate",
    "5_escalation_appropriateness": "5. Escalation appropriateness",
    "6_mean_diagnostic_steps": "6. Mean diagnostic steps",
    "7_mean_time_to_resolution_minutes": "7. Mean time to resolution (simulated minutes)",
    "8_human_intervention_rate": "8. Human intervention rate",
    "9_false_remediation_rate": "9. False remediation rate",
    "10_tool_selection_accuracy": "10. Tool selection accuracy",
    "11_evidence_utilization": "11. Evidence utilization",
    "12_sla_breach_rate": "12. SLA breach rate",
}


def fmt(v):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2f}" if v > 1.5 else f"{v * 100:.0f}%" if v <= 1.0 else f"{v:.2f}"
    return str(v)


def write_report(results: list, agg: dict):
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "evaluation_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = []
    lines.append("# Evaluation Report")
    lines.append("")
    lines.append(f"Generated {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')} "
                 f"by `scripts/run_benchmark.py` "
                 f"against {agg['n_cases']} benchmark cases (`data/benchmark_incidents.json`). "
                 f"Methodology: [EVALUATION.md](EVALUATION.md).")
    lines.append("")
    lines.append("## Aggregate Metrics")
    lines.append("")
    lines.append("| # | Metric | Value |")
    lines.append("|---|---|---|")
    for key, label in METRIC_LABELS.items():
        lines.append(f"| {label.split('.')[0]} | {label.split('. ',1)[1]} | {fmt(agg[key])} |")
    lines.append("")

    lines.append("## Per-Case Results")
    lines.append("")
    lines.append("| Benchmark ID | Incident | Ground Truth | Final Stage | Root Cause OK | First Action OK | "
                  "Escalation OK | Diag Steps | Tool Sel. Acc. | Evidence Util. | SLA Breach |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['benchmark_id']} | {r['incident_id']} | {r['ground_truth_root_cause'] or 'n/a'} | "
            f"{r['final_stage']} | {r['root_cause_correct']} | {r['first_action_correct']} | "
            f"{r['escalation_appropriate']} | {r['diagnostic_steps']} | "
            f"{fmt(r['tool_selection_accuracy'])} | {fmt(r['evidence_utilization'])} | {r['sla_breached']} |"
        )
    lines.append("")

    failures = [r for r in results if r["root_cause_correct"] is False or not r["escalation_appropriate"]
                or r["false_remediation"]]
    if failures:
        lines.append("## Notable Failures")
        lines.append("")
        for r in failures:
            reasons = []
            if r["root_cause_correct"] is False:
                reasons.append("wrong root cause identified")
            if not r["escalation_appropriate"]:
                reasons.append("escalation decision did not match expectation")
            if r["false_remediation"]:
                reasons.append("executed a remediation outside the acceptable set")
            lines.append(f"- **{r['benchmark_id']}** ({r['incident_id']}): {', '.join(reasons)}. "
                         f"Final stage: {r['final_stage']}, ground truth: {r['ground_truth_root_cause']}.")
        lines.append("")

    (REPORTS_DIR / "EVALUATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    benchmark = json.loads((DATA_DIR / "benchmark_incidents.json").read_text(encoding="utf-8"))
    store = DataStore()

    results = []
    for case in benchmark:
        state = run_case(case, store)
        results.append(score_case(case, state))

    agg = aggregate(results)
    write_report(results, agg)

    print(f"Ran {len(results)} benchmark cases.")
    for key, label in METRIC_LABELS.items():
        print(f"  {label}: {fmt(agg[key])}")
    print(f"\nReport written to reports/EVALUATION_REPORT.md")


if __name__ == "__main__":
    main()
