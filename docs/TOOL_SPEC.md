# Tool Specification: Enterprise Integration Incident Resolution Agent

Companion to [PROJECT_SPEC.md](PROJECT_SPEC.md) and [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md). Defines every tool in the catalog available to the Agent Reasoning component (§1.1/§1.2 of the architecture doc). All tools are backed by simulated fixture data for MVP; no real external systems are called. Do not implement yet.

## 0. Conventions Used Below

- **Read/write classification**: `READ` (no side effects, safe to call any number of times) or `WRITE` (mutates simulated system state or incident record).
- **Risk level**: `None` (pure read), `Low`, `Medium`, `High` — per spec §11. Only applies meaningfully to `WRITE` tools; `READ` tools are risk `None` by construction.
- **Human approval required**: whether the Guardrail Layer routes this tool through the Human Approval Interface (architecture §1.7) before execution.
- **Agent may call autonomously**: whether the agent can propose *and* have it execute without any gate (true only for `None`/`Low` risk tools).
- **Interface durability note**: every tool is specified as a stable function signature (name, input schema, output schema) independent of its implementation. The MVP implementation resolves each call against fixture data; a future implementation swaps in a real SAP PO/CPI adapter or an MCP server tool behind the same signature. Agent-facing contracts (schemas, risk tier, approval requirement) do not change when the backing implementation changes — this is enforced by treating every tool as an interface + adapter pair (see §10).

---

## 1. OBSERVATION Tools

Used primarily at OBSERVE and early INVESTIGATING; also callable later if the agent needs to re-confirm baseline facts.

### 1.1 `get_incident`

- **Purpose**: Retrieve the full incident record as originally raised (ticket fields, reporter, initial symptom description, affected interface reference).
- **Input schema**:
  ```
  { incident_id: string }
  ```
- **Output schema**:
  ```
  {
    incident_id: string
    reported_at: timestamp
    reporter: string
    interface_id: string
    symptom_description: string
    reported_severity: string        # as raised, may conflict with computed criticality
    business_context: string | null   # e.g. "affects month-end close"
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: `incident_id` must exist in the incident store.
- **Failure modes**: `incident_id` not found; incident record malformed/incomplete (missing `interface_id`) — surfaced as evidence, not silently defaulted.
- **Human approval required**: No
- **Agent may call autonomously**: Yes (typically the mandatory first call at OBSERVE)

### 1.2 `get_interface_metadata`

- **Purpose**: Retrieve the simulated CMDB record for an interface — topology, criticality tier, SLA, ownership, known fragility flags.
- **Input schema**:
  ```
  { interface_id: string }
  ```
- **Output schema**:
  ```
  {
    interface_id: string
    name: string
    source_system: string
    target_system: string
    message_type: string              # e.g. "IDoc", "SOAP", "REST/JSON"
    sla_tier: string                   # maps to Criticality via policy memory
    owning_team: string
    known_fragility_flags: [string]    # e.g. ["cert_expires_soon", "legacy_adapter"]
    decommissioned: bool
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: `interface_id` must exist in the metadata catalog.
- **Failure modes**: `interface_id` not found (possible symptom of stale incident data referencing a decommissioned/renamed interface — itself diagnostically relevant); `decommissioned == true` returned (signals the incident may be misrouted).
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 1.3 `search_logs`

- **Purpose**: Query simulated SAP PO/CPI message and error logs for an interface within a time window, optionally filtered by keyword/error-code pattern.
- **Input schema**:
  ```
  {
    interface_id: string
    time_range: { from: timestamp, to: timestamp }
    filter: string | null              # free-text or error-code filter, optional
    max_results: int (default 50)
  }
  ```
- **Output schema**:
  ```
  {
    entries: [
      {
        log_id: string
        timestamp: timestamp
        level: "INFO" | "WARN" | "ERROR"
        message_id: string | null
        error_code: string | null
        raw_text: string
      }
    ]
    total_matched: int                  # may exceed len(entries) if truncated by max_results
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: valid `interface_id`; `time_range.from <= time_range.to`.
- **Failure modes**: empty result set (no logs in window — itself evidence, e.g. total silence may indicate channel is down rather than erroring); truncated results (`total_matched > len(entries)`, agent must know to narrow the filter/time range rather than assume completeness).
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 1.4 `get_recent_changes`

- **Purpose**: Retrieve recent configuration/deployment changes to the interface or its adjacent systems (a common real-world root-cause correlator — "what changed right before this broke").
- **Input schema**:
  ```
  {
    interface_id: string
    lookback_window: duration (default "72h")
  }
  ```
- **Output schema**:
  ```
  {
    changes: [
      {
        change_id: string
        timestamp: timestamp
        change_type: "config" | "certificate" | "deployment" | "credential_rotation" | "network"
        description: string
        changed_by: string
      }
    ]
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: valid `interface_id`.
- **Failure modes**: empty result (no recent changes — narrows hypothesis space away from change-induced causes); change log itself unavailable (simulated as a possible tool-level error, distinct from "no changes found").
- **Human approval required**: No
- **Agent may call autonomously**: Yes

---

## 2. DIAGNOSIS Tools

Used during INVESTIGATING to discriminate between competing hypotheses. Each is deliberately scoped to test one class of root cause.

### 2.1 `test_connectivity`

- **Purpose**: Simulated network/endpoint reachability check between the interface's source and target (or a specified endpoint).
- **Input schema**:
  ```
  { interface_id: string, endpoint: "source" | "target" | "both" (default "both") }
  ```
- **Output schema**:
  ```
  {
    results: [
      { endpoint: string, reachable: bool, latency_ms: int | null, error: string | null }
    ]
  }
  ```
- **Read/write**: READ (a probe, not a mutating action — simulated as a non-intrusive ping/handshake check)
- **Risk level**: None
- **Prerequisites**: valid `interface_id`.
- **Failure modes**: endpoint unreachable (evidence for timeout/partner-outage hypotheses); tool itself times out (distinguish from "target reported unreachable" — a probe failure is weaker evidence than a target-reported failure).
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 2.2 `inspect_certificate_status`

- **Purpose**: Check TLS/client certificate validity and expiry for the interface's adapter — a frequent, specific SAP PO/CPI root cause.
- **Input schema**:
  ```
  { interface_id: string }
  ```
- **Output schema**:
  ```
  {
    certificate_present: bool
    expires_at: timestamp | null
    is_expired: bool
    is_expiring_soon: bool             # e.g. within 7 days, per policy threshold
    issuer: string | null
    validation_error: string | null
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: valid `interface_id`; interface must use a cert-based adapter (else returns `certificate_present: false`, itself informative).
- **Failure modes**: certificate store unreachable in simulation; ambiguous state (present but validation_error non-null for a reason other than expiry, e.g. wrong issuer — must not be conflated with expiry by the agent).
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 2.3 `inspect_credentials`

- **Purpose**: Check status of the adapter's authentication credentials (service account lock status, password/API-key expiry, permission scope) without ever returning the secret value itself.
- **Input schema**:
  ```
  { interface_id: string }
  ```
- **Output schema**:
  ```
  {
    auth_type: "basic" | "oauth2" | "certificate" | "api_key"
    credential_status: "valid" | "expired" | "locked" | "revoked" | "unknown"
    last_rotated_at: timestamp | null
    permission_scope_sufficient: bool | null   # null if not determinable in simulation
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: valid `interface_id`.
- **Failure modes**: `credential_status: unknown` (simulated ambiguity — must not be treated as "valid" by default); tool must never expose actual secret material even in simulation, to keep the interface realistic for a future real-adapter swap.
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 2.4 `compare_with_previous_successful_run`

- **Purpose**: Diff the failing message/run against the interface's last known-good run (payload shape, timing, endpoint, config) to surface what's different — useful against mapping/schema-mismatch and encoding-error hypotheses.
- **Input schema**:
  ```
  { interface_id: string, failing_message_id: string }
  ```
- **Output schema**:
  ```
  {
    previous_success_found: bool
    previous_run_timestamp: timestamp | null
    diffs: [
      { field: string, previous_value: string, current_value: string }
    ]
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: `failing_message_id` must reference a message tied to `interface_id`; requires at least one prior successful run to exist (else `previous_success_found: false`).
- **Failure modes**: no previous successful run exists (new interface, or persistently broken — informative by itself, rules out "regression" framing); diff engine returns no diffs despite failure (suggests external/environmental cause rather than payload/config, redirecting toward connectivity/partner hypotheses).
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 2.5 `search_historical_incidents`

- **Purpose**: Query Long-Term Resolution Memory (architecture §1.5) for prior incidents with a similar structured symptom signature, returning candidate `ResolutionRecord`s as evidence, not answers to copy.
- **Input schema**:
  ```
  {
    symptom_signature: {
      interface_type: string | null
      error_category: string | null      # RootCauseCategory enum value, if already suspected
      log_pattern_tags: [string]
      affected_component: string | null
    }
    max_results: int (default 5)
  }
  ```
- **Output schema**:
  ```
  {
    matches: [
      {
        record_id: string
        similarity_score: float [0,1]
        root_cause: string
        remediation_tool: string
        outcome: "Resolved" | "Escalated"
        human_feedback: "Correct" | "Incorrect" | "Unreviewed"
      }
    ]
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: none (empty signature returns low-relevance/no matches rather than erroring).
- **Failure modes**: no matches (novel incident pattern); high-similarity match with `human_feedback: Incorrect` (agent must weight this down, not treat retrieval hits as ground truth — a design point carried from spec §14 "memory that doesn't generalize").
- **Human approval required**: No
- **Agent may call autonomously**: Yes

### 2.6 `retrieve_runbook`

- **Purpose**: Retrieve a static, human-authored runbook/procedure document matching a root-cause category, if one exists — supplemental reference knowledge distinct from case memory (this is documentation, not precedent).
- **Input schema**:
  ```
  { root_cause_category: string | null, interface_id: string | null }
  ```
- **Output schema**:
  ```
  {
    runbook_found: bool
    runbook_id: string | null
    title: string | null
    steps: [string]
    last_reviewed_at: timestamp | null
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: none.
- **Failure modes**: no runbook exists for the category (common for novel patterns); stale runbook (`last_reviewed_at` far in the past — agent should weight it as lower-confidence guidance, not authoritative).
- **Human approval required**: No
- **Agent may call autonomously**: Yes

---

## 3. ACTION Tools

Remediation and incident-management actions, risk-tiered per spec §11. `create_escalation` and `update_incident` are always available exits/record-keeping actions rather than "fixes."

### 3.1 `retry_message`

- **Purpose**: Resend/reprocess a single specific failed message through the interface.
- **Input schema**:
  ```
  { interface_id: string, message_id: string }
  ```
- **Output schema**:
  ```
  { accepted: bool, new_processing_id: string | null, error: string | null }
  ```
- **Read/write**: WRITE
- **Risk level**: Medium (revised — see [RISK_POLICY.md](RISK_POLICY.md); scoped to one message and individually reversible, but it still mutates live message/queue state, which the risk-policy framework classifies as Medium regardless of blast radius)
- **Prerequisites**: `message_id` must exist and be in a failed/error state; interface must not be fully down (else this will predictably fail — agent should have called `test_connectivity` first, though the tool does not enforce ordering itself).
- **Failure modes**: message already reprocessed by another actor (race/duplicate risk — output should reflect this rather than silently double-submitting); underlying root cause still present, so retry "succeeds" at submission but fails again downstream (this is exactly the verification-blindness case verification must catch).
- **Human approval required**: No by default in this simulated environment (Medium-risk tools require approval unless explicitly configured safe — `config/risk_policy.json` marks `retry_message` safe here; a deployment could flip that without any agent code change).
- **Agent may call autonomously**: Yes, when the override above is in effect; otherwise routed to Human Approval Interface like any other gated Medium-risk tool.

### 3.2 `restart_interface`

- **Purpose**: Restart the messaging channel/adapter instance for the interface (clears a stuck channel state, re-establishes connections).
- **Input schema**:
  ```
  { interface_id: string, channel_id: string }
  ```
- **Output schema**:
  ```
  { restarted: bool, previous_state: string, new_state: string, error: string | null }
  ```
- **Read/write**: WRITE
- **Risk level**: Medium (affects all in-flight traffic on the channel, briefly; reversible but not scoped to one message — risk of duplicate/out-of-order processing for in-flight messages)
- **Prerequisites**: channel must exist for the interface; agent must have a stated hypothesis this addresses (e.g., stuck/blocked channel), not called speculatively.
- **Failure modes**: restart fails (channel stuck in a bad state, itself new evidence); restart "succeeds" but underlying network/partner issue persists (verification must re-check, not assume restart == fix).
- **Human approval required**: Yes, by default (see [RISK_POLICY.md](RISK_POLICY.md)) — `config/risk_policy.json` deliberately does *not* mark `restart_interface` safe in this simulated environment, unlike `retry_message`/`refresh_connection`, to demonstrate the override is per-tool rather than blanket-Medium.
- **Agent may call autonomously**: No, under the current policy config. If a deployment explicitly configures it safe, it would execute with mandatory rationale logging and owning-team notification as a side effect (not a separate agent decision) — same as any other Medium-risk override.

### 3.3 `refresh_connection`

- **Purpose**: Force re-authentication / refresh of the adapter's connection pool or token (distinct from a full channel restart — narrower, targets auth/session-expiry-shaped issues specifically).
- **Input schema**:
  ```
  { interface_id: string }
  ```
- **Output schema**:
  ```
  { refreshed: bool, new_session_established: bool, error: string | null }
  ```
- **Read/write**: WRITE
- **Risk level**: Medium (drops and re-establishes live connections; reversible, brief disruption)
- **Prerequisites**: interface must use a session/token-based auth mechanism (else not applicable — should not be proposed against e.g. pure certificate-based one-way adapters without a session concept).
- **Failure modes**: refresh fails because underlying credential itself is invalid/expired (distinguishes "just needed a refresh" from "credential rotation needed," steering toward `inspect_credentials` follow-up rather than repeated refresh attempts).
- **Human approval required**: No by default in this simulated environment ([RISK_POLICY.md](RISK_POLICY.md) marks it safe) — a deployment could flip this without any agent code change.
- **Agent may call autonomously**: Yes, when the override is in effect, with rationale logging (Medium tier)

### 3.4 `create_escalation`

- **Purpose**: Formally hand off the incident to a human, with the structured `Escalation` brief (architecture §2.7). Always available; not itself a "failure" of the agent.
- **Input schema**:
  ```
  {
    incident_id: string
    reason: string                       # one of Escalation.reason enum values
    summary_for_human: string
    leading_hypothesis_ids: [string]
    unresolved_questions: [string]
  }
  ```
- **Output schema**:
  ```
  { escalation_id: string, routed_to: string, created_at: timestamp }
  ```
- **Read/write**: WRITE (creates an escalation record and notifies a human queue/owning team — simulated)
- **Risk level**: Low (no system-state change to the interface itself; purely informational handoff)
- **Prerequisites**: incident must not already be escalated or resolved.
- **Failure modes**: escalation queue/routing target unavailable in simulation (should still record the escalation locally, not silently drop it); missing/incomplete brief fields (guardrail-level validation should reject an escalation attempt with an empty summary rather than let a low-quality handoff through).
- **Human approval required**: No (this tool *is* the human-facing action, not something requiring pre-approval to invoke)
- **Agent may call autonomously**: Yes — and must always remain callable regardless of other guardrail state (architecture §3, "safe exit" invariant)

### 3.5 `update_incident`

- **Purpose**: Write diagnosis, evidence summary, actions taken, and outcome back to the incident record (the ticketing-system-of-record update) — bookkeeping, not remediation.
- **Input schema**:
  ```
  {
    incident_id: string
    status: string
    diagnosis_summary: string
    evidence_refs: [string]
    actions_taken: [string]
    outcome: string | null
  }
  ```
- **Output schema**:
  ```
  { updated: bool, incident_id: string, updated_at: timestamp }
  ```
- **Read/write**: WRITE (to the incident record only — no effect on the interface/system under diagnosis)
- **Risk level**: Low
- **Prerequisites**: `incident_id` must exist.
- **Failure modes**: incident record locked/already closed by another actor (simulated conflict case); partial write failure (should be retried or surfaced, not silently swallowed, since this is the audit-facing record of what happened).
- **Human approval required**: No
- **Agent may call autonomously**: Yes — in fact this is typically called at every stage transition, not only at the end, to keep the ticket-of-record current

---

## 4. Remediation Tools Requiring Approval (High Risk)

These two were named in spec §7/§11 as High-risk exemplars and are included here for completeness of the ACTION category's risk spectrum, even though not explicitly re-listed in this task's tool grouping — they share the ACTION category's shape and are cross-referenced so the risk gradient (Low → Medium → High) is fully represented in one document.

### 4.1 `update_adapter_timeout`

- **Purpose**: Modify the adapter's configured timeout value — addresses a slow-downstream-response root cause, but is a persistent config change affecting all future traffic.
- **Input schema**: `{ interface_id: string, new_timeout_ms: int }`
- **Output schema**: `{ updated: bool, previous_timeout_ms: int, new_timeout_ms: int }`
- **Read/write**: WRITE
- **Risk level**: High (persistent config change, affects all future traffic, not easily/instantly reversible in a live enterprise setting)
- **Prerequisites**: valid interface; new value within a sane bounds check (guardrail-level, not agent-judgment).
- **Failure modes**: value rejected by underlying (simulated) config validation; change applied but does not address the actual root cause (e.g., root cause was auth, not timeout) — verification must catch this.
- **Human approval required**: **Yes**
- **Agent may call autonomously**: No — proposal only, execution gated on `ApprovalRequest` approval

### 4.2 `rotate_certificate`

- **Purpose**: Trigger certificate renewal/rotation for the interface's adapter.
- **Input schema**: `{ interface_id: string }`
- **Output schema**: `{ rotated: bool, new_expiry: timestamp | null, error: string | null }`
- **Read/write**: WRITE
- **Risk level**: High (security-sensitive, affects trust chain for all traffic, not trivially reversible)
- **Prerequisites**: `inspect_certificate_status` should already have been called and shown a cert-related issue (guardrail may enforce this as a required preceding evidence type, not just a convention).
- **Failure modes**: rotation fails (simulated CA/renewal error); rotation succeeds but downstream partner hasn't trusted the new cert yet (a realistic partial-fix case verification must surface).
- **Human approval required**: **Yes**
- **Agent may call autonomously**: No

### 4.3 `reprocess_message_batch`

- **Purpose**: Bulk delete/reprocess of a message batch — the "destructive batch action" exemplar from the risk-based autonomy framework ([RISK_POLICY.md](RISK_POLICY.md)).
- **Input schema**: `{ interface_id: string, batch_id: string, message_count: int }`
- **Output schema**: `{ reprocessed: bool, batch_id: string, message_count: int }`
- **Read/write**: WRITE
- **Risk level**: High (irreversible at scale; affects a whole batch, not a single message)
- **Prerequisites**: not currently mapped to any root-cause category by the reasoning engine (`agent/reasoning.py`'s `REMEDIATION_TOOL_BY_CATEGORY` has no entry pointing here) — present for policy-framework completeness and future extension, not autonomously selected today.
- **Failure modes**: partial batch failure (some messages reprocessed, others not) — must be verified per-message, not assumed uniform.
- **Human approval required**: **Yes, always** — no override exists for High tier under any configuration.
- **Agent may call autonomously**: No.

### 4.4 `update_routing_rule`

- **Purpose**: Change message routing for an interface — the "change routing" exemplar from the risk-based autonomy framework.
- **Input schema**: `{ interface_id: string, rule_id: string, new_target: string }`
- **Output schema**: `{ updated: bool, rule_id: string, previous_target: string, new_target: string }`
- **Read/write**: WRITE
- **Risk level**: High (affects where all future traffic on this interface is delivered)
- **Prerequisites**: same as `reprocess_message_batch` — present for framework completeness, not currently autonomously selected.
- **Failure modes**: rule accepted but target unreachable/misconfigured; ambiguous ownership of the new target system.
- **Human approval required**: **Yes, always**.
- **Agent may call autonomously**: No.

---

## 5. VERIFICATION Tools

Structurally these are `DiagnosticAction`s with `is_verification: true` (architecture §1.8, §2.4) — same category as OBSERVATION/DIAGNOSIS tools mechanically, but listed separately here because they're invoked under a different rule (mandatory, immediately after any executed `RemediationAction`, never optional).

### 5.1 `verify_interface_health`

- **Purpose**: Re-check overall interface/channel operational status after a remediation (channel up, connectivity restored, no active error state) — the broad "is the interface healthy" check.
- **Input schema**:
  ```
  { interface_id: string, baseline_snapshot_id: string }
  ```
  (`baseline_snapshot_id` references the pre-action evidence snapshot captured automatically by the orchestrator before the `RemediationAction` executed — see architecture §2.6 `pre_action_signature`.)
- **Output schema**:
  ```
  {
    channel_status: "up" | "down" | "degraded"
    error_state_cleared: bool
    comparison_to_baseline: string
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: a `RemediationAction` must have just executed; `baseline_snapshot_id` must be valid.
- **Failure modes**: `channel_status: degraded` (an ambiguous middle state that must map to `VerificationResult.outcome = Inconclusive`, not be forced into Passed/Failed); baseline snapshot missing (orchestrator-level bug, should hard-fail rather than let verification proceed unanchored).
- **Human approval required**: No
- **Agent may call autonomously**: Yes (and it is architecturally mandatory, not merely permitted)

### 5.2 `verify_message_processing`

- **Purpose**: Narrow, message-specific check — did the specific originally-failing message (or its retry) actually complete successfully end-to-end, as opposed to just being accepted for reprocessing.
- **Input schema**:
  ```
  { interface_id: string, message_id: string, expected_end_state: string }
  ```
- **Output schema**:
  ```
  {
    current_state: string
    matches_expected: bool
    processing_errors: [string]
  }
  ```
- **Read/write**: READ
- **Risk level**: None
- **Prerequisites**: `message_id` must correspond to the message targeted by the just-executed `RemediationAction` (e.g., the one `retry_message` resent).
- **Failure modes**: message still in-flight/pending (neither clearly passed nor failed yet — `Inconclusive`, may warrant a bounded wait-and-recheck rather than immediate retry or escalate); message reached a *new* distinct error (evidence that the original hypothesis was wrong, not that the fix "failed" in the same way — should route the agent toward hypothesis pivot, not a mechanical repeat of the same remediation).
- **Human approval required**: No
- **Agent may call autonomously**: Yes (architecturally mandatory after `retry_message` specifically; `verify_interface_health` is the broader check used after channel-level actions like `restart_interface`/`refresh_connection`)

---

## 6. Summary Table

| Tool | Category | R/W | Risk | Approval Required | Autonomous |
|---|---|---|---|---|---|
| get_incident | Observation | READ | None | No | Yes |
| get_interface_metadata | Observation | READ | None | No | Yes |
| search_logs | Observation | READ | None | No | Yes |
| get_recent_changes | Observation | READ | None | No | Yes |
| test_connectivity | Diagnosis | READ | None | No | Yes |
| inspect_certificate_status | Diagnosis | READ | None | No | Yes |
| inspect_credentials | Diagnosis | READ | None | No | Yes |
| compare_with_previous_successful_run | Diagnosis | READ | None | No | Yes |
| search_historical_incidents | Diagnosis | READ | None | No | Yes |
| retrieve_runbook | Diagnosis | READ | None | No | Yes |
| retry_message | Action | WRITE | Medium | No\* | Yes\* |
| restart_interface | Action | WRITE | Medium | Yes | No (safe-override disabled by default) |
| refresh_connection | Action | WRITE | Medium | No\* | Yes\* |
| create_escalation | Action | WRITE | Low | No | Yes (always available) |
| update_incident | Action | WRITE | Low | No | Yes |
| update_adapter_timeout | Action | WRITE | High | **Yes** | No |
| rotate_certificate | Action | WRITE | High | **Yes** | No |
| reprocess_message_batch | Action | WRITE | High | **Yes** | No |
| update_routing_rule | Action | WRITE | High | **Yes** | No |
| verify_interface_health | Verification | READ | None | No | Yes (mandatory) |
| verify_message_processing | Verification | READ | None | No | Yes (mandatory) |

\* Medium-risk approval requirement is conditional, not fixed — see [RISK_POLICY.md](RISK_POLICY.md). The "No"/"Yes" shown here reflects the current default in `config/risk_policy.json` for this simulated environment, not a hardcoded property of the tool.

## 7. Cross-Cutting Design Notes

- **No tool returns raw credentials or secret material**, even in simulation (`inspect_credentials`) — this is a deliberate realism/safety constraint carried forward to any future real-adapter implementation, not a simulation shortcut.
- **Every READ tool is risk `None`/Low and always agent-autonomous** — this preserves the architecture's principle that investigation breadth is never gated by approval, only remediation is.
- **Medium-risk tools require approval by default; autonomy is an explicit, auditable override**, not the default assumption. `refresh_connection` and `retry_message` are configured safe in this simulated environment (`config/risk_policy.json`); `restart_interface` is deliberately left requiring approval to demonstrate the override is real and per-tool, not blanket. See [RISK_POLICY.md](RISK_POLICY.md) for the full framework and enforcement mechanism.
- **High-risk tools are proposal-only from the agent's perspective**: the agent's output for these is identical in shape to any other `RemediationAction` proposal; the *only* difference is what the Guardrail Layer does with it (route to Human Approval Interface instead of Tool Execution). The agent does not need special-case logic for "this is a High tool" beyond citing risk-appropriate justification — the gating is structural, per architecture §1.6.
- **Verification tools are mechanically identical to diagnosis tools** (same READ/None/autonomous shape) but differ in *when* they're invoked (mandatory, tied to a specific `remediation_action_id`) — this is an orchestrator-level rule (architecture §4), not a property of the tool itself.

## 8. Interface Durability for Future Real Integration

Each tool above is specified as a name + input schema + output schema contract, deliberately independent of implementation. To swap a simulated tool for a real one later (e.g., a real SAP PO monitoring API, or an MCP server exposing equivalent operations):

- The **schema stays the same** — callers (the Agent Reasoning component, orchestrator, guardrail layer) are written against the schema, never against "the fixture data format."
- The **adapter underneath changes**: MVP adapters read from local fixture files/an in-memory simulated store; a future adapter would call a real API/MCP tool and map its response into the same output schema.
- **Risk tier and approval requirement are metadata attached to the tool's registry entry**, not derived from the implementation — so swapping SAP PO for, say, MuleSoft later doesn't require re-deriving which operations are High-risk; it requires re-confirming the registry entry is still accurate for the new backing system.
- No tool defined here assumes a specific real vendor API shape (no SAP-specific field names, no vendor SDK types in the schemas) — this is deliberate per the task's constraint not to invent external APIs yet, and keeps the contract vendor-neutral until a real integration is scoped.
