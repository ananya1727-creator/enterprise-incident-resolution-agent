"""
Generates the simulated enterprise integration environment for the MVP.
Deterministic (seeded) so the dataset is reproducible and internally consistent:
incidents/logs/resolutions all reference real interface_ids from the interface catalog.
"""
import json
import random
from pathlib import Path
from datetime import datetime, timedelta

random.seed(20260927)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Reference vocab
# ---------------------------------------------------------------------------

MESSAGE_TYPES = ["REST", "SOAP", "SFTP", "AS2", "JDBC", "IDoc"]

SOURCE_SYSTEMS = [
    "SAP ERP ECC6", "SAP S/4HANA", "SAP SuccessFactors EC", "SAP CPI", "SAP PO 7.5",
]

TARGET_SYSTEMS = [
    "Salesforce Sales Cloud", "Workday HCM", "SuccessFactors Employee Central",
    "Acme Freight EDI Gateway", "Global Logistics Partner Portal", "Northwind Distributors AS2 Endpoint",
    "Oracle Finance DB", "SQL Server Reporting DB", "PostgreSQL Analytics DB",
    "IRS FIRE System", "State Unemployment Tax Portal", "Customs Declaration System (ACE)",
    "Concur Expense", "Ariba Network", "ADP Payroll", "Coupa Procurement",
    "Bank of America Payment Gateway", "Wells Fargo Lockbox SFTP", "DHL Shipment API",
    "Partner Co-op EDI Hub",
]

OWNING_TEAMS = [
    "Integration COE", "SAP Basis Team", "HR Systems Team", "Finance Systems Team",
    "Order Management Team", "Partner Connectivity Team", "Data Platform Team",
]

FRAGILITY_FLAGS_POOL = [
    "cert_expires_soon", "legacy_adapter", "known_flaky_partner", "high_volume",
    "manual_failover_only", "deprecated_auth_scheme", "single_node_no_ha",
]

ROOT_CAUSE_CATEGORIES = [
    "auth_failure", "cert_expiry", "connectivity_failure", "timeout",
    "mapping_error", "malformed_payload", "endpoint_unavailable",
    "sftp_credential_failure", "message_processing_failure", "duplicate_message",
    "database_connection_failure",
]

SLA_TIERS = ["P1", "P2", "P3", "P4"]

AUTH_TYPES = ["basic", "oauth2", "certificate", "api_key", "sftp_key", "sftp_password"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts(days_ago, hour=None, minute=None):
    base = datetime(2026, 9, 27, 9, 0, 0) - timedelta(days=days_ago)
    if hour is not None:
        base = base.replace(hour=hour)
    if minute is not None:
        base = base.replace(minute=minute)
    return base.isoformat() + "Z"

def make_interface_name(idx, source, target, mtype):
    return f"{source.split()[0]}-to-{target.split()[0]}-{mtype}-{idx:03d}"

# ---------------------------------------------------------------------------
# 1. Interfaces (~50)
# ---------------------------------------------------------------------------

interfaces = []
INTERFACE_COUNT = 50

for i in range(1, INTERFACE_COUNT + 1):
    iface_id = f"IFACE-{i:03d}"
    source = random.choice(SOURCE_SYSTEMS)
    target = random.choice(TARGET_SYSTEMS)
    mtype = random.choice(MESSAGE_TYPES)

    # Government/partner systems skew toward AS2/SFTP; DBs skew JDBC; HR systems skew REST/SOAP
    if "DB" in target or "Database" in target:
        mtype = random.choice(["JDBC", "REST"])
    elif "IRS" in target or "Customs" in target or "Tax Portal" in target:
        mtype = random.choice(["AS2", "SFTP"])
    elif target in ("Workday HCM", "SuccessFactors Employee Central", "Salesforce Sales Cloud"):
        mtype = random.choice(["REST", "SOAP", "IDoc"])
    elif "EDI" in target or "Partner" in target or "Co-op" in target:
        mtype = random.choice(["AS2", "SFTP"])

    sla_tier = random.choices(SLA_TIERS, weights=[15, 35, 35, 15])[0]
    decommissioned = random.random() < 0.06  # a few red-herring decommissioned interfaces
    fragility = random.sample(FRAGILITY_FLAGS_POOL, k=random.choice([0, 0, 1, 1, 2]))

    interfaces.append({
        "interface_id": iface_id,
        "name": make_interface_name(i, source, target, mtype),
        "source_system": source,
        "target_system": target,
        "message_type": mtype,
        "sla_tier": sla_tier,
        "owning_team": random.choice(OWNING_TEAMS),
        "known_fragility_flags": fragility,
        "decommissioned": decommissioned,
    })

(DATA / "interfaces.json").write_text(json.dumps(interfaces, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# 2. Interface configuration records (~30, subset of interfaces)
# ---------------------------------------------------------------------------

configured_interfaces = random.sample(interfaces, k=30)
interface_configs = []

for iface in configured_interfaces:
    mtype = iface["message_type"]
    if mtype in ("SFTP",):
        auth_type = random.choice(["sftp_key", "sftp_password"])
    elif mtype == "AS2":
        auth_type = "certificate"
    elif mtype == "JDBC":
        auth_type = "basic"
    else:
        auth_type = random.choice(["oauth2", "certificate", "api_key", "basic"])

    timeout_ms = random.choice([5000, 10000, 15000, 30000, 60000])
    interface_configs.append({
        "config_id": f"CFG-{iface['interface_id'][-3:]}",
        "interface_id": iface["interface_id"],
        "auth_type": auth_type,
        "endpoint_url": f"https://sim-endpoint.internal/{iface['interface_id'].lower()}"
                        if mtype in ("REST", "SOAP") else f"sim://{mtype.lower()}/{iface['interface_id'].lower()}",
        "timeout_ms": timeout_ms,
        "retry_policy": {
            "max_retries": random.choice([0, 1, 3, 5]),
            "backoff_seconds": random.choice([5, 15, 30, 60]),
        },
        "adapter_version": f"{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}",
        "cert_bound": auth_type in ("certificate",),
    })

(DATA / "interface_configs.json").write_text(json.dumps(interface_configs, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# 3. Runbooks (~20)
# ---------------------------------------------------------------------------

runbook_templates = {
    "auth_failure": ("Authentication Failure Triage", [
        "Check inspect_credentials for expiry/lock/revocation status.",
        "Cross-reference recent credential_rotation changes.",
        "If credential invalid, engage identity/owning team; do not attempt local fix.",
        "If credential valid, attempt refresh_connection and verify with a follow-up message.",
    ]),
    "cert_expiry": ("Certificate Expiry Response", [
        "Run inspect_certificate_status to confirm expiry vs. other validation errors.",
        "If genuinely expired, request approval for rotate_certificate.",
        "After rotation, verify handshake success with a live message, not just tool success.",
        "If partner trust store not updated post-rotation, escalate for partner coordination.",
    ]),
    "connectivity_failure": ("Connectivity Loss Triage", [
        "Run test_connectivity against both source and target.",
        "Distinguish local channel-down from partner-side outage before acting.",
        "For local channel issues, restart_interface; for partner outages, escalate.",
        "Re-verify interface health after any action.",
    ]),
    "timeout": ("Timeout and Latency Investigation", [
        "Check latency_ms from test_connectivity separately from application timeouts.",
        "Correlate with recent config/deployment changes.",
        "Only propose timeout increases when latency is consistently borderline, not for hard failures.",
        "Escalate suspected downstream degradation rather than masking with a longer timeout.",
    ]),
    "mapping_error": ("Mapping and Transformation Failure Response", [
        "Use compare_with_previous_successful_run to isolate the structural diff.",
        "Correlate with recent mapping/schema deployments.",
        "Do not retry a message against an unchanged, confirmed-defective mapping.",
        "Escalate to the mapping-owning team with the specific diff evidence.",
    ]),
    "malformed_payload": ("Malformed Payload Handling", [
        "Confirm payload structure against the expected schema via comparison tooling.",
        "Determine whether the issue is source-side data quality or a mapping defect.",
        "Escalate source-side data issues to the originating system's owning team.",
    ]),
    "endpoint_unavailable": ("Endpoint Unavailability Response", [
        "Confirm unavailability with test_connectivity rather than assuming from log silence alone.",
        "Check for scheduled maintenance windows via recent changes.",
        "Escalate persistent unavailability rather than repeatedly retrying.",
    ]),
    "sftp_credential_failure": ("SFTP Credential Failure Triage", [
        "Check inspect_credentials for SFTP key/password specific status.",
        "Confirm whether a recent key rotation occurred without corresponding update on this end.",
        "SFTP credential resets are outside the agent's remediation surface; escalate.",
    ]),
    "message_processing_failure": ("Stuck Message Processing Response", [
        "Determine if this is a single stuck message or a broader channel-level backlog.",
        "For single messages with no duplication evidence, retry_message.",
        "For backlog/congestion, consider restart_interface at the channel level instead.",
        "Re-scan the broader message set post-action, not just the originally flagged message.",
    ]),
    "duplicate_message": ("Duplicate Message Investigation", [
        "Check whether a recent restart or retry in this same incident could explain duplication.",
        "Do not retry further once duplication is confirmed.",
        "Escalate for downstream de-duplication/data correction.",
    ]),
    "database_connection_failure": ("Database Connectivity Failure Response", [
        "Run test_connectivity against the database endpoint.",
        "Check inspect_credentials for the database service account.",
        "Check for recent network/firewall changes affecting DB connectivity.",
        "Escalate persistent DB-side outages to the Data Platform Team.",
    ]),
}

runbooks = []
rb_id = 1
for category, (title, steps) in runbook_templates.items():
    runbooks.append({
        "runbook_id": f"RB-{rb_id:03d}",
        "title": title,
        "root_cause_category": category,
        "steps": steps,
        "last_reviewed_at": ts(random.randint(10, 400)),
    })
    rb_id += 1

# add a few more runbooks: interface-type-specific and a couple deliberately stale ones
extra_runbooks = [
    ("SFTP Key Rotation Standard Procedure", "sftp_credential_failure", [
        "Confirm rotation schedule with partner before assuming failure is unplanned.",
        "Validate new key fingerprint against configuration record.",
    ]),
    ("AS2 Partner Onboarding Connectivity Checklist", "connectivity_failure", [
        "Verify MDN acknowledgment settings match partner agreement.",
        "Confirm firewall allowlisting for new partner IP ranges.",
    ]),
    ("IDoc Status 51 Triage (Legacy)", "mapping_error", [
        "Check IDoc status record for the specific error segment.",
        "Cross-reference against ECC application log.",
    ]),
    ("JDBC Pool Exhaustion Response", "database_connection_failure", [
        "Check active connection count against configured pool size.",
        "Restart the adapter connection pool only after confirming exhaustion, not on first symptom.",
    ]),
    ("General P1 Escalation Fast-Path (Draft, Unreviewed)", "auth_failure", [
        "Draft procedure — not yet validated by CoE.",
        "Use with caution; prefer specific-category runbooks where available.",
    ]),
    ("REST/SOAP Endpoint Unavailability Checklist", "endpoint_unavailable", [
        "Confirm HTTP status pattern (503 vs 404) before assuming outage type.",
        "Check for a scheduled maintenance window on the target system.",
    ]),
    ("Malformed Payload Source-Data Triage", "malformed_payload", [
        "Determine whether the malformed field originates upstream or in transit.",
        "Escalate persistent source-data-quality issues to the originating system owner.",
    ]),
    ("Duplicate IDoc Detection Procedure (Legacy)", "duplicate_message", [
        "Check IDoc control record for duplicate status flag.",
        "Cross-reference with any recent channel restart in the same window.",
    ]),
    ("Partner AS2 MDN Timeout Response", "timeout", [
        "Confirm whether MDN acknowledgment itself is timing out vs. the underlying transfer.",
        "Engage partner connectivity team if MDN delay is consistently on their side.",
    ]),
]

for title, category, steps in extra_runbooks:
    stale = "Unreviewed" in title
    runbooks.append({
        "runbook_id": f"RB-{rb_id:03d}",
        "title": title,
        "root_cause_category": category,
        "steps": steps,
        "last_reviewed_at": None if stale else ts(random.randint(5, 200)),
    })
    rb_id += 1

(DATA / "runbooks.json").write_text(json.dumps(runbooks, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# 4. Historical incidents (~100) + linked log events (~200) + resolution records (~20)
# ---------------------------------------------------------------------------

REPORTERS = [
    "monitoring-bot@integration.internal", "jsmith@enterprise.local", "priya.k@enterprise.local",
    "helpdesk-tier1@enterprise.local", "carlos.m@enterprise.local", "auto-alert@sap-po.internal",
]

ERROR_CODE_BY_CATEGORY = {
    "auth_failure": ["401", "403", "AUTH_REJECTED", "TOKEN_EXPIRED"],
    "cert_expiry": ["SSL_HANDSHAKE_FAILED", "CERT_EXPIRED", "CERT_UNTRUSTED"],
    "connectivity_failure": ["CONN_REFUSED", "HOST_UNREACHABLE", "DNS_RESOLUTION_FAILED"],
    "timeout": ["READ_TIMEOUT", "CONNECT_TIMEOUT", "GATEWAY_TIMEOUT_504"],
    "mapping_error": ["MAPPING_EXCEPTION", "SCHEMA_VALIDATION_FAILED", "XSLT_ERROR"],
    "malformed_payload": ["INVALID_XML", "MISSING_MANDATORY_FIELD", "PAYLOAD_TOO_LARGE"],
    "endpoint_unavailable": ["SERVICE_UNAVAILABLE_503", "ENDPOINT_NOT_FOUND_404"],
    "sftp_credential_failure": ["SFTP_AUTH_FAILED", "SFTP_KEY_REJECTED"],
    "message_processing_failure": ["MESSAGE_STUCK", "PROCESSING_TIMEOUT", "QUEUE_BLOCKED"],
    "duplicate_message": ["DUPLICATE_MESSAGE_ID", "IDEMPOTENCY_CONFLICT"],
    "database_connection_failure": ["DB_CONN_REFUSED", "POOL_EXHAUSTED", "ORA-12170", "LOGIN_TIMEOUT"],
}

SYMPTOM_TEMPLATES = {
    "auth_failure": "Messages failing with authentication rejection since {when}.",
    "cert_expiry": "TLS handshake failures reported on interface, partner reporting rejected connections.",
    "connectivity_failure": "No messages processed since {when}; monitoring shows total silence.",
    "timeout": "Increasing number of timeout errors, latency appears elevated.",
    "mapping_error": "Subset of messages failing with mapping/schema validation errors.",
    "malformed_payload": "Source system sending payloads that fail structural validation.",
    "endpoint_unavailable": "Target endpoint returning unavailable/not-found errors intermittently.",
    "sftp_credential_failure": "SFTP file transfer failing authentication against partner server.",
    "message_processing_failure": "Messages stuck in processing state, not completing.",
    "duplicate_message": "Downstream system reporting duplicate records from this interface.",
    "database_connection_failure": "Database-bound interface failing to establish connection.",
}

BUSINESS_CONTEXTS = [
    "affects month-end close", "impacts order-to-cash flow", "blocks payroll run",
    "affects partner SLA commitment", "impacts customer shipment notifications",
    "regulatory filing deadline dependent", None, None, None,
]

resolvable_interfaces = [i for i in interfaces if not i["decommissioned"]]

incidents = []
log_events = []
resolution_records = []

INCIDENT_COUNT = 100
LOG_COUNT_TARGET = 200
RESOLUTION_COUNT = 20

log_id_counter = 1

# Outcome distribution: resolved-with-known-precedent, resolved-novel, ambiguous, no-known-resolution
OUTCOME_BUCKETS = (
    ["resolved_known_precedent"] * 30
    + ["resolved_novel"] * 30
    + ["ambiguous_evidence"] * 20
    + ["no_known_resolution"] * 20
)
random.shuffle(OUTCOME_BUCKETS)

for i in range(1, INCIDENT_COUNT + 1):
    incident_id = f"INC-{i:04d}"
    iface = random.choice(resolvable_interfaces if random.random() > 0.04 else interfaces)  # rare decommissioned-ref incident
    category = random.choice(ROOT_CAUSE_CATEGORIES)
    bucket = OUTCOME_BUCKETS[i - 1]
    days_ago = random.randint(1, 540)
    reported_severity = random.choice(["P1", "P2", "P3", "P4"])

    symptom = SYMPTOM_TEMPLATES[category].format(when=ts(days_ago))
    business_context = random.choice(BUSINESS_CONTEXTS)

    # generate 1-4 log events per incident, tagged with plausible error codes
    n_logs = random.choice([1, 2, 2, 3, 4])
    incident_log_ids = []
    for _ in range(n_logs):
        if log_id_counter > LOG_COUNT_TARGET:
            break
        error_code = random.choice(ERROR_CODE_BY_CATEGORY[category])
        level = "ERROR" if random.random() > 0.15 else "WARN"
        log_entry = {
            "log_id": f"LOG-{log_id_counter:04d}",
            "interface_id": iface["interface_id"],
            "incident_id": incident_id,
            "timestamp": ts(days_ago, hour=random.randint(0, 23), minute=random.randint(0, 59)),
            "level": level,
            "message_id": f"MSG-{incident_id[-4:]}-{random.randint(1,999):03d}",
            "error_code": error_code,
            "raw_text": f"[{level}] {iface['interface_id']} {error_code}: simulated failure detail for {category.replace('_',' ')} scenario.",
        }
        log_events.append(log_entry)
        incident_log_ids.append(log_entry["log_id"])
        log_id_counter += 1

    # deliberately noisy/red-herring log for some ambiguous incidents: an unrelated WARN
    has_red_herring = bucket == "ambiguous_evidence" and random.random() > 0.4
    if has_red_herring and log_id_counter <= LOG_COUNT_TARGET:
        distractor_category = random.choice([c for c in ROOT_CAUSE_CATEGORIES if c != category])
        distractor_code = random.choice(ERROR_CODE_BY_CATEGORY[distractor_category])
        log_entry = {
            "log_id": f"LOG-{log_id_counter:04d}",
            "interface_id": iface["interface_id"],
            "incident_id": incident_id,
            "timestamp": ts(days_ago, hour=random.randint(0, 23), minute=random.randint(0, 59)),
            "level": "WARN",
            "message_id": f"MSG-{incident_id[-4:]}-{random.randint(1,999):03d}",
            "error_code": distractor_code,
            "raw_text": f"[WARN] {iface['interface_id']} {distractor_code}: unrelated transient warning (red herring).",
        }
        log_events.append(log_entry)
        incident_log_ids.append(log_entry["log_id"])
        log_id_counter += 1

    incident = {
        "incident_id": incident_id,
        "interface_id": iface["interface_id"],
        "reported_at": ts(days_ago),
        "reporter": random.choice(REPORTERS),
        "symptom_description": symptom,
        "reported_severity": reported_severity,
        "business_context": business_context,
        "true_root_cause_category": category,  # ground truth, for eval harness use only
        "related_log_ids": incident_log_ids,
        "outcome_bucket": bucket,
    }

    if bucket == "resolved_known_precedent":
        incident["status"] = "Resolved"
        incident["resolution_summary"] = f"Resolved via standard {category.replace('_',' ')} remediation; matched prior precedent."
        incident["has_known_resolution"] = True
    elif bucket == "resolved_novel":
        incident["status"] = "Resolved"
        incident["resolution_summary"] = f"Resolved after investigation; root cause was {category.replace('_',' ')}, no direct precedent existed at the time."
        incident["has_known_resolution"] = True
    elif bucket == "ambiguous_evidence":
        incident["status"] = random.choice(["Resolved", "Escalated"])
        incident["resolution_summary"] = "Evidence was contradictory/ambiguous; resolution required additional human judgment." if incident["status"] == "Escalated" else f"Eventually attributed to {category.replace('_',' ')} despite initially conflicting signals."
        incident["has_known_resolution"] = incident["status"] == "Resolved"
    else:  # no_known_resolution
        incident["status"] = "Escalated"
        incident["resolution_summary"] = "Root cause not conclusively determined; escalated to owning team, no resolution recorded."
        incident["has_known_resolution"] = False

    incidents.append(incident)

(DATA / "incidents.json").write_text(json.dumps(incidents, indent=2), encoding="utf-8")

# top up log_events if under target (some incidents may have hit the cap early)
while log_id_counter <= LOG_COUNT_TARGET:
    iface = random.choice(interfaces)
    category = random.choice(ROOT_CAUSE_CATEGORIES)
    error_code = random.choice(ERROR_CODE_BY_CATEGORY[category])
    log_events.append({
        "log_id": f"LOG-{log_id_counter:04d}",
        "interface_id": iface["interface_id"],
        "incident_id": None,
        "timestamp": ts(random.randint(1, 540), hour=random.randint(0,23), minute=random.randint(0,59)),
        "level": random.choice(["INFO", "WARN", "ERROR"]),
        "message_id": f"MSG-BG-{log_id_counter:04d}",
        "error_code": error_code if random.random() > 0.3 else None,
        "raw_text": f"Background {category.replace('_',' ')} related log entry, not tied to a raised incident.",
    })
    log_id_counter += 1

(DATA / "log_events.json").write_text(json.dumps(log_events, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# 5. Historical resolution records (~20) — the ResolutionRecord / case-memory store
# ---------------------------------------------------------------------------

REMEDIATION_TOOL_BY_CATEGORY = {
    "auth_failure": "refresh_connection",
    "cert_expiry": "rotate_certificate",
    "connectivity_failure": "restart_interface",
    "timeout": "update_adapter_timeout",
    "mapping_error": None,  # no autonomous tool; escalation-only pattern
    "malformed_payload": None,
    "endpoint_unavailable": "restart_interface",
    "sftp_credential_failure": None,
    "message_processing_failure": "retry_message",
    "duplicate_message": None,
    "database_connection_failure": "restart_interface",
}

resolved_candidates = [inc for inc in incidents if inc["has_known_resolution"]]
resolution_sample = random.sample(resolved_candidates, k=min(RESOLUTION_COUNT, len(resolved_candidates)))

for idx, inc in enumerate(resolution_sample, start=1):
    category = inc["true_root_cause_category"]
    iface = next(x for x in interfaces if x["interface_id"] == inc["interface_id"])
    tool = REMEDIATION_TOOL_BY_CATEGORY[category]
    outcome = "Resolved" if tool else "Escalated"
    feedback = random.choices(["Correct", "Correct", "Correct", "Incorrect", "Unreviewed"], weights=[4,4,4,1,2])[0]

    resolution_records.append({
        "record_id": f"RES-{idx:03d}",
        "symptom_signature": {
            "interface_type": iface["message_type"],
            "error_category": category,
            "log_pattern_tags": ERROR_CODE_BY_CATEGORY[category][:2],
            "affected_component": random.choice(["adapter", "channel", "mapping_program", "receiver_channel", "sender_channel"]),
        },
        "root_cause": category,
        "root_cause_statement": f"{category.replace('_',' ').capitalize()} identified on {iface['interface_id']} ({iface['name']}).",
        "remediation_tool": tool,
        "remediation_params_template": ({"interface_id": "{{interface_id}}"} if tool else None),
        "verification_signature": {
            "check": "verify_interface_health" if tool in ("restart_interface", "refresh_connection", None) else "verify_message_processing",
            "expected_state": "healthy",
        },
        "outcome": outcome,
        "tool_call_count": random.randint(2, 7),
        "human_feedback": feedback,
        "source_incident_id": inc["incident_id"],
        "created_at": ts(random.randint(1, 500)),
    })

(DATA / "resolution_records.json").write_text(json.dumps(resolution_records, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"interfaces: {len(interfaces)}")
print(f"interface_configs: {len(interface_configs)}")
print(f"runbooks: {len(runbooks)}")
print(f"incidents: {len(incidents)}")
print(f"log_events: {len(log_events)}")
print(f"resolution_records: {len(resolution_records)}")
