# Skill: Incident Investigation

## When This Skill Is Relevant

Always, at the start of every incident. This is the entry/triage skill — it doesn't diagnose a specific root-cause class itself, it establishes enough context to decide *which specialist skill(s)* (Auth, Connectivity, Certificate, Mapping, Timeout, Message Processing) the agent should draw on next, and surfaces criticality/SLA context that shapes every later decision. It remains relevant throughout the incident as a fallback whenever evidence stops fitting any specialist skill cleanly, or when two specialist hypotheses conflict and need reconciling at a higher level.

## Evidence to Gather

- Incident intake facts: reporter, symptom description, reported severity, affected interface reference (`get_incident`).
- Interface identity and context: criticality tier, SLA, ownership, known fragility flags, decommission status (`get_interface_metadata`).
- A broad log scan across a reasonable recent window, unfiltered or lightly filtered, to see the *shape* of the failure (error codes present, frequency, whether it's one message or many, whether it's total silence vs. active errors) (`search_logs`).
- Recent changes to the interface or adjacent systems, since a change immediately before failure onset is one of the strongest generic priors in enterprise integration incidents (`get_recent_changes`).
- Prior incidents on this interface or with a similar coarse signature, to see if this is a recurring, already-understood pattern (`search_historical_incidents`).

Not every item above needs to be gathered before moving on — the agent should stop triaging and route into a specialist skill as soon as the symptom shape points somewhere specific (e.g., a log scan immediately showing `TLS handshake failed` should pull in Certificate Failure Investigation without needing to also exhaustively check recent changes first).

## Diagnostic Reasoning Approach

This skill is about **shaping the hypothesis space**, not resolving it. The reasoning move is: look at the coarse symptom signature (log error-code patterns, whether the failure is total or partial, whether it correlates with a recent change, whether business-criticality framing conflicts with technical severity) and form an initial, deliberately broad set of candidate `RootCauseCategory` values with rough, low-confidence weights — then hand off deeper discrimination to the relevant specialist skill(s). Reconciling the SLA/criticality signal against the reported severity is also part of this skill: a mismatch (e.g., a P1 ticket against a `decommissioned: true` interface) is itself a finding worth surfacing, potentially warranting escalation for ticket-data-quality reasons rather than a technical root cause at all.

This skill is also the place where competing specialist hypotheses get reconciled if evidence supports more than one class at once (e.g., logs show both an auth error *and* a recent cert rotation) — the agent should not silently pick one skill to "win"; it should keep both hypothesis families active until discriminating evidence arrives.

## Possible Hypotheses (coarse, pre-specialist)

- Not yet a specific `RootCauseCategory` — this skill's job is to narrow from "unknown failure" to a short list of plausible categories (`auth_failure`, `timeout`, `cert_expiry`, `mapping_error`, `partner_outage`, `queue_congestion`, `duplicate_message`, `encoding_error`, `other`) with rough priors, informed by symptom shape:
  - Total silence, no errors logged at all → lean toward connectivity/partner-outage or channel-down (Connectivity skill).
  - Active errors with auth-specific codes/messages (401, 403, "unauthorized", "token expired") → Authentication skill.
  - TLS/handshake-specific errors → Certificate skill.
  - Errors correlated with payload content, schema validation failures → Mapping/Data Transformation skill.
  - Slow responses, timeouts, degraded-but-not-down → Timeout/Performance skill.
  - Message stuck/duplicated/out-of-order without a clear transport error → Message Processing Failure skill.
- **Not-a-technical-incident hypothesis**: ticket-data-quality issue (wrong interface referenced, already-resolved, decommissioned system) — this is a legitimate outcome of this skill and can lead directly to `update_incident`/`create_escalation` without any specialist skill being invoked.

## Evidence That Confirms/Refutes Each (Coarse) Hypothesis

- Recent-change correlation strongly raises priors on whichever category matches the change type (`certificate` change type → raises cert_expiry prior; `credential_rotation` → raises auth_failure prior; `config`/`deployment` → raises mapping_error or timeout priors depending on what changed).
- A high-similarity `search_historical_incidents` match with `human_feedback: Correct` substantially raises the prior on that match's `root_cause`, but should not be treated as confirmed without at least one piece of fresh corroborating evidence from this incident.
- Reported severity wildly inconsistent with interface criticality/decommission status refutes the premise that this is a normal technical incident at all and should raise the "ticket-data-quality" hypothesis.

## Tools That May Be Used

`get_incident`, `get_interface_metadata`, `search_logs`, `get_recent_changes`, `search_historical_incidents`, `retrieve_runbook`. This skill does not itself call any diagnostic-deep-dive or remediation/verification tools — those belong to the specialist skills once routed.

## Remediation Options

None directly — this skill routes to a specialist skill (or to Incident Escalation if the coarse triage itself surfaces a non-technical issue, e.g. malformed ticket data, in which case `update_incident` closing/redirecting the ticket may be the appropriate action without any remediation tool at all).

## Verification Requirements

Not applicable directly — verification is owned by whichever specialist skill's remediation eventually executes. This skill's own "success" criterion is simply: has the hypothesis space been narrowed enough that a specialist skill (or direct escalation) is clearly indicated. If it hasn't after a reasonable amount of coarse evidence-gathering, that itself is a signal (see Escalation Conditions below).

## Escalation Conditions

- Coarse evidence remains genuinely ambiguous across 3+ candidate categories with no discriminating signal after the incident-level evidence menu above has been exhausted — hand off to Incident Escalation with all candidate hypotheses listed, rather than guessing which specialist skill to force.
- Incident references a decommissioned or unknown interface, or other data-quality problems that make technical diagnosis meaningless until a human corrects the ticket.
- Any contradiction between multiple data sources that this skill cannot itself resolve (e.g., metadata says `decommissioned: true` but logs show live traffic) — this is a system-of-record inconsistency, not a diagnosable technical fault, and should be escalated as such.
