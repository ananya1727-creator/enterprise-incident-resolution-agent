"""
End-to-end test suite validating the core agent works before any dashboard
is built. Run with:

    pytest tests/test_e2e_agent.py -v

Covers 10 scenarios (data/e2e_scenarios.json) spanning: authentication
failure, expired certificate, SFTP connectivity failure, REST endpoint
failure, mapping/data-transformation failure, timeout/performance,
database connectivity failure, multiple interfaces sharing one dependency,
a genuinely ambiguous incident, and an incident that must escalate because
no safe remediation exists -- at least 3 of them adversarial (the obvious
first hypothesis is wrong).
"""
import pytest

from agent.data_store import DataStore
from tests.e2e_lib import load_scenarios, run_scenario, check_capabilities, agent_chosen_diagnostic_actions

SCENARIOS = load_scenarios()


@pytest.fixture(scope="module")
def store():
    return DataStore()


@pytest.fixture(scope="module")
def results(store):
    """Run every scenario once (module-scoped) and cache the outcomes so both
    the per-scenario tests and the cross-scenario "not a fixed workflow"
    test reuse the same executions instead of re-running the agent."""
    out = {}
    for scn in SCENARIOS:
        out[scn["e2e_id"]] = run_scenario(scn, store)
    return out


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["e2e_id"] for s in SCENARIOS])
def test_capabilities(scenario, results):
    state = results[scenario["e2e_id"]]
    checks = check_capabilities(scenario, state)
    failed = {name: detail for name, (passed, detail) in checks.items() if not passed}
    assert not failed, f"{scenario['e2e_id']} failed capability checks: {failed}"


def test_at_least_three_adversarial_scenarios_present():
    adversarial = [s for s in SCENARIOS if s.get("adversarial")]
    assert len(adversarial) >= 3, f"expected >=3 adversarial scenarios, found {len(adversarial)}"


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.get("adversarial")],
                          ids=[s["e2e_id"] for s in SCENARIOS if s.get("adversarial")])
def test_adversarial_trap_was_genuinely_tested_and_revised(scenario, results):
    """For each adversarial case, confirm the trap hypothesis was actually
    touched by evidence (not just ignored) and that the final answer moved
    away from it -- proof the agent revised its reasoning rather than
    committing to the first plausible signal."""
    state = results[scenario["e2e_id"]]
    trap = scenario.get("trap_category")
    if not trap:
        pytest.skip("no trap_category on this adversarial scenario")
    hyp = next((h for h in state.hypotheses if h.category == trap), None)
    assert hyp is not None and len(hyp.confidence_history) > 0, \
        f"trap hypothesis '{trap}' was never touched by any evidence"

    final_root = None
    if state.resolution and state.resolution.outcome == "Resolved":
        final_root = state.resolution.root_cause
    elif state.escalation and state.escalation.leading_hypotheses:
        final_root = state.escalation.leading_hypotheses[0]["category"]
    expected_roots = scenario.get("expected_root_cause") or []
    if expected_roots and trap not in expected_roots:
        assert final_root != trap, f"agent's final answer was still the trap category '{trap}'"


def test_not_a_fixed_workflow(results):
    """
    The core anti-pattern this whole system is designed against: a fixed
    classify->search->recommend->close chain. Assert the agent-chosen
    diagnostic tool SEQUENCES differ across scenarios -- if every incident
    produced the identical tool order, that would mean the agent is
    following a script rather than reasoning from each incident's evidence.
    """
    sequences = {}
    for e2e_id, state in results.items():
        seq = tuple(a.tool_name for a in agent_chosen_diagnostic_actions(state))
        sequences[e2e_id] = seq

    distinct_sequences = set(sequences.values())
    assert len(distinct_sequences) > 1, (
        f"all {len(sequences)} scenarios produced the identical tool-call sequence "
        f"{next(iter(distinct_sequences))} -- this would indicate a fixed workflow, not dynamic reasoning"
    )
    # Stronger bar: given 10 genuinely different symptom profiles, expect
    # meaningfully more variety than "two sequences, one shared by everyone."
    assert len(distinct_sequences) >= 6, (
        f"only {len(distinct_sequences)} distinct tool sequences across {len(sequences)} scenarios "
        f"-- expected broader variety for this many genuinely different symptom profiles: {sequences}"
    )


def test_no_approval_bypass_across_all_scenarios(results):
    """Structural safety check independent of any single scenario's
    expectations: no executed remediation, anywhere in the suite, ever ran
    without a valid granted approval when the risk policy required one."""
    from agent import risk_policy
    violations = []
    for e2e_id, state in results.items():
        for rem in state.remediation_actions:
            if rem.status.value != "Executed":
                continue
            if risk_policy.requires_approval(rem.tool_name):
                req = next((r for r in state.approval_requests
                           if r.approval_request_id == rem.approval_request_id), None)
                if not req or req.decision != "approved":
                    violations.append((e2e_id, rem.tool_name))
    assert not violations, f"approval bypass detected: {violations}"
