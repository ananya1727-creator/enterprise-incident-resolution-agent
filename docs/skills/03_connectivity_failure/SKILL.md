# Skill: Connectivity Failure Investigation

## When This Skill Is Relevant

Total silence in logs (no traffic at all, not even errors), network-layer error signatures (connection refused, DNS failure, unreachable host), or a sudden drop from consistent throughput to zero. Also relevant as a ruling-out step for other skills — e.g., Authentication and Timeout investigations should typically confirm the endpoint is at least network-reachable before localizing further, since a fully-down endpoint can produce log signatures that superficially resemble auth or timeout failures.

## Evidence to Gather

- Direct reachability check of source/target endpoints (`test_connectivity`).
- Log scan for the *absence* pattern specifically — total silence is itself a distinct signature from active error logging, and the two point toward different sub-hypotheses (`search_logs`).
- Recent network/infrastructure-adjacent changes (`get_recent_changes`, filtered toward `network`/`config` change types).
- Whether other interfaces sharing the same target system are also affected — not directly a tool in the catalog, but inferable by running `test_connectivity`/`check_downstream_system_health`-style checks against the same target from a different interface's context if the agent has reason to suspect a broader outage rather than an interface-specific issue.
- Historical precedent for connectivity issues on this interface or target system (`search_historical_incidents`).

## Diagnostic Reasoning Approach

The central discriminating question is: **is our side down, or is the other side down (or unreachable from here)?** `test_connectivity` against both `source` and `target` endpoints directly addresses this and should generally be one of the earliest calls once this skill is active, since it cheaply partitions the hypothesis space. A `both` result of "source reachable, target unreachable" points toward partner-outage or network-path issues outside the agent's remediation surface; "both unreachable" or ambiguous/erroring probes point toward a local/channel-level issue that `restart_interface` might address. The agent should also weigh log silence duration against normal traffic patterns for this interface if that context is available (a low-volume batch interface being silent for an hour is very different evidence than a normally-continuous real-time interface being silent for the same duration) — this is a case where `get_interface_metadata`'s message_type/context should inform interpretation, not just raw silence.

## Possible Hypotheses

- `partner_outage` — target system/endpoint unreachable or down
- `timeout`-adjacent — network path degraded but not fully down (slow, not unreachable; may overlap with the Timeout/Performance skill)
- Local channel/adapter stuck or down (not a partner issue at all — our side's channel needs restarting)
- `other` — DNS/routing/firewall change causing a local misconfiguration rather than a true partner outage (correlates with a `network` recent-change entry)

## Evidence That Confirms/Refutes Each Hypothesis

- `test_connectivity` reporting `reachable: false` for `target` specifically (with `source` reachable) strongly confirms partner-outage/network-path hypotheses and should lower confidence in any "our side" hypothesis (channel-stuck, local misconfig).
- `test_connectivity` reporting both unreachable, or the probe itself erroring/timing out, is weaker, more ambiguous evidence — it doesn't cleanly discriminate partner-outage from a local network/DNS problem, and the agent should seek a second signal (e.g., a `network`-type entry in `get_recent_changes`) before committing confidence to either.
- A `network`-type recent change shortly before onset, combined with a local/our-side-looking failure, supports the local-misconfiguration hypothesis over partner-outage.
- Reachability restored on a later recheck without any action taken (evidence gathered incidentally while investigating something else) is itself informative — a transient partner-side blip may not need remediation at all beyond `retry_message` for any messages that failed during the window, refuting the need for a channel restart.

## Tools That May Be Used

`test_connectivity`, `search_logs`, `get_recent_changes`, `search_historical_incidents`, `retrieve_runbook`, `restart_interface` (remediation), `retry_message` (remediation, for individual messages once connectivity is confirmed restored), `create_escalation`, `update_incident`.

## Remediation Options

- **`restart_interface` (Medium risk, autonomous with notify)** — appropriate when evidence points to a local channel/adapter being stuck rather than the target genuinely being down; restarting a channel while the actual root cause is a partner outage will predictably fail verification and should not be attempted as a first guess without at least one piece of evidence suggesting the local side is implicated.
- **`retry_message` (Low risk, autonomous)** — appropriate once connectivity is confirmed restored (either by a passive recheck or after a channel restart) to reprocess any messages that failed during the outage window, rather than waiting for them to be picked up by normal retry mechanisms if the agent has reason to believe they won't be.
- **No autonomous tool exists to fix a genuine partner-side outage** — the appropriate action is `create_escalation` (to notify the owning team / open a partner-facing ticket), not a repeated local restart, since restarting our own channel cannot resolve a problem on the other side of the connection.

## Verification Requirements

After `restart_interface`, verification must recheck reachability (`verify_interface_health`) *and*, if there were specific failed messages, confirm they now process (`verify_message_processing`) — a restarted channel that's reachable but still failing the same messages suggests the root cause wasn't a stuck channel at all, and the agent should pivot toward partner-outage or a different hypothesis rather than repeating the restart.

## Escalation Conditions

- `test_connectivity` confirms the target endpoint itself is unreachable/down (partner-side) — no local remediation tool can fix this; escalate with the specific unreachable endpoint and timing so a human can engage the partner/network team.
- `restart_interface` executes and reachability is restored, but message processing verification still fails for reasons unrelated to connectivity (pivot signal — likely a different root-cause skill applies; if none fits cleanly, escalate).
- Reachability is intermittent/flapping in a way that doesn't cleanly resolve or fail across the investigation-cycle ceiling — genuinely ambiguous connectivity states should not be forced into a confident RESOLVE.
