"""
Loads the simulated enterprise integration environment (data/*.json) and
exposes lookups. This is Knowledge Retrieval's static/reference surface
(architecture 1.3) plus the case-memory store (1.5).
"""
import json
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

LEARNED_RESOLUTIONS_FILE = DATA_DIR / "learned_resolutions.json"
ESCALATION_ARCHIVE_FILE = DATA_DIR / "escalation_archive.json"


def _load(name: str) -> list:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


class DataStore:
    def __init__(self):
        self.interfaces = _load("interfaces")
        self.interface_configs = _load("interface_configs")
        self.runbooks = _load("runbooks")
        self.incidents = _load("incidents")
        self.log_events = _load("log_events")
        self.resolution_records = _load("resolution_records") + _load("learned_resolutions")

        self._interfaces_by_id = {i["interface_id"]: i for i in self.interfaces}
        self._configs_by_iface = {c["interface_id"]: c for c in self.interface_configs}
        self._incidents_by_id = {i["incident_id"]: i for i in self.incidents}

    # --- accessors -----------------------------------------------------

    def get_incident(self, incident_id: str) -> Optional[dict]:
        return self._incidents_by_id.get(incident_id)

    def get_interface(self, interface_id: str) -> Optional[dict]:
        return self._interfaces_by_id.get(interface_id)

    def get_config(self, interface_id: str) -> Optional[dict]:
        return self._configs_by_iface.get(interface_id)

    def logs_for_interface(self, interface_id: str, incident_id: Optional[str] = None) -> list:
        entries = [l for l in self.log_events if l["interface_id"] == interface_id]
        if incident_id:
            direct = [l for l in entries if l.get("incident_id") == incident_id]
            if direct:
                return direct
        return entries

    def recent_changes_for_interface(self, interface_id: str) -> list:
        # Simulated: derive plausible "recent changes" deterministically from
        # the interface's known_fragility_flags and config, since no separate
        # change-log fixture file exists. This keeps behavior interface-specific
        # and reproducible rather than random per call.
        iface = self.get_interface(interface_id)
        if not iface:
            return []
        changes = []
        flags = iface.get("known_fragility_flags", [])
        if "cert_expires_soon" in flags:
            changes.append({
                "change_id": f"CHG-{interface_id}-CERT",
                "change_type": "certificate",
                "description": "Certificate renewal window opened; rotation pending.",
                "changed_by": "cert-lifecycle-automation",
            })
        if "deprecated_auth_scheme" in flags:
            changes.append({
                "change_id": f"CHG-{interface_id}-AUTH",
                "change_type": "credential_rotation",
                "description": "Credential rotation performed as part of deprecated-scheme migration.",
                "changed_by": "identity-team",
            })
        cfg = self.get_config(interface_id)
        if cfg and cfg.get("adapter_version", "").startswith("1."):
            changes.append({
                "change_id": f"CHG-{interface_id}-DEPLOY",
                "change_type": "deployment",
                "description": "Legacy adapter version; last mapping deployment predates current data contract.",
                "changed_by": "integration-coe",
            })
        return changes

    def runbook_for_category(self, category: Optional[str]) -> Optional[dict]:
        if not category:
            return None
        matches = [r for r in self.runbooks if r["root_cause_category"] == category]
        return matches[0] if matches else None

    def search_historical_incidents(self, symptom_signature: dict, max_results: int = 5) -> list:
        scored = []
        for rec in self.resolution_records:
            sig = rec["symptom_signature"]
            score = 0.0
            total = 0
            for key in ("interface_type", "error_category", "affected_component"):
                if symptom_signature.get(key):
                    total += 1
                    if sig.get(key) == symptom_signature.get(key):
                        score += 1
            tags_a = set(symptom_signature.get("log_pattern_tags") or [])
            tags_b = set(sig.get("log_pattern_tags") or [])
            if tags_a:
                total += 1
                if tags_a & tags_b:
                    score += len(tags_a & tags_b) / len(tags_a)
            similarity = score / total if total else 0.0
            if similarity > 0:
                scored.append((similarity, rec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "record_id": rec["record_id"],
                "similarity_score": round(sim, 2),
                "root_cause": rec["root_cause"],
                "remediation_tool": rec["remediation_tool"],
                "outcome": rec["outcome"],
                "human_feedback": rec["human_feedback"],
            }
            for sim, rec in scored[:max_results]
        ]

    def append_learned_resolution(self, record: dict):
        """
        Writes to the long-term, RETRIEVABLE resolution/case-memory store.
        Callers must go through agent.memory.ResolutionMemory.promote(),
        which is the only code path that enforces "verified successful
        resolutions only" -- this method itself does no such check, so it
        must never be called directly from orchestrator/reasoning code.
        """
        existing = _load("learned_resolutions")
        existing.append(record)
        LEARNED_RESOLUTIONS_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def append_escalation_archive(self, summary: dict):
        """
        Escalations are recorded here for audit/analytics ONLY -- this file is
        never read by search_historical_incidents / retrieve(), so an
        escalated (never-verified) incident can never resurface as case-memory
        precedent for a future incident's hypothesis generation.
        """
        existing = _load("escalation_archive")
        existing.append(summary)
        ESCALATION_ARCHIVE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
