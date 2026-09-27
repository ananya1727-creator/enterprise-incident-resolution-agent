"""
Adversarial evaluation harness. Runs every scenario in data/eval_scenarios.json
through the real Orchestrator (using ScenarioEnvironment instead of the
fixture-driven SimulatedEnvironment) and scores each against its expected
outcome. Also checks, for scenarios with a `trap_category`, that the agent
actually entertained the misleading hypothesis (confidence rose meaningfully)
before revising away from it -- evidence of genuine investigation rather than
committing to whichever hypothesis happened to be scored first.

Usage:
    python scripts/run_eval.py
    python scripts/run_eval.py --verbose
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.data_store import DataStore
from agent.eval_environment import ScenarioEnvironment
from agent.orchestrator import Orchestrator

SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "data" / "eval_scenarios.json"


def trap_was_considered(state, trap_category: str) -> bool:
    """
    True if the trap hypothesis was actually touched by evidence (seeded
    and/or updated by a diagnostic tool result) rather than simply never
    examined. This is deliberately evidence-presence-based, not a confidence
    magnitude threshold: a keyword-trap scenario is meant to show the agent
    explicitly tests and refutes the misleading category, which may never
    involve it rising to a high number at all.
    """
    hyp = next((h for h in state.hypotheses if h.category == trap_category), None)
    return bool(hyp and len(hyp.confidence_history) > 0)


def score_scenario(scenario: dict, state) -> dict:
    checks = []
    ok = True

    expected_stage = scenario.get("expected_stage")
    actual_stage = state.stage.value
    stage_ok = (actual_stage == expected_stage)
    checks.append(("final stage", expected_stage, actual_stage, stage_ok))
    ok &= stage_ok

    if expected_stage == "RESOLVED":
        expected_root = scenario.get("expected_root_cause")
        actual_root = state.resolution.root_cause if state.resolution else None
        root_ok = (actual_root == expected_root)
        checks.append(("root cause", expected_root, actual_root, root_ok))
        ok &= root_ok

    if expected_stage == "ESCALATED":
        expected_reason = scenario.get("expected_escalation_reason")
        actual_reason = state.escalation.reason.value if state.escalation else None
        reason_ok = (actual_reason == expected_reason)
        checks.append(("escalation reason", expected_reason, actual_reason, reason_ok))
        ok &= reason_ok

    trap = scenario.get("trap_category")
    if trap:
        trap_ok = trap_was_considered(state, trap)
        checks.append(("trap hypothesis genuinely considered", f"peak_conf>=0.2 for '{trap}'",
                       "yes" if trap_ok else "no", trap_ok))
        ok &= trap_ok
        if expected_stage == "RESOLVED":
            final_root = state.resolution.root_cause if state.resolution else None
            revised_away = final_root != trap
            checks.append(("revised away from trap by resolution", f"final != '{trap}'",
                           final_root, revised_away))
            ok &= revised_away

    return {"pass": ok, "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    store = DataStore()

    total = 0
    passed = 0
    results = []

    for scenario in scenarios:
        total += 1
        env = ScenarioEnvironment(scenario)
        orch = Orchestrator(incident_id=scenario["incident_stub"]["incident_id"], store=store,
                             env=env, verbose=args.verbose)
        state = orch.run()
        result = score_scenario(scenario, state)
        results.append((scenario, result))
        if result["pass"]:
            passed += 1

    print(f"\n=== Adversarial Eval: {passed}/{total} scenarios passed ===\n")
    for scenario, result in results:
        status = "PASS" if result["pass"] else "FAIL"
        print(f"[{status}] {scenario['scenario_id']} ({scenario['pattern']})")
        for name, expected, actual, ok in result["checks"]:
            mark = "OK " if ok else "!! "
            print(f"    {mark}{name}: expected={expected!r} actual={actual!r}")
        if not result["pass"]:
            print(f"    notes: {scenario.get('notes')}")
    print()

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
