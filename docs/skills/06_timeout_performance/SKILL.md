# Skill: Timeout/Performance Investigation

## When This Skill Is Relevant

Logs show timeout-specific errors, elevated latency without hard failure, or a "degraded but not down" pattern — distinguishable from Connectivity's clean unreachable/reachable split by the presence of *slow but eventually responding (or responding-then-timing-out)* behavior. Also relevant when `test_connectivity` (run under another skill) reports `reachable: true` with unusually high `latency_ms`, which should pull this skill in even if the initiating symptom looked connectivity-shaped.

## Evidence to Gather

- Connectivity/latency check on source and target endpoints, focusing on `latency_ms` rather than just the boolean `reachable` (`test_connectivity`).
- Log scan for timeout-specific error codes and their timing pattern — constant slowness vs. intermittent spikes vs. a step-change onset (`search_logs`).
- Adapter-level configuration, specifically the currently configured timeout threshold, to judge whether failures are "genuinely too slow" vs. "an overly aggressive timeout setting for otherwise-acceptable latency" (`get_adapter_config`-equivalent evidence, or inferred from `compare_with_previous_successful_run` if timing fields are included in the diff).
- Recent changes, especially deployment/config changes that might have altered timeout thresholds, payload sizes, or downstream processing load (`get_recent_changes`).
- Historical precedent — performance degradation is often recurring (e.g., load-related, time-of-day-related) and prior incidents may reveal a pattern the agent should look for directly (`search_historical_incidents`).

## Diagnostic Reasoning Approach

The core discrimination this skill must make is **where the latency lives**: network path, target system processing time, or an overly strict local timeout threshold relative to genuinely-acceptable-but-slow responses. `test_connectivity`'s `latency_ms` output is the primary signal for network-path latency; log timing patterns (specifically whether failures cluster at a threshold value matching the configured timeout, vs. failures scattered across widely varying delays) help distinguish "borderline latency hitting a strict threshold" from "genuinely broken/very slow downstream processing." The agent should be wary of treating "increase the timeout" as a default fix — per spec's risk framing, `update_adapter_timeout` is High-risk and persistent, so it should only be proposed when evidence specifically supports "latency is consistently just over threshold and otherwise healthy," not as a first response to any timeout-shaped symptom. A step-change onset correlated with a recent deployment/config change should raise suspicion that something *changed* to cause new load or slowness, rather than that the timeout was always too strict.

## Possible Hypotheses

- `timeout` — network path latency has increased (infrastructure/routing change, congestion)
- `timeout` — downstream/target system itself is processing slowly (increased load, degraded downstream service, unrelated to our configuration)
- `timeout` — local timeout threshold is simply too strict for genuinely-acceptable (if slow) response times, with no actual degradation upstream
- `queue_congestion` — messages are backing up locally, so effective processing time increases even though per-message downstream latency hasn't changed (overlaps with Message Processing Failure skill)

## Evidence That Confirms/Refutes Each Hypothesis

- `test_connectivity` showing elevated `latency_ms` on the network probe itself (not just application-level timeouts in logs) supports a network-path latency hypothesis over a downstream-processing or threshold hypothesis.
- Failures clustering right around the currently configured timeout value, with the underlying operation seemingly completing successfully shortly after (visible via retry patterns or a subsequent successful reprocessing) supports the "threshold too strict" hypothesis specifically — this is the one hypothesis in this skill that plausibly justifies `update_adapter_timeout`.
- A recent deployment/config change to the downstream/target system correlated with onset, with connectivity `latency_ms` normal but application-level processing consistently slow, supports the downstream-system-degradation hypothesis (which the agent cannot fix directly — points to escalation, not a local timeout increase).
- Growing backlog/queue depth signals (if surfaced via logs or a queue-status-shaped tool result) rather than per-message slowness supports `queue_congestion` over a pure timeout hypothesis, and points toward `clear_queue_block`-type remediation (see Message Processing Failure skill) rather than a timeout adjustment.
- No recent change, stable historical timeout threshold, and no historical precedent of this being borderline — refutes the "threshold too strict" hypothesis and should raise suspicion of a genuine, newer degradation instead.

## Tools That May Be Used

`test_connectivity`, `search_logs`, `get_recent_changes`, `compare_with_previous_successful_run`, `search_historical_incidents`, `retrieve_runbook`, `update_adapter_timeout` (remediation, High risk), `retry_message` (remediation, for individual timed-out messages), `create_escalation`, `update_incident`.

## Remediation Options

- **`update_adapter_timeout` (High risk, requires human approval)** — appropriate *only* when evidence specifically supports the "threshold too strict relative to consistently-recoverable latency" hypothesis. Because it's a persistent config change affecting all future traffic, the agent must construct a well-evidenced `ApprovalRequest` (specific latency figures, how they relate to the current vs. proposed threshold, why this isn't masking a genuine degradation) rather than proposing it reflexively on any timeout symptom.
- **`retry_message` (Low risk, autonomous)** — appropriate for individual messages that timed out during a transient spike, once evidence suggests the underlying latency issue has subsided (e.g., a later connectivity check shows normal latency).
- **No remediation tool directly addresses genuine downstream-system degradation** (target system itself slow) — this is outside the agent's control surface; the correct action is `create_escalation` to notify the owning team of the target system, not a local timeout increase (which would only mask, not fix, a real downstream problem, and risks allowing genuinely broken requests to hang longer before failing).

## Verification Requirements

After `update_adapter_timeout`, verification must confirm actual message processing now completes successfully within the new threshold (`verify_message_processing`) across more than a single instance if possible, not just that the config value was accepted — a single successful retry after a timeout increase is weak evidence, since it doesn't rule out the config change simply giving a still-degrading downstream system more rope before an eventual harder failure. If verification shows continued failures even at the new threshold, this strongly suggests genuine downstream degradation rather than a threshold problem, and the agent should pivot to escalation rather than proposing a further timeout increase.

## Escalation Conditions

- Evidence points to genuine downstream/target system degradation rather than a local threshold issue — no local remediation tool addresses this; escalate with the specific latency pattern and affected downstream system.
- `update_adapter_timeout` approved and executed, but verification still shows failures at the new threshold — escalate rather than propose a further increase, since this suggests the hypothesis was wrong or the degradation is worsening.
- Latency evidence is too intermittent/inconsistent to confidently attribute to network path, downstream system, or threshold — escalate with the raw pattern rather than guessing at a High-risk config change on ambiguous evidence.
