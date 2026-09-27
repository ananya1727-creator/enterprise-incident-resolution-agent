"""
Simulated implementations of every tool in docs/TOOL_SPEC.md. Each function's
signature (inputs/outputs) is the durable contract; the body is the "adapter"
that would be swapped for a real SAP PO/CPI/MCP-backed adapter later
(docs/TOOL_SPEC.md section 8). No tool here ever returns a real secret value.

Simulation approach: each incident's fixture record carries a hidden
`true_root_cause_category` and `outcome_bucket` (data/incidents.json) that the
agent itself never reads directly -- only these tool functions consult them,
exactly the way a real environment's ground truth is inaccessible to the agent
except through the evidence tools expose. This is the "oracle" the fixture
data drives.
"""
import hashlib
import random
from typing import Optional

from .data_store import DataStore

# Root-cause category -> the one remediation tool that actually addresses it.
# None means "no autonomous or approvable remediation tool exists" -- these
# categories must always resolve via escalation, matching the skills docs.
REMEDIATION_TOOL_BY_CATEGORY = {
    "auth_failure": "refresh_connection",
    "cert_expiry": "rotate_certificate",
    "connectivity_failure": "restart_interface",
    "timeout": "update_adapter_timeout",
    "mapping_error": None,
    "malformed_payload": None,
    "endpoint_unavailable": "restart_interface",
    "sftp_credential_failure": None,
    "message_processing_failure": "retry_message",
    "duplicate_message": None,
    "database_connection_failure": "restart_interface",
}


def _rng_for(incident_id: str, salt: str) -> random.Random:
    seed = int(hashlib.sha256(f"{incident_id}:{salt}".encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed)


class ToolError(Exception):
    pass


class SimulatedEnvironment:
    """
    Holds the ground truth for the incident under investigation and dispatches
    every tool call against it + the static fixture data. This is the Tool
    Execution Layer (architecture 1.2) for the MVP.
    """

    def __init__(self, store: DataStore, incident_id: str):
        self.store = store
        self.incident_id = incident_id
        self.incident = store.get_incident(incident_id)
        if not self.incident:
            raise ToolError(f"incident {incident_id} not found")
        self.interface_id = self.incident["interface_id"]
        self.category = self.incident["true_root_cause_category"]
        self.bucket = self.incident["outcome_bucket"]
        self._remediation_attempts_seen = 0

    # -----------------------------------------------------------------
    # OBSERVATION
    # -----------------------------------------------------------------

    def get_incident(self, incident_id: str) -> dict:
        inc = self.store.get_incident(incident_id)
        if not inc:
            raise ToolError("incident not found")
        return {
            "incident_id": inc["incident_id"],
            "reported_at": inc["reported_at"],
            "reporter": inc["reporter"],
            "interface_id": inc["interface_id"],
            "symptom_description": inc["symptom_description"],
            "reported_severity": inc["reported_severity"],
            "business_context": inc.get("business_context"),
        }

    def get_interface_metadata(self, interface_id: str) -> dict:
        iface = self.store.get_interface(interface_id)
        if not iface:
            raise ToolError("interface not found")
        return dict(iface)

    def search_logs(self, interface_id: str, time_range: dict, filter: Optional[str] = None,
                     max_results: int = 50) -> dict:
        entries = self.store.logs_for_interface(interface_id, self.incident_id)
        if filter:
            entries = [e for e in entries if filter.lower() in (e.get("error_code") or "").lower()
                       or filter.lower() in e["raw_text"].lower()]
        total = len(entries)
        return {"entries": entries[:max_results], "total_matched": total}

    def get_recent_changes(self, interface_id: str, lookback_window: str = "72h") -> dict:
        return {"changes": self.store.recent_changes_for_interface(interface_id)}

    # -----------------------------------------------------------------
    # DIAGNOSIS  (oracle-driven simulation)
    # -----------------------------------------------------------------

    def _noisy(self, true_value: bool) -> bool:
        """Ambiguous-evidence incidents get one signal flipped from ground truth."""
        if self.bucket == "ambiguous_evidence":
            rng = _rng_for(self.incident_id, "noise")
            if rng.random() < 0.5:
                return not true_value
        return true_value

    def test_connectivity(self, interface_id: str, endpoint: str = "both") -> dict:
        rng = _rng_for(self.incident_id, "connectivity")
        results = []
        endpoints = ["source", "target"] if endpoint == "both" else [endpoint]
        target_down = self.category in ("connectivity_failure", "endpoint_unavailable",
                                         "database_connection_failure")
        latency_spike = self.category == "timeout"
        for ep in endpoints:
            reachable = True
            latency = rng.randint(50, 300)
            error = None
            if ep == "target" and target_down:
                reachable = self._noisy(False)
                if not reachable:
                    error = "HOST_UNREACHABLE" if self.category != "endpoint_unavailable" else "SERVICE_UNAVAILABLE_503"
            if latency_spike:
                latency = rng.randint(6000, 15000) if self.bucket != "no_known_resolution" else rng.randint(2000, 20000)
            if self.bucket == "no_known_resolution":
                # genuinely inconclusive: intermittent, doesn't cleanly resolve either way
                reachable = rng.random() > 0.5
            results.append({"endpoint": ep, "reachable": reachable,
                             "latency_ms": latency if reachable else None, "error": error})
        return {"results": results}

    def inspect_certificate_status(self, interface_id: str) -> dict:
        rng = _rng_for(self.incident_id, "cert")
        is_expired = self._noisy(self.category == "cert_expiry")
        expiring_soon = is_expired or (
            "cert_expires_soon" in (self.store.get_interface(interface_id) or {}).get("known_fragility_flags", [])
        )
        if self.bucket == "no_known_resolution" and self.category == "cert_expiry":
            # deliberately unresolved: cert looks fine on paper, real cause elsewhere/unclear
            is_expired = False
            expiring_soon = True
        return {
            "certificate_present": True,
            "expires_at": "2026-10-01T00:00:00Z" if is_expired else "2027-06-01T00:00:00Z",
            "is_expired": is_expired,
            "is_expiring_soon": expiring_soon,
            "issuer": "Simulated Enterprise CA",
            "validation_error": "certificate has expired" if is_expired else None,
        }

    def inspect_credentials(self, interface_id: str) -> dict:
        rng = _rng_for(self.incident_id, "creds")
        cfg = self.store.get_config(interface_id) or {}
        auth_type = cfg.get("auth_type", "oauth2")
        status = "valid"
        if self.category == "auth_failure":
            status = self._noisy_status(["expired", "locked"], "valid", rng)
        elif self.category == "sftp_credential_failure":
            status = self._noisy_status(["locked", "expired"], "valid", rng)
        if self.bucket == "no_known_resolution" and self.category in ("auth_failure", "sftp_credential_failure"):
            status = "unknown"
        return {
            "auth_type": auth_type,
            "credential_status": status,
            "last_rotated_at": "2026-09-20T00:00:00Z",
            "permission_scope_sufficient": None if status == "unknown" else (status == "valid"),
        }

    def _noisy_status(self, true_statuses: list, false_status: str, rng: random.Random) -> str:
        true_val = rng.choice(true_statuses)
        if self.bucket == "ambiguous_evidence" and rng.random() < 0.5:
            return false_status
        return true_val

    def compare_with_previous_successful_run(self, interface_id: str, failing_message_id: str) -> dict:
        diffs = []
        found_previous = True
        if self.category in ("mapping_error", "malformed_payload"):
            if self.category == "mapping_error":
                diffs = [{"field": "OrderLineType", "previous_value": "STD", "current_value": "NEW_VARIANT"}]
            else:
                diffs = [{"field": "payload_encoding", "previous_value": "UTF-8", "current_value": "WINDOWS-1252 (unexpected)"}]
            if self.bucket == "no_known_resolution":
                diffs = [{"field": "timestamp_only", "previous_value": "t0", "current_value": "t1"}]  # no causal diff surfaces
        elif self.bucket == "ambiguous_evidence":
            diffs = [{"field": "batch_size", "previous_value": "100", "current_value": "250"}]
        else:
            found_previous = self.category != "duplicate_message"
        return {"previous_success_found": found_previous, "previous_run_timestamp": "2026-09-20T00:00:00Z" if found_previous else None,
                "diffs": diffs}

    def search_historical_incidents(self, symptom_signature: dict, max_results: int = 5) -> dict:
        return {"matches": self.store.search_historical_incidents(symptom_signature, max_results)}

    def retrieve_runbook(self, root_cause_category: Optional[str] = None,
                          interface_id: Optional[str] = None) -> dict:
        rb = self.store.runbook_for_category(root_cause_category)
        if not rb:
            return {"runbook_found": False, "runbook_id": None, "title": None, "steps": [], "last_reviewed_at": None}
        return {"runbook_found": True, "runbook_id": rb["runbook_id"], "title": rb["title"],
                "steps": rb["steps"], "last_reviewed_at": rb.get("last_reviewed_at")}

    # -----------------------------------------------------------------
    # ACTION (remediation) -- outcome depends on whether the tool matches
    # the true category AND the incident's designed-outcome bucket.
    # -----------------------------------------------------------------

    def _remediation_will_fix(self, tool_name: str) -> bool:
        correct_tool = REMEDIATION_TOOL_BY_CATEGORY.get(self.category)
        matches = correct_tool == tool_name
        if not matches:
            return False
        if self.bucket in ("resolved_known_precedent", "resolved_novel"):
            return True
        if self.bucket == "ambiguous_evidence":
            # first attempt against the right tool still fails once (needs a retry)
            return self._remediation_attempts_seen >= 1
        if self.bucket == "no_known_resolution":
            return False
        return False

    def retry_message(self, interface_id: str, message_id: str) -> dict:
        self._remediation_attempts_seen += 1
        will_fix = self._remediation_will_fix("retry_message")
        return {"accepted": True, "new_processing_id": f"PROC-{message_id}", "error": None,
                "_will_fix": will_fix}

    def restart_interface(self, interface_id: str, channel_id: str) -> dict:
        self._remediation_attempts_seen += 1
        will_fix = self._remediation_will_fix("restart_interface")
        return {"restarted": True, "previous_state": "stuck", "new_state": "running", "error": None,
                "_will_fix": will_fix}

    def refresh_connection(self, interface_id: str) -> dict:
        self._remediation_attempts_seen += 1
        will_fix = self._remediation_will_fix("refresh_connection")
        return {"refreshed": True, "new_session_established": True, "error": None,
                "_will_fix": will_fix}

    def update_adapter_timeout(self, interface_id: str, new_timeout_ms: int) -> dict:
        self._remediation_attempts_seen += 1
        will_fix = self._remediation_will_fix("update_adapter_timeout")
        cfg = self.store.get_config(interface_id) or {}
        return {"updated": True, "previous_timeout_ms": cfg.get("timeout_ms", 10000),
                "new_timeout_ms": new_timeout_ms, "_will_fix": will_fix}

    def rotate_certificate(self, interface_id: str) -> dict:
        self._remediation_attempts_seen += 1
        will_fix = self._remediation_will_fix("rotate_certificate")
        return {"rotated": True, "new_expiry": "2027-09-27T00:00:00Z", "error": None,
                "_will_fix": will_fix}

    def reprocess_message_batch(self, interface_id: str, batch_id: str, message_count: int) -> dict:
        """
        HIGH-risk: bulk delete/reprocess of a message batch. Not mapped to any
        root-cause category in REMEDIATION_TOOL_BY_CATEGORY -- the reasoning
        engine never proposes this autonomously; it exists to demonstrate the
        risk-policy framework covers destructive batch operations even though
        no current diagnostic hypothesis routes to it automatically.
        """
        self._remediation_attempts_seen += 1
        return {"reprocessed": True, "batch_id": batch_id, "message_count": message_count,
                "_will_fix": self._remediation_will_fix("reprocess_message_batch")}

    def update_routing_rule(self, interface_id: str, rule_id: str, new_target: str) -> dict:
        """HIGH-risk: changes message routing. Same rationale as
        reprocess_message_batch -- present for policy-framework completeness,
        not currently selected by the reasoning engine."""
        self._remediation_attempts_seen += 1
        return {"updated": True, "rule_id": rule_id, "previous_target": "unknown", "new_target": new_target,
                "_will_fix": self._remediation_will_fix("update_routing_rule")}

    def create_escalation(self, incident_id: str, reason: str, summary_for_human: str,
                           leading_hypothesis_ids: list, unresolved_questions: list) -> dict:
        return {"escalation_id": new_escalation_id(incident_id), "routed_to": "integration-oncall-queue",
                "created_at": "now"}

    def update_incident(self, incident_id: str, status: str, diagnosis_summary: str,
                         evidence_refs: list, actions_taken: list, outcome: Optional[str]) -> dict:
        return {"updated": True, "incident_id": incident_id, "updated_at": "now"}

    # -----------------------------------------------------------------
    # VERIFICATION
    # -----------------------------------------------------------------

    def verify_interface_health(self, interface_id: str, baseline_snapshot_id: str,
                                 will_fix_hint: Optional[bool] = None) -> dict:
        healthy = bool(will_fix_hint)
        return {
            "channel_status": "up" if healthy else ("degraded" if self.bucket == "no_known_resolution" else "down"),
            "error_state_cleared": healthy,
            "comparison_to_baseline": "matches healthy baseline" if healthy else "still diverges from healthy baseline",
        }

    def verify_message_processing(self, interface_id: str, message_id: str, expected_end_state: str,
                                   will_fix_hint: Optional[bool] = None) -> dict:
        succeeded = bool(will_fix_hint)
        return {
            "current_state": expected_end_state if succeeded else "error",
            "matches_expected": succeeded,
            "processing_errors": [] if succeeded else ["still_failing_post_remediation"],
        }


_escalation_counter = [0]


def new_escalation_id(incident_id: str) -> str:
    _escalation_counter[0] += 1
    return f"ESC-{incident_id}-{_escalation_counter[0]}"
