# Skill: Certificate Failure Investigation

## When This Skill Is Relevant

TLS/handshake-specific log signatures ("certificate expired," "SSL handshake failed," "untrusted certificate," "certificate validation error"), a `certificate` entry in `get_recent_changes`, an interface metadata `known_fragility_flags` entry like `cert_expires_soon`, or historical precedent for cert issues on this interface. One of the highest-value skills to check early given how common and specifically-diagnosable cert expiry is in SAP PO/CPI environments — but the agent should still confirm rather than assume, since handshake-shaped errors can also stem from cipher/protocol mismatches or genuine network issues that superficially look similar in logs.

## Evidence to Gather

- Direct certificate status: presence, expiry, expiring-soon flag, issuer, any validation error detail (`inspect_certificate_status`).
- Recent changes specifically of type `certificate` (`get_recent_changes`).
- Log detail on the specific handshake failure mode, since "expired" vs. "untrusted issuer" vs. "hostname mismatch" are different sub-problems with the same coarse symptom (`search_logs`).
- Whether the failure is universal (every message failing, consistent with a genuinely broken/expired cert) or intermittent (which would be inconsistent with most certificate hypotheses and should redirect suspicion) (`search_logs`).
- Historical precedent, since certificate expiry is often recurring/predictable if the underlying renewal process is unreliable (`search_historical_incidents`).

## Diagnostic Reasoning Approach

`inspect_certificate_status` is close to a direct oracle for this hypothesis class — unlike auth or timeout issues, certificate state is close to a fact the agent can just look up rather than infer indirectly. The reasoning burden is less about detection and more about (a) distinguishing *which* certificate problem it is (expired vs. wrong issuer vs. hostname mismatch vs. not-yet-trusted-by-partner after a recent rotation) since only some of these are addressable by `rotate_certificate`, and (b) correctly weighing the risk/urgency of a High-risk remediation action against the confirmation strength of the evidence, since certificate rotation is not reversible-on-a-whim and should not be proposed on a hunch. The agent should treat `inspect_certificate_status` returning a clean, non-expired, non-error state as a strong refutation and actively look elsewhere (likely Connectivity or Mapping) rather than continuing to probe certificate-adjacent tools.

## Possible Hypotheses

- `cert_expiry` — certificate has expired
- `cert_expiry` — certificate expiring imminently and already being rejected by a strict partner-side policy (pre-expiry rejection)
- Certificate valid, but genuinely different problem: wrong issuer/untrusted chain, hostname mismatch, or a recently-rotated certificate not yet trusted by the partner (a *partial* fix state, since rotation happened but propagation/trust hasn't completed)
- Reclassify away from certificate entirely — handshake-shaped error was actually a network/cipher-mismatch issue (redirect to Connectivity skill)

## Evidence That Confirms/Refutes Each Hypothesis

- `is_expired: true` directly confirms the expiry hypothesis with high confidence.
- `is_expiring_soon: true` with `is_expired: false` but active handshake failures in logs supports the pre-expiry-rejection hypothesis (some partner policies reject certs within N days of expiry even before actual expiry).
- `validation_error` present but `is_expired: false` (e.g., issuer/chain problem) refutes the simple expiry hypothesis and should redirect toward the "valid-but-different-problem" hypothesis — critically, `rotate_certificate` is not obviously the right fix for an issuer/chain problem the way it is for expiry, and the agent should reason about this distinction rather than reflexively rotating on any certificate-tool-surfaced error.
- A `certificate` change in `get_recent_changes` very close to onset, combined with `is_expired: false` and active handshake failures, supports "recently rotated, not yet trusted by partner" — this specifically argues *against* rotating again (that would not fix a trust-propagation problem and could make it worse) and toward escalation/partner coordination instead.
- Intermittent (not universal) handshake failures refute most certificate hypotheses, since an expired/invalid cert should fail consistently, and should redirect toward Connectivity (e.g., load-balanced endpoints where only some backend nodes have the stale cert).

## Tools That May Be Used

`inspect_certificate_status`, `get_recent_changes`, `search_logs`, `search_historical_incidents`, `retrieve_runbook`, `rotate_certificate` (remediation, High risk), `create_escalation`, `update_incident`.

## Remediation Options

- **`rotate_certificate` (High risk, requires human approval)** — the only tool that directly addresses a confirmed expired (or imminently-expiring-and-already-rejected) certificate. Because it is High risk, the agent's job is to construct a well-justified `ApprovalRequest` (confirmed expiry evidence, affected interface, urgency given SLA) rather than to execute it — per architecture §1.6/§1.7, this always routes through Human Approval regardless of how confident the agent is.
- **No remediation action is appropriate** for the "recently rotated, not yet trusted by partner" sub-hypothesis — rotating again does not address a trust-propagation delay and may introduce a new certificate the partner also hasn't picked up. The correct action here is `create_escalation` for partner/owning-team coordination, or a wait-and-recheck via further `inspect_certificate_status`/log calls if SLA allows, not a remediation tool call.
- **No remediation action is appropriate** for issuer/chain/hostname-mismatch sub-hypotheses either — these typically require a genuinely different (not just renewed) certificate or partner-side trust store update, which is outside this tool's scope; escalate with the specific `validation_error` detail.

## Verification Requirements

After `rotate_certificate` executes and is approved, verification must confirm both that the new certificate is now valid/not-expiring (`inspect_certificate_status` rechecked) and that actual message traffic succeeds post-rotation (`verify_interface_health` and/or `verify_message_processing`), since a successful rotation on our side does not guarantee the partner's trust store has picked up the new certificate immediately — a "rotated successfully" result with continued handshake failures is a distinct, known failure mode (trust propagation delay) and should not be treated as a fixable-by-retry situation; it should prompt escalation for partner coordination rather than a second rotation attempt.

## Escalation Conditions

- Confirmed expiry, but SLA/urgency requires action faster than the approval loop can reasonably complete, or approval is rejected/times out — escalate with the confirmed evidence so a human can expedite.
- Certificate valid but a chain/issuer/hostname problem is identified — no autonomous or even approvable remediation tool addresses this cleanly; escalate with the specific `validation_error`.
- Rotation approved and executed, but post-rotation verification still fails (trust-propagation or a wrong sub-hypothesis) — escalate for partner-side coordination rather than retrying rotation.
