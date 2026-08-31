"use strict";
// Operator console (SPEC §9). Vanilla JS, no framework. Four views over the API.

const TIERS = { 0: "ASSIST", 1: "BOUNDED", 2: "SUPERVISED" };
const TABS = [
  ["dashboard", "Autonomy"],
  ["queue", "Review queue"],
  ["runs", "Runs"],
  ["ledger", "Trust ledger"],
  ["outcomes", "Outcomes"],
  ["security", "Security"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x) => `${(x * 100).toFixed(1)}%`;
const get = (u) => fetch(u).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));

function tierBadge(t) {
  const cls = { 0: "bg-neutral-200 text-neutral-700", 1: "bg-accent-soft text-accent", 2: "bg-green-100 text-green-700" }[t];
  return `<span class="badge ${cls}">T${t} · ${TIERS[t]}</span>`;
}
function standingBadge(s) {
  const map = {
    active: "bg-green-100 text-green-700",
    probation: "bg-amber-100 text-amber-800",
    investigation_required: "bg-red-100 text-red-700",
  };
  return `<span class="badge ${map[s] || "bg-neutral-200"}">${esc(s)}</span>`;
}

// ---- Dashboard (view 1) ----------------------------------------------------
function reasonFor(d) {
  if (d.standing === "probation") return "On probation — must pass the golden-subset challenge to be restored.";
  if (d.standing === "investigation_required") return "Probation failed — flagged for investigation, no auto re-promotion.";
  if (d.blocked_by_ceiling) return "At the brand's max allowed tier — promotion is capped here.";
  if (d.cooldown_remaining > 0) return `In cooldown: ${d.cooldown_remaining} more runs before promotion is eligible.`;
  if (d.next_tier === null) return "At the top tier the framework supports.";
  if (d.runs_to_min > 0) return `Needs ${d.runs_to_min} more runs before the ${d.min_runs}-run minimum is met.`;
  if (d.gate_met) return "Gate cleared — promotes on the next run.";
  return `Wilson lower bound ${d.wilson_lower_bound.toFixed(3)} vs threshold ${d.threshold?.toFixed(2)}.`;
}

async function renderDashboard() {
  const data = await get("/api/dashboard");
  const cards = data
    .map((d) => {
      const frac = d.threshold ? Math.min(1, d.wilson_lower_bound / d.threshold) : 0;
      const gate =
        d.next_tier === null
          ? `<div class="text-neutral-400">No higher tier.</div>`
          : `<div class="flex justify-between text-neutral-500"><span>Wilson lower bound → T${d.next_tier}</span>
               <span class="font-mono">${d.wilson_lower_bound.toFixed(3)} / ${d.threshold?.toFixed(2)}</span></div>
             <div class="meter my-1"><span style="width:${(frac * 100).toFixed(0)}%; background:${d.gate_met ? "#16a34a" : "#2563eb"}"></span></div>
             <div class="flex justify-between text-neutral-400">
               <span>runs ${d.successes_in_window}/${d.runs_in_window} in last ${d.window}</span>
               <span>${d.gate_met ? "gate met" : d.runs_to_min > 0 ? `${d.runs_to_min} to min` : "below threshold"}</span></div>`;
      return `<div class="card p-3">
        <div class="flex items-center justify-between">
          <span class="font-semibold">${esc(d.campaign_type)}</span>
          ${tierBadge(d.tier)}
        </div>
        <div class="mt-1 mb-2 flex gap-1">${standingBadge(d.standing)}${
          d.effective_tier !== d.tier ? `<span class="badge bg-neutral-100 text-neutral-500">capped→T${d.effective_tier}</span>` : ""
        }</div>
        ${gate}
        <div class="mt-2 border-t border-neutral-100 pt-2 text-neutral-600"><span class="text-neutral-400">Why: </span>${esc(reasonFor(d))}</div>
      </div>`;
    })
    .join("");
  document.getElementById("view-dashboard").innerHTML =
    `<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">${cards}</div>`;
}

// ---- Review queue (view 2) -------------------------------------------------
function slaTag(d) {
  if (d.escalated) return `<span class="badge bg-amber-100 text-amber-800">SLA · escalated</span>`;
  return `<span class="text-neutral-400">${pct(d.fraction_elapsed)} elapsed</span>`;
}
function itemRow(d) {
  const it = d.item;
  const flags = [
    ...it.critical_flags.map((f) => `<span class="badge bg-red-100 text-red-700">${esc(f)}</span>`),
    ...it.constraint_codes.map((c) => `<span class="badge bg-amber-100 text-amber-800">${esc(c)}</span>`),
  ].join(" ");
  return `<div class="row flex items-center gap-3 border-b border-neutral-100 px-2 py-1.5" data-run="${esc(it.run_id)}">
      <input type="checkbox" class="batch-check" value="${esc(it.run_id)}" onclick="event.stopPropagation()" />
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2"><span class="font-medium">${esc(it.campaign_type)}</span>
          <span class="text-neutral-400">${esc(it.segment)}</span>${flags}</div>
        <div class="truncate text-neutral-500">${esc(it.rationale?.[0] || "")}</div>
      </div>
      <div class="text-right"><div class="font-mono">risk ${d.risk_score.toFixed(2)}</div><div>${slaTag(d)}</div></div>
    </div>`;
}

async function renderQueue() {
  const v = await get("/api/queue");
  const batchIds = v.batch.map((d) => d.item.run_id);
  document.getElementById("view-queue").innerHTML = `
    <div class="grid gap-4 lg:grid-cols-2">
      <div class="card">
        <div class="flex items-center justify-between border-b border-neutral-200 px-3 py-2">
          <div><span class="font-semibold">Batch lane</span> <span class="text-neutral-400">${v.batch.length} · look-alikes, approve as a group</span></div>
          <button id="approve-all" class="badge bg-accent text-white px-2 py-1">Approve all</button>
        </div>
        <div>${v.batch.map(itemRow).join("") || '<div class="p-3 text-neutral-400">Empty.</div>'}</div>
      </div>
      <div class="card">
        <div class="border-b border-neutral-200 px-3 py-2"><span class="font-semibold">Judgment lane</span>
          <span class="text-neutral-400">${v.judgment.length} · sorted by risk</span></div>
        <div>${v.judgment.map(itemRow).join("") || '<div class="p-3 text-neutral-400">Empty.</div>'}</div>
      </div>
    </div>
    ${v.newly_expired.length ? `<div class="mt-3 text-neutral-400">${v.newly_expired.length} item(s) expired and left the queue this refresh.</div>` : ""}`;

  document.getElementById("approve-all").onclick = async () => {
    await fetch("/api/queue/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_ids: batchIds }),
    });
    renderQueue();
  };
  document.querySelectorAll("#view-queue .row").forEach((r) => (r.onclick = () => openRun(r.dataset.run)));
}

// ---- Runs + Run detail (view 3) -------------------------------------------
async function renderRuns() {
  const runs = await get("/api/runs");
  const rows = runs
    .map(
      (r) => `<tr class="row" data-run="${esc(r.run_id)}">
        <td class="font-mono">${esc(r.run_id)}</td><td>${esc(r.campaign_type)}</td>
        <td>${r.evaluation.dimensions ? "" : ""}<span class="badge ${r.decision.decision === "auto_send" ? "bg-green-100 text-green-700" : "bg-neutral-200 text-neutral-700"}">${esc(r.decision.decision)}</span></td>
        <td>${r.decision.passed ? "✓" : "✗"}</td>
        <td>${tierBadge(r.decision.effective_tier)}</td>
        <td class="text-neutral-400">${esc(r.created_at.slice(0, 16).replace("T", " "))}</td></tr>`
    )
    .join("");
  document.getElementById("view-runs").innerHTML = `<div class="card p-2"><table>
    <thead><tr><th>run</th><th>type</th><th>decision</th><th>pass</th><th>tier</th><th>when</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="text-neutral-400">No runs.</td></tr>'}</tbody></table></div>`;
  document.querySelectorAll("#view-runs .row").forEach((r) => (r.onclick = () => openRun(r.dataset.run)));
}

function closeDrawer() {
  document.getElementById("drawer").classList.add("hidden");
  document.getElementById("scrim").classList.add("hidden");
}
async function openRun(runId) {
  const drawer = document.getElementById("drawer");
  let r;
  try {
    r = await get(`/api/runs/${encodeURIComponent(runId)}`);
  } catch {
    drawer.innerHTML = `<div class="p-4"><button onclick="closeDrawer()" class="badge bg-neutral-200 mb-3">Close</button>
      <div class="text-neutral-500">No full trace stored for <span class="font-mono">${esc(runId)}</span> (a queued item without a recorded run).</div></div>`;
    show(drawer);
    return;
  }
  const dims = Object.values(r.evaluation.dimensions)
    .map(
      (d) => `<tr><td>${esc(d.dimension)}</td>
        <td><span class="badge ${d.verdict === "pass" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}">${esc(d.verdict)}</span></td>
        <td class="font-mono">${d.score.toFixed(2)}</td>
        <td class="text-neutral-500">${esc(d.reasoning)}${d.evidence?.length ? `<div class="text-neutral-400">${d.evidence.map(esc).join("; ")}</div>` : ""}</td></tr>`
    )
    .join("");
  const c = r.content;
  drawer.innerHTML = `<div class="p-4 space-y-4">
    <div class="flex items-center justify-between">
      <div><span class="font-semibold">Run ${esc(r.run_id)}</span> <span class="text-neutral-400">${esc(r.campaign_type)}</span></div>
      <button onclick="closeDrawer()" class="badge bg-neutral-200">Close</button>
    </div>
    <div class="card p-3">
      <div class="mb-1 text-neutral-400">Controller decision</div>
      <div class="flex items-center gap-2">
        <span class="badge ${r.decision.decision === "auto_send" ? "bg-green-100 text-green-700" : "bg-neutral-200 text-neutral-700"}">${esc(r.decision.decision)}</span>
        ${tierBadge(r.decision.effective_tier)}
        ${r.decision.promoted ? '<span class="badge bg-green-100 text-green-700">promoted</span>' : ""}
        ${r.decision.demoted ? '<span class="badge bg-red-100 text-red-700">demoted</span>' : ""}
        <span class="text-neutral-400">revisions: ${r.revisions}</span>
      </div>
      <ul class="mt-2 list-disc pl-5 text-neutral-600">${r.decision.rationale.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    </div>
    ${c ? `<div class="card p-3"><div class="mb-1 text-neutral-400">Campaign</div>
      <div class="font-medium">${esc(c.subject)}</div><div class="text-neutral-600">${esc(c.body)}</div>
      <div class="mt-1 text-neutral-400">→ ${esc(c.cta_text)} · ${esc(c.cta_url)}</div></div>` : ""}
    <div class="card p-2"><div class="px-1 pb-1 text-neutral-400">Dimensions</div>
      <table><thead><tr><th>dimension</th><th>verdict</th><th>score</th><th>reasoning</th></tr></thead><tbody>${dims}</tbody></table></div>
  </div>`;
  show(drawer);
}
function show(drawer) {
  drawer.classList.remove("hidden");
  const scrim = document.getElementById("scrim");
  scrim.classList.remove("hidden");
  scrim.onclick = closeDrawer;
}

// ---- Trust ledger (view 4) ------------------------------------------------
async function renderLedger() {
  const { transitions } = await get("/api/ledger");
  const rows = transitions
    .map((t) => {
      const ev = Object.entries(t.evidence || {})
        .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
        .join(", ");
      return `<tr><td class="text-neutral-400">${esc(t.ts.slice(0, 19).replace("T", " "))}</td>
        <td>${esc(t.campaign_type)}</td>
        <td><span class="badge bg-neutral-100 text-neutral-700">${esc(t.reason)}</span></td>
        <td>T${t.from_tier} → T${t.to_tier}</td>
        <td>${esc(t.standing_after)}</td>
        <td class="font-mono text-neutral-500">${esc(ev)}</td></tr>`;
    })
    .join("");
  document.getElementById("view-ledger").innerHTML = `<div class="card p-2"><table>
    <thead><tr><th>when</th><th>type</th><th>reason</th><th>tier</th><th>standing</th><th>evidence</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="text-neutral-400">No tier changes yet.</td></tr>'}</tbody></table></div>`;
}

// ---- Outcomes / post-send loop (view) -------------------------------------
const CAMPAIGN_TYPES = [
  "newsletter",
  "promotional_discount",
  "product_launch",
  "winback",
  "restock_alert",
];
const SCENARIOS = ["nominal", "judge_blindspot", "degrading"];

async function runSimulation() {
  const btn = document.getElementById("sim-run");
  const type = document.getElementById("sim-type").value;
  const scenario = document.getElementById("sim-scenario").value;
  btn.disabled = true;
  btn.textContent = "Running…";
  const res = await fetch("/api/simulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ campaign_type: type, n: 8, scenario }),
  }).then((r) => r.json());
  // Refresh the dashboard underneath so the tier change is visible, then re-render.
  await renderDashboard();
  await renderOutcomes();
  const note = document.getElementById("sim-note");
  if (note)
    note.textContent =
      `Ran ${res.auto_sent} auto-sends · ${res.breaches} breaches · ` +
      `${res.demotions} demotions · ${res.blind_spots} judge blind spots found.`;
}

async function renderOutcomes() {
  const data = await get("/api/outcomes");
  const cards = data
    .map((d) => {
      const bars = d.timeline
        .map(
          (t) =>
            `<span title="${esc(t.ts)} spam=${(t.spam * 100).toFixed(3)}%" style="display:inline-block;width:8px;height:18px;margin-right:2px;border-radius:2px;background:${
              t.blind_spot ? "#b91c1c" : t.breached ? "#f59e0b" : "#16a34a"
            }"></span>`
        )
        .join("");
      return `<div class="card p-3">
        <div class="flex items-center justify-between">
          <span class="font-semibold">${esc(d.campaign_type)}</span>
          <span class="text-neutral-400">${d.sent} sent</span></div>
        <div class="mt-1 flex justify-between text-neutral-500">
          <span>predicted quality (Wilson) <b>${d.predicted_pass_rate}</b></span>
          <span>actual clean rate <b>${d.actual_clean_rate}</b></span></div>
        <div class="mt-2">${bars || '<span class="text-neutral-400">no sends</span>'}</div>
        <div class="mt-2 text-neutral-600">breaches <b>${d.breaches}</b> ·
          <span class="text-red-700">judge blind spots found <b>${d.blind_spots}</b></span></div>
      </div>`;
    })
    .join("");
  const opts = (arr, sel) =>
    arr.map((v) => `<option value="${v}"${v === sel ? " selected" : ""}>${v}</option>`).join("");
  document.getElementById("view-outcomes").innerHTML = `
    <div class="card p-3 mb-4">
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold">Run simulation</span>
        <select id="sim-type" class="border border-neutral-300 rounded px-2 py-1">${opts(CAMPAIGN_TYPES, "newsletter")}</select>
        <select id="sim-scenario" class="border border-neutral-300 rounded px-2 py-1">${opts(SCENARIOS, "judge_blindspot")}</select>
        <button id="sim-run" class="badge bg-accent text-white px-3 py-1">Run 8 campaigns</button>
        <span id="sim-note" class="text-neutral-500"></span>
      </div>
      <div class="mt-1 text-neutral-400">Sends N campaigns at the current tier, simulates real
        deliverability, and feeds breaches back into standing. A run that passed every eval but
        breached is flagged a <span class="text-red-700">judge blind spot</span> and added to
        golden-set candidates.</div>
    </div>
    <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">${cards || '<div class="text-neutral-400">No outcomes yet — run a simulation.</div>'}</div>`;
  document.getElementById("sim-run").onclick = runSimulation;
}

// ---- Security + monitoring (view 5) ---------------------------------------
async function renderSecurity() {
  const [sec, mon] = await Promise.all([get("/api/security"), get("/api/monitoring")]);
  const rows = sec.events
    .map(
      (e) => `<tr><td class="text-neutral-400">${esc(e.ts.slice(0, 19).replace("T", " "))}</td>
        <td>${esc(e.campaign_type)}</td>
        <td><span class="badge bg-amber-100 text-amber-800">${esc(e.event_type)}</span></td>
        <td>${e.resisted ? '<span class="badge bg-green-100 text-green-700">resisted</span>' : ""}</td>
        <td class="text-neutral-500">${esc(e.detail)}</td></tr>`
    )
    .join("");
  const total = mon.brief_instructed + mon.agent_originated + mon.unclassified;
  document.getElementById("view-security").innerHTML = `
    <div class="grid gap-4 lg:grid-cols-3">
      <div class="card p-3"><div class="text-neutral-400">Resisted attacks</div>
        <div class="text-2xl font-semibold">${sec.count}</div>
        <div class="text-neutral-500">Logged even when the run otherwise succeeded.</div></div>
      <div class="card p-3 lg:col-span-2"><div class="text-neutral-400">M1 · quality-failure origin</div>
        <div class="mt-1">brief-instructed <b>${mon.brief_instructed}</b> · agent-originated
          <b>${mon.agent_originated}</b> · unclassified <b>${mon.unclassified}</b> (of ${total})</div>
        <div class="text-neutral-500 mt-1">If brief-instructed failures dominate, the evasion rule
          (GS-PD-16) is reclassified to a constraint block — see docs/autonomy-model.md.</div></div>
    </div>
    <div class="card p-2 mt-4"><table>
      <thead><tr><th>when</th><th>type</th><th>event</th><th></th><th>detail</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="text-neutral-400">No security events.</td></tr>'}</tbody>
    </table></div>`;
}

// ---- Shell ----------------------------------------------------------------
const RENDER = {
  dashboard: renderDashboard,
  queue: renderQueue,
  runs: renderRuns,
  ledger: renderLedger,
  outcomes: renderOutcomes,
  security: renderSecurity,
};
function switchTab(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  document.getElementById(`view-${name}`).classList.remove("hidden");
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  RENDER[name]();
}
function init() {
  const tabs = document.getElementById("tabs");
  tabs.innerHTML = TABS.map(([k, label]) => `<span class="tab" data-tab="${k}">${label}</span>`).join("");
  tabs.querySelectorAll(".tab").forEach((t) => (t.onclick = () => switchTab(t.dataset.tab)));
  document.getElementById("clock").textContent = new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC";
  switchTab("dashboard");
}
window.closeDrawer = closeDrawer;
init();
