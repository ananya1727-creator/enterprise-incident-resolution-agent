# Evaluation Framework

Companion to [PROJECT_SPEC.md](PROJECT_SPEC.md) §16 (Evaluation Methodology). Defines the 12 measured metrics, the benchmark construction, and how to reproduce the generated report.

Run it:

```bash
python scripts/build_benchmark.py   # regenerates data/benchmark_incidents.json (deterministic, seeded)
python scripts/run_benchmark.py     # runs the agent against it, writes reports/EVALUATION_REPORT.md
```

The most recent generated report is at **[reports/EVALUATION_REPORT.md](../reports/EVALUATION_REPORT.md)**, with the full per-case raw data at `reports/evaluation_results.json`.

## 1. Benchmark Construction

**30 benchmark cases** (`data/benchmark_incidents.json`), built by `scripts/build_benchmark.py` from two sources:

- **10 adversarial scenarios**, reused from `data/eval_scenarios.json` (misleading errors, multiple plausible causes, shared-infrastructure failure, stale historical precedent, insufficient evidence, confirmed-cause-needs-escalation, appears-fixed-but-verification-fails, duplicate-from-prior-action, contradictory cross-source evidence, keyword trap). Two of these (`SCN-006` insufficient evidence, `SCN-009` contradictory evidence) have no single defensible ground-truth root cause and are marked `ground_truth_root_cause: null` — excluded from the root-cause-accuracy denominator, still scored on escalation appropriateness.
- **20 incidents sampled from the bulk fixture set** (`data/incidents.json`), covering all 11 root-cause categories at least once, weighted toward resolvable cases for category coverage plus a deliberate mix of `ambiguous_evidence`/`no_known_resolution` outcome buckets to stress escalation behavior, not just easy resolutions.

Each benchmark case carries:

```json
{
  "benchmark_id": "BENCH-INC-0041",
  "incident_id": "INC-0041",
  "ground_truth_root_cause": "auth_failure",
  "acceptable_diagnostic_tools": ["inspect_credentials", "get_recent_changes", "test_connectivity",
                                   "search_historical_incidents", "retrieve_runbook"],
  "acceptable_remediation": "refresh_connection",
  "unacceptable_actions": ["restart_interface", "retry_message", "rotate_certificate",
                             "update_adapter_timeout", "reprocess_message_batch", "update_routing_rule"],
  "expected_escalation": { "should_escalate": false, "acceptable_reasons": [] }
}
```

`acceptable_diagnostic_tools` and `acceptable_remediation` are derived programmatically from the same tables the agent itself uses (`agent/skills.py`'s `SKILL_TOOL_PRIORITY`, `agent/simulated_tools.py`'s `REMEDIATION_TOOL_BY_CATEGORY`) — this keeps "acceptable" consistent with the documented skills rather than an independently hand-guessed list that could silently drift from what the agent is actually designed to do. `expected_escalation.should_escalate` is `null` for the `ambiguous_evidence` bucket, meaning either outcome is legitimate depending on how the (deliberately mixed) evidence resolves — such cases are scored as escalation-appropriate regardless of outcome.

## 2. Metrics

| # | Metric | Definition | Computed from |
|---|---|---|---|
| 1 | **Root-cause identification accuracy** | Fraction of cases (excluding `null`-ground-truth ones) where the agent's final belief matches ground truth: the confirmed hypothesis's category if `RESOLVED`, or the top-ranked `Escalation.leading_hypotheses[0]` category if `ESCALATED`. | `state.resolution.root_cause` or `state.escalation.leading_hypotheses[0]` |
| 2 | **First-action accuracy** | Fraction of cases where the agent's *first self-chosen* diagnostic tool call (excluding the two fixed OBSERVE-stage triage calls, `search_logs`/`get_recent_changes`) is in `acceptable_diagnostic_tools`. | first non-OBSERVE `DiagnosticAction` |
| 3 | **Successful autonomous resolution rate** | Fraction of cases `RESOLVED` with **zero** `ApprovalRequest`s created during the run — i.e., handled entirely without any human decision point, not merely "resolved eventually." | `state.resolution.outcome == "Resolved"` and `len(state.approval_requests) == 0` |
| 4 | **Verification success rate** | Fraction of *all* `VerificationResult`s across the benchmark (not one per incident) with `outcome == Passed` — an event-level reliability measure of remediation attempts, since one incident can carry more than one verification. | `state.verification_results` |
| 5 | **Escalation appropriateness** | Fraction of cases where the actual escalate/resolve decision matches `expected_escalation` (and, when escalated, the reason is in `acceptable_reasons` if any are specified). | `state.stage`, `state.escalation.reason` |
| 6 | **Mean diagnostic steps** | Average count of agent-chosen (non-OBSERVE, non-verification) `DiagnosticAction`s per case — a proxy for investigation efficiency. | `state.investigation_cycle_count`-equivalent count |
| 7 | **Mean time to resolution** | Average of the simulated investigation clock (`state.simulated_elapsed_minutes`, architecture §7) at the terminal stage, across all cases regardless of outcome. | `state.simulated_elapsed_minutes` |
| 8 | **Human intervention rate** | Fraction of cases where at least one `ApprovalRequest` was created, or the case ended `ESCALATED` — i.e., needed a human decision point of any kind. | `state.approval_requests`, `state.stage` |
| 9 | **False remediation rate** | Fraction of cases where an **executed** `RemediationAction`'s tool is in `unacceptable_actions` (or, for categories with no acceptable remediation at all, any execution counts as false). This is independent of final outcome — a case can resolve "by luck" via an inappropriate tool and still register a false remediation. | executed `RemediationAction.tool_name` vs. `unacceptable_actions` |
| 10 | **Tool selection accuracy** | Per-case fraction of agent-chosen diagnostic calls that fall in `acceptable_diagnostic_tools`, macro-averaged across cases. **Caveat** (see §3 below): this only credits tools relevant to the *confirmed* category — legitimately investigating a competing hypothesis before ruling it out is expected agent behavior, not a defect, so this number is not a pure "mistake rate." | agent-chosen `DiagnosticAction.tool_name` vs. `acceptable_diagnostic_tools` |
| 11 | **Evidence utilization** | Fraction of all `Hypothesis.confidence_history` entries across a case whose cited `evidence_id` actually exists in `state.evidence` — the direct, mechanical check for the "confidence theater" anti-pattern (architecture §15): every confidence change must be attributable to real evidence, not asserted. | `Hypothesis.confidence_history` vs. `state.evidence` |
| 12 | **SLA breach rate** | Fraction of cases where the simulated SLA clock (architecture §7, `SLA_REMAINING_FORCE_DECIDE_THRESHOLD`) reached or passed the deadline (`state.sla_remaining_fraction() <= 0`) by the time a terminal stage was reached. | `state.sla_remaining_fraction()` |

## 3. Known Limitations of This Report

- **Metric 10 (tool selection accuracy) undercounts legitimate multi-hypothesis exploration.** The agent is designed to hold and test competing hypotheses simultaneously (architecture §5) — a benchmark case where the agent correctly explores and rules out a second, genuinely plausible category before confirming the true one will show a lower score here even though that's exactly the intended behavior, not a mistake. Read this metric alongside metric 1 (did it get there in the end) and metric 6 (how many steps it took), not in isolation.
- **Metric 3 (autonomous resolution rate) is sensitive to the risk-policy config** (`config/risk_policy.json`, [RISK_POLICY.md](RISK_POLICY.md)), not just agent skill. `restart_interface` currently requires approval by default in this simulated environment, so every `connectivity_failure`/`endpoint_unavailable`/`database_connection_failure` case that needs it will correctly register as *not* fully autonomous even when the agent's diagnosis and remediation choice were entirely correct. This is intentional (it's exercising the risk-based autonomy framework), but it means this metric measures policy configuration as much as agent behavior.
- **Metric 4/5/9 depend on the fixture oracle's designed outcome buckets** (`resolved_known_precedent`/`resolved_novel`/`ambiguous_evidence`/`no_known_resolution`, `scripts/generate_fixtures.py`) for the 20 bulk-fixture cases — they are measuring the agent against a simulated ground truth, not a real production system.
- **Small-n instability**: with 30 cases, per-category subsets (e.g., only 1 `timeout` case) can swing aggregate percentages a lot on a re-run with a different `--seed`. Treat single-digit-percentage differences between report runs as noise, not signal.
- **A systematic source of "wrong root cause, but still RESOLVED" cases**: `agent/simulated_tools.py`'s `REMEDIATION_TOOL_BY_CATEGORY` maps both `connectivity_failure` and `database_connection_failure` to the same tool, `restart_interface` (a real-world-accurate overlap — both are plausibly fixed by the same channel restart). When evidence is ambiguous between the two (as in `BENCH-INC-0037`, seen in a run of this report), the agent can correctly execute the right *action* while labeling the *category* differently from the fixture's designated ground truth — verification genuinely passes, because the tool really was the correct one for the true underlying fault, just not filed under the fixture's exact category name. This lowers metric 1 without indicating an actual remediation failure; check `reports/EVALUATION_REPORT.md`'s "Notable Failures" section and cross-reference the executed remediation tool before treating a "wrong root cause" case as a genuine diagnostic error.
- **Metric 9 (false remediation) needs to be read alongside the eventual outcome, not in isolation**: `BENCH-INC-0063` (`ground_truth_root_cause: malformed_payload`) is flagged false-remediation because the agent executed `rotate_certificate`. What actually happened: the fixture's deliberate `ambiguous_evidence` noise flipped `inspect_certificate_status` to report an expired certificate that wasn't the real cause; the agent reasonably acted on that direct (if misleading) evidence, requested and received approval, executed the rotation — and `verify_interface_health` correctly returned `Failed`. That failure refuted the certificate hypothesis, `malformed_payload` (which had separate, genuine `compare_with_previous_successful_run` evidence) rose to the top, and since no autonomous tool exists for that category, the agent escalated correctly (`NoAutonomousRemediation`) rather than false-resolving. This is the verification/retry/escalate loop working as designed under noisy input, not a safety failure — but it does mean a real approval and a real High-risk action were spent on a wrong hypothesis before self-correcting, which is exactly what metric 9 is meant to surface for scrutiny.

## 4. Regenerating the Report

The benchmark set is deterministic (`build_benchmark.py` is seeded), and the agent's reasoning is deterministic given a fixed fixture dataset, so re-running `python scripts/run_benchmark.py` against an unchanged `data/` directory reproduces the same report. Re-run it after any change to `agent/reasoning.py`, `agent/guardrails.py`, `agent/risk_policy.py`, `config/risk_policy.json`, or the skill/tool mapping tables, to see the effect on these 12 numbers directly rather than relying on the adversarial eval suite (`scripts/run_eval.py`) alone — that suite checks specific hand-authored scenarios pass/fail; this report gives the aggregate picture across a broader, more representative case mix.
