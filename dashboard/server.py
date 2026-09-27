"""
Dashboard backend. Flask app exposing a small JSON API over the real agent
registry (dashboard/registry.py) and real generated reports
(dashboard/data_sources.py). This file contains NO incident-resolution
logic -- every route either reads existing state/reports or forwards a
request into the registry, which itself only launches/reads real
agent.orchestrator.Orchestrator instances.

Run:
    python -m dashboard.server
Then open http://127.0.0.1:5050/
"""
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import data_sources as ds
from .registry import DashboardRegistry
from .serializer import build_investigation_view, build_queue_row, sla_status

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = Flask(__name__, static_folder=None)
registry = DashboardRegistry()

TOOL_IMPACT_SUMMARY = {
    "retry_message": "Resends one specific failed message. Scoped to a single message; reversible.",
    "restart_interface": "Restarts the messaging channel/adapter. Briefly disrupts all in-flight traffic on the channel.",
    "refresh_connection": "Drops and re-establishes the adapter's connection/session. Brief disruption, no persistent change.",
    "update_adapter_timeout": "Persistently changes the adapter's timeout configuration. Affects ALL future traffic on this interface.",
    "rotate_certificate": "Rotates the interface's TLS/client certificate. Security-sensitive; affects trust chain for all traffic.",
    "reprocess_message_batch": "Bulk delete/reprocess of a message batch. Irreversible at scale.",
    "update_routing_rule": "Changes where future messages on this interface are delivered.",
}


# ------------------------------------------------------------------
# Static frontend
# ------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# ------------------------------------------------------------------
# 1. Incident Queue
# ------------------------------------------------------------------

@app.route("/api/queue")
def api_queue():
    registry.refresh_historical_runs()
    interfaces = ds.load_interfaces_by_id()
    fixtures_by_id = {i["incident_id"]: i for i in ds.load_fixture_incidents()}

    rows = []
    for incident_id, tracked in registry.tracked.items():
        raw = registry.get_raw_state(incident_id)
        fixture = fixtures_by_id.get(incident_id)
        interface = interfaces.get(raw["interface_id"]) if raw else None
        rows.append(build_queue_row(incident_id, tracked.source, raw, fixture, interface))

    tracked_ids = set(registry.tracked.keys())
    queued_shown = 0
    MAX_QUEUED = 40
    for fixture in ds.load_fixture_incidents():
        if fixture["incident_id"] in tracked_ids:
            continue
        if queued_shown >= MAX_QUEUED:
            break
        interface = interfaces.get(fixture["interface_id"])
        rows.append(build_queue_row(fixture["incident_id"], "queued", None, fixture, interface))
        queued_shown += 1

    return jsonify({
        "rows": rows,
        "total_tracked": len(registry.tracked),
        "total_fixture_incidents": len(fixtures_by_id),
        "queued_shown": queued_shown,
    })


@app.route("/api/incidents/<incident_id>/launch", methods=["POST"])
def api_launch(incident_id):
    try:
        registry.launch(incident_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"launched": incident_id})


# ------------------------------------------------------------------
# 2. Incident Investigation
# ------------------------------------------------------------------

@app.route("/api/incidents/<incident_id>")
def api_incident_detail(incident_id):
    raw = registry.get_raw_state(incident_id)
    if not raw:
        return jsonify({"error": "unknown incident"}), 404
    return jsonify(build_investigation_view(raw))


# ------------------------------------------------------------------
# 3. Human Approval Center
# ------------------------------------------------------------------

@app.route("/api/approvals")
def api_approvals():
    pending = registry.list_pending_approvals()
    return jsonify([{
        "incident_id": pa.incident_id,
        "approval_request_id": pa.approval_request_id,
        "proposed_action": pa.tool_name,
        "reason": pa.rationale,
        "risk_level": pa.risk_tier,
        "hypothesis_category": pa.hypothesis_category,
        "hypothesis_confidence": pa.hypothesis_confidence,
        "evidence": pa.supporting_evidence,
        "expected_impact": TOOL_IMPACT_SUMMARY.get(pa.tool_name, "No impact summary available for this tool."),
    } for pa in pending])


@app.route("/api/approvals/<approval_request_id>/decide", methods=["POST"])
def api_decide_approval(approval_request_id):
    body = request.get_json(force=True, silent=True) or {}
    approve = bool(body.get("approve"))
    ok = registry.decide_approval(approval_request_id, approve)
    if not ok:
        return jsonify({"error": "no such pending approval (already decided, or unknown id)"}), 404
    return jsonify({"approval_request_id": approval_request_id, "decision": "approved" if approve else "rejected"})


# ------------------------------------------------------------------
# 4. Analytics
# ------------------------------------------------------------------

@app.route("/api/analytics")
def api_analytics():
    registry.refresh_historical_runs()
    raw_states = registry.all_raw_states()
    terminal = [r for r in raw_states if r["stage"] in ("RESOLVED", "ESCALATED")]
    resolved = [r for r in terminal if r["stage"] == "RESOLVED"]
    escalated = [r for r in terminal if r["stage"] == "ESCALATED"]
    autonomous = [r for r in resolved if len(r.get("approval_requests", [])) == 0]
    needs_human = [r for r in raw_states if r.get("approval_requests") or r["stage"] == "ESCALATED"]

    all_verifications = [v for r in raw_states for v in r.get("verification_results", [])]
    verification_success = (
        sum(1 for v in all_verifications if v["outcome"] == "Passed") / len(all_verifications)
        if all_verifications else None
    )

    diag_step_counts = [
        len([a for a in r.get("diagnostic_actions", [])
             if not a["is_verification"] and not a["rationale"].startswith("OBSERVE-stage")])
        for r in raw_states
    ]
    avg_diag_steps = sum(diag_step_counts) / len(diag_step_counts) if diag_step_counts else None

    resolution_times = [r.get("simulated_elapsed_minutes") for r in terminal if r.get("simulated_elapsed_minutes") is not None]
    avg_resolution_time = sum(resolution_times) / len(resolution_times) if resolution_times else None

    sla_breaches = sum(1 for r in raw_states if sla_status(r) == "Breached")

    real_operational = {
        "label": "REAL operational metrics -- computed live from incidents actually run through this dashboard "
                 "(this process's session plus any prior runs found in runs/*.json). Empty/zero if nothing has "
                 "been launched yet.",
        "total_incidents": len(raw_states),
        "resolved": len(resolved),
        "escalated": len(escalated),
        "awaiting_approval": len(registry.list_pending_approvals()),
        "autonomous_resolution_rate": (len(autonomous) / len(resolved)) if resolved else None,
        "human_intervention_rate": (len(needs_human) / len(raw_states)) if raw_states else None,
        "verification_success_rate": verification_success,
        "avg_diagnostic_steps": avg_diag_steps,
        "avg_resolution_time_minutes": avg_resolution_time,
        "sla_breaches": sla_breaches,
    }

    bench_agg = ds.load_benchmark_aggregate()
    simulated_benchmark = {
        "label": "SIMULATED benchmark metrics -- from the 30-case adversarial+fixture benchmark "
                 "(docs/EVALUATION.md), generated by `python scripts/run_benchmark.py`. Not live operational data.",
        "available": bench_agg is not None,
    }
    if bench_agg:
        simulated_benchmark.update({
            "total_incidents": bench_agg["n_cases"],
            "root_cause_accuracy": bench_agg["1_root_cause_identification_accuracy"],
            "autonomous_resolution_rate": bench_agg["3_successful_autonomous_resolution_rate"],
            "verification_success_rate": bench_agg["4_verification_success_rate"],
            "escalation_appropriateness": bench_agg["5_escalation_appropriateness"],
            "avg_diagnostic_steps": bench_agg["6_mean_diagnostic_steps"],
            "avg_resolution_time_minutes": bench_agg["7_mean_time_to_resolution_minutes"],
            "human_intervention_rate": bench_agg["8_human_intervention_rate"],
            "false_remediation_rate": bench_agg["9_false_remediation_rate"],
            "sla_breach_rate": bench_agg["12_sla_breach_rate"],
        })
    else:
        simulated_benchmark["note"] = "Run `python scripts/build_benchmark.py && python scripts/run_benchmark.py` to generate this."

    return jsonify({"real_operational": real_operational, "simulated_benchmark": simulated_benchmark})


# ------------------------------------------------------------------
# 5. Agent Performance
# ------------------------------------------------------------------

@app.route("/api/performance")
def api_performance():
    registry.refresh_historical_runs()
    raw_states = registry.all_raw_states()

    tool_usage = ds.tool_usage_from_raw_states(raw_states)
    escalation_rate = ds.escalation_rate_from_raw_states(raw_states)
    root_causes = ds.most_common_root_causes(raw_states)

    bench_agg = ds.load_benchmark_aggregate()

    return jsonify({
        "tool_usage": tool_usage,
        "escalation_rate_real": escalation_rate,
        "most_common_root_causes_real": root_causes,
        "benchmark": {
            "available": bench_agg is not None,
            "resolution_accuracy": bench_agg["1_root_cause_identification_accuracy"] if bench_agg else None,
            "false_remediation_rate": bench_agg["9_false_remediation_rate"] if bench_agg else None,
            "tool_selection_accuracy": bench_agg["10_tool_selection_accuracy"] if bench_agg else None,
        } if bench_agg else {"available": False},
    })


def main():
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)


if __name__ == "__main__":
    main()
