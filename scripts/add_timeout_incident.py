"""
One-off script: adds a second deliberately complex, approval-requiring
incident to the fixture dataset -- this one routes through
`update_adapter_timeout` (HIGH risk, always requires approval, no override
possible per config/risk_policy.json), distinct from INC-0101's
`restart_interface` (Medium risk) path, so there's a second, different
Human Approval Center scenario to exercise.

Run once:
    python scripts/add_timeout_incident.py
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load(name):
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def save(name, data):
    (DATA_DIR / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    interfaces = load("interfaces")
    incidents = load("incidents")
    logs = load("log_events")

    interface_id = "IFACE-102"
    incident_id = "INC-0102"

    if any(i["interface_id"] == interface_id for i in interfaces):
        print(f"{interface_id} already exists -- nothing to do.")
        return

    interfaces.append({
        "interface_id": interface_id,
        "name": "SAP-CPI-to-Ariba-REST-102",
        "source_system": "SAP CPI",
        "target_system": "Ariba Network",
        "message_type": "REST",
        "sla_tier": "P1",
        "owning_team": "Order Management Team",
        "known_fragility_flags": ["high_volume"],
        "decommissioned": False,
    })

    incidents.append({
        "incident_id": incident_id,
        "interface_id": interface_id,
        "reported_at": "2026-09-27T08:00:00Z",
        "reporter": "auto-alert@sap-po.internal",
        "symptom_description": (
            "Increasing response latency across order confirmation calls since ~08:00 -- some requests "
            "eventually succeed after long delays, others time out entirely. On-call is unsure if this is a "
            "genuine backend performance issue, since a stray authentication warning also appeared in the logs "
            "around the same time."
        ),
        "reported_severity": "P1",
        "business_context": "impacts real-time order confirmation SLAs",
        "true_root_cause_category": "timeout",
        "related_log_ids": ["LOG-0204", "LOG-0205", "LOG-0206"],
        "outcome_bucket": "ambiguous_evidence",
        "status": "Escalated",
        "resolution_summary": "Complex, approval-requiring case for live demonstration -- see docs/DASHBOARD.md.",
        "has_known_resolution": False,
    })

    logs.extend([
        {
            "log_id": "LOG-0204", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T08:01:00Z", "level": "ERROR",
            "message_id": "MSG-0102-001", "error_code": "READ_TIMEOUT",
            "raw_text": "[ERROR] IFACE-102 READ_TIMEOUT: response exceeded configured timeout",
        },
        {
            "log_id": "LOG-0205", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T08:02:00Z", "level": "WARN",
            "message_id": "MSG-0102-002", "error_code": "401",
            "raw_text": "[WARN] IFACE-102 401: isolated unauthorized response, possibly unrelated -- red herring under investigation",
        },
        {
            "log_id": "LOG-0206", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T08:05:00Z", "level": "ERROR",
            "message_id": "MSG-0102-003", "error_code": "GATEWAY_TIMEOUT_504",
            "raw_text": "[ERROR] IFACE-102 GATEWAY_TIMEOUT_504: gateway timed out waiting on downstream response",
        },
    ])

    save("interfaces", interfaces)
    save("incidents", incidents)
    save("log_events", logs)
    print(f"Added {interface_id} / {incident_id} with 3 log entries.")


if __name__ == "__main__":
    main()
