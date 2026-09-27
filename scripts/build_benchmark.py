"""
Builds data/benchmark_incidents.json: >=30 benchmark cases, each annotated
with ground-truth root cause, acceptable diagnostic paths, acceptable
remediation, unacceptable actions, and an expected escalation condition.

Two sources, combined:
  - All 10 adversarial scenarios from data/eval_scenarios.json (already have
    precise, hand-authored ground truth from the adversarial-eval task).
  - 20 incidents sampled from the bulk fixture set (data/incidents.json),
    chosen to cover every root-cause category and a mix of outcome buckets
    (resolved_known_precedent / resolved_novel / ambiguous_evidence /
    no_known_resolution), so the benchmark isn't dominated by easy cases.

"Acceptable diagnostic tools" and "unacceptable actions" are derived
programmatically from the same skill-priority table and remediation mapping
the agent itself uses (agent/skills.py, agent/simulated_tools.py) -- this
keeps the benchmark's notion of "acceptable" consistent with the documented
skills rather than an independently hand-guessed list.
"""
import json
import random
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills import SKILL_TOOL_PRIORITY, SUPPLEMENTARY_TOOLS
from agent.simulated_tools import REMEDIATION_TOOL_BY_CATEGORY

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ALL_REMEDIATION_TOOLS = {"retry_message", "restart_interface", "refresh_connection",
                          "update_adapter_timeout", "rotate_certificate",
                          "reprocess_message_batch", "update_routing_rule"}

random.seed(20260927)


def acceptable_tools_for(category: str) -> list:
    return list(dict.fromkeys(SKILL_TOOL_PRIORITY.get(category, []) + SUPPLEMENTARY_TOOLS))


def unacceptable_actions_for(category: str) -> list:
    acceptable = REMEDIATION_TOOL_BY_CATEGORY.get(category)
    return sorted(t for t in ALL_REMEDIATION_TOOLS if t != acceptable)


def escalation_expectation_from_bucket(category: str, bucket: str) -> dict:
    has_tool = REMEDIATION_TOOL_BY_CATEGORY.get(category) is not None
    if bucket in ("resolved_known_precedent", "resolved_novel") and has_tool:
        return {"should_escalate": False,
                "acceptable_reasons": []}
    if not has_tool:
        return {"should_escalate": True,
                "acceptable_reasons": ["NoAutonomousRemediation"]}
    if bucket == "ambiguous_evidence":
        return {"should_escalate": None,  # legitimately either, depending on how evidence resolves
                "acceptable_reasons": ["LowConfidence", "ContradictoryEvidence", "RetriesExhausted"]}
    # no_known_resolution
    return {"should_escalate": True,
            "acceptable_reasons": ["LowConfidence", "RetriesExhausted", "ContradictoryEvidence"]}


def build_from_eval_scenarios() -> list:
    scenarios = json.loads((DATA_DIR / "eval_scenarios.json").read_text(encoding="utf-8"))
    out = []
    for scn in scenarios:
        category = scn["true_root_cause"]
        expected_stage = scn.get("expected_stage")
        should_escalate = expected_stage == "ESCALATED"
        acceptable_reasons = [scn["expected_escalation_reason"]] if scn.get("expected_escalation_reason") else []
        out.append({
            "benchmark_id": f"BENCH-{scn['scenario_id']}",
            "source": "adversarial_eval_scenario",
            "incident_id": scn["incident_stub"]["incident_id"],
            "run_mode": "scenario",  # run via ScenarioEnvironment
            "ground_truth_root_cause": category if category != "unknown" else None,
            "acceptable_diagnostic_tools": acceptable_tools_for(category) if category != "unknown" else
                                            list(dict.fromkeys(sum(SKILL_TOOL_PRIORITY.values(), []))),
            "acceptable_remediation": REMEDIATION_TOOL_BY_CATEGORY.get(category),
            "unacceptable_actions": unacceptable_actions_for(category) if category != "unknown" else
                                     sorted(ALL_REMEDIATION_TOOLS),
            "expected_escalation": {"should_escalate": should_escalate, "acceptable_reasons": acceptable_reasons},
            "notes": scn["notes"],
        })
    return out


def build_from_bulk_fixtures(n: int = 20) -> list:
    incidents = json.loads((DATA_DIR / "incidents.json").read_text(encoding="utf-8"))
    by_category = {}
    for inc in incidents:
        by_category.setdefault(inc["true_root_cause_category"], []).append(inc)

    categories = sorted(by_category.keys())
    chosen = []
    # First pass: one per category, preferring a resolved bucket so the
    # benchmark includes at least one clean "should resolve" case per category.
    for cat in categories:
        pool = by_category[cat]
        resolved = [i for i in pool if i["outcome_bucket"] in ("resolved_known_precedent", "resolved_novel")]
        chosen.append(random.choice(resolved) if resolved else random.choice(pool))

    # Second pass: fill remaining slots with a mix of ambiguous/no-resolution
    # cases across categories, to stress escalation-appropriateness.
    remaining_slots = n - len(chosen)
    remaining_pool = [i for i in incidents if i not in chosen and
                       i["outcome_bucket"] in ("ambiguous_evidence", "no_known_resolution")]
    random.shuffle(remaining_pool)
    chosen.extend(remaining_pool[:remaining_slots])

    out = []
    for inc in chosen[:n]:
        category = inc["true_root_cause_category"]
        bucket = inc["outcome_bucket"]
        out.append({
            "benchmark_id": f"BENCH-{inc['incident_id']}",
            "source": "bulk_fixture",
            "incident_id": inc["incident_id"],
            "run_mode": "fixture",  # run via SimulatedEnvironment/DataStore
            "ground_truth_root_cause": category,
            "acceptable_diagnostic_tools": acceptable_tools_for(category),
            "acceptable_remediation": REMEDIATION_TOOL_BY_CATEGORY.get(category),
            "unacceptable_actions": unacceptable_actions_for(category),
            "expected_escalation": escalation_expectation_from_bucket(category, bucket),
            "notes": f"Bulk fixture incident, outcome_bucket={bucket}.",
        })
    return out


def main():
    benchmark = build_from_eval_scenarios() + build_from_bulk_fixtures(20)
    out_path = DATA_DIR / "benchmark_incidents.json"
    out_path.write_text(json.dumps(benchmark, indent=2), encoding="utf-8")
    print(f"Wrote {len(benchmark)} benchmark cases to {out_path}")


if __name__ == "__main__":
    main()
