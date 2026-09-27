# Skill: Incident Escalation

## When This Skill Is Relevant

Not a root-cause investigation skill — this governs *how* to hand off to a human, and is relevant whenever any other skill (or the general Incident Investigation skill) determines escalation is the right next step, or whenever the orchestrator's guardrails force a transition to `ESCALATED` (architecture §3.1, §7: cycle ceilings, remediation-attempt ceilings, approval timeout, exhausted verification retries). It is also relevant as a constant background consideration throughout every other skill — every specialist skill above lists its own escalation conditions, and this skill defines the shared standard those conditions feed into, so that an escalation is equally well-formed regardless of which investigation path produced it.

## Evidence to Gather

Escalation itself doesn't require *new* evidence-gathering tool calls in the way diagnostic skills do — its job is to assemble what's already in `IncidentState` into a brief a human can act on without re-deriving the investigation from scratch. That said, before finalizing an escalation the agent should consider one pass of:

- Confirming the current top-ranked hypotheses and their confidence scores are up to date with the latest evidence (no stale/unrevised hypothesis left at the top of the ranking).
- Confirming every attempted action (diagnostic and remediation) and its outcome is accounted for, so the human isn't missing something already tried.
- Checking `search_historical_incidents`/`retrieve_runbook` one last time if not already done, in case a documented procedure exists for exactly this unresolved situation (worth surfacing to the human even if the agent itself couldn't execute it).

## Diagnostic Reasoning Approach

Escalation quality is judged on whether a human receiving the brief can pick up productively *without* re-running the investigation from zero — this is the single design goal for this skill. The reasoning task is compression with fidelity: include the leading hypotheses (plural, if genuinely still competing — don't force a false single "the" answer if evidence didn't actually converge), the specific evidence for and against each, everything already attempted and its result (including rejected/blocked proposals — a blocked high-risk action the human still needs to authorize is important context, not noise), and a precise statement of what remains unknown or unresolved. The agent must resist two failure modes symmetrically: **under-escalating** detail (a vague "couldn't figure it out" brief that forces the human to start over) and **over-escalating** detail (a raw dump of every tool call/log line with no synthesis, which is equally unusable under time pressure). The right level is the same one a competent human investigator would leave for the next shift: hypothesis-first, evidence-cited, action-history-included.

## Possible "Hypotheses" (Escalation Reason Categories)

These map to `Escalation.reason` (architecture §2.7), and are themselves worth stating explicitly and accurately in the brief rather than defaulting to a generic reason:

- `LowConfidence` — investigation completed within available cycles, but no hypothesis reached sufficient confidence to justify remediation.
- `RiskTooHigh` — a high-confidence hypothesis exists with a corresponding remediation, but that remediation's risk tier requires approval that either wasn't sought yet, was rejected, or timed out.
- `CyclesExhausted` — the investigation cycle ceiling was reached before convergence.
- `RetriesExhausted` — a remediation was attempted (possibly more than once, per the retry ceiling), verification failed, and no further attempts are permitted.
- `ApprovalTimeout` — a high-risk action was proposed and awaited approval, but no response arrived within the configured window.
- `ContradictoryEvidence` — evidence from different sources/tools genuinely conflicts in a way the agent cannot resolve with available tools (e.g., metadata says decommissioned, logs show live traffic).

## Evidence That Confirms/Refutes Each Escalation Reason

This is more a matter of accurate bookkeeping than inference: the correct `reason` value should be directly derivable from `IncidentState` (which counters hit their ceiling, whether an `ApprovalRequest` is pending/timed-out/rejected, whether the top hypothesis's confidence is below the acting threshold, whether two evidence items in state directly contradict each other). The agent should not select a reason that doesn't match what actually happened in state — e.g., labeling something `LowConfidence` when the real blocker was actually `RiskTooHigh` (a confident hypothesis existed, but no approval was obtained) would mislead the human about what kind of help is actually needed (a technical second opinion vs. an approval decision).

## Tools That May Be Used

`create_escalation` (the terminal action for this skill), `update_incident` (to keep the ticket-of-record synchronized with the escalation), `search_historical_incidents`, `retrieve_runbook` (a final check, per Evidence to Gather above). No diagnostic-deep-dive or remediation tools are called *from within* this skill — those belong to whichever specialist skill was active before escalation was triggered.

## Remediation Options

None — by definition, this skill is invoked precisely when the agent has determined autonomous remediation is not appropriate (either no sufficiently confident/low-risk option exists, or a higher-risk option needs human authorization it hasn't received). `create_escalation` is an information/handoff action, not a technical fix.

## Verification Requirements

Escalation "verification" is different in kind from remediation verification: there's no system state to re-check for a fix, but the *escalation brief itself* should be checked against a quality bar before being finalized — specifically, that `Escalation.leading_hypotheses`, `actions_attempted`, and `unresolved_questions` are all non-empty/populated where the investigation actually produced content for them (an escalation with an empty `unresolved_questions` list after a `LowConfidence` reason, for instance, is internally inconsistent — if confidence was low, there should be at least one stated open question), and that `update_incident` is called so the record-of-truth reflects the escalation, not just the internal `IncidentState`.

## Escalation Conditions (Meta)

This skill is itself the escalation destination, so it has no further escalation path — but it does have a hard requirement: escalation must always remain reachable and executable regardless of any other guardrail state (architecture §3, "safe exit" invariant; spec §12, §15 anti-goal #8). If, hypothetically, `create_escalation` itself failed (simulated tool-level error per [TOOL_SPEC.md](../TOOL_SPEC.md) §3.4 failure modes), the orchestrator — not this skill/the agent — is responsible for a hard fallback (e.g., logging a critical failure and surfacing it through the Observability layer) so that a broken escalation tool can never silently strand an incident with no human-visible outcome at all.
