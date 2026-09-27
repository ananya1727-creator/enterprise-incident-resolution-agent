"""
ScenarioEnvironment: a fully hand-scripted, author-controlled stand-in for
SimulatedEnvironment, used only by the adversarial eval harness
(scripts/run_eval.py). Every tool response is exactly what the scenario
author wrote in data/eval_scenarios.json -- there is no hidden category-driven
oracle here, so scenario authors have precise control over misleading
signals, contradictions, and "looks fixed but isn't" sequences.

Implements the same method surface as SimulatedEnvironment so the
Orchestrator can use either interchangeably (agent/orchestrator.py's `env`
constructor override).
"""
from typing import Optional

DEFAULT_RESPONSES = {
    "search_logs": {"entries": [], "total_matched": 0},
    "get_recent_changes": {"changes": []},
    "test_connectivity": {"results": [
        {"endpoint": "source", "reachable": True, "latency_ms": 100, "error": None},
        {"endpoint": "target", "reachable": True, "latency_ms": 100, "error": None},
    ]},
    "inspect_certificate_status": {"certificate_present": True, "expires_at": "2027-01-01T00:00:00Z",
                                    "is_expired": False, "is_expiring_soon": False,
                                    "issuer": "Simulated Enterprise CA", "validation_error": None},
    "inspect_credentials": {"auth_type": "oauth2", "credential_status": "valid",
                             "last_rotated_at": "2026-06-01T00:00:00Z", "permission_scope_sufficient": True},
    "compare_with_previous_successful_run": {"previous_success_found": True,
                                              "previous_run_timestamp": "2026-09-20T00:00:00Z", "diffs": []},
    "search_historical_incidents": {"matches": []},
    "retrieve_runbook": {"runbook_found": False, "runbook_id": None, "title": None, "steps": [],
                          "last_reviewed_at": None},
    "retry_message": {"accepted": True, "new_processing_id": "PROC-SIM", "error": None, "_will_fix": False},
    "restart_interface": {"restarted": True, "previous_state": "stuck", "new_state": "running",
                           "error": None, "_will_fix": False},
    "refresh_connection": {"refreshed": True, "new_session_established": True, "error": None, "_will_fix": False},
    "update_adapter_timeout": {"updated": True, "previous_timeout_ms": 10000, "new_timeout_ms": 30000,
                                "_will_fix": False},
    "rotate_certificate": {"rotated": True, "new_expiry": "2027-09-27T00:00:00Z", "error": None, "_will_fix": False},
    "reprocess_message_batch": {"reprocessed": True, "batch_id": "SIM", "message_count": 0, "_will_fix": False},
    "update_routing_rule": {"updated": True, "rule_id": "SIM", "previous_target": "unknown",
                             "new_target": "unknown", "_will_fix": False},
}


class ScenarioEnvironment:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.incident = dict(scenario["incident_stub"])
        self.interface = dict(scenario["interface_stub"])
        self.interface_id = self.interface["interface_id"]
        self.incident_id = self.incident["incident_id"]
        self._script = scenario.get("script", {})
        self._call_counts = {}

    def _next(self, tool_name: str, overrides: Optional[dict] = None) -> dict:
        scripted = self._script.get(tool_name)
        if scripted is None:
            base = DEFAULT_RESPONSES.get(tool_name, {})
        elif isinstance(scripted, list):
            idx = self._call_counts.get(tool_name, 0)
            base = scripted[min(idx, len(scripted) - 1)]
            self._call_counts[tool_name] = idx + 1
        else:
            base = scripted
        result = dict(base)
        if overrides:
            result.update(overrides)
        return result

    # -- observation ----------------------------------------------------

    def get_incident(self, incident_id: str) -> dict:
        return dict(self.incident)

    def get_interface_metadata(self, interface_id: str) -> dict:
        return dict(self.interface)

    def search_logs(self, interface_id: str, time_range: dict, filter: Optional[str] = None,
                     max_results: int = 50) -> dict:
        return self._next("search_logs")

    def get_recent_changes(self, interface_id: str, lookback_window: str = "72h") -> dict:
        return self._next("get_recent_changes")

    # -- diagnosis --------------------------------------------------------

    def test_connectivity(self, interface_id: str, endpoint: str = "both") -> dict:
        return self._next("test_connectivity")

    def inspect_certificate_status(self, interface_id: str) -> dict:
        return self._next("inspect_certificate_status")

    def inspect_credentials(self, interface_id: str) -> dict:
        return self._next("inspect_credentials")

    def compare_with_previous_successful_run(self, interface_id: str, failing_message_id: str) -> dict:
        return self._next("compare_with_previous_successful_run")

    def search_historical_incidents(self, symptom_signature: dict, max_results: int = 5) -> dict:
        return self._next("search_historical_incidents")

    def retrieve_runbook(self, root_cause_category: Optional[str] = None,
                          interface_id: Optional[str] = None) -> dict:
        return self._next("retrieve_runbook")

    # -- action -----------------------------------------------------------

    def retry_message(self, interface_id: str, message_id: str) -> dict:
        return self._next("retry_message")

    def restart_interface(self, interface_id: str, channel_id: str) -> dict:
        return self._next("restart_interface")

    def refresh_connection(self, interface_id: str) -> dict:
        return self._next("refresh_connection")

    def update_adapter_timeout(self, interface_id: str, new_timeout_ms: int) -> dict:
        return self._next("update_adapter_timeout")

    def rotate_certificate(self, interface_id: str) -> dict:
        return self._next("rotate_certificate")

    def reprocess_message_batch(self, interface_id: str, batch_id: str, message_count: int) -> dict:
        return self._next("reprocess_message_batch")

    def update_routing_rule(self, interface_id: str, rule_id: str, new_target: str) -> dict:
        return self._next("update_routing_rule")

    def create_escalation(self, incident_id: str, reason: str, summary_for_human: str,
                           leading_hypothesis_ids: list, unresolved_questions: list) -> dict:
        return {"escalation_id": f"ESC-{incident_id}", "routed_to": "integration-oncall-queue", "created_at": "now"}

    def update_incident(self, incident_id: str, status: str, diagnosis_summary: str,
                         evidence_refs: list, actions_taken: list, outcome: Optional[str]) -> dict:
        return {"updated": True, "incident_id": incident_id, "updated_at": "now"}

    # -- verification -------------------------------------------------------

    def verify_interface_health(self, interface_id: str, baseline_snapshot_id: str,
                                 will_fix_hint: Optional[bool] = None) -> dict:
        healthy = bool(will_fix_hint)
        return {"channel_status": "up" if healthy else "down", "error_state_cleared": healthy,
                "comparison_to_baseline": "matches healthy baseline" if healthy else "still diverges from healthy baseline"}

    def verify_message_processing(self, interface_id: str, message_id: str, expected_end_state: str,
                                   will_fix_hint: Optional[bool] = None) -> dict:
        succeeded = bool(will_fix_hint)
        return {"current_state": expected_end_state if succeeded else "error",
                "matches_expected": succeeded,
                "processing_errors": [] if succeeded else ["still_failing_post_remediation"]}
