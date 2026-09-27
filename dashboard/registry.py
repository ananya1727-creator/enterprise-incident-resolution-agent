"""
The only place the dashboard touches the agent's execution machinery. This
module does not reimplement any incident-resolution logic -- it launches
real agent.orchestrator.Orchestrator instances in background threads and
exposes their live agent.models.IncidentState for the UI to read, plus a
real blocking bridge for Human Approval Center decisions.

Approval bridging is the part worth being precise about: Orchestrator's
`approval_callback` is called SYNCHRONOUSLY, on the orchestrator's own
thread, at the exact point execution must pause for a High/gated-Medium
action (agent/orchestrator.py `_route_to_approval`). The callback built here
registers a `threading.Event` and blocks that real orchestrator thread on
`event.wait()` until a dashboard API call (`decide_approval`) sets a
decision and the event. This means an "Approve"/"Reject" click in the UI
does not just update a database row -- it is the only thing that can unblock
the real agent thread and let it call the actual gated tool (or not).
"""
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent.data_store import DataStore
from agent.models import ApprovalRequest
from agent.orchestrator import Orchestrator, RUNS_DIR, _state_to_dict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Purely a UX pacing choice for incidents launched from the dashboard, so an
# operator watching the Investigation view sees each step land instead of
# the whole run completing before the page even fetches once. Never applies
# to main.py/scripts/tests -- see Orchestrator.step_delay_seconds.
LIVE_STEP_DELAY_SECONDS = 0.9


@dataclass
class PendingApproval:
    incident_id: str
    approval_request_id: str
    tool_name: str
    risk_tier: str
    rationale: str
    hypothesis_category: str
    hypothesis_confidence: float
    supporting_evidence: list
    event: threading.Event = field(default_factory=threading.Event)
    decision: Optional[bool] = None


@dataclass
class TrackedIncident:
    incident_id: str
    source: str  # "live" | "historical"
    orchestrator: Optional[Orchestrator] = None
    thread: Optional[threading.Thread] = None
    raw_state: Optional[dict] = None  # populated for historical; recomputed on read for live


class DashboardRegistry:
    """
    In-process registry of every incident the dashboard knows about:
    historical ones discovered from real prior runs (runs/*.json, written by
    main.py / scripts/run_eval.py / scripts/run_benchmark.py /
    scripts/run_e2e_tests.py -- never fabricated), plus any launched live
    during this dashboard session.
    """

    def __init__(self):
        self.store = DataStore()
        self.tracked: dict[str, TrackedIncident] = {}
        self.pending_approvals: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()
        self._load_historical_runs()

    # -- discovery of real, already-generated data --------------------------

    def _load_historical_runs(self):
        if not RUNS_DIR.exists():
            return
        for path in RUNS_DIR.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            incident_id = raw.get("incident_id") or path.stem
            self.tracked[incident_id] = TrackedIncident(incident_id=incident_id, source="historical", raw_state=raw)

    def refresh_historical_runs(self):
        """Re-scan runs/ for traces written by scripts run outside this dashboard process
        (e.g. someone re-running scripts/run_benchmark.py in another terminal)."""
        with self._lock:
            for path in RUNS_DIR.glob("*.json"):
                incident_id = path.stem
                if incident_id in self.tracked and self.tracked[incident_id].source == "live":
                    continue  # never clobber a live/in-progress run's own state
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                self.tracked[incident_id] = TrackedIncident(incident_id=incident_id, source="historical", raw_state=raw)

    # -- launching a real, live agent run ------------------------------------

    def is_launchable(self, incident_id: str) -> bool:
        existing = self.tracked.get(incident_id)
        if existing and existing.source == "live" and existing.thread and existing.thread.is_alive():
            return False
        return self.store.get_incident(incident_id) is not None

    def launch(self, incident_id: str) -> TrackedIncident:
        if not self.is_launchable(incident_id):
            raise ValueError(f"incident {incident_id} is not launchable (already running, or unknown)")

        callback = self._make_approval_callback(incident_id)
        orch = Orchestrator(incident_id, store=self.store, approval_callback=callback, verbose=False,
                             step_delay_seconds=LIVE_STEP_DELAY_SECONDS)
        tracked = TrackedIncident(incident_id=incident_id, source="live", orchestrator=orch)
        with self._lock:
            self.tracked[incident_id] = tracked

        def _run():
            orch.run()  # blocks THIS background thread when approval is needed; writes runs/*.json on completion

        t = threading.Thread(target=_run, daemon=True, name=f"agent-{incident_id}")
        tracked.thread = t
        t.start()
        return tracked

    def _make_approval_callback(self, incident_id: str):
        def callback(req: ApprovalRequest) -> bool:
            # Find the RemediationAction this ApprovalRequest belongs to, on
            # the live state, to surface the evidence-based rationale --
            # read-only inspection, no logic duplicated.
            orch_state = self.tracked[incident_id].orchestrator.state
            remediation = next((r for r in orch_state.remediation_actions
                                if r.approval_request_id == req.approval_request_id), None)
            hyp = orch_state.get_hypothesis(remediation.targets_hypothesis_id) if remediation else None

            pa = PendingApproval(
                incident_id=incident_id,
                approval_request_id=req.approval_request_id,
                tool_name=remediation.tool_name if remediation else "unknown",
                risk_tier=req.risk_tier,
                rationale=req.justification,
                hypothesis_category=hyp.category if hyp else "unknown",
                hypothesis_confidence=hyp.confidence if hyp else 0.0,
                supporting_evidence=[
                    {"evidence_id": eid, "summary": next(
                        (e.summary for e in orch_state.evidence if e.evidence_id == eid), eid)}
                    for eid in (hyp.supporting_evidence_ids if hyp else [])
                ],
            )
            with self._lock:
                self.pending_approvals[req.approval_request_id] = pa

            pa.event.wait()  # <-- blocks the REAL orchestrator thread until decide_approval() is called

            with self._lock:
                self.pending_approvals.pop(req.approval_request_id, None)
            return bool(pa.decision)
        return callback

    def decide_approval(self, approval_request_id: str, approve: bool) -> bool:
        with self._lock:
            pa = self.pending_approvals.get(approval_request_id)
        if not pa:
            return False
        pa.decision = approve
        pa.event.set()  # <-- the only line that actually unblocks the agent thread
        return True

    # -- reading state (live or historical) uniformly ------------------------

    def get_raw_state(self, incident_id: str) -> Optional[dict]:
        tracked = self.tracked.get(incident_id)
        if not tracked:
            return None
        if tracked.source == "live":
            return _state_to_dict(tracked.orchestrator.state)
        return tracked.raw_state

    def all_raw_states(self) -> list:
        out = []
        for incident_id in list(self.tracked.keys()):
            raw = self.get_raw_state(incident_id)
            if raw:
                out.append(raw)
        return out

    def list_pending_approvals(self) -> list:
        with self._lock:
            return list(self.pending_approvals.values())
