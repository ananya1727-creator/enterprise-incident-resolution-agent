# Skill: Message Processing Failure Investigation

## When This Skill Is Relevant

Messages are stuck (accepted but never completing), duplicated, processed out of order, or backing up in a queue — without a clean transport-layer error (that would point to Connectivity), auth error (Authentication), cert error (Certificate), or payload/schema error (Mapping/Transformation). Also relevant as a landing point when another skill's investigation rules out its own hypothesis and the remaining symptom is best described as "message got in, but something about its processing/state is wrong" rather than "message couldn't get in at all."

## Evidence to Gather

- Detailed log scan for the specific message(s) in question — state transitions, timestamps, whether the same message ID appears more than once (duplicate signal), whether messages appear out of the expected sequence (`search_logs`).
- Comparison against a known-good run for the same message type, to see whether the processing pattern (timing, state sequence) differs structurally from normal (`compare_with_previous_successful_run`).
- Recent changes, particularly deployment changes to queue/processing configuration (`get_recent_changes`).
- Historical precedent — stuck-message and duplicate-message patterns are often tied to specific known bugs/conditions that may already be documented (`search_historical_incidents`, `retrieve_runbook`).
- Broader channel health, since a backlog of stuck messages can be a symptom of an underlying channel/queue block rather than a problem with any individual message (`search_logs` across a wider set of messages on the same interface, in addition to the one originally flagged).

## Diagnostic Reasoning Approach

This skill covers a cluster of related-but-distinct symptom shapes (stuck, duplicated, out-of-order, backlogged) that share the property of "the message got past connectivity/auth/cert/mapping but something about its lifecycle state is wrong." The first reasoning task is disambiguating *which* of these it is, since the shape strongly constrains plausible fixes: a single stuck message is often addressable with `retry_message`, whereas a systemic backlog across many messages usually indicates a channel/queue-level block, which is a different scope of problem (and different remediation — a channel-level action, not a per-message one) even though it may look like "many stuck messages" at first glance. The agent should look at *how many* messages are affected and whether they share a common entry point/timing (suggesting a shared queue block) before deciding whether this is a single-message or channel-level issue. Duplicate-message and out-of-order signatures often correlate with a retry or restart having already happened (possibly by another actor, or an earlier remediation attempt in this same incident) — the agent should check whether an earlier action in `IncidentState.remediation_actions` could itself be the cause before assuming an independent root cause.

## Possible Hypotheses

- `duplicate_message` — a message was processed more than once, likely due to a retry racing with original processing, or a restart replaying in-flight messages
- Single-message stuck/hung state — an individual message entered a bad state without a clear transport/auth/cert/mapping error, and is not progressing
- Channel/queue-level congestion — many messages backed up behind a block, not each message being individually broken (overlaps with Timeout/Performance's `queue_congestion` hypothesis)
- Out-of-order processing causing downstream logical errors even though each message individually "succeeded" in transport

## Evidence That Confirms/Refutes Each Hypothesis

- The same message ID appearing more than once in logs, especially correlated with a recent `restart_interface` or `retry_message` action already in `IncidentState`, strongly confirms the duplicate-message hypothesis and should point the agent to consider whether an earlier remediation in *this* incident (or a concurrent human action) caused it, rather than treating it as an independent new root cause.
- A single message stuck at the same processing state across repeated log checks over time, with no error logged and no evidence of transport/auth/cert/mapping problems (already ruled out or never indicated), supports the single-message-hung hypothesis.
- Many distinct message IDs all stuck at the same processing stage, entering that stage around the same time, supports channel/queue-level congestion over independent single-message failures — this should redirect the remediation scope from per-message to channel-level.
- Downstream logical errors (e.g., a dependent record processed before its prerequisite) without any individual message-level error supports the out-of-order hypothesis, which needs different handling (potentially process-level correction, likely outside this tool catalog) than a simple retry.

## Tools That May Be Used

`search_logs`, `compare_with_previous_successful_run`, `get_recent_changes`, `search_historical_incidents`, `retrieve_runbook`, `retry_message` (remediation, single-message hypothesis), `restart_interface` (remediation, channel-level congestion hypothesis), `create_escalation`, `update_incident`.

## Remediation Options

- **`retry_message` (Low risk, autonomous)** — appropriate for the single-message-hung hypothesis, and explicitly *not* appropriate if duplicate-message evidence is present (retrying an already-duplicated message risks a second duplicate) — the agent should resolve the duplication concern first (likely via escalation or a data-correction step outside this tool catalog) rather than retry into an already-ambiguous state.
- **`restart_interface` (Medium risk, autonomous with notify)** — appropriate for the channel/queue-congestion hypothesis where many messages are backed up behind a shared block, rather than calling `retry_message` individually on each one (which wouldn't address a shared underlying block and would be an inefficient, evidence-blind use of remediation attempts).
- **No remediation tool exists for the out-of-order/duplicate-correction case** beyond retrying a genuinely-stuck-but-not-yet-duplicated message — actual de-duplication or re-sequencing of already-processed messages is a data-correction action outside this catalog's scope; escalate.

## Verification Requirements

After `retry_message` on a stuck message, verification must confirm the message reaches a genuinely different, terminal, successful state (`verify_message_processing`), not merely that it moved out of its previous stuck state into some other non-terminal state (which could itself be a new form of stuck). After `restart_interface` for congestion, verification must check that the broader backlog is actually draining (re-scan of the wider message set, not just the one message that prompted the investigation) — a single message succeeding post-restart is weak evidence that the whole backlog issue is resolved.

## Escalation Conditions

- Duplicate-message evidence is found — the agent should not attempt further remediation that could compound duplication; escalate with the specific message IDs and suspected cause (e.g., a preceding restart in this incident).
- Out-of-order processing causing downstream logical inconsistency — no tool addresses re-sequencing or logical correction; escalate with the specific sequence evidence.
- A restart is executed for suspected congestion but the backlog does not drain on recheck — escalate rather than repeating the restart, since this suggests the block has a cause the agent cannot address (e.g., a downstream system unable to keep up, overlapping with Timeout/Performance's downstream-degradation hypothesis).
