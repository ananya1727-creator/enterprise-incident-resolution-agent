# Risk-Based Autonomy Framework

Companion to [TOOL_SPEC.md](TOOL_SPEC.md) and [AGENT_ARCHITECTURE.md](AGENT_ARCHITECTURE.md). Defines the LOW/MEDIUM/HIGH action classification and, critically, *how it is enforced* — as tool-layer policy the orchestrator applies to every proposed action, not as instructions given to the reasoning model.

## 1. Classification

| Tier | Definition | Examples in this catalog | Autonomy |
|---|---|---|---|
| **LOW** | Read-only, or writes only to the incident record itself (no effect on the interface/system under investigation) | `search_logs`, `retrieve_runbook`, `test_connectivity`, `get_interface_metadata`, `inspect_credentials`, `inspect_certificate_status`, `compare_with_previous_successful_run`, `search_historical_incidents`, `verify_interface_health`, `verify_message_processing`, `create_escalation`, `update_incident` | Always autonomous. Never gated, under any configuration. |
| **MEDIUM** | Reversible but mutates live system/message/channel state | `retry_message`, `restart_interface`, `refresh_connection` | Requires approval **by default**. Autonomous **only** if explicitly configured safe for the running environment (`config/risk_policy.json`). |
| **HIGH** | Persistent config/security change, destructive at scale, or changes where traffic is delivered | `update_adapter_timeout` (modify production configuration), `rotate_certificate` (modify credentials), `reprocess_message_batch` (delete/reprocess large message batches), `update_routing_rule` (change routing) | **Always** requires human approval. No override exists for this tier under any configuration. |

This is a strict widening from the original per-tool risk tiers introduced in `docs/TOOL_SPEC.md`: `retry_message` was originally classified Low (justified there as "scoped to one message, reversible"); this framework reclassifies it Medium, since it still mutates live message state regardless of blast radius. `docs/TOOL_SPEC.md` has been updated to match.

## 2. Where the enforcement actually lives

The critical requirement — *"the agent must never bypass the approval mechanism"* and *"implement this as an enforceable tool-layer policy rather than merely a prompt instruction"* — is met by three independent, layered checks, none of which the agent's reasoning code (`agent/reasoning.py`) can see or influence:

1. **`agent/registry.py`** — the static, frozen record of each tool's risk tier. The agent has no tool that reads or writes this file's contents; it is Python source, not agent-accessible state.
2. **`agent/risk_policy.py`** — the single source of truth for `requires_approval(tool_name)`. It is a pure function of (a) the registry's risk tier and (b) `config/risk_policy.json`'s `medium_risk_safe_overrides` map, loaded once at process start. For HIGH-tier tools, the function doesn't even consult the config file — there is no code path by which HIGH can resolve to "autonomous."
3. **`agent/guardrails.py`** — `check_remediation_proposal()` calls `risk_policy.requires_approval()` before any remediation proposal is allowed to reach Tool Execution, and routes it to `AWAITING_APPROVAL` if required. This is the primary gate.
4. **`agent/orchestrator.py`'s `_act()`** — a *second*, independent check, immediately before the tool actually executes: `risk_policy.assert_approved_if_required(tool_name, approval_decision)` raises `ApprovalBypassError` (a hard exception, not a soft warning) if a gated tool is about to run without a `decision == "approved"` record on file. This exists specifically so that a bug in the routing logic in step 3 — or any future code path that constructs a `RemediationAction` and tries to execute it directly — still cannot slip a gated action through. The check re-derives the requirement from the registry/policy every time; it does not trust a cached flag on the action object.

```
Agent proposes RemediationAction
        │
        ▼
guardrails.check_remediation_proposal()  ──uses──▶  risk_policy.requires_approval(tool_name)
        │                                                     │
        ├─ not required ─────────────────────────────────────▶ execute
        │                                                     │
        └─ required ──▶ AWAITING_APPROVAL ──▶ Human Approval ──▶ decision recorded
                                                                 │
                                                                 ▼
                                              orchestrator._act(): risk_policy.assert_approved_if_required()
                                                                 │
                                                        (raises if not approved) ──▶ execute
```

The agent's only lever anywhere in this chain is the *initial proposal* (which tool, what params, what rationale) — it has no way to mark a proposal "pre-approved," no tool that writes to `config/risk_policy.json`, and no code path that reaches tool execution without passing through both checks above.

## 3. The MEDIUM override mechanism

`config/risk_policy.json`:

```json
{
  "medium_risk_safe_overrides": {
    "retry_message": true,
    "refresh_connection": true,
    "restart_interface": false
  }
}
```

- A MEDIUM tool **not listed** in this map defaults to `false` (requires approval) — the safe default is "ask," not "assume safe."
- `retry_message` and `refresh_connection` are marked safe *in this simulated environment*, which is why earlier demo runs (`python main.py INC-0099`) showed them executing without a visible approval step — that was always this override in effect, now made an explicit, auditable config value instead of an implicit code default.
- `restart_interface` is deliberately left `false` to prove the override is genuinely per-tool: it now routes through `AWAITING_APPROVAL` even though it is Medium tier, exactly like a HIGH-risk tool would, until a human (not the agent) changes this file.
- This file is read once at import time (`agent/risk_policy.py`'s module-level `_MEDIUM_OVERRIDES`), not re-read per incident or per call — the policy is fixed for the process lifetime, not something a running investigation can influence.

## 4. Why HIGH has no override

MEDIUM's override exists because "reversible but mutates live state" is a genuine spectrum — a well-understood, low-blast-radius action (like resending one message) can reasonably be pre-cleared for autonomous execution in a given environment. HIGH-tier actions (persistent config change, credential/certificate rotation, destructive batch operations, routing changes) are excluded from that mechanism entirely: `risk_policy.requires_approval()` returns `True` for HIGH unconditionally, before it ever looks at the config file. Making HIGH configurable would reintroduce exactly the risk the framework exists to prevent — a config file mistake silently making a destructive action autonomous.

## 5. Demonstrating the guarantee

`reprocess_message_batch` and `update_routing_rule` (HIGH tier) are implemented in the simulated tool layer and exposed over MCP (`docs/MCP_ARCHITECTURE.md`) but are **not** wired into the reasoning engine's automatic remediation selection (`REMEDIATION_TOOL_BY_CATEGORY` in `agent/simulated_tools.py` has no category pointing to either). They exist to complete the risk catalog the task specified, and to make the enforcement mechanism testable independent of whether the current reasoning heuristics happen to select them:

```python
from agent import risk_policy

risk_policy.requires_approval("search_logs")            # False -- LOW
risk_policy.requires_approval("retry_message")           # False -- MEDIUM, overridden safe
risk_policy.requires_approval("restart_interface")       # True  -- MEDIUM, not overridden
risk_policy.requires_approval("rotate_certificate")      # True  -- HIGH, no override possible
risk_policy.requires_approval("reprocess_message_batch") # True  -- HIGH, no override possible

risk_policy.assert_approved_if_required("rotate_certificate", approval_decision=None)
# raises ApprovalBypassError
```
