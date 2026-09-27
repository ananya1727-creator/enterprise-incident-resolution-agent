# Skill: Authentication Failure Investigation

## When This Skill Is Relevant

Logs show auth-shaped error signatures (401/403 status codes, "unauthorized," "invalid token," "access denied," "credential rejected"), or `get_recent_changes` shows a `credential_rotation` change type near the failure onset, or `search_historical_incidents` surfaces prior incidents on this interface with `root_cause: auth_failure`. Can be pulled in mid-investigation from the general Incident Investigation skill or from another specialist skill (e.g., a Connectivity investigation that finds the endpoint reachable but rejecting the connection at the auth layer).

## Evidence to Gather

- Credential status for the interface's adapter: type, validity, lock/expiry state, last rotation time (`inspect_credentials`).
- Recent changes specifically filtered toward credential/config changes, if not already pulled by the entry skill (`get_recent_changes`).
- Log entries specifically around the auth handshake/request stage, not just generic errors, to distinguish "credential rejected" from "credential never sent" from "permission insufficient" (`search_logs` with an auth-focused filter).
- Whether the failure is total (every message failing auth) or partial (some succeed) — a partial pattern is inconsistent with most auth hypotheses and should redirect suspicion elsewhere (`search_logs`, `compare_with_previous_successful_run`).
- Any historical precedent for this exact interface/credential type (`search_historical_incidents`).

## Diagnostic Reasoning Approach

Auth failures are usually binary at the transport layer (either the credential is accepted or it isn't), which makes them relatively easy to *detect* but requires care to *localize*: expired credential vs. locked account vs. recently-rotated-but-not-propagated vs. insufficient permission scope vs. downstream system itself misconfigured to reject a still-valid credential. The reasoning move is to use `inspect_credentials` as the primary discriminator (it directly reports status), then use recent-changes correlation and log detail to decide *which* auth failure mode is in play, since the remediation differs meaningfully between them (a locked account needs a different fix path than an expired token, and neither is something this agent should attempt to un-lock/reset directly — see Remediation Options). A near-total, sudden-onset failure pattern correlated with a `credential_rotation` change is the strongest, most common signature; the agent should still check for it explicitly rather than assume it from the change log alone, since the rotation could be unrelated.

## Possible Hypotheses

- `auth_failure` — credential expired
- `auth_failure` — credential locked/revoked
- `auth_failure` — credential recently rotated, adapter still using stale cached value
- `auth_failure` — permission scope insufficient (credential valid but lacks required access)
- Reclassify away from auth entirely — e.g., target system is actually down and returning a generic error that only superficially resembles an auth rejection (redirect to Connectivity skill)

## Evidence That Confirms/Refutes Each Hypothesis

- `inspect_credentials` returning `credential_status: expired` strongly confirms the expiry hypothesis; `locked`/`revoked` confirms the locked/revoked hypothesis; `valid` refutes both and shifts weight toward the stale-cache or permission-scope hypotheses, or away from auth entirely.
- A `credential_rotation` change in `get_recent_changes` within a short window before failure onset, combined with `credential_status: valid`, is the specific signature for "recently rotated, adapter cache stale" — valid credential but still failing suggests the adapter itself hasn't picked up the new value yet.
- `permission_scope_sufficient: false` (when determinable) directly confirms the permission-scope hypothesis; `null`/undeterminable neither confirms nor refutes and should lower confidence in *all* auth hypotheses slightly since the tool couldn't fully characterize the credential.
- A partial failure pattern (some messages succeeding) strongly refutes most auth hypotheses, since auth is normally interface-wide, and should redirect the agent to consider message-specific causes (Mapping/Message Processing skills) instead.
- Target system reachable at the network layer (per a connectivity check) but still rejecting requests supports auth-layer localization over a pure connectivity/partner-outage explanation.

## Tools That May Be Used

`inspect_credentials`, `get_recent_changes`, `search_logs`, `compare_with_previous_successful_run`, `search_historical_incidents`, `retrieve_runbook`, `test_connectivity` (to rule out a pure network cause before committing to auth), `refresh_connection` (remediation), `create_escalation`, `update_incident`.

## Remediation Options

- **`refresh_connection` (Medium risk, autonomous)** — appropriate specifically for the "stale cached credential/session" hypothesis, where the underlying credential is valid but the adapter's active session/token needs to be re-established. Not appropriate for expired/locked/revoked credentials, since refreshing a session built on a bad credential will simply fail again.
- **No autonomous fix exists for expired/locked/revoked credentials or insufficient permission scope** in this tool catalog — these require a human/owning-team action outside the agent's remediation surface (credential reset, account unlock, permission grant are all identity-system changes, not adapter-level changes). The correct agent action for these is `create_escalation` (or `notify_owning_team` via the Medium-risk side effect of a related action) rather than forcing a remediation attempt that has no available tool.
- The agent must not attempt `refresh_connection` repeatedly against a confirmed expired/locked credential as a substitute for escalation — that would violate the remediation-attempt ceiling for no diagnostic benefit, since evidence already rules out the hypothesis it would address.

## Verification Requirements

After `refresh_connection`, verification must confirm not just that a new session was established (`refreshed: true`) but that an actual subsequent request/message using that session succeeds (`verify_message_processing` on a real or synthetic follow-up request), since a refreshed session can still fail if the underlying stale-cache hypothesis was wrong. A "session established" result with a still-failing follow-up request should be treated as `Inconclusive`/`Failed`, prompting a pivot toward the permission-scope or wrong-target-system hypotheses rather than a second identical refresh attempt.

## Escalation Conditions

- `inspect_credentials` reports `expired`, `locked`, `revoked`, or insufficient permission scope — no autonomous remediation tool exists for these; escalate with the specific credential state and recommended human action (e.g., "credential expired, needs identity-team rotation") rather than a generic "auth is broken" brief.
- `refresh_connection` executes successfully but verification still fails — indicates the stale-cache hypothesis was wrong; if no other auth sub-hypothesis fits, escalate rather than retry the same action.
- Ambiguous `permission_scope_sufficient: null` combined with otherwise-valid credentials and no other explanation — escalate for a human to check permission scope directly, since the agent has no tool to determine this conclusively.
