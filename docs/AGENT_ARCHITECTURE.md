# Agent Architecture: Enterprise Integration Incident Resolution Agent

Companion to [PROJECT_SPEC.md](PROJECT_SPEC.md). This document defines the system's components, state model, and control flow. No implementation yet.

## 1. Component Separation

```
                         ┌─────────────────────────┐
                         │   Observability/Eval    │  (watches everything, changes nothing)
                         └───────────▲─────────────┘
                                     │ emits events
┌──────────────┐   proposes action  │   ┌─────────────────┐
│ Agent         │────────────────────┼──▶│ Guardrail Layer │──▶ allow / block / require-approval
│ Reasoning/    │                    │   └────────┬────────┘
│ Orchestration │◀───────────────────┼────────────┘
│ (the "brain") │  reads/writes      │
└──────┬────────┘                    │
       │ calls                       │
       ▼                             │
┌──────────────┐   ┌──────────────┐  │   ┌──────────────────┐
│ Tool          │──▶│ Knowledge    │  │   │ Human Approval    │
│ Execution     │   │ Retrieval    │  │   │ Interface         │
│ Layer         │   └──────────────┘  │   └──────────────────┘
└──────┬────────┘                    │
       │ results                     │
       ▼                             │
┌──────────────────────────────────────────────────┐
│ Incident State (working memory, single incident)  │
└──────────────────┬─────────────────────────────────┘
                    │ on RESOLVE/ESCALATE, distilled into
                    ▼
       ┌─────────────────────────────┐
       │ Long-Term Resolution Memory  │  (cross-incident, read at INVESTIGATE)
       └─────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ Verification: a mode of Tool Execution + State     │
│ update, not a separate execution path              │
└──────────────────────────────────────────────────┘
```

The critical property: **the Agent Reasoning component never calls a tool or mutates state directly.** It only *proposes* the next action (a `DiagnosticAction` or `RemediationAction`) given the current `IncidentState`. The orchestration loop takes that proposal, runs it through the Guardrail Layer, dispatches it to Tool Execution if allowed, and folds the result back into `IncidentState`. This indirection is what makes guardrails and audit enforceable independent of what the agent "decides" — the agent proposes, the orchestrator disposes.

### 1.1 Agent Reasoning/Orchestration

Responsible for, on every turn: reading current `IncidentState` (all evidence, hypotheses, actions-so-far), and producing exactly one of:
- a `DiagnosticAction` proposal (which tool, what params, what evidence gap it targets, which hypothesis it discriminates between)
- a `RemediationAction` proposal (which tool, params, targeted hypothesis, stated confidence, risk acknowledgment)
- a hypothesis revision (update confidence/ranking of the hypothesis set given evidence already in state — this can happen without a new tool call)
- a terminal proposal: `RESOLVE` or `ESCALATE`, with justification

It never sees raw tool APIs — it sees a catalog of available tools (name, description, params, risk tier, cost) and picks by name. It is stateless between turns; all continuity lives in `IncidentState`, which is handed to it fresh each turn. This is deliberate: it forces every decision to be re-derivable from state, which is what the audit trail and eval harness depend on (see §9).

### 1.2 Tool Execution Layer

A uniform dispatcher: `execute(action: DiagnosticAction | RemediationAction) -> ToolResult`. Responsibilities:
- Look up the tool implementation (against simulated fixture data for MVP).
- Enforce param validation (schema, not semantics).
- Execute and capture result, latency, and any simulated error.
- Return a structured `ToolResult`, never a raw string blob the agent has to re-parse ambiguously — but content within it (e.g., a log excerpt) is unstructured text where that's realistic.

Does not decide *whether* an action is allowed — that's the Guardrail Layer, which sits between the Agent and this layer.

### 1.3 Knowledge Retrieval

Two distinct retrieval surfaces, both accessed as tools like anything else (the agent doesn't get free/implicit RAG context injected — every retrieval is an explicit, loggable action):
- **Static/reference retrieval**: interface metadata catalog, policy/config store (risk tiers, SLA mapping) — read-only, slow-changing.
- **Case memory retrieval**: `search_historical_incidents(symptom_signature)` against Long-Term Resolution Memory (§1.5). Returns candidate prior `ResolutionRecord`s ranked by signature similarity, which the agent can fold into its hypothesis set as evidence (not as an answer to copy).

### 1.4 Incident State

The single source of truth for one incident's lifecycle (`IncidentState`, defined in §2). Append-only for evidence/actions (nothing is deleted, only superseded), mutable for the "current" pointers (current hypothesis ranking, current stage, cycle counters). This is what's handed to the Agent Reasoning component each turn and what the Guardrail Layer inspects to enforce ceilings.

### 1.5 Long-Term Resolution Memory

Cross-incident persistent store of `ResolutionRecord`s (§2.8), written only at LEARN, never during an active incident. Indexed by symptom signature (structured fields: interface type, error category, affected component, log-pattern tags — not free-text incident descriptions) so retrieval generalizes across differently-worded incidents with the same underlying pattern (this directly addresses spec §14's "memory that doesn't generalize" failure mode).

### 1.6 Guardrail Layer

A deterministic gate the orchestrator invokes on every proposed action, before Tool Execution. Not an LLM call. Responsibilities:
- Check risk tier of a proposed `RemediationAction` against the approval registry — if high-risk and no matching approval exists in state, reject the proposal and force a route to Human Approval instead of execution.
- Check cycle counters (investigation steps, remediation attempts) against configured ceilings (§7) — if exceeded, reject any further `DiagnosticAction`/`RemediationAction` proposals and force `ESCALATE`.
- Check SLA time-remaining against a threshold — if below it, force the agent's next turn to be a DECIDE-only turn (act/escalate, no more investigation).
- Validate that a `RemediationAction` proposal cites a specific hypothesis and confidence value above the acting threshold — reject (return to agent with a rejection reason) if missing.
This layer is what makes guardrails structural rather than "prompted for."

### 1.7 Human Approval Interface

A simulated (for MVP) interrupt mechanism. When the Guardrail Layer routes a high-risk `RemediationAction` here:
- An `ApprovalRequest` is created (action, target, risk tier, hypothesis + evidence justification, confidence) and the incident enters `AWAITING_APPROVAL`.
- A human responds via CLI/config-driven mock (approve / reject / reject-with-note).
- Approval or rejection becomes part of `IncidentState` (an approval record), and control returns to the orchestration loop — approval doesn't auto-execute, it just unblocks the Guardrail check on a resubmitted proposal.
- A configurable timeout on `AWAITING_APPROVAL` forces `ESCALATE` (ground truth per open design question in the spec; MVP default: escalate on timeout, no silent auto-approve).

### 1.8 Verification

Not a separate tool category architecturally — it's a `DiagnosticAction` subtype (`VerificationAction`) that's *mandatory* immediately after any `RemediationAction` executes, enforced by the orchestrator (you cannot propose RESOLVE without a `VerificationResult` in state tied to the most recent remediation). It re-runs the same evidence-gathering tools used to originally detect the symptom (not just checking "did the tool call not error") and diffs against the pre-action snapshot.

### 1.9 Observability/Evaluation

A pure observer: subscribes to every state transition, tool call, proposal, guardrail decision, and approval event, and writes them to an immutable event log (`IncidentEventLog`, ordered, timestamped). Never influences the agent or orchestrator. Two consumers of this log:
- **Runtime audit**: human-readable trace of "what did the agent do and why" for any incident, including rejected proposals (important — a proposal the guardrail blocked is evidence the guardrail is working, and must be visible).
- **Eval harness**: replays the fixture scenario suite (spec §16), scores against ground truth, and computes the metrics in spec §13 by reading this log, not by asking the agent to self-report.

## 2. State Model

All objects are immutable once created except where noted; state evolution is via appending new objects and updating pointers/status fields, never rewriting history.

### 2.1 IncidentState

The root object, one per incident, threaded through the whole lifecycle.

```
IncidentState {
  incident_id: string
  stage: Stage                        # enum, see §3
  criticality: Criticality             # P1-P4, from OBSERVE
  sla_deadline: timestamp
  interface_id: string

  evidence: [Evidence]                 # append-only
  hypotheses: [Hypothesis]             # current ranked set (superseded ones kept, marked inactive)
  diagnostic_actions: [DiagnosticAction]     # append-only, includes verification actions
  remediation_actions: [RemediationAction]   # append-only
  verification_results: [VerificationResult] # append-only
  approval_requests: [ApprovalRequest]  # append-only

  investigation_cycle_count: int       # incremented per DiagnosticAction
  remediation_attempt_count: int       # incremented per RemediationAction

  escalation: Escalation | null
  resolution: ResolutionRecord | null

  created_at, updated_at: timestamp
}
```

### 2.2 Evidence

A single unit of information gathered from a tool call (diagnostic or verification) or supplied at OBSERVE.

```
Evidence {
  evidence_id: string
  source: string                # tool name, or "incident_intake"
  produced_by_action_id: string | null   # links to DiagnosticAction that produced it
  content: structured | text     # tool-specific payload
  summary: string                # short agent-or-tool-generated gloss, for trace readability
  timestamp: timestamp
}
```

### 2.3 Hypothesis

One candidate root-cause explanation, part of a competing set.

```
Hypothesis {
  hypothesis_id: string
  statement: string                     # e.g. "TLS certificate for interface X expired"
  category: RootCauseCategory            # enum: cert_expiry, timeout, mapping_error, auth_failure,
                                          # partner_outage, queue_congestion, duplicate_message,
                                          # encoding_error, other
  confidence: float [0,1]
  status: Active | Demoted | Confirmed | Refuted
  supporting_evidence_ids: [string]
  contradicting_evidence_ids: [string]
  confidence_history: [(evidence_id, prior_confidence, new_confidence, timestamp)]  # mandatory trace
  created_at, updated_at: timestamp
}
```

The `confidence_history` field exists specifically to make spec §14's "confidence theater" failure mode checkable: every confidence delta must cite the evidence that caused it.

### 2.4 DiagnosticAction

A proposed-then-executed (or rejected) investigation step, including verification steps.

```
DiagnosticAction {
  action_id: string
  tool_name: string
  params: object
  targets_hypothesis_ids: [string]     # which hypotheses this is meant to discriminate between
  rationale: string                     # agent's stated reason for choosing this action now
  is_verification: bool                 # true if this is a post-remediation check
  verifies_remediation_id: string | null
  status: Proposed | Executed | RejectedByGuardrail
  rejection_reason: string | null
  result_evidence_id: string | null     # link to the Evidence it produced
  timestamp: timestamp
}
```

### 2.5 RemediationAction

A proposed-then-executed (or rejected/awaiting-approval) corrective action.

```
RemediationAction {
  action_id: string
  tool_name: string
  params: object
  targets_hypothesis_id: string          # the single hypothesis this addresses
  hypothesis_confidence_at_decision: float
  risk_tier: Low | Medium | High          # looked up from static registry, not agent-declared
  status: Proposed | AwaitingApproval | Approved | Rejected | Executed | RejectedByGuardrail
  approval_request_id: string | null
  rationale: string
  timestamp: timestamp
}
```

### 2.6 VerificationResult

```
VerificationResult {
  verification_id: string
  remediation_action_id: string
  diagnostic_action_id: string           # the DiagnosticAction(is_verification=true) that produced it
  outcome: Passed | Failed | Inconclusive
  comparison: {
    pre_action_signature: object          # snapshot of symptom evidence before remediation
    post_action_signature: object         # same evidence type, gathered after
    diff_summary: string
  }
  timestamp: timestamp
}
```

`Inconclusive` is a first-class outcome (not silently coerced to Passed or Failed) — it's what forces a DECIDE-stage re-evaluation rather than a mechanical retry.

### 2.7 Escalation

```
Escalation {
  escalation_id: string
  reason: LowConfidence | RiskTooHigh | CyclesExhausted | RetriesExhausted | ApprovalTimeout | ContradictoryEvidence
  summary_for_human: string               # structured brief, not a raw dump
  leading_hypotheses: [Hypothesis]        # top-ranked at time of escalation, with evidence
  actions_attempted: [string]              # action_ids, for the human to review without re-deriving
  unresolved_questions: [string]
  timestamp: timestamp
}
```

### 2.8 ResolutionRecord

Written at LEARN, persisted to Long-Term Resolution Memory. This is the generalized artifact, not a transcript dump.

```
ResolutionRecord {
  record_id: string
  symptom_signature: {                   # structured, for matching — this is the retrieval key
    interface_type: string
    error_category: RootCauseCategory
    log_pattern_tags: [string]
    affected_component: string
  }
  root_cause: RootCauseCategory
  root_cause_statement: string
  remediation_tool: string
  remediation_params_template: object      # generalized, params abstracted where possible
  verification_signature: object           # what "resolved" looked like, for future comparison
  outcome: Resolved | Escalated
  tool_call_count: int                     # for measuring memory-driven speedup on replays
  human_feedback: Correct | Incorrect | Unreviewed
  source_incident_id: string
  created_at: timestamp
}
```

## 3. Stage Model (explicit state machine)

```
enum Stage {
  OBSERVE
  INVESTIGATING          # INVESTIGATE ⇄ HYPOTHESIZE collapse into one agent-turn loop stage
  DECIDING                # DECIDE
  AWAITING_APPROVAL
  ACTING                  # ACT (remediation dispatch)
  VERIFYING                # VERIFY
  RESOLVED
  ESCALATED
}
```

Note `INVESTIGATE` and `HYPOTHESIZE` are represented as a single `INVESTIGATING` stage rather than two, because per spec they are not sequential phases but the same loop iteration: every diagnostic turn is "gather evidence, then revise hypotheses in light of it" — there is no case where the agent is in one without immediately doing the other in the same turn. Splitting them into separate machine states would falsely imply a fixed order (gather *then* hypothesize) rather than the intended fused, repeatable unit. `DECIDING` is what actually branches to ACT vs ESCALATE vs back to INVESTIGATING.

### 3.1 Transition Table

| From | Trigger | To | Notes |
|---|---|---|---|
| OBSERVE | intake complete, criticality/SLA resolved | INVESTIGATING | deterministic step, minimal agent judgment (mostly reconciling conflicting signals) |
| INVESTIGATING | agent proposes DiagnosticAction, guardrail allows | INVESTIGATING | evidence appended, hypotheses revised, cycle count +1 |
| INVESTIGATING | agent proposes DiagnosticAction, guardrail rejects (cycle ceiling) | DECIDING | forced |
| INVESTIGATING | agent proposes hypothesis-confidence-sufficient / act-now | DECIDING | agent-initiated |
| INVESTIGATING | agent proposes ESCALATE directly (e.g. contradictory evidence) | ESCALATED | agent-initiated, allowed at any point |
| DECIDING | agent proposes RemediationAction, risk=Low/Medium, guardrail allows | ACTING | |
| DECIDING | agent proposes RemediationAction, risk=High | AWAITING_APPROVAL | |
| DECIDING | agent proposes "need more evidence" AND cycle ceiling not reached | INVESTIGATING | back-edge |
| DECIDING | agent proposes ESCALATE, or confidence below threshold with no more evidence to gather | ESCALATED | |
| DECIDING | remediation-attempt ceiling reached | ESCALATED | forced |
| AWAITING_APPROVAL | human approves | ACTING | |
| AWAITING_APPROVAL | human rejects | DECIDING | agent must pick a different path; rejection recorded as evidence-like signal |
| AWAITING_APPROVAL | timeout | ESCALATED | forced (MVP default) |
| ACTING | remediation tool executes | VERIFYING | verification is mandatory, automatic next step, not agent-proposed |
| VERIFYING | VerificationResult = Passed | RESOLVED | |
| VERIFYING | VerificationResult = Failed or Inconclusive, retry budget remains | DECIDING | agent re-evaluates: retry same hypothesis w/ different action, pivot hypothesis, or escalate |
| VERIFYING | VerificationResult = Failed/Inconclusive, retry budget exhausted | ESCALATED | forced |
| RESOLVED | — | (terminal) | triggers LEARN write of ResolutionRecord |
| ESCALATED | — | (terminal) | triggers LEARN write of ResolutionRecord (outcome=Escalated) with partial diagnosis |

LEARN is not a distinct `Stage` value — it's a mandatory side effect fired on entering either terminal stage, always, regardless of outcome (this guarantees spec §15 anti-goal #5, "no memory," can't silently happen).

## 4. State Update Rule (per tool call)

After every `DiagnosticAction` or `RemediationAction` executes (i.e., Tool Execution returns a `ToolResult`), the orchestrator applies this fixed sequence — deterministic, not agent-judgment:

1. Append a new `Evidence` object (linked via `produced_by_action_id`) to `IncidentState.evidence`.
2. Mark the originating `DiagnosticAction`/`RemediationAction` status `Executed`.
3. Increment the relevant counter (`investigation_cycle_count` or `remediation_attempt_count`).
4. Hand the updated `IncidentState` back to the Agent Reasoning component for a fresh turn — this turn is where the agent (not the orchestrator) revises `Hypothesis.confidence` values and `confidence_history`, using the new evidence. The orchestrator enforces *that* a revision must happen (every hypothesis touched by the new evidence must show a `confidence_history` entry with this evidence_id, or the turn is invalid and re-prompted) but doesn't compute the revision itself.
5. If the executed action was a `RemediationAction`, the orchestrator forces the very next `DiagnosticAction` to be `is_verification=true` targeting that remediation — the agent cannot skip straight to RESOLVE.
6. Guardrail Layer re-checks ceilings and SLA-remaining against the updated counters/state and may force a stage transition (§3.1) independent of what the agent proposes next.
7. Observability layer logs the full delta (old state pointer → new state pointer, action, result, any forced transition) as one immutable event.

This same rule applies uniformly whether the tool was a diagnostic, a verification, or a remediation call — there is one state-update pathway, not per-tool-type special cases.

## 5. Dynamic Decision-Making (how the fixed-sequence anti-pattern is avoided)

Concretely, at every `INVESTIGATING`-stage turn, the Agent Reasoning component is given:
- Full current `IncidentState` (all evidence so far, current hypothesis ranking, actions already taken/rejected).
- The full tool catalog (not a filtered subset for this "phase") with descriptions, params, and — importantly — which hypotheses each historically discriminates between (metadata, not a rule).
- Explicit instruction to propose exactly one next action *or* a hypothesis revision *or* a stage-transition proposal (advance to DECIDING / escalate), justified against current evidence gaps.

There is no orchestrator-level restriction on *which* tool can be called at what point — restrictions come only from the Guardrail Layer (ceilings, risk gates), never from a scripted phase order. This is what allows: calling `search_historical_incidents` before or after `get_message_logs` depending on what the incident intake already suggests; calling `check_certificate_status` only when a TLS-shaped symptom appears in evidence rather than every time; skipping tools entirely when case-memory retrieval already gives high-confidence discriminating evidence (this is the mechanism behind the "replay" scenarios in spec §16 needing fewer tool calls). The eval suite (spec §16) directly tests this by requiring *different* tool orders across scenarios with different symptom profiles; identical ordering across dissimilar scenarios is treated as a failure signal during eval review, not just a style concern.

## 6. Termination Conditions

The incident lifecycle terminates (reaches `RESOLVED` or `ESCALATED`) if and only if one of:

- **RESOLVED**: a `VerificationResult.outcome == Passed` is recorded for the most recent `RemediationAction`.
- **ESCALATED**, by exactly one of:
  - Agent-initiated escalation from `INVESTIGATING` or `DECIDING` (low confidence, contradictory evidence, or explicit "no safe action" judgment).
  - Investigation cycle ceiling reached (§7) without sufficient confidence to act.
  - Remediation attempt ceiling reached (§7) without a passing verification.
  - `AWAITING_APPROVAL` timeout.
  - Verification returns `Failed`/`Inconclusive` with zero retry budget remaining.

There is no path that leaves the incident indefinitely in `INVESTIGATING`, `DECIDING`, `ACTING`, or `VERIFYING` — every one of those stages has a guardrail-enforced forced exit in addition to its agent-initiated exit, guaranteeing a terminal state is always reachable (spec §12, §15 anti-goal #8).

## 7. Maximum Investigation Steps / Loop Bounds

Static, orchestrator-enforced ceilings (configurable, MVP defaults shown):

| Bound | Default | Enforced by | Effect on breach |
|---|---|---|---|
| `MAX_INVESTIGATION_CYCLES` | 8 | Guardrail Layer, checked before allowing another `DiagnosticAction` in `INVESTIGATING` | Forces transition to `DECIDING` (must act or escalate with current evidence) |
| `MAX_REMEDIATION_ATTEMPTS` | 2 | Guardrail Layer, checked before allowing another `RemediationAction` | Forces `ESCALATED` |
| `MAX_VERIFICATION_RETRIES_PER_REMEDIATION` | 1 (i.e., one retry of the *same* remediation strategy before requiring a pivot or escalation) | Guardrail Layer at `VERIFYING` → `DECIDING` transition | Forces hypothesis pivot or `ESCALATED`; cannot repeat an identical remediation action a third time |
| `AWAITING_APPROVAL_TIMEOUT` | configurable (e.g., simulated as N orchestrator ticks for MVP, since no real clock/human latency exists) | Human Approval Interface | Forces `ESCALATED` |
| `SLA_REMAINING_FORCE_DECIDE_THRESHOLD` | e.g., 20% of SLA window remaining | Guardrail Layer, checked every turn | Forces stage to `DECIDING` regardless of investigation cycle count, i.e. cuts investigation short under time pressure |

These are read from Policy Memory (spec §5, §9) — not hardcoded in the agent's prompt and not adjustable by the agent itself.

## 8. Open Items Carried Forward

Unchanged from the prior unresolved-decisions list in the spec discussion (not re-litigated here): confidence-score computation mechanism, orchestrator implementation choice (custom state machine vs. framework), exact `symptom_signature` schema fields, approval-timeout UX, fixture-generation approach. This document assumes answers to those will be made at implementation time and is written to be compatible with multiple choices for each.
