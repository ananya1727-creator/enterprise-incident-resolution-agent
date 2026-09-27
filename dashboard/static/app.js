const state = { view: "queue", selectedIncident: null, pollTimer: null };

function $(sel, root) { return (root || document).querySelector(sel); }
function el(tag, attrs, children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "text") e.textContent = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  (children || []).forEach(c => e.appendChild(c));
  return e;
}
function pct(x) { return x === null || x === undefined ? "n/a" : Math.round(x * 100) + "%"; }
function num(x, digits) { return x === null || x === undefined ? "n/a" : x.toFixed(digits === undefined ? 1 : digits); }

function badgeClass(value) {
  const v = (value || "").toLowerCase();
  if (["ok", "resolved", "approved"].includes(v)) return "ok";
  if (["at risk", "awaiting_approval", "awaiting approval"].includes(v)) return "warn";
  if (["breached", "escalated", "rejected"].includes(v)) return "danger";
  return "neutral";
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// Fetch + skip-if-unchanged. Polling re-renders were rebuilding each view's
// entire DOM from scratch every 3s regardless of whether the underlying data
// had actually changed, which is what caused the visible flicker/scroll-jump
// -- not just the scroll position, but the teardown-and-rebuild itself.
// Returns null (skip re-rendering) only when `force` is false AND the
// payload is byte-identical to the last fetch of the same path. The cache is
// always kept up to date regardless of `force`, so a manual (forced) render
// never leaves a stale entry that would trigger a spurious extra re-render
// on the next poll tick.
const _lastPayload = new Map();
async function fetchForView(path, force) {
  const data = await api(path);
  const serialized = JSON.stringify(data);
  const unchanged = _lastPayload.get(path) === serialized;
  _lastPayload.set(path, serialized);
  if (!force && unchanged) return null;
  return data;
}

// ---------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------
function switchView(name) {
  state.view = name;
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + name));
  render();
}
document.querySelectorAll("nav button").forEach(b => b.addEventListener("click", () => switchView(b.dataset.view)));

function render() {
  if (state.view === "queue") renderQueue(true);
  else if (state.view === "investigation") renderInvestigation(true);
  else if (state.view === "approvals") renderApprovals(true);
  else if (state.view === "analytics") renderAnalytics();
  else if (state.view === "performance") renderPerformance();
  refreshApprovalBadge();
}

async function refreshApprovalBadge() {
  try {
    const approvals = await api("/api/approvals");
    const badge = $("#approval-count-badge");
    badge.textContent = approvals.length ? `(${approvals.length})` : "";
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------------
// 1. Incident Queue
// ---------------------------------------------------------------------
// The three buckets are mutually exclusive (every row falls in exactly one):
//   0 = Unresolved -- still queued or actively in progress (no attention yet)
//   1 = Escalated  -- needs human follow-up
//   2 = Resolved   -- done
// "Unresolved" deliberately does NOT include Escalated -- that has its own
// tab, and counting it twice was the confusing part.
function statusBucket(row) {
  if (row.resolution_status === "Resolved") return 2;
  if (row.resolution_status === "Escalated") return 1;
  return 0;
}

const QUEUE_FILTERS = [
  { key: "all", label: "All" },
  { key: "unresolved", label: "Unresolved", bucket: 0 },
  { key: "escalated", label: "Escalated", bucket: 1 },
  { key: "resolved", label: "Resolved", bucket: 2 },
];
let _queueFilter = "all";

function applyQueueFilter(rows, filterKey) {
  const spec = QUEUE_FILTERS.find(f => f.key === filterKey);
  const filtered = (spec && spec.bucket !== undefined) ? rows.filter(r => statusBucket(r) === spec.bucket) : rows;
  // Within whatever's shown, order by bucket (unresolved, then escalated,
  // then resolved) -- Array#sort is stable, so rows keep their relative
  // order within each group. A no-op when a single bucket is already filtered.
  return [...filtered].sort((a, b) => statusBucket(a) - statusBucket(b));
}

async function renderQueue(force) {
  const data = await fetchForView("/api/queue", force);
  if (data === null) return; // unchanged since last poll -- don't touch the DOM at all
  const root = $("#view-queue");
  root.innerHTML = "";

  root.appendChild(el("div", { class: "note", text:
    `Showing ${data.rows.length} incidents (${data.total_tracked} tracked from real agent runs, ` +
    `${data.queued_shown} of ${data.total_fixture_incidents} not-yet-launched fixture incidents shown). ` +
    `Click "Launch" to run a real agent instance against a queued incident. Unresolved incidents are ` +
    `sorted to the top.` }));

  const filterBar = el("div", { class: "filter-bar" });
  QUEUE_FILTERS.forEach(f => {
    const count = f.bucket === undefined ? data.rows.length : data.rows.filter(r => statusBucket(r) === f.bucket).length;
    const btn = el("button", {
      class: "filter-btn" + (_queueFilter === f.key ? " active" : ""),
      text: `${f.label} (${count})`,
    });
    btn.addEventListener("click", () => { _queueFilter = f.key; renderQueue(true); });
    filterBar.appendChild(btn);
  });
  root.appendChild(filterBar);

  const visibleRows = applyQueueFilter(data.rows, _queueFilter);

  const table = el("table", {}, [
    el("thead", {}, [el("tr", {}, ["Incident", "Title", "Severity", "Criticality", "SLA", "Agent State",
      "Team", "Resolution", ""].map(h => el("th", { text: h })))]),
  ]);
  const tbody = el("tbody");
  if (!visibleRows.length) {
    root.appendChild(el("div", { class: "empty", text: `No incidents match "${_queueFilter}".` }));
    return;
  }
  visibleRows.forEach(row => {
    const tr = el("tr", { class: row.launchable ? "" : "clickable" });
    if (!row.launchable) {
      tr.addEventListener("click", () => {
        if (state.selectedIncident !== row.incident_id) resetInvestigationStream();
        state.selectedIncident = row.incident_id;
        switchView("investigation");
      });
    }
    tr.appendChild(el("td", { text: row.incident_id }));
    tr.appendChild(el("td", { text: row.title }));
    tr.appendChild(el("td", { text: row.severity }));
    tr.appendChild(el("td", { text: row.criticality }));
    tr.appendChild(el("td", {}, [el("span", { class: "badge " + badgeClass(row.sla_status), text: row.sla_status })]));
    tr.appendChild(el("td", { text: row.agent_state }));
    tr.appendChild(el("td", { text: row.assigned_team }));
    tr.appendChild(el("td", {}, [el("span", { class: "badge " + badgeClass(row.resolution_status), text: row.resolution_status })]));
    const actionCell = el("td");
    if (row.launchable) {
      const btn = el("button", { class: "action", text: "Launch" });
      btn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        btn.disabled = true; btn.textContent = "Launching...";
        await api(`/api/incidents/${row.incident_id}/launch`, { method: "POST" });
        // Jump straight to the live investigation so the step-by-step
        // process (evidence -> hypotheses -> decisions -> actions ->
        // verification) is visible as it happens, not just the end result.
        state.selectedIncident = row.incident_id;
        resetInvestigationStream();
        switchView("investigation");
      });
      actionCell.appendChild(btn);
    }
    tr.appendChild(actionCell);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  root.appendChild(table);
}

// ---------------------------------------------------------------------
// 2. Incident Investigation
// ---------------------------------------------------------------------
const PIPELINE_STAGES = ["OBSERVE", "INVESTIGATING", "DECIDING", "AWAITING_APPROVAL", "ACTING", "VERIFYING"];
const PIPELINE_TERMINAL = ["RESOLVED", "ESCALATED"];

// Streaming state: which incident's timeline we're currently appending to,
// and how many timeline items have already been rendered for it. Reset
// whenever the user selects a different incident (see resetInvestigationStream).
let _streamIncidentId = null;
let _streamRenderedCount = 0;

function resetInvestigationStream() {
  _streamIncidentId = null;
  _streamRenderedCount = 0;
}

function buildTimelineItem(item) {
  const node = el("div", { class: "timeline-item timeline-item-new" }, [
    el("span", { class: "ts", text: item.timestamp }),
    el("span", { class: "kind " + item.kind, text: item.kind }),
    el("span", { text: item.summary }),
  ]);
  // Fade the "new" highlight out after it's had a moment to be noticed --
  // this is what makes newly-streamed-in steps visually pop without
  // requiring the whole view to flash.
  setTimeout(() => node.classList.remove("timeline-item-new"), 1200);
  return node;
}

function buildStagePipeline(currentStage) {
  const isTerminal = PIPELINE_TERMINAL.includes(currentStage);
  const stages = isTerminal ? [...PIPELINE_STAGES, currentStage] : PIPELINE_STAGES;
  const currentIndex = stages.indexOf(currentStage);
  return el("div", { class: "pipeline" }, stages.map((s, i) => {
    let cls = "pipeline-step";
    if (i < currentIndex) cls += " done";
    else if (i === currentIndex) cls += isTerminal ? (currentStage === "ESCALATED" ? " current danger" : " current ok") : " current";
    return el("span", { class: cls, text: s.replace("_", " ") });
  }));
}

async function renderInvestigation(force) {
  const root = $("#view-investigation");
  if (!state.selectedIncident) {
    root.innerHTML = "";
    root.appendChild(el("div", { class: "empty", text: "Select an incident from the Incident Queue to view its investigation." }));
    return;
  }
  const path = `/api/incidents/${state.selectedIncident}`;
  let data;
  try {
    data = await fetchForView(path, force);
  } catch (e) {
    root.innerHTML = "";
    root.appendChild(el("div", { class: "empty", text: "Incident not found or not yet started." }));
    return;
  }
  if (data === null) return; // unchanged since last poll

  // Incremental path: same incident already streaming -- update the stage
  // pipeline and summary cards in place, and APPEND only the new timeline
  // items, without ever clearing the timeline list itself (no flicker, no
  // scroll reset, and the operator can watch new steps land one by one as
  // the live agent thread progresses).
  const existingList = document.getElementById("activity-timeline-list");
  if (_streamIncidentId === state.selectedIncident && existingList) {
    const pipelineHolder = document.getElementById("stage-pipeline-holder");
    if (pipelineHolder) {
      pipelineHolder.innerHTML = "";
      pipelineHolder.appendChild(buildStagePipeline(data.current_state.stage));
    }
    const summaryHolder = document.getElementById("investigation-summary");
    if (summaryHolder) {
      summaryHolder.innerHTML = "";
      summaryHolder.appendChild(buildInvestigationSummary(data));
    }
    const newItems = data.activity_timeline.slice(_streamRenderedCount);
    newItems.forEach(item => existingList.appendChild(buildTimelineItem(item)));
    _streamRenderedCount = data.activity_timeline.length;
    existingList.scrollTop = existingList.scrollHeight;
    return;
  }

  // Full (re)build: first view of this incident, or switched incidents.
  _streamIncidentId = state.selectedIncident;
  _streamRenderedCount = data.activity_timeline.length;
  root.innerHTML = "";

  const back = el("span", { class: "back-link", text: "<- Back to queue" });
  back.addEventListener("click", () => switchView("queue"));
  root.appendChild(back);
  root.appendChild(el("h2", { text: `Incident ${data.incident_details.incident_id}`, style: "margin:6px 0 14px 0" }));

  const pipelineHolder = el("div", { id: "stage-pipeline-holder" }, [buildStagePipeline(data.current_state.stage)]);
  root.appendChild(pipelineHolder);

  const summaryHolder = el("div", { id: "investigation-summary" }, [buildInvestigationSummary(data)]);
  root.appendChild(summaryHolder);

  const timelineList = el("div", { id: "activity-timeline-list", class: "timeline-scroll" },
    data.activity_timeline.map(item => buildTimelineItem(item)));
  root.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Chronological Agent Activity Timeline" }),
    el("div", { class: "note", text: "Streaming live -- new steps append here as the agent investigates." }),
    timelineList,
  ]));
  timelineList.scrollTop = timelineList.scrollHeight;
}

function buildInvestigationSummary(data) {
  const col1 = el("div");
  const col2 = el("div");

  // Incident details + current state
  col1.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Incident Details" }),
    el("div", { text: `Interface: ${data.incident_details.interface_id}` }),
    el("div", { text: `Criticality: ${data.incident_details.criticality || "n/a"}` }),
    el("div", { text: `Reported: ${data.incident_details.reported_at || "n/a"}` }),
    el("div", { text: `SLA deadline: ${data.incident_details.sla_deadline || "n/a"}` }),
  ]));
  col1.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Current State" }),
    el("div", {}, [el("span", { class: "badge " + badgeClass(data.current_state.stage), text: data.current_state.stage })]),
    el("div", { text: `Investigation cycles used: ${data.current_state.investigation_cycle_count}` }),
    el("div", { text: `Remediation attempts used: ${data.current_state.remediation_attempt_count}` }),
    el("div", { text: `Simulated elapsed: ${num(data.current_state.simulated_elapsed_minutes, 0)} min` }),
    el("div", {}, [el("span", { class: "badge " + badgeClass(data.current_state.sla_status), text: "SLA: " + data.current_state.sla_status })]),
  ]));

  // Selected skill(s)
  col1.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Selected Investigation Skill(s)" }),
    ...(data.selected_skills.length
      ? data.selected_skills.map(s => el("div", { text: `${s.skill} (${s.categories.join(", ")})` }))
      : [el("div", { class: "note", text: "No specialist skill reached yet." })]),
  ]));

  // Hypotheses + confidence
  col1.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Hypotheses & Confidence" }),
    ...data.hypotheses.map(h => el("div", { style: "margin-bottom:6px" }, [
      el("span", { text: `${h.status}: ${h.category} -- ${(h.confidence * 100).toFixed(0)}%` }),
      el("span", { class: "hyp-bar-bg" }, [el("span", { class: "hyp-bar", style: `width:${h.confidence * 100}%` })]),
    ])),
  ]));

  // Escalation / Resolution
  if (data.escalation) {
    col1.appendChild(el("div", { class: "card" }, [
      el("h3", { text: "Escalation" }),
      el("div", { text: `Reason: ${data.escalation.reason}` }),
      el("div", { text: data.escalation.summary_for_human, style: "margin-top:6px;color:var(--muted)" }),
    ]));
  }
  if (data.final_resolution) {
    col1.appendChild(el("div", { class: "card" }, [
      el("h3", { text: "Final Resolution" }),
      el("div", { text: `Outcome: ${data.final_resolution.outcome}` }),
      el("div", { text: `Root cause: ${data.final_resolution.root_cause}` }),
      el("div", { text: data.final_resolution.root_cause_statement, style: "margin-top:6px;color:var(--muted)" }),
    ]));
  }

  // Evidence
  col2.appendChild(el("div", { class: "card" }, [
    el("h3", { text: `Evidence Collected (${data.evidence_collected.length})` }),
    ...data.evidence_collected.map(ev => el("div", { style: "margin-bottom:6px;font-size:12px" }, [
      el("span", { text: `[${ev.source}] `, style: "color:var(--accent)" }),
      el("span", { text: ev.summary }),
    ])),
  ]));

  // Decisions (concise, evidence-based rationale only)
  col2.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Decisions" }),
    el("div", { class: "note", text: "Evidence-based rationales only -- no hidden reasoning is stored or shown." }),
    ...data.decisions.map(d => el("div", { style: "margin-bottom:8px;font-size:12px" }, [
      el("div", { text: d.decision, style: "font-weight:600" }),
      el("div", { text: d.reason, style: "color:var(--muted)" }),
    ])),
  ]));

  // Verification results
  col2.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Verification Results" }),
    ...(data.verification_results.length
      ? data.verification_results.map(v => el("div", {}, [
          el("span", { class: "badge " + badgeClass(v.outcome === "Passed" ? "ok" : v.outcome === "Failed" ? "danger" : "warn"), text: v.outcome }),
          el("span", { text: " remediation " + v.remediation_action_id, style: "margin-left:6px;color:var(--muted)" }),
        ]))
      : [el("div", { class: "note", text: "No remediation executed -- verification not applicable." })]),
  ]));

  return el("div", { class: "two-col" }, [col1, col2]);
}

// ---------------------------------------------------------------------
// 3. Human Approval Center
// ---------------------------------------------------------------------
async function renderApprovals(force) {
  const approvals = await fetchForView("/api/approvals", force);
  if (approvals === null) return; // unchanged since last poll
  const root = $("#view-approvals");
  root.innerHTML = "";
  if (!approvals.length) {
    root.appendChild(el("div", { class: "empty", text: "No actions currently awaiting approval." }));
    return;
  }
  approvals.forEach(a => {
    const card = el("div", { class: "card approval-card" }, [
      el("div", { class: "row" }, [
        el("div", { text: `${a.proposed_action}`, style: "font-weight:700;font-size:15px" }),
        el("span", { class: "badge " + badgeClass(a.risk_level === "High" ? "danger" : "warn"), text: a.risk_level + " risk" }),
      ]),
      el("div", { text: `Incident: ${a.incident_id}`, style: "color:var(--muted);margin-bottom:6px" }),
      el("div", { text: `Reason: ${a.reason}` }),
      el("div", { text: `Hypothesis: ${a.hypothesis_category} (confidence ${(a.hypothesis_confidence * 100).toFixed(0)}%)`, style: "margin-top:4px" }),
      el("div", { text: `Expected impact: ${a.expected_impact}`, style: "margin-top:4px;color:var(--muted)" }),
    ]);
    if (a.evidence.length) {
      const ev = el("div", { style: "margin-top:6px;font-size:12px" }, [el("div", { text: "Supporting evidence:", style: "color:var(--muted)" })]);
      a.evidence.forEach(e => ev.appendChild(el("div", { text: `- ${e.summary}` })));
      card.appendChild(ev);
    }
    const actions = el("div", { style: "margin-top:12px;display:flex;gap:8px" });
    const approveBtn = el("button", { class: "action", text: "Approve" });
    const rejectBtn = el("button", { class: "action reject", text: "Reject" });
    async function decide(approve) {
      approveBtn.disabled = true; rejectBtn.disabled = true;
      await api(`/api/approvals/${a.approval_request_id}/decide`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve }),
      });
      renderApprovals(true);
    }
    approveBtn.addEventListener("click", () => decide(true));
    rejectBtn.addEventListener("click", () => decide(false));
    actions.appendChild(approveBtn); actions.appendChild(rejectBtn);
    card.appendChild(actions);
    root.appendChild(card);
  });
}

// ---------------------------------------------------------------------
// 4. Analytics
// ---------------------------------------------------------------------
function metricGrid(pairs) {
  return el("div", { class: "metric-grid" }, pairs.map(([label, value]) =>
    el("div", { class: "metric" }, [el("div", { class: "value", text: value }), el("div", { class: "label", text: label })])
  ));
}

async function renderAnalytics() {
  const root = $("#view-analytics");
  root.innerHTML = "";
  const data = await api("/api/analytics");
  const ro = data.real_operational, sb = data.simulated_benchmark;

  root.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Real Operational Metrics" }),
    el("div", { class: "note", text: ro.label }),
    metricGrid([
      ["Total incidents", ro.total_incidents], ["Resolved", ro.resolved], ["Escalated", ro.escalated],
      ["Awaiting approval", ro.awaiting_approval],
      ["Autonomous resolution rate", pct(ro.autonomous_resolution_rate)],
      ["Human intervention rate", pct(ro.human_intervention_rate)],
      ["Verification success rate", pct(ro.verification_success_rate)],
      ["Avg diagnostic steps", num(ro.avg_diagnostic_steps)],
      ["Avg resolution time (min)", num(ro.avg_resolution_time_minutes)],
      ["SLA breaches", ro.sla_breaches],
    ]),
  ]));

  const benchCard = el("div", { class: "card" }, [el("h3", { text: "Simulated Benchmark Metrics" }), el("div", { class: "note", text: sb.label })]);
  if (sb.available) {
    benchCard.appendChild(metricGrid([
      ["Total incidents (benchmark)", sb.total_incidents],
      ["Root-cause accuracy", pct(sb.root_cause_accuracy)],
      ["Autonomous resolution rate", pct(sb.autonomous_resolution_rate)],
      ["Verification success rate", pct(sb.verification_success_rate)],
      ["Escalation appropriateness", pct(sb.escalation_appropriateness)],
      ["Avg diagnostic steps", num(sb.avg_diagnostic_steps)],
      ["Avg resolution time (min)", num(sb.avg_resolution_time_minutes)],
      ["Human intervention rate", pct(sb.human_intervention_rate)],
      ["False remediation rate", pct(sb.false_remediation_rate)],
      ["SLA breach rate", pct(sb.sla_breach_rate)],
    ]));
  } else {
    benchCard.appendChild(el("div", { class: "empty", text: sb.note }));
  }
  root.appendChild(benchCard);
}

// ---------------------------------------------------------------------
// 5. Agent Performance
// ---------------------------------------------------------------------
async function renderPerformance() {
  const root = $("#view-performance");
  root.innerHTML = "";
  const data = await api("/api/performance");

  root.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Tool Usage (all real runs seen by this dashboard)" }),
    el("table", {}, [
      el("thead", {}, [el("tr", {}, ["Tool", "Success", "Failed"].map(h => el("th", { text: h })))]),
      el("tbody", {}, Object.entries(data.tool_usage).map(([tool, c]) =>
        el("tr", {}, [el("td", { text: tool }), el("td", { text: c.success }), el("td", { text: c.error })])
      )),
    ]),
  ]));

  root.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Escalation Rate (real runs)" }),
    metricGrid([["Escalation rate", pct(data.escalation_rate_real)]]),
  ]));

  root.appendChild(el("div", { class: "card" }, [
    el("h3", { text: "Most Common Root Causes (real runs)" }),
    ...(data.most_common_root_causes_real.length
      ? data.most_common_root_causes_real.map(([cat, count]) => el("div", { text: `${cat}: ${count}` }))
      : [el("div", { class: "empty", text: "No resolved incidents yet." })]),
  ]));

  const benchCard = el("div", { class: "card" }, [el("h3", { text: "Resolution Accuracy & False Remediation (from benchmark testing)" })]);
  if (data.benchmark.available) {
    benchCard.appendChild(metricGrid([
      ["Resolution accuracy", pct(data.benchmark.resolution_accuracy)],
      ["False remediation rate", pct(data.benchmark.false_remediation_rate)],
      ["Tool selection accuracy", pct(data.benchmark.tool_selection_accuracy)],
    ]));
  } else {
    benchCard.appendChild(el("div", { class: "empty", text: "Run scripts/build_benchmark.py + scripts/run_benchmark.py to populate this." }));
  }
  root.appendChild(benchCard);
}

// ---------------------------------------------------------------------
// Polling: keep queue/approvals fresh while visible (real state changes as
// live orchestrator threads progress in the background).
// ---------------------------------------------------------------------
async function pollingRerender(renderFn) {
  // Polled re-renders rebuild the view's DOM from scratch (innerHTML = ""),
  // which resets scroll position to the top and causes visible flicker even
  // when nothing changed. Save/restore scroll across the rebuild so a poll
  // tick doesn't yank the page back to the top while someone is reading or
  // scrolling.
  const scrollY = window.scrollY;
  await renderFn();
  window.scrollTo(0, scrollY);
}

// Investigation polls faster than the other views: dashboard-launched runs
// are deliberately paced (~0.9s/step, agent/orchestrator.py's
// step_delay_seconds) specifically so an operator watching this view sees
// each step land, and a 3s poll would miss/batch several of them together.
setInterval(() => {
  if (document.hidden) return; // don't churn the DOM in a background tab
  if (state.view === "queue") pollingRerender(renderQueue);
  else if (state.view === "approvals") pollingRerender(renderApprovals);
  refreshApprovalBadge();
}, 3000);

setInterval(() => {
  if (document.hidden) return;
  if (state.view === "investigation") pollingRerender(renderInvestigation);
}, 700);

render();
