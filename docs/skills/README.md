# Skills

Companion to [PROJECT_SPEC.md](../PROJECT_SPEC.md), [AGENT_ARCHITECTURE.md](../AGENT_ARCHITECTURE.md), and [TOOL_SPEC.md](../TOOL_SPEC.md).

A **skill** is a reasoning procedure, not a tool and not a decision tree. It tells the agent: what situations it's relevant to, what evidence tends to matter, what hypotheses are plausible, how to weigh evidence for/against each, and what remediation/verification/escalation shapes are appropriate. It never tells the agent a fixed order of tool calls or a fixed branch of if/else logic. The agent (architecture §1.1) reads the applicable skill(s) as context, then still makes its own per-turn proposal (which tool, which hypothesis update, act/investigate-more/escalate) using the current `IncidentState`. Multiple skills can be relevant simultaneously (e.g., a timeout symptom might implicate both Connectivity and Performance skills); the agent is expected to hold hypotheses from more than one skill in its competing hypothesis set at once.

Skill selection itself is dynamic: the agent chooses which skill(s) apply based on early evidence (symptom description, first log scan), and may pull in an additional skill mid-investigation if evidence points somewhere new (e.g., a connectivity investigation surfaces a cert expiry, pulling in the Certificate skill). No skill is entered via a hardcoded incident-type-to-skill lookup table alone — that lookup is a *starting suggestion*, not a constraint, and the agent may deviate from it.

## Index

1. [Incident Investigation](01_incident_investigation/SKILL.md) — the general-purpose entry skill, always relevant, used to triage and route into specialist skills.
2. [Authentication Failure Investigation](02_authentication_failure/SKILL.md)
3. [Connectivity Failure Investigation](03_connectivity_failure/SKILL.md)
4. [Certificate Failure Investigation](04_certificate_failure/SKILL.md)
5. [Mapping/Data Transformation Failure Investigation](05_mapping_transformation_failure/SKILL.md)
6. [Timeout/Performance Investigation](06_timeout_performance/SKILL.md)
7. [Message Processing Failure Investigation](07_message_processing_failure/SKILL.md)
8. [Incident Escalation](08_incident_escalation/SKILL.md) — not a root-cause skill; governs how/when to hand off regardless of which specialist skill was in use.

## Shared Conventions

- **"Evidence to gather" is a menu, not a checklist.** The agent picks from it based on what's still uncertain given `IncidentState.evidence` so far — it does not need to (and should not always) call every listed tool.
- **"Hypotheses" are named `RootCauseCategory` values** (architecture §2.3) so a skill's hypotheses map directly onto `Hypothesis.category` in state.
- **"Confirms/refutes" statements are heuristics for updating `Hypothesis.confidence`**, not boolean gates — evidence can weakly or strongly shift confidence, and the agent is expected to reason about degree, not just direction.
- **Remediation options are candidates**, listed with their risk tier (per [TOOL_SPEC.md](../TOOL_SPEC.md)) — the agent still must justify why the specific one chosen fits the confirmed hypothesis, current SLA pressure, and risk posture.
- **Verification requirements** describe *what "actually fixed" looks like for this hypothesis class*, since a generic "tool call succeeded" is insufficient (spec §14, "verification blindness").
- **Escalation conditions** are skill-specific triggers that feed into the general [Incident Escalation](08_incident_escalation/SKILL.md) skill, not a separate escalation mechanism.
