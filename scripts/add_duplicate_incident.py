"""
One-off script: adds a third complex incident to the fixture dataset --
this one's true root cause (duplicate_message) has NO autonomous or
approvable remediation tool at all (docs/TOOL_SPEC.md), so it demonstrates
the "confirmed root cause, but must escalate because no safe remediation
exists" path -- distinct from INC-0101 (Medium-risk approval) and INC-0102
(autonomous fail -> pivot -> High-risk approval).

Deliberately includes a red herring: the first log entry looks like a
plain stuck-message processing issue (message_processing_failure, which DOES
have a remediation -- retry_message), before a second log entry reveals the
message was already processed once before a prior manual restart, which is
the specific signature the Message Processing/Duplicate skills say must
override any retry, since retrying an already-duplicated message risks
compounding the duplication.

Run once:
    python scripts/add_duplicate_incident.py
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

    interface_id = "IFACE-103"
    incident_id = "INC-0103"

    if any(i["interface_id"] == interface_id for i in interfaces):
        print(f"{interface_id} already exists -- nothing to do.")
        return

    interfaces.append({
        "interface_id": interface_id,
        "name": "SAP-ERP-to-Ariba-IDoc-103",
        "source_system": "SAP ERP ECC6",
        "target_system": "Ariba Network",
        "message_type": "IDoc",
        "sla_tier": "P2",
        "owning_team": "Order Management Team",
        "known_fragility_flags": [],
        "decommissioned": False,
    })

    incidents.append({
        "incident_id": incident_id,
        "interface_id": interface_id,
        "reported_at": "2026-09-27T09:00:00Z",
        "reporter": "carlos.m@enterprise.local",
        "symptom_description": (
            "Purchase order messages appear stuck in processing, but the downstream Ariba system is also "
            "reporting duplicate purchase order records for at least one of them. On-call flagged both symptoms "
            "together and isn't sure whether this is a simple stuck-message issue or something that needs more care."
        ),
        "reported_severity": "P2",
        "business_context": "affects vendor purchase order confirmations",
        "true_root_cause_category": "duplicate_message",
        "related_log_ids": ["LOG-0207", "LOG-0208", "LOG-0209"],
        # "ambiguous_evidence", not "no_known_resolution": the latter also
        # randomizes test_connectivity's reachability wholesale (by design,
        # for genuinely unresolvable incidents), which drowned out the
        # duplicate-message signal entirely when first tried here. This
        # bucket still injects a red herring (verified: it produced a
        # cert_expiry false lead that even reached an approved
        # rotate_certificate before failing verification) without erasing
        # the real signal.
        "outcome_bucket": "ambiguous_evidence",
        "status": "Escalated",
        "resolution_summary": "Complex, no-safe-remediation case for live demonstration -- see docs/DASHBOARD.md.",
        "has_known_resolution": False,
    })

    logs.extend([
        {
            "log_id": "LOG-0207", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T08:50:00Z", "level": "INFO",
            "message_id": "MSG-0103-001", "error_code": "MESSAGE_STUCK",
            "raw_text": "[INFO] IFACE-103 MSG-0103-001 appears stuck in processing queue",
        },
        {
            "log_id": "LOG-0208", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T08:52:00Z", "level": "WARN",
            "message_id": "MSG-0103-001", "error_code": "DUPLICATE_MESSAGE_ID",
            "raw_text": "[WARN] IFACE-103 DUPLICATE_MESSAGE_ID: MSG-0103-001 already processed once before this restart",
        },
        {
            "log_id": "LOG-0209", "interface_id": interface_id, "incident_id": incident_id,
            "timestamp": "2026-09-27T08:44:00Z", "level": "INFO",
            "message_id": "MSG-0103-001", "error_code": "CONFIG_CHANGE",
            "raw_text": "[INFO] IFACE-103 channel was restarted manually by on-call prior to this incident being raised",
        },
    ])

    save("interfaces", interfaces)
    save("incidents", incidents)
    save("log_events", logs)
    print(f"Added {interface_id} / {incident_id} with 3 log entries.")


if __name__ == "__main__":
    main()
