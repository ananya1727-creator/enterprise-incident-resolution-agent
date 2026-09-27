"""
Pure projection functions: agent state (as the dict shape produced by
agent.orchestrator._state_to_dict) -> JSON view models for the dashboard.
No incident-resolution logic lives here -- only formatting/grouping of data
the agent already produced. This mirrors agent/observability.py's rendering
logic but emits structured JSON for the UI instead of a text timeline; both
read the same underlying fields and neither exposes anything the other
doesn't (in particular: no hidden chain-of-thought, because none exists --
see agent/observability.py's module docstring).
"""
from datetime import datetime

from agent.skills import CATEGORY_TO_SKILL

SLA_STATUS_OK = "OK"
SLA_STATUS_AT_RISK = "At Risk"
SLA_STATUS_BREACHED = "Breached"


def _sla_remaining_fraction(raw: dict) -> float:
    sla_deadline = raw.get("sla_deadline")
    reported_at = raw.get("reported_at")
    elapsed = raw.get("simulated_elapsed_minutes", 0.0)
    if not sla_deadline or not reported_at:
        return 1.0
    try:
        total_minutes = (
            datetime.fromisoformat(sla_deadline.rstrip("Z")) - datetime.fromisoformat(reported_at.rstrip("Z"))
        ).total_seconds() / 60.0
    except ValueError:
        return 1.0
    if total_minutes <= 0:
        return 0.0
    return max(0.0, min(1.0, (total_minutes - elapsed) / total_minutes))


def sla_status(raw: dict) -> str:
    frac = _sla_remaining_fraction(raw)
    if frac <= 0:
        return SLA_STATUS_BREACHED
    if frac < 0.2:
        return SLA_STATUS_AT_RISK
    return SLA_STATUS_OK


def engaged_skills(raw: dict) -> list:
    skill_to_categories = {}
    for h in raw.get("hypotheses", []):
        if h.get("confidence_history"):
            skill = CATEGORY_TO_SKILL.get(h["category"], "Incident Investigation")
            skill_to_categories.setdefault(skill, []).append(h["category"])
    return [{"skill": s, "categories": cats} for s, cats in skill_to_categories.items()]


def build_queue_row(incident_id: str, source: str, raw: dict | None, fixture: dict | None,
                     interface: dict | None) -> dict:
    if raw:
        stage = raw.get("stage")
        resolution = raw.get("resolution")
        return {
            "incident_id": incident_id,
            "title": (fixture or {}).get("symptom_description", raw.get("incident_id", incident_id))[:90],
            "severity": (fixture or {}).get("reported_severity", "n/a"),
            "criticality": raw.get("criticality") or "n/a",
            "sla_status": sla_status(raw),
            "agent_state": stage,
            "assigned_team": (interface or {}).get("owning_team", "n/a"),
            "resolution_status": resolution["outcome"] if resolution else stage,
            "source": source,
            "launchable": False,
        }
    return {
        "incident_id": incident_id,
        "title": (fixture or {}).get("symptom_description", incident_id)[:90],
        "severity": (fixture or {}).get("reported_severity", "n/a"),
        "criticality": "n/a",
        "sla_status": "Not Started",
        "agent_state": "Queued",
        "assigned_team": (interface or {}).get("owning_team", "n/a"),
        "resolution_status": "Not Started",
        "source": "queued",
        "launchable": True,
    }


def build_investigation_view(raw: dict) -> dict:
    hypotheses = sorted(raw.get("hypotheses", []), key=lambda h: h["confidence"], reverse=True)
    touched_hypotheses = [h for h in hypotheses if h.get("confidence_history") or h["confidence"] > 0.05]

    confidence_changes = []
    for h in hypotheses:
        for d in h.get("confidence_history", []):
            confidence_changes.append({
                "category": h["category"],
                "prior_confidence": d["prior_confidence"],
                "new_confidence": d["new_confidence"],
                "evidence_id": d["evidence_id"],
                "reason": d["reason"],
                "timestamp": d["timestamp"],
            })
    confidence_changes.sort(key=lambda c: c["timestamp"])

    timeline = []
    for call in raw.get("tool_call_log", []):
        timeline.append({"timestamp": call["timestamp"], "kind": "tool_call",
                          "summary": f"{call['tool_name']}() -> {call['status']}"})
    for d in raw.get("decision_log", []):
        timeline.append({"timestamp": d["timestamp"], "kind": "decision", "summary": d["decision"],
                          "reason": d["reason"]})
    for v in raw.get("verification_results", []):
        timeline.append({"timestamp": v["timestamp"], "kind": "verification",
                          "summary": f"verification -> {v['outcome']}"})
    timeline.sort(key=lambda e: e["timestamp"])

    return {
        "incident_details": {
            "incident_id": raw["incident_id"],
            "interface_id": raw["interface_id"],
            "criticality": raw.get("criticality"),
            "sla_deadline": raw.get("sla_deadline"),
            "reported_at": raw.get("reported_at"),
        },
        "current_state": {
            "stage": raw["stage"],
            "investigation_cycle_count": raw["investigation_cycle_count"],
            "remediation_attempt_count": raw["remediation_attempt_count"],
            "simulated_elapsed_minutes": raw.get("simulated_elapsed_minutes"),
            "sla_status": sla_status(raw),
        },
        "selected_skills": engaged_skills(raw),
        "evidence_collected": raw.get("evidence", []),
        "hypotheses": touched_hypotheses,
        "confidence_changes": confidence_changes,
        "diagnostic_tools_called": raw.get("tool_call_log", []),
        "decisions": raw.get("decision_log", []),
        "actions_taken": {
            "diagnostic": raw.get("diagnostic_actions", []),
            "remediation": raw.get("remediation_actions", []),
        },
        "verification_results": raw.get("verification_results", []),
        "escalation": raw.get("escalation"),
        "final_resolution": raw.get("resolution"),
        "activity_timeline": timeline,
    }
