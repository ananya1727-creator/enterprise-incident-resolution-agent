# Project Specification: Enterprise Integration Incident Resolution Agent

## 1. Problem Statement

Enterprise integration platforms (SAP PO, SAP CPI, MuleSoft, Boomi, etc.) run thousands of interfaces connecting ERP, CRM, EDI, and partner systems. When an interface fails — a message stuck in error, a mapping exception, a connectivity timeout, an auth failure — resolution today depends on an on-call integration engineer who:

- Receives an alert or ticket with minimal context.
- Manually pulls logs from multiple monitoring consoles.
- Cross-references interface documentation, past incidents, and tribal knowledge.
- Forms a theory of what broke, tests it, and iterates.
- Applies a fix (resend message, restart channel, correct payload, engage upstream team).
- Confirms the fix worked and closes the ticket, rarely recording *why* in a reusable form.

This is slow (MTTR often hours), inconsistent (depends on who's on call), and repetitive (the same 20 root causes recur constantly, but every incident is diagnosed from scratch).

The problem is **not** "summarize logs" or "search a knowledge base and suggest an answer." It is: **given an ambiguous, partially-observed failure, autonomously drive an investigation to a confident root cause, take bounded corrective action, verify the action worked, and know when to stop and ask a human.** That loop — evidence-driven, revisable, action-taking, self-verifying — is what makes this agentic rather than a lookup tool.

## 2. Target Users

- **Primary: Integration on-call engineers / L2 support.** The agent should reduce their triage load and handle routine incidents end-to-end, escalating only what genuinely needs a human.
- **Secondary: Integration Center of Excellence / platform owners.** They define escalation policy, risk tiers, and review the agent's learned resolutions.
- **Tertiary: Business stakeholders (order management, finance ops, etc.)** who are indirectly served by faster SLA-compliant resolution but never interact with the agent directly.
- **Non-user: End customers / trading partners.** Out of scope for direct interaction; they are only affected via SLA outcomes.

## 3. Incident Lifecycle

The agent operates a strict, revisitable loop. Every state can transition backward (e.g., VERIFY failing sends the agent back to HYPOTHESIZE), which is the core anti-pattern being designed against (see §13).

```
OBSERVE
  → INVESTIGATE ⇄ HYPOTHESIZE     (repeats: gather evidence, update belief, decide what to gather next)
  → DECIDE
  → ACT
  → VERIFY
      → RESOLVE
      → (verify fails) → back to HYPOTHESIZE or INVESTIGATE (bounded retries)
      → ESCALATE        (confidence too low, risk too high, retries exhausted)
  → LEARN                        (always, regardless of outcome)
```

| Stage | Purpose | Key question the agent must answer |
|---|---|---|
| OBSERVE | Ingest incident, classify criticality/SLA | "What broke, for whom, how urgently?" |
| INVESTIGATE | Pull evidence: logs, interface metadata, historical incidents, run diagnostics | "What evidence is missing to discriminate between my hypotheses?" |
| HYPOTHESIZE | Maintain a ranked, competing set of root-cause hypotheses with confidence scores | "What are the plausible explanations, and how does new evidence shift them?" |
| DECIDE | Choose next action: gather more evidence, remediate, or escalate | "Do I have enough confidence and low enough risk to act, or do I need more data / a human?" |
| ACT | Execute a diagnostic or remediation tool | "Which specific tool call, with which parameters?" |
| VERIFY | Check whether the action produced the expected system state | "Did that actually fix it, or just appear to?" |
| RESOLVE / ESCALATE | Close with full evidence trail, or hand off with a structured brief | "Is this truly done, or does a human need to take over?" |
| LEARN | Persist the case (symptoms → root cause → fix → verification) for future retrieval | "What should future-me remember from this?" |

## 4. Agent Responsibilities (what the AI must decide, not just execute)

The agent — not a script — is responsible for:

1. Assessing business criticality/SLA from incident + interface metadata (not a static lookup table alone; it must reconcile conflicting signals, e.g. "P1 tag but affects a decommissioned interface").
2. Choosing *which* evidence to pull next, in what order, based on current hypothesis uncertainty — not a fixed checklist run every time.
3. Forming and maintaining multiple competing hypotheses simultaneously, with explicit confidence weights.
4. Updating hypothesis confidence after each piece of evidence (Bayesian-ish belief revision, not "first match wins").
5. Deciding when confidence is sufficient to act vs. when more investigation is needed.
6. Selecting the remediation tool and parameters appropriate to the diagnosed root cause and its risk tier.
7. Deciding autonomously to execute low-risk actions.
8. Deciding to *pause and request approval* for high-risk actions, with a clear justification package.
9. Verifying outcomes against the original failure signature, not just "did the tool call return success."
10. Deciding whether a failed verification warrants a retry, a different hypothesis, or escalation — and not looping indefinitely.
11. Constructing the escalation brief (what was tried, what evidence contradicts what, what's still unknown) if it can't resolve.
12. Deciding what part of this episode is worth persisting to long-term memory, and in what generalized form.

## 5. Non-Agent Responsibilities (deliberately NOT left to model judgment)

To keep the system safe and testable, the following are handled by deterministic code/config, not LLM judgment:

- **Risk tier classification of each remediation tool** — a static registry (tool → risk level), not something the agent infers per-call.
- **Enforcement of the approval gate** — the orchestrator, not the agent, blocks execution of high-risk tools until an approval record exists. The agent can *request*; it cannot *waive*.
- **Retry/loop bounds** — max investigation cycles, max remediation attempts, max wall-clock time are hard-coded ceilings the orchestrator enforces regardless of what the agent "wants."
- **SLA clock and criticality-to-SLA mapping** — sourced from a config/CMDB-like table, not inferred from free text alone (the agent may flag inconsistency but does not overrule the source of truth).
- **Audit logging of every tool call, decision, and state transition** — automatic, not agent-authored (agent output is *content* of the log; the act of logging is guaranteed by the harness).
- **Credential/access management for real systems** (N/A in MVP since everything is simulated, but the principle holds for future scope).
- **Final authorization of high-risk actions** — always a human.

## 6. Agentic Decision Points (the parts that must show real reasoning under uncertainty)

These are the specific junctures where the system is tested for being "agentic" vs. scripted:

- **D1 — Evidence selection**: given current hypotheses, which of N available diagnostic tools/logs to consult next (must vary incident-to-incident based on state, not a fixed sequence).
- **D2 — Hypothesis competition**: holding ≥2 competing root-cause theories with confidence scores that shift as evidence arrives; must be able to *demote* an initially-favored hypothesis.
- **D3 — Act-or-investigate-more**: threshold decision balancing confidence, time-to-SLA-breach, and cost/risk of being wrong.
- **D4 — Remediation selection**: mapping a specific diagnosed root cause to one of several candidate fixes, including "do nothing, just notify" as a valid choice.
- **D5 — Autonomy gate**: low-risk → act now; high-risk → construct approval request and wait.
- **D6 — Verification interpretation**: distinguishing "tool reported success" from "symptom actually resolved" (these can diverge — e.g., message resent but still erroring for a different reason).
- **D7 — Retry vs. pivot vs. escalate**: after failed verification, decide whether the *hypothesis* was wrong (pivot) or the *execution* was wrong (retry) or neither is safe to keep trying (escalate).
- **D8 — Escalation content**: what to tell the human so they don't restart from zero.
- **D9 — Memory write**: what generalization of this episode is worth keeping, and how it should be indexed for retrieval (symptom pattern, not incident ID).

## 7. Tools Required (simulated for MVP)

Diagnostic tools:
- `get_incident_details(incident_id)`
- `get_interface_metadata(interface_id)` — endpoints, message types, SLA tier, owning team, known fragility flags
- `search_historical_incidents(query / symptom_signature)`
- `get_message_logs(interface_id, time_range)` — simulated SAP PO/CPI style error logs
- `get_channel_status(channel_id)` — up/down, last successful message
- `get_adapter_config(interface_id)` — auth type, endpoint URL, timeout settings
- `check_downstream_system_health(system_id)` — simulated ping/heartbeat
- `inspect_payload(message_id)` — simulated payload/mapping error detail
- `check_certificate_status(interface_id)` — expiry, validity (a very common SAP PO root cause)

Remediation tools, each tagged with a static risk tier:
- `resend_message(message_id)` — **low risk**
- `restart_channel(channel_id)` — **medium risk**
- `clear_queue_block(queue_id)` — **medium risk**
- `update_adapter_timeout(interface_id, value)` — **high risk** (config change)
- `rotate_certificate(interface_id)` — **high risk**
- `notify_owning_team(team, brief)` — **low risk** (non-destructive, always available)
- `open_partner_ticket(partner_id, brief)` — **low risk**

Verification tools:
- `replay_and_check(message_id)` reuses log/message-status tools to confirm resolution
- `get_message_logs` again, diffed against the pre-action snapshot

All of these are simulated against a fixture dataset for MVP (§14) — no real SAP connection.

## 8. Knowledge Sources

- **Interface metadata catalog** (simulated CMDB): topology, criticality, ownership, SLA tier.
- **Historical incident store**: past incidents with symptom, root cause, fix, outcome — the substrate for both retrieval and learning.
- **Runtime log/message store**: simulated SAP PO/CPI message and error logs, generated to include a fixed library of root-cause patterns (cert expiry, endpoint timeout, mapping/schema mismatch, auth failure, partner-side outage, queue congestion, duplicate message, encoding error) plus noise/red herrings.
- **Policy/config store**: risk tiers, SLA-to-criticality mapping, approval routing rules — explicitly *not* knowledge the LLM infers, but data it reads.

## 9. Memory Requirements

Three distinct memory types, not to be conflated:

- **Working memory (episodic, per-incident)**: the evolving hypothesis set, evidence gathered, actions taken, confidence trajectory — scoped to one incident's lifecycle, discarded/archived at RESOLVE/ESCALATE.
- **Case memory (long-term, cross-incident)**: a structured record per resolved incident — symptom signature, evidence pattern, root cause, remediation, verification outcome, human feedback if any — written at LEARN, retrieved at INVESTIGATE via `search_historical_incidents`. This is what lets the agent get *faster and better* over time, not just repeat OBSERVE→...→LEARN identically forever.
- **Policy memory (slow-changing)**: risk tiers, escalation thresholds, approval routing — updated by humans/CoE, read by the agent, never self-modified by the agent.

Critical design point: case memory must store the *generalized pattern* (e.g., "cert expiry within 7 days of failure, TLS handshake error signature") not just a transcript, or retrieval degrades to keyword matching on free text and stops being useful evidence.

## 10. Human-in-the-Loop Points

- **Approval gate** before any high-risk remediation tool executes (rotate_certificate, update_adapter_timeout).
- **Escalation handoff** when confidence stays below threshold after bounded investigation, or retries are exhausted, or evidence is contradictory.
- **Post-resolution review (async, optional for MVP)**: humans can annotate a LEARNed case as correct/incorrect, which should downweight bad generalizations later (full active-learning loop is future scope; MVP just needs the annotation hook to exist).
- **Override at any time**: a human can inject a directive mid-incident (e.g., "stop, I'll take it from here") — the orchestrator must honor this as a hard interrupt, not something the agent can reason its way past.

## 11. Risk Levels

| Tier | Definition | Examples | Autonomy |
|---|---|---|---|
| Low | Reversible, no data loss, no config change, scoped to one message/instance | resend_message, notify_owning_team | Agent may act autonomously |
| Medium | Reversible but affects live channel/queue state, could double-process if wrong | restart_channel, clear_queue_block | Agent may act autonomously but must log rationale + auto-notify owning team |
| High | Config/security change, hard to reverse quickly, affects all future traffic on the interface | update_adapter_timeout, rotate_certificate | Requires explicit human approval before execution |

Risk tier is a static property of the tool (per §5), not something the agent negotiates.

## 12. Guardrails

- Hard ceiling on investigation cycles per incident (e.g., max 8 tool calls) before forced escalation.
- Hard ceiling on remediation attempts per incident (e.g., max 2 distinct remediation actions) before forced escalation.
- No remediation tool call may execute without: (a) an explicit stated hypothesis it addresses, (b) a confidence score above a configured threshold, (c) risk-tier-appropriate authorization.
- Verification is mandatory after every remediation — no action is marked RESOLVE without a passing VERIFY step.
- Escalation is always a safe, always-available exit — the agent must never be in a state where it can only pick "retry again."
- All tool calls, hypothesis states, and confidence scores are logged immutably for audit, independent of the agent's own narrative summary.
- The agent cannot modify policy memory (risk tiers, thresholds, SLA mapping) — read-only.
- Time-boxing: if time-to-SLA-breach drops below a threshold mid-investigation, the agent is forced to re-evaluate act-vs-investigate (D3) immediately rather than continuing a long evidence-gathering chain.

## 13. Success Metrics

- **Resolution accuracy**: % of agent-resolved incidents where the diagnosed root cause matches ground truth (in simulated eval set).
- **False resolve rate**: % of RESOLVE outcomes where the underlying issue actually recurs (critical to track near zero).
- **Escalation precision**: % of escalations that a human agrees were appropriate (agent didn't escalate incidents it should've solved, and didn't "resolve" things it should've escalated).
- **Evidence efficiency**: number of tool calls to reach correct diagnosis (lower is better, but not at the cost of accuracy).
- **Hypothesis trajectory quality**: whether confidence in the eventually-correct hypothesis rises monotonically-ish as relevant evidence arrives (a proxy for "is it actually reasoning from evidence").
- **Guardrail adherence**: zero violations of hard ceilings/approval gates in eval runs.
- **Time-to-resolution (simulated)**: wall-clock/tool-call count vs. baseline scripted approach.

## 14. Failure Modes (and what they'd look like here)

- **Premature convergence**: agent locks onto first plausible hypothesis and stops gathering discriminating evidence — detect via hypothesis-trajectory metric and eval cases with deliberate red herrings.
- **Confidence theater**: agent states high confidence without evidence actually supporting it — detect by requiring every confidence update to cite which evidence changed it, and auditing that against ground truth.
- **Verification blindness**: treats tool-call success as symptom resolution — must be explicitly tested with eval cases where the remediation "succeeds" but the underlying symptom persists.
- **Infinite/near-infinite looping**: retry-hypothesize-retry without bound — prevented by hard ceilings (§12), tested by adversarial eval cases with no clean resolution.
- **Escalation avoidance**: agent keeps trying remediation to avoid escalating (a plausible reward-hacking-like failure if success metrics overweight resolution rate) — mitigate by weighting false-resolve rate and guardrail adherence at least as heavily as raw resolution rate.
- **Escalation laziness**: agent escalates too eagerly to avoid any risk — mitigate by measuring escalation precision, not just escalation safety, so an eval case with an obvious low-risk fix should be resolved, not punted.
- **Static checklist masquerading as agent**: always pulls the same evidence in the same order regardless of hypothesis state — directly tested by eval cases that require *different* evidence-gathering orders for different symptom profiles (see §16).
- **Unaudited/unsafe autonomy**: high-risk action executed without approval record — must be a hard orchestrator-level invariant, tested adversarially.
- **Memory that doesn't generalize**: case memory stores raw transcripts, so retrieval only works for near-identical future incidents — tested by evaluating retrieval usefulness on paraphrased/varied incident descriptions of the same root cause.

## 15. What Would Make This System NOT Agentic (explicit anti-goals)

The system fails as "agentic" — becoming a rules engine or chatbot with tool access — if any of the following hold, and the architecture must be designed against each:

1. **Fixed evidence-gathering order regardless of state.** *Design against:* the orchestrator must let the agent choose the next tool call from the full available set based on current hypothesis state, never a hardcoded sequence. Eval suite must include cases requiring different orders.
2. **Single hypothesis, no revision.** *Design against:* hypothesis state is explicitly a ranked set with confidence scores, and the state schema requires re-evaluation after every evidence event, not just an appended "final answer."
3. **No real branching on outcomes** (i.e., verification always assumed to pass, retry logic absent). *Design against:* VERIFY is a first-class stage with its own tool calls and explicit fail path back into HYPOTHESIZE/INVESTIGATE, bounded by guardrails, not skipped.
4. **Action selection reduces to a lookup table** (root-cause string → fixed remediation). *Design against:* remediation selection must weigh risk tier, SLA remaining, and confidence together, and "notify only, no remediation" must be a live option even when a technical fix exists, so the mapping isn't 1:1.
5. **No memory / no learning** — every incident handled identically forever. *Design against:* LEARN is mandatory in the lifecycle, case memory is queried at INVESTIGATE, and eval must show later incidents of a known pattern resolved with fewer tool calls than the first occurrence.
6. **Human approval treated as a formality the agent can bypass or auto-approve.** *Design against:* enforced at the orchestrator layer as a hard gate, independent of agent output (§5).
7. **Confidence scores that are decorative** (never move, or don't correspond to evidence). *Design against:* every confidence change must be attributable to a specific evidence item in the trace, and this is checked in eval, not just displayed.
8. **Unbounded or absent stopping condition** (no escalation path, or escalation as an afterthought). *Design against:* escalation is a guaranteed reachable terminal state from any point in the loop (§12).

## 16. Evaluation Methodology

- **Fixture-based scenario suite**: a fixed library of simulated incidents (target: 15-25 for MVP) spanning all root-cause categories in §7's log generator, each with:
  - Ground-truth root cause
  - Ground-truth correct remediation (or "no autonomous action, escalate")
  - Ground-truth risk tier of the correct action
  - At least 3 scenarios with deliberate red herrings (evidence that initially supports a wrong hypothesis)
  - At least 2 scenarios where the "obvious" first remediation fails verification and a second strategy is needed
  - At least 2 scenarios that must escalate (ambiguous evidence or high-risk-only fix with no approval available in the eval harness)
  - At least 2 "replay" scenarios reusing a root-cause pattern from an earlier scenario, to test case-memory speedup
- **Per-scenario scoring**: root cause correctness, remediation correctness, risk-tier/approval-gate compliance, verification correctness, escalation appropriateness, tool-call efficiency, hypothesis-trajectory sanity (via evidence-citation check in §15/D9... i.e. §6/§14).
- **Aggregate scoring**: the metrics in §13, computed across the full suite, tracked over time as the system changes (regression detection).
- **Adversarial/guardrail suite**: separate small suite specifically probing the anti-goals in §15 (e.g., an incident with no clean resolution path, to confirm the agent escalates rather than looping or false-resolving).
- **No LLM-as-sole-judge for correctness**: root cause/remediation/risk-tier correctness are checked against the fixture's ground truth programmatically; an LLM judge (if used at all) is reserved for qualitative checks like escalation-brief clarity, not for pass/fail on the core lifecycle.

## 17. MVP Scope

In scope:
- Single domain: SAP PO/SAP CPI-style integration incidents, fully simulated (no real SAP connectivity).
- Full OBSERVE→LEARN lifecycle implemented end-to-end for at least the root-cause categories listed in §8.
- All tools in §7 implemented as simulated functions over fixture data (deterministic or seeded-random, not calling any real external API).
- Hypothesis state with confidence scores, revised across multiple evidence-gathering turns.
- Risk-tiered remediation with a real (simulated) approval-gate interrupt for high-risk actions — approval can be simulated via a CLI/config prompt, not a full ticketing UI.
- Verification step with real pass/fail branching and bounded retry.
- Escalation path producing a structured brief.
- Case memory: read (retrieval) and write (learning) against a simple persistent store (e.g., local file/DB) — does not need a vector DB or production-grade retrieval; a structured symptom-signature match is sufficient for MVP.
- Guardrails (§12) enforced at the orchestrator level, not just prompted for.
- Evaluation suite per §16, runnable locally, producing the metrics in §13.

Out of scope for MVP:
- Any real integration platform connectivity (SAP PO/CPI, actual REST/SOAP endpoints, real certificates).
- Multi-domain support (only integration incidents, not general IT incidents).
- Production-grade approval UI/workflow (Slack/ServiceNow integration, etc.) — simulated approval interface only.
- Vector-embedding-based semantic retrieval for case memory (structured/simulated matching is enough to prove the learning loop).
- Multi-agent collaboration (single agent orchestrating tool calls; no sub-agent delegation).
- Fine-tuning or online learning of the underlying model — "learning" here means memory writes, not weight updates.
- Real-time streaming ingestion of incidents (batch/single-incident invocation is fine).

## 18. Future Scope

- Real connectivity to SAP PO/CPI monitoring APIs (or equivalent iPaaS) once the simulated version validates the lifecycle and guardrails.
- Multi-domain generalization (network incidents, application incidents, cloud infra) reusing the same lifecycle/orchestrator with domain-specific tool sets.
- Production approval workflows integrated with real ticketing/chat systems.
- Semantic/embedding-based case memory retrieval, plus active learning from human annotations on past resolutions.
- Multi-agent decomposition (e.g., a dedicated "evidence gathering" sub-agent vs. a "remediation planning" sub-agent) if single-agent orchestration proves to be a bottleneck.
- Proactive/predictive mode: using case memory patterns to flag likely-to-fail interfaces before an incident is even raised.
- Cross-incident correlation (recognizing that 5 simultaneous incidents share one upstream root cause).
