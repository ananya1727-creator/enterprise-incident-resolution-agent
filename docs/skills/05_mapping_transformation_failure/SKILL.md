# Skill: Mapping/Data Transformation Failure Investigation

## When This Skill Is Relevant

Errors correlated with specific message content rather than the interface as a whole (some messages succeed, others fail), schema validation errors, mapping exceptions, field-not-found/type-mismatch errors in logs, or encoding-error-shaped signatures. Distinguished early from Auth/Connectivity/Certificate/Timeout skills by its characteristic *partial* failure pattern — a mapping problem rarely takes down 100% of traffic unless a shared/global mapping component broke, whereas most other classes here tend toward all-or-nothing failure.

## Evidence to Gather

- Diff between the specific failing message and the interface's last known-good run — field-level, timing, endpoint, config differences (`compare_with_previous_successful_run`).
- Broader log scan for the specific error class (schema validation, mapping exception, encoding error) and its frequency/pattern across recent messages (`search_logs`).
- Recent `config`/`deployment` changes, since mapping logic itself is a deployable artifact and a recent mapping/schema deployment is a strong prior (`get_recent_changes`).
- Whether the failure affects all messages of a particular sub-type (e.g., only orders above a certain size, only messages from a particular source-side variant) vs. genuinely random — inferable from patterns across multiple `search_logs`/`compare_with_previous_successful_run` calls on different failing messages if more than one is available.
- Historical precedent, since mapping issues are often introduced by specific deployments and may recur across similar message variants (`search_historical_incidents`).

## Diagnostic Reasoning Approach

The key discriminator for this class is **partiality with structure**: not just "some messages fail" (which could be many things) but "the messages that fail share an identifiable characteristic distinguishing them from the messages that succeed." `compare_with_previous_successful_run` is the primary tool for finding that structure — it directly surfaces field-level diffs between a failing case and a known-good baseline, which is usually far more efficient than manually reasoning about log text. The agent should be cautious about over-indexing on a single diff result, though: a diff might surface many superficial differences (timestamps, IDs) that are not causal, alongside the one or two that are. Weighing `get_recent_changes` (was a mapping/schema change deployed recently) against the diff content (does the diff plausibly relate to that change) together is stronger evidence than either alone. If multiple failing messages are available, comparing diffs across them to find the common structural difference is stronger evidence than a single diff.

## Possible Hypotheses

- `mapping_error` — a mapping/transformation deployment introduced a defect affecting a specific message shape or field
- `encoding_error` — character encoding/format mismatch (e.g., a source-side encoding change, or messages containing characters the mapping doesn't handle)
- Schema drift — the source or target system's data contract changed without a corresponding mapping update (distinct from a mapping *deployment* defect — here the mapping hasn't changed but the data has)
- Reclassify — apparent mapping failure is actually a `duplicate_message` or ordering issue being misread as a transformation error (redirect to Message Processing Failure skill)

## Evidence That Confirms/Refutes Each Hypothesis

- A recent `config`/`deployment` change to the interface's mapping/transformation logic, combined with a diff showing the failure correlates with a field/shape that plausibly relates to that change, strongly confirms the mapping-deployment-defect hypothesis.
- A diff showing character-set/encoding-specific differences (unexpected byte sequences, unusual characters in a text field) supports the encoding-error hypothesis over a general mapping defect.
- Failures correlating with a *specific upstream data shape* that has no corresponding recent deployment on our side (no mapping change, but the data itself now includes a new field/value/format) supports schema drift over a mapping-deployment defect — this distinction matters for remediation, since a mapping deployment defect might warrant a rollback-style fix (outside this tool catalog, likely an escalation) while schema drift needs the mapping to be *updated* to handle new data (also outside this catalog, an escalation to the mapping-owning team).
- Diff results with no meaningful structural difference despite consistent failure refute all hypotheses in this skill and should redirect the agent to reconsider Connectivity/Timeout explanations instead.

## Tools That May Be Used

`compare_with_previous_successful_run`, `search_logs`, `get_recent_changes`, `search_historical_incidents`, `retrieve_runbook`, `retry_message` (remediation, only appropriate in narrow circumstances — see below), `create_escalation`, `update_incident`.

## Remediation Options

- **`retry_message` (Low risk, autonomous)** is appropriate *only* if the agent has reason to believe the specific message's failure was transient/non-structural (e.g., a one-off data glitch on the source side that's already been corrected upstream, evidenced by other similar messages processing fine before and after) — it is explicitly **not** an appropriate remediation for a confirmed mapping-deployment defect or schema-drift hypothesis, since retrying an unchanged, still-defective mapping against the same message shape will predictably fail again and would misleadingly look like "trying to fix it" while wasting a remediation attempt.
- **No autonomous or approvable remediation tool exists** for an actual mapping-logic defect or schema-drift fix in this catalog — correcting mapping logic is a development/deployment action outside the agent's tool surface. The correct action is `create_escalation` to the mapping-owning team with the specific diff evidence, not a repeated retry.

## Verification Requirements

If `retry_message` is used for a suspected transient/non-structural case, verification must confirm the specific message now processes successfully end-to-end (`verify_message_processing`) — and critically, if it fails again with the *same* error signature, this refutes the "transient" framing and should immediately redirect toward the mapping-defect/schema-drift hypotheses and escalation, rather than a second retry.

## Escalation Conditions

- A structural, reproducible mapping/schema defect is identified (via diff + recent-change correlation) — no tool in this catalog can fix mapping logic; escalate to the owning team with the diff evidence, affected message shape, and suspected deployment/change reference.
- Schema drift identified (data shape changed upstream, no corresponding mapping update exists) — escalate with the specific new field/format observed, since this needs a mapping update, not a retry.
- `retry_message` attempted on a suspected-transient case but fails again with the same signature — escalate rather than retry further, since evidence now contradicts the transient hypothesis.
