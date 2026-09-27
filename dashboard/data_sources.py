"""
Loads REAL, already-generated data the dashboard displays: fixture incidents
(for the launchable queue), interface metadata (owning team / SLA tier), and
the benchmark/eval/e2e reports already produced by scripts/run_benchmark.py,
scripts/run_eval.py, and scripts/run_e2e_tests.py. Nothing here invents
numbers -- if a report hasn't been generated yet, the corresponding section
says so explicitly rather than showing a fabricated value.
"""
import json
import sys
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_fixture_incidents() -> list:
    return _load_json(DATA_DIR / "incidents.json") or []


def load_interfaces_by_id() -> dict:
    ifaces = _load_json(DATA_DIR / "interfaces.json") or []
    return {i["interface_id"]: i for i in ifaces}


def load_benchmark_results() -> list:
    return _load_json(REPORTS_DIR / "evaluation_results.json") or []


def load_benchmark_aggregate() -> dict | None:
    results = load_benchmark_results()
    if not results:
        return None
    try:
        import run_benchmark as bench  # scripts/run_benchmark.py
    except ImportError:
        return None
    return bench.aggregate(results)


def most_common_root_causes(raw_states: list, top_n: int = 8) -> list:
    counts = Counter()
    for raw in raw_states:
        resolution = raw.get("resolution")
        if resolution and resolution.get("root_cause"):
            counts[resolution["root_cause"]] += 1
    return counts.most_common(top_n)


def tool_usage_from_raw_states(raw_states: list) -> dict:
    """Aggregates tool_call_log across every real run trace the dashboard
    knows about (historical + live) -- success vs error counts per tool."""
    per_tool = {}
    for raw in raw_states:
        for call in raw.get("tool_call_log", []):
            name = call["tool_name"]
            bucket = per_tool.setdefault(name, {"success": 0, "error": 0})
            if call["status"] == "success":
                bucket["success"] += 1
            else:
                bucket["error"] += 1
    return dict(sorted(per_tool.items(), key=lambda kv: kv[1]["success"] + kv[1]["error"], reverse=True))


def escalation_rate_from_raw_states(raw_states: list) -> float | None:
    terminal = [r for r in raw_states if r.get("stage") in ("RESOLVED", "ESCALATED")]
    if not terminal:
        return None
    escalated = sum(1 for r in terminal if r["stage"] == "ESCALATED")
    return escalated / len(terminal)
