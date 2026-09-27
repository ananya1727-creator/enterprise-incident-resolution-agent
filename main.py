"""
CLI entrypoint for the MVP Incident Resolution Agent.

Usage:
    python main.py INC-0007
    python main.py INC-0007 --quiet
    python main.py --sample 10          # run N random incidents and summarize outcomes
"""
import argparse
import random
import sys

from agent.data_store import DataStore
from agent.orchestrator import Orchestrator


def run_one(incident_id: str, store: DataStore, verbose: bool = True, use_mcp: bool = False):
    env = None
    client = None
    if use_mcp:
        from agent.mcp_tool_client import MCPToolClient
        client = MCPToolClient(incident_id)
        env = client
    orch = Orchestrator(incident_id, store=store, verbose=verbose, env=env)
    try:
        state = orch.run()
    finally:
        if client:
            client.close()
    return state


def main():
    parser = argparse.ArgumentParser(description="Enterprise Integration Incident Resolution Agent")
    parser.add_argument("incident_id", nargs="?", help="Incident ID, e.g. INC-0007")
    parser.add_argument("--quiet", action="store_true", help="Suppress step-by-step trace output")
    parser.add_argument("--sample", type=int, default=0, help="Run N random incidents and print a summary table")
    parser.add_argument("--mcp", action="store_true",
                         help="Run the agent against the MCP server (mcp_server/server.py) instead of "
                              "importing the simulated tool layer directly")
    parser.add_argument("--observe", action="store_true",
                         help="Print the full observability timeline (incident, state, skill, tools, "
                              "hypotheses, confidence changes, decisions, actions, verification, "
                              "escalation, resolution) to the console after the run")
    args = parser.parse_args()

    store = DataStore()

    if args.sample:
        ids = [i["incident_id"] for i in store.incidents]
        chosen = random.sample(ids, k=min(args.sample, len(ids)))
        rows = []
        for iid in chosen:
            state = run_one(iid, store, verbose=not args.quiet, use_mcp=args.mcp)
            outcome = state.stage.value
            root = None
            if state.resolution:
                root = state.resolution.root_cause
            tool_calls = len(state.tool_call_log)
            rows.append((iid, outcome, root, tool_calls))
        print("\n=== Sample Summary ===")
        print(f"{'incident':10} {'outcome':10} {'root_cause':28} {'tool_calls'}")
        for r in rows:
            print(f"{r[0]:10} {r[1]:10} {str(r[2]):28} {r[3]}")
        return

    if not args.incident_id:
        parser.print_help()
        sys.exit(1)

    state = run_one(args.incident_id, store, verbose=not args.quiet, use_mcp=args.mcp)
    print("\n=== Final State ===")
    print(f"Stage: {state.stage.value}")
    if state.resolution:
        print(f"Root cause: {state.resolution.root_cause}")
        print(f"Outcome: {state.resolution.outcome}")
    print(f"Investigation cycles: {state.investigation_cycle_count}")
    print(f"Remediation attempts: {state.remediation_attempt_count}")
    print(f"Decisions logged: {len(state.decision_log)}")
    print(f"Tool calls logged: {len(state.tool_call_log)}")

    if args.observe:
        from agent.observability import render_timeline
        print(render_timeline(state))


if __name__ == "__main__":
    main()
