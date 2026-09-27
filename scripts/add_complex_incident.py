"""
One-off script: adds a single deliberately complex incident to the fixture
dataset (data/interfaces.json, data/incidents.json, data/log_events.json) so
it shows up as a launchable row in the dashboard's Incident Queue, backed by
the same SimulatedEnvironment oracle as every other bulk fixture incident --
not a special-cased scenario.

"Complex" here means: high business criticality, a genuine root cause
(connectivity_failure) PLUS a red-herring log signal from an unrelated
category (mapping_error) present from the very first OBSERVE-stage log
scan, and an outcome bucket ("ambiguous_evidence") that makes the first
remediation attempt's verification fail before the agent can resolve it --
so watching it live actually shows multiple competing hypotheses, an
approval-gated action, a failed verification, and a real re-decision.

Run once:
    python scripts/add_complex_incident.py
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

    interface_id = "IFACE-101"
    incident_id = "INC-0101"

    if any(i["interface_id"] == interface_id for i in interfaces):
        print(f"{interface_id} already exists -- nothing to do.")
        return

    interfaces.append({
        "interface_id": interface_id,
        "name": "SAP-CPI-to-GlobalLogisticsPartner-REST-101",
        "source_system": "SAP CPI",
        "target_system": "Global Logistics Partner Portal",
        "message_type": "REST",
        "sla_tier": "P1",
        "owning_team": "Partner Connectivity Team",
        "known_fragility_flags": ["known_flaky_partner", "legacy_adapter"],
        "decommissioned": False,
    })

    incidents.append({
        "incident_id": incident_id,
        "interface_id": interface_id,
        "reported_at": "2026-09-27T07:00:00Z",
        "reporter": "monitoring-bot@integration.internal",
        "symptom_description": (
            "Multiple downstream order confirmations failing intermittently since ~07:00. Partner endpoint "
            "returns a mix of connection timeouts and occasional 200-OK responses with incomplete payload data. "
            "On-call is unsure whether this is a network/partner-side outage or a recent mapping deployment issue -- "
            "both a connectivity error and a schema validation warning appear in the logs around the same time."
        ),
        "reported_severity": "P1",
        "business_context": "affects live customer order fulfillment",
        "true_root_cause_category": "connectivity_failure",
        "related_log_ids": ["LOG-0201", "LOG-0202", "LOG-0203"],
        "outcome_bucket": "ambiguous_evidence",
        "status": "Escalated",
        "resolution_summary": "Complex, deliberately ambiguous case for live demonstration -- see docs/DASHBOARD.md.",
        "has_known_resolution": False,
    })

    logs.extend([
        {
            "log_id": "LOG-0201", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T07:01:00Z", "level": "ERROR",
            "message_id": "MSG-0101-001", "error_code": "CONN_REFUSED",
            "raw_text": "[ERROR] IFACE-101 CONN_REFUSED: connection to partner endpoint refused intermittently",
        },
        {
            "log_id": "LOG-0202", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T07:03:00Z", "level": "WARN",
            "message_id": "MSG-0101-002", "error_code": "SCHEMA_VALIDATION_FAILED",
            "raw_text": "[WARN] IFACE-101 SCHEMA_VALIDATION_FAILED: response payload missing expected field "
                        "'OrderConfirmationId' (possibly unrelated -- red herring under investigation)",
        },
        {
            "log_id": "LOG-0203", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T07:05:00Z", "level": "ERROR",
            "message_id": "MSG-0101-003", "error_code": "CONN_REFUSED",
            "raw_text": "[ERROR] IFACE-101 CONN_REFUSED: second connection attempt also refused",
        },
    ])

    save("interfaces", interfaces)
    save("incidents", incidents)
    save("log_events", logs)
    print(f"Added {interface_id} / {incident_id} with 3 log entries.")


if __name__ == "__main__":
    main()
