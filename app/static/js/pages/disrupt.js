/**
 * Disruption Console Page View Controller (Step 4)
 * 
 * - Time scrubber under Gantt synchronized with "now" line
 * - Event form with explicit fields (machine, start time, duration) for 4 incident types
 * - Event history as chips with Undo and Reset
 * - Impact card: cost change with color & sign, jobs affected, target status, moved-job count, 1-line explanation
 * - Rescheduled Gantt with ghost bars of prior plan and diagonal hatching for failed machines
 * - Diff table grouped by reason with counts, filters, clock times, and per-job cost deltas
 * - Target at Risk alert banner with unplaced jobs
 */

import { api } from "../api.js";
import { store, getTariffForRequest } from "../state.js";
import { formatMoney, formatNumber, slotToClock, slotRangeToClock } from "../format.js";
import { renderGantt } from "../charts.js";

// Module state for history, active filters, and current form
let eventHistory = [];
let activeIncidentType = "breakdown"; // "breakdown" | "spike" | "rush" | "delay"
let activeDiffFilter = "all";         // "all" | "rerouted" | "moved" | "unplaced"
let lastActiveEvent = null;           // Most recent event details for chart options

export function initDisruptPage() {
  const container = document.getElementById("page-disrupt");
  if (!container) return;

  renderTemplate(container);
  bindEvents(container);
  updateContent();

  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#disrupt") {
      requestAnimationFrame(updateContent);
    }
  });

  store.subscribe(() => {
    if (window.location.hash === "#disrupt") {
      updateContent();
    }
  });

  // Handle URL auto-disruption query parameter
  const urlParams = new URLSearchParams(window.location.search);
  const autoParam = urlParams.get("disrupt");
  if (autoParam) {
    let handled = false;
    store.subscribe(async () => {
      const s = store.get();
      if (!handled && s.plant && s.tariff) {
        handled = true;
        if (!s.lastResult) {
          try {
            const optRes = await api.optimize({
              plant: s.plant,
              tariff: s.tariff,
              config: s.config,
            });
            store.set({ lastResult: optRes });
          } catch (e) {
            console.error("Auto optimize failed:", e);
            return;
          }
        }
        await triggerAutoDisruption(autoParam);
      }
    });
  }
}

async function triggerAutoDisruption(param) {
  const s = store.get();
  if (!s.plant || !s.tariff || !s.lastResult) return;

  const slotMin = s.plant.slot_minutes || 30;
  const nowSlot = 12;

  const slotSlider = document.getElementById("disrupt-now-slot");
  if (slotSlider) {
    slotSlider.value = String(nowSlot);
    const badge = document.getElementById("scrubber-badge");
    if (badge) badge.textContent = `${slotToClock(nowSlot, slotMin)} · Slot ${nowSlot}`;
  }

  if (param === "m3_down") {
    activeIncidentType = "breakdown";
    lastActiveEvent = { type: "breakdown", machineId: "M3", fromSlot: nowSlot };
    const payload = {
      type: "machine_down",
      machine_id: "M3",
      from_slot: nowSlot,
      until_slot: s.plant.horizon_slots,
    };
    await executeReplan(payload, nowSlot, `M3 down @ 06:00`);

  } else if (param === "risk") {
    activeIncidentType = "rush";
    lastActiveEvent = { type: "rush" };
    const payload = {
      type: "rush_job",
      job: {
        id: "RUSH_01",
        duration_slots: 36,
        release_slot: nowSlot,
        deadline_slot: Math.min(nowSlot + 12, s.plant.horizon_slots),
        allowed_machines: s.plant.machines.map(m => m.id),
        priority: 10,
        units: 24,
        mandatory: true,
      },
    };
    await executeReplan(payload, nowSlot, `+RUSH_01 (36 slots)`);
  }
}

function renderTemplate(container) {
  const horizon = 48;

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1 class="page-title">Disruption console</h1>
        <p class="page-desc">Simulate shop-floor disruptions with dynamic re-planning, time scrubbing, and live impact auditing.</p>
      </div>
      <div>
        <span id="disrupt-status-badge" class="badge badge-info">Status: Ready</span>
      </div>
    </div>

    <!-- Alert Banner for Infeasible / At-Risk Schedules -->
    <div id="disrupt-risk-banner" class="banner banner-danger" style="display:none; margin-bottom:1.5rem;" aria-live="assertive">
      <div style="display:flex; align-items:flex-start; gap:0.75rem;">
        <span style="font-size:1.25rem; line-height:1;">⚠️</span>
        <div>
          <div style="font-weight:700; font-size:1rem; margin-bottom:2px;">Target at risk: Capacity shortfall detected</div>
          <div id="disrupt-risk-desc" style="font-size:0.875rem;">Production targets cannot be fully satisfied within the current shift horizon.</div>
          <div id="disrupt-risk-unplaced" style="margin-top:0.5rem; display:flex; gap:0.5rem; flex-wrap:wrap;"></div>
        </div>
      </div>
    </div>

    <!-- Event History Chips Bar -->
    <div class="event-history-bar" id="disrupt-history-bar" style="display:none;">
      <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
        <span style="font-size:var(--text-xs); font-weight:700; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.04em;">Active disruptions:</span>
        <div class="event-history-list" id="disrupt-history-chips"></div>
      </div>
      <div class="event-history-actions">
        <button class="btn btn-secondary btn-sm" id="btn-undo-event" title="Undo the most recent disruption event">
          <span>↺ Undo last</span>
        </button>
        <button class="btn btn-secondary btn-sm" id="btn-reset-baseline" title="Reset back to baseline optimized schedule">
          <span>Reset to baseline</span>
        </button>
      </div>
    </div>

    <!-- 1. Event Injection Form Card -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem; flex-wrap:wrap; gap:0.5rem;">
        <h2 class="card-title" style="margin-bottom:0;">Simulate shop-floor incident</h2>
        <span style="font-size:var(--text-xs); color:var(--text-secondary);">Jobs started before the scrubber time are frozen</span>
      </div>

      <!-- Incident Type Tabs -->
      <div class="incident-type-nav" role="tablist">
        <button class="incident-tab-btn active" data-type="breakdown" role="tab" id="tab-btn-breakdown">
          <span>⚡ Machine breakdown</span>
        </button>
        <button class="incident-tab-btn" data-type="spike" role="tab" id="tab-btn-spike">
          <span>📈 Tariff spike (1.5×)</span>
        </button>
        <button class="incident-tab-btn" data-type="rush" role="tab" id="tab-btn-rush">
          <span>🚨 Rush order</span>
        </button>
        <button class="incident-tab-btn" data-type="delay" role="tab" id="tab-btn-delay">
          <span>⏱ Job delay</span>
        </button>
      </div>

      <!-- Form Fields (Dynamically switched) -->
      <div id="incident-form-container">
        <!-- Breakdown Fields -->
        <div class="incident-pane" id="pane-breakdown">
          <div class="grid-3" style="gap:1rem; align-items:flex-end;">
            <div>
              <label class="form-label" for="select-failed-machine">Machine to fail</label>
              <div id="machine-select-container">
                <select class="form-control font-mono" id="select-failed-machine" disabled>
                  <option value="">Loading machines…</option>
                </select>
              </div>
            </div>
            <div>
              <label class="form-label" for="input-breakdown-start">Breakdown start time</label>
              <input type="text" class="form-control font-mono" id="input-breakdown-start" readonly value="06:00 (Slot 12)">
            </div>
            <div>
              <label class="form-label" for="select-breakdown-duration">Offline duration</label>
              <select class="form-control font-mono" id="select-breakdown-duration">
                <option value="shift_end" selected>Until shift end (24:00)</option>
                <option value="8">4 hours (8 slots)</option>
                <option value="16">8 hours (16 slots)</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Tariff Spike Fields -->
        <div class="incident-pane" id="pane-spike" style="display:none;">
          <div class="grid-3" style="gap:1rem; align-items:flex-end;">
            <div>
              <label class="form-label" for="select-spike-multiplier">Rate multiplier</label>
              <select class="form-control font-mono" id="select-spike-multiplier">
                <option value="1.5" selected>1.50× surcharge (+50%)</option>
                <option value="1.75">1.75× surcharge (+75%)</option>
                <option value="2.0">2.00× peak spike (+100%)</option>
              </select>
            </div>
            <div>
              <label class="form-label" for="input-spike-start">Spike effective from</label>
              <input type="text" class="form-control font-mono" id="input-spike-start" readonly value="06:00 (Slot 12)">
            </div>
            <div>
              <label class="form-label" for="select-spike-window">Affected tariff window</label>
              <select class="form-control font-mono" id="select-spike-window">
                <option value="all_peak" selected>All peak & evening hours</option>
                <option value="entire_day">Entire remainder of day</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Rush Order Fields -->
        <div class="incident-pane" id="pane-rush" style="display:none;">
          <div class="grid-4" style="gap:1rem; align-items:flex-end;">
            <div>
              <label class="form-label" for="input-rush-id">Job ID</label>
              <input type="text" class="form-control font-mono" id="input-rush-id" value="RUSH_01">
            </div>
            <div>
              <label class="form-label" for="input-rush-dur">Duration (slots)</label>
              <input type="number" class="form-control font-mono" id="input-rush-dur" min="1" max="12" value="4">
            </div>
            <div>
              <label class="form-label" for="input-rush-release">Ready from</label>
              <input type="text" class="form-control font-mono" id="input-rush-release" readonly value="06:00 (Slot 12)">
            </div>
            <div>
              <label class="form-label" for="input-rush-deadline">Strict deadline</label>
              <input type="text" class="form-control font-mono" id="input-rush-deadline" readonly value="12:00 (Slot 24)">
            </div>
          </div>
        </div>

        <!-- Job Delay Fields -->
        <div class="incident-pane" id="pane-delay" style="display:none;">
          <div class="grid-3" style="gap:1rem; align-items:flex-end;">
            <div>
              <label class="form-label" for="select-delay-job">Target job</label>
              <select class="form-control font-mono" id="select-delay-job">
                <option value="">Loading jobs…</option>
              </select>
            </div>
            <div>
              <label class="form-label" for="input-delay-slots">Added delay</label>
              <select class="form-control font-mono" id="input-delay-slots">
                <option value="4" selected>+4 slots (2 hours)</option>
                <option value="6">+6 slots (3 hours)</option>
                <option value="8">+8 slots (4 hours)</option>
              </select>
            </div>
            <div>
              <label class="form-label">Impact</label>
              <div style="font-size:var(--text-xs); color:var(--text-secondary); padding-bottom:10px;">Downstream jobs re-evaluate sequence</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Action Button -->
      <div style="margin-top:1.25rem; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:1rem;">
        <button class="btn btn-primary" id="btn-trigger-replan">
          <span>Apply disruption & re-plan</span>
        </button>
        <div id="disrupt-incident-banner" style="font-size:var(--text-xs); color:#f87171; font-weight:600; display:none;"></div>
      </div>
    </div>

    <!-- 2. Impact Card (Cost change, Jobs affected, Target status, Moved count, 1-line explanation) -->
    <div class="disrupt-impact-card" id="disrupt-impact-container" style="display:none;">
      <div class="impact-kpi-grid">
        <div class="impact-kpi-item">
          <span class="impact-kpi-label">Energy cost change</span>
          <span class="impact-kpi-val" id="impact-cost-change">--</span>
        </div>
        <div class="impact-kpi-item">
          <span class="impact-kpi-label">Jobs affected</span>
          <span class="impact-kpi-val" id="impact-jobs-affected" style="color:var(--text-primary);">0</span>
        </div>
        <div class="impact-kpi-item">
          <span class="impact-kpi-label">Production target</span>
          <div id="impact-target-badge" style="margin-top:4px;">
            <span class="badge badge-success">Target Met ✓</span>
          </div>
        </div>
        <div class="impact-kpi-item">
          <span class="impact-kpi-label">Jobs relocated</span>
          <span class="impact-kpi-val" id="impact-moved-count" style="color:#38bdf8;">0</span>
        </div>
      </div>
      <div class="impact-explanation" id="impact-explanation-text">
        <span>●</span>
        <span id="impact-explanation-content">Awaiting initial disruption simulation.</span>
      </div>
    </div>

    <!-- 3. Rescheduled Gantt Chart & Time Scrubber -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:0.75rem; flex-wrap:wrap; gap:0.5rem;">
        <h2 class="card-title" style="margin-bottom:0;">Rescheduled machine allocation</h2>
        <div style="display:flex; align-items:center; gap:0.75rem; font-size:var(--text-xs); color:var(--text-secondary);">
          <span><span style="display:inline-block; width:12px; height:12px; border:1.5px dashed #94a3b8; background:rgba(148,163,184,0.15); vertical-align:middle; margin-right:4px;"></span>Prior plan (Ghost)</span>
          <span><span style="display:inline-block; width:12px; height:12px; background:#f87171; vertical-align:middle; margin-right:4px;"></span>Breakdown</span>
          <span><span style="display:inline-block; width:12px; height:12px; border:2px solid #38bdf8; background:transparent; vertical-align:middle; margin-right:4px;"></span>Moved</span>
        </div>
      </div>

      <div id="chart-gantt-disrupt" class="chart-container" style="min-height:320px;">
        <div class="loading-overlay">Select an incident above to simulate real-time re-planning.</div>
      </div>

      <!-- Time Scrubber directly under Gantt with NOW Line -->
      <div class="time-scrubber-container">
        <div class="scrubber-header">
          <div class="scrubber-title">Execution time scrubber (<span class="font-mono">now_slot</span>)</div>
          <div class="scrubber-pill" id="scrubber-badge">06:00 · Slot 12</div>
        </div>
        <input type="range" class="form-range" id="disrupt-now-slot" min="0" max="${horizon - 1}" step="1" value="12" style="width:100%; cursor:ew-resize;">
        <div class="scrubber-ticks">
          <span>00:00 (S0)</span>
          <span>04:00 (S8)</span>
          <span>08:00 (S16)</span>
          <span>12:00 (S24)</span>
          <span>16:00 (S32)</span>
          <span>20:00 (S40)</span>
          <span>24:00 (S48)</span>
        </div>
      </div>
    </div>

    <!-- 4. Schedule Diff Table grouped by reason with counts & filters -->
    <div class="card">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem; flex-wrap:wrap; gap:0.5rem;">
        <h2 class="card-title" style="margin-bottom:0;">Schedule diff & relocation audit</h2>
        <!-- Filter Tabs Grouped by Reason -->
        <div class="diff-filter-nav" role="tablist">
          <button class="diff-filter-btn active" data-filter="all" id="diff-filter-all">All (<span id="count-diff-all">0</span>)</button>
          <button class="diff-filter-btn" data-filter="rerouted" id="diff-filter-rerouted">Machine down (<span id="count-diff-rerouted">0</span>)</button>
          <button class="diff-filter-btn" data-filter="moved" id="diff-filter-moved">Moved by re-plan (<span id="count-diff-moved">0</span>)</button>
          <button class="diff-filter-btn" data-filter="unplaced" id="diff-filter-unplaced">Unplaced (<span id="count-diff-unplaced">0</span>)</button>
        </div>
      </div>

      <div class="table-responsive" style="max-height: 440px;">
        <table class="data-table">
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Action / Reason</th>
              <th>Original placement</th>
              <th>Rescheduled placement</th>
              <th style="text-align:right;">Cost impact</th>
            </tr>
          </thead>
          <tbody id="disrupt-diff-tbody">
            <tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:2rem 0;">No disruption active. Apply an incident above to audit relocation diffs.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function bindEvents(container) {
  // 1. Incident Type Tabs
  const tabs = container.querySelectorAll(".incident-tab-btn");
  tabs.forEach(btn => {
    btn.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      btn.classList.add("active");
      activeIncidentType = btn.dataset.type;

      // Switch pane visibility
      ["breakdown", "spike", "rush", "delay"].forEach(t => {
        const pane = container.querySelector(`#pane-${t}`);
        if (pane) pane.style.display = (t === activeIncidentType) ? "block" : "none";
      });
    });
  });

  // 2. Time Scrubber Slider Input
  const slotSlider = container.querySelector("#disrupt-now-slot");
  const scrubberBadge = container.querySelector("#scrubber-badge");

  slotSlider?.addEventListener("input", (e) => {
    const s = store.get();
    const slotMin = s.plant?.slot_minutes || 30;
    const val = parseInt(e.target.value, 10);
    const clock = slotToClock(val, slotMin);

    if (scrubberBadge) {
      scrubberBadge.textContent = `${clock} · Slot ${val}`;
    }

    // Update synced start inputs across incident panes
    const bdStart = container.querySelector("#input-breakdown-start");
    if (bdStart) bdStart.value = `${clock} (Slot ${val})`;

    const spkStart = container.querySelector("#input-spike-start");
    if (spkStart) spkStart.value = `${clock} (Slot ${val})`;

    const rshStart = container.querySelector("#input-rush-release");
    if (rshStart) rshStart.value = `${clock} (Slot ${val})`;

    const rshDead = container.querySelector("#input-rush-deadline");
    const deadVal = Math.min(val + 12, s.plant?.horizon_slots || 48);
    if (rshDead) rshDead.value = `${slotToClock(deadVal, slotMin)} (Slot ${deadVal})`;

    // Re-render chart if we already have a disruption or initial result
    const d = s.disruptResult;
    const schedToRender = d ? d.schedule : s.lastResult?.schedule;
    if (schedToRender) {
      renderDisruptGantt(schedToRender, val);
    }
  });

  // 3. Trigger Disruption CTA
  container.querySelector("#btn-trigger-replan")?.addEventListener("click", () => {
    applyCurrentDisruption();
  });

  // 4. Undo and Reset buttons
  container.querySelector("#btn-undo-event")?.addEventListener("click", () => {
    undoLastEvent();
  });

  container.querySelector("#btn-reset-baseline")?.addEventListener("click", () => {
    resetToBaseline();
  });

  // 5. Diff Table Filter Tabs
  const filterBtns = container.querySelectorAll(".diff-filter-btn");
  filterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      filterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeDiffFilter = btn.dataset.filter;
      renderDiffTable();
    });
  });
}

function applyCurrentDisruption() {
  const s = store.get();
  if (!s.plant || !s.tariff || !s.lastResult) {
    const banner = document.getElementById("disrupt-incident-banner");
    if (banner) {
      banner.style.display = "block";
      banner.innerHTML = "⚠️ Please run an initial optimization on the Load or Compare page first.";
    }
    return;
  }

  const slotSlider = document.getElementById("disrupt-now-slot");
  const nowSlot = parseInt(slotSlider?.value || "12", 10);
  let payload = null;
  let label = "";

  if (activeIncidentType === "breakdown") {
    const selectEl = document.getElementById("select-failed-machine");
    const failedMid = selectEl?.value || s.plant.machines[0]?.id || "M1";
    const durOpt = document.getElementById("select-breakdown-duration")?.value || "shift_end";
    const untilSlot = durOpt === "shift_end" ? s.plant.horizon_slots : Math.min(nowSlot + parseInt(durOpt, 10), s.plant.horizon_slots);

    payload = {
      type: "machine_down",
      machine_id: failedMid,
      from_slot: nowSlot,
      until_slot: untilSlot,
    };
    label = `${failedMid} down @ ${slotToClock(nowSlot, s.plant.slot_minutes)}`;
    lastActiveEvent = { type: "breakdown", machineId: failedMid, fromSlot: nowSlot };

  } else if (activeIncidentType === "spike") {
    const mult = parseFloat(document.getElementById("select-spike-multiplier")?.value || "1.5");
    const modifiedWindows = s.tariff.windows.map(w => ({
      ...w,
      multiplier: w.multiplier > 1.0 ? mult : w.multiplier,
    }));

    payload = {
      type: "tariff_change",
      tariff: getTariffForRequest({ ...s.tariff, windows: modifiedWindows }),
    };
    label = `Tariff ${mult}× @ ${slotToClock(nowSlot, s.plant.slot_minutes)}`;
    lastActiveEvent = { type: "spike" };

  } else if (activeIncidentType === "rush") {
    const rushId = (document.getElementById("input-rush-id")?.value || "RUSH_01").trim();
    const durSlots = parseInt(document.getElementById("input-rush-dur")?.value || "4", 10);
    const deadline = Math.min(nowSlot + 12, s.plant.horizon_slots);

    const rushJob = {
      id: rushId,
      duration_slots: durSlots,
      release_slot: nowSlot,
      deadline_slot: deadline,
      allowed_machines: s.plant.machines.map(m => m.id),
      priority: 10,
      units: 1,
      mandatory: true,
    };

    payload = {
      type: "rush_job",
      job: rushJob,
    };
    label = `+${rushId} (${durSlots * s.plant.slot_minutes}m)`;
    lastActiveEvent = { type: "rush" };

  } else if (activeIncidentType === "delay") {
    const delayJob = document.getElementById("select-delay-job")?.value || s.plant.jobs[0]?.id;
    const extraSlots = parseInt(document.getElementById("input-delay-slots")?.value || "4", 10);

    payload = {
      type: "job_delay",
      job_id: delayJob,
      extra_slots: extraSlots,
    };
    label = `${delayJob} +${extraSlots}s`;
    lastActiveEvent = { type: "delay" };
  }

  if (payload) {
    executeReplan(payload, nowSlot, label);
  }
}

async function executeReplan(eventPayload, nowSlot, chipLabel) {
  const s = store.get();
  const currentSched = s.disruptResult?.schedule || s.lastResult.schedule;

  const badge = document.getElementById("disrupt-status-badge");
  if (badge) {
    badge.className = "badge badge-warning";
    badge.textContent = "Re-planning…";
  }

  const errBanner = document.getElementById("disrupt-incident-banner");
  if (errBanner) errBanner.style.display = "none";

  try {
    const res = await api.replan({
      plant: s.plant,
      tariff: s.tariff,
      schedule: currentSched,
      now_slot: nowSlot,
      event: eventPayload,
      seed: s.seed,
    });

    // Record into history
    eventHistory.push({
      label: chipLabel,
      type: activeIncidentType,
      payload: eventPayload,
      nowSlot,
      priorSchedule: currentSched,
      result: res,
    });

    store.set({ disruptResult: res });
    updateContent();
  } catch (err) {
    if (badge) {
      badge.className = "badge badge-danger";
      badge.textContent = "Re-plan failed";
    }
    if (errBanner) {
      const detail = err.detail || err.message || String(err);
      const msg = typeof detail === "object" ? (detail.message || JSON.stringify(detail)) : detail;
      errBanner.style.display = "block";
      errBanner.innerHTML = `❌ Re-plan error: <b>${msg}</b>`;
    }
  }
}

function undoLastEvent() {
  if (eventHistory.length === 0) return;
  eventHistory.pop();

  if (eventHistory.length > 0) {
    const last = eventHistory[eventHistory.length - 1];
    store.set({ disruptResult: last.result });
  } else {
    resetToBaseline();
  }
  updateContent();
}

function resetToBaseline() {
  eventHistory = [];
  lastActiveEvent = null;
  store.set({ disruptResult: null });

  const riskBanner = document.getElementById("disrupt-risk-banner");
  if (riskBanner) riskBanner.style.display = "none";

  const impactContainer = document.getElementById("disrupt-impact-container");
  if (impactContainer) impactContainer.style.display = "none";

  const errBanner = document.getElementById("disrupt-incident-banner");
  if (errBanner) errBanner.style.display = "none";

  const badge = document.getElementById("disrupt-status-badge");
  if (badge) {
    badge.className = "badge badge-info";
    badge.textContent = "Status: Ready";
  }

  updateContent();
}

export function updateContent() {
  const s = store.get();

  // 1. Populate real machine dropdown
  const machineContainer = document.getElementById("machine-select-container");
  if (machineContainer) {
    if (s.plant?.machines?.length) {
      const currentVal = document.getElementById("select-failed-machine")?.value;
      machineContainer.innerHTML = `
        <select class="form-control font-mono" id="select-failed-machine"
                title="Select machine to fail">
          ${s.plant.machines.map(m =>
            `<option value="${m.id}"${m.id === currentVal ? " selected" : ""}>${m.id} (${m.run_kw} kW)</option>`
          ).join("")}
        </select>`;
    } else {
      machineContainer.innerHTML = `
        <select class="form-control font-mono" id="select-failed-machine" disabled>
          <option value="">Load a plant first…</option>
        </select>`;
    }
  }

  // 2. Populate jobs dropdown for delay incident
  const delayJobSelect = document.getElementById("select-delay-job");
  if (delayJobSelect && s.lastResult?.schedule?.placements) {
    const placedJobs = s.lastResult.schedule.placements.map(p => p.job_id);
    const uniqueJobs = Array.from(new Set(placedJobs));
    delayJobSelect.innerHTML = uniqueJobs.map(jid => `<option value="${jid}">${jid}</option>`).join("");
  }

  // 3. Update scrubber max
  const slotSlider = document.getElementById("disrupt-now-slot");
  if (slotSlider && s.plant?.horizon_slots) {
    slotSlider.max = String(s.plant.horizon_slots - 1);
  }

  // 4. Render Event History Chips Bar
  const historyBar = document.getElementById("disrupt-history-bar");
  const chipsList = document.getElementById("disrupt-history-chips");
  if (historyBar && chipsList) {
    if (eventHistory.length > 0) {
      historyBar.style.display = "flex";
      chipsList.innerHTML = eventHistory.map((h, idx) => {
        const chipClass = h.type === "breakdown" ? "chip-down" : (h.type === "spike" ? "chip-tariff" : (h.type === "rush" ? "chip-rush" : "chip-delay"));
        return `<span class="event-chip ${chipClass}">#${idx + 1} ${h.label}</span>`;
      }).join("");
    } else {
      historyBar.style.display = "none";
      chipsList.innerHTML = "";
    }
  }

  const d = s.disruptResult;
  const nowSlot = parseInt(slotSlider?.value || "12", 10);

  // If no disruption has been run yet, render baseline schedule in Gantt if available
  if (!d) {
    if (s.lastResult?.schedule) {
      renderDisruptGantt(s.lastResult.schedule, nowSlot);
    }
    const diffTbody = document.getElementById("disrupt-diff-tbody");
    if (diffTbody) {
      diffTbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:2rem 0;">No disruption active. Apply an incident above to audit relocation diffs.</td></tr>`;
    }
    return;
  }

  // 5. Target at Risk Banner
  const riskBanner = document.getElementById("disrupt-risk-banner");
  const riskDesc = document.getElementById("disrupt-risk-desc");
  const riskUnplaced = document.getElementById("disrupt-risk-unplaced");
  const badge = document.getElementById("disrupt-status-badge");

  if (d.status === "target_at_risk") {
    if (badge) {
      badge.className = "badge badge-danger";
      badge.textContent = "Target at risk ✗";
    }
    if (riskBanner) {
      riskBanner.style.display = "block";
      if (riskDesc) {
        riskDesc.textContent = `Production target could not be met due to capacity shortage. Shortfall: ${d.units_short} units.`;
      }
      if (riskUnplaced && d.unplaced) {
        riskUnplaced.innerHTML = d.unplaced.map(u => {
          const jid = typeof u === "string" ? u : u.job_id;
          return `<span class="badge badge-danger font-mono">${jid} (Unplaced)</span>`;
        }).join("");
      }
    }
  } else {
    if (badge) {
      badge.className = "badge badge-success";
      badge.textContent = "Target met ✓";
    }
    if (riskBanner) riskBanner.style.display = "none";
  }

  // 6. Impact Card
  const impactContainer = document.getElementById("disrupt-impact-container");
  if (impactContainer) impactContainer.style.display = "block";

  const costChangeEl = document.getElementById("impact-cost-change");
  const jobsAffectedEl = document.getElementById("impact-jobs-affected");
  const targetBadgeEl = document.getElementById("impact-target-badge");
  const movedCountEl = document.getElementById("impact-moved-count");
  const explanationEl = document.getElementById("impact-explanation-content");

  const costDelta = d.metrics_after.energy_cost - d.metrics_before.energy_cost;
  if (costChangeEl) {
    if (costDelta > 1) {
      costChangeEl.className = "impact-kpi-val cost-up";
      costChangeEl.textContent = `+${formatMoney(costDelta)}`;
    } else if (costDelta < -1) {
      costChangeEl.className = "impact-kpi-val cost-down";
      costChangeEl.textContent = `−${formatMoney(Math.abs(costDelta))}`;
    } else {
      costChangeEl.className = "impact-kpi-val cost-neutral";
      costChangeEl.textContent = "₹0 (no change)";
    }
  }

  const movedJobsCount = d.diff ? d.diff.length : 0;
  if (jobsAffectedEl) jobsAffectedEl.textContent = `${movedJobsCount} jobs`;
  if (movedCountEl) movedCountEl.textContent = String(movedJobsCount);

  if (targetBadgeEl) {
    if (d.status === "target_at_risk") {
      targetBadgeEl.innerHTML = `<span class="badge badge-danger">Target at risk ✗</span>`;
    } else {
      targetBadgeEl.innerHTML = `<span class="badge badge-success">Target met ✓</span>`;
    }
  }

  // One-line Plain English Explanation
  if (explanationEl) {
    explanationEl.textContent = generateImpactExplanation(d, costDelta);
  }

  // 7. Render Rescheduled Gantt with Ghost Bars & Failed Hatching
  renderDisruptGantt(d.schedule, nowSlot, d.diff);

  // 8. Render Diff Table
  renderDiffTable();
}

function renderDisruptGantt(schedule, nowSlot, diffList = []) {
  const s = store.get();
  if (!s.plant || !s.tariff) return;

  const movedJobIds = new Set(diffList.map(item => item.job_id));

  // Options for charts.js renderGantt
  const options = {
    nowSlot,
    ghostSchedule: s.lastResult?.schedule,
    movedJobIds,
    highlightMoved: true,
  };

  // If latest event was machine breakdown, pass failedMachineId
  if (lastActiveEvent && lastActiveEvent.type === "breakdown") {
    options.failedMachineId = lastActiveEvent.machineId;
    options.failedFromSlot = lastActiveEvent.fromSlot;
  }

  renderGantt(
    "chart-gantt-disrupt",
    schedule,
    s.plant,
    s.tariff,
    "Rescheduled Machine Allocation",
    options
  );
}

function generateImpactExplanation(d, costDelta) {
  const s = store.get();
  const moved = d.diff ? d.diff.length : 0;
  const isAtRisk = d.status === "target_at_risk";

  if (isAtRisk) {
    const unplacedStr = d.unplaced?.length ? d.unplaced.map(u => typeof u === "string" ? u : u.job_id).join(", ") : "mandatory jobs";
    return `Capacity deficit prevents scheduling ${unplacedStr}; delivery target at risk with a shortfall of ${d.units_short || 0} units.`;
  }

  if (lastActiveEvent?.type === "breakdown") {
    const mid = lastActiveEvent.machineId;
    const costText = costDelta > 0 ? `increasing energy cost by ${formatMoney(costDelta)}` : (costDelta < 0 ? `saving ${formatMoney(Math.abs(costDelta))}` : `with neutral cost impact`);
    return `Breakdown on ${mid} relocated ${moved} jobs to active lines; production target was preserved while ${costText}.`;
  }

  if (lastActiveEvent?.type === "spike") {
    return `Peak tariff surcharge re-routed ${moved} jobs toward lower-rate slots to defend the operating budget.`;
  }

  if (lastActiveEvent?.type === "rush") {
    return `High-priority rush order accommodated within target window; ${moved} jobs were adjusted to guarantee throughput.`;
  }

  if (lastActiveEvent?.type === "delay") {
    return `Job completion delay absorbed with ${moved} downstream jobs time-shifted into available capacity.`;
  }

  return `Dynamic re-planning completed: ${moved} jobs adjusted, target preserved with net cost change of ${formatMoney(costDelta)}.`;
}

function computePlacementCost(machineId, startSlot, endSlot, plant, tariff) {
  if (!plant || !tariff || startSlot === undefined || endSlot === undefined) return 0;
  const machine = plant.machines.find(m => m.id === machineId);
  if (!machine) return 0;

  const slotMinutes = plant.slot_minutes || 30;
  const hoursPerSlot = slotMinutes / 60;
  const kw = machine.run_kw || 0;
  let totalCost = 0;

  for (let s = startSlot; s < endSlot && s < plant.horizon_slots; s++) {
    const hour = (s * slotMinutes) / 60;
    let rate = tariff.base_rate || 7.50;
    if (tariff.windows) {
      for (const w of tariff.windows) {
        if (hour >= w.start_hour && hour < w.end_hour) {
          rate = (tariff.base_rate || 7.50) * (w.multiplier || 1.0);
          break;
        }
      }
    }
    totalCost += kw * hoursPerSlot * rate;
  }
  return totalCost;
}

function renderDiffTable() {
  const s = store.get();
  const d = s.disruptResult;
  const tbody = document.getElementById("disrupt-diff-tbody");
  if (!tbody) return;

  if (!d || !d.diff || d.diff.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#34d399; padding:2rem 0;">Schedule intact. Zero relocations required.</td></tr>`;
    updateFilterCounts(0, 0, 0, 0);
    return;
  }

  const slotMin = s.plant?.slot_minutes || 30;

  // Categorize items
  const allItems = d.diff.map(item => {
    const isRerouted = item.reason && item.reason.includes("REROUTED_MACHINE_DOWN");
    const isUnplaced = item.new === null || !item.new;
    const category = isUnplaced ? "unplaced" : (isRerouted ? "rerouted" : "moved");

    // Compute old and new placement energy cost
    const oldCost = item.old ? computePlacementCost(item.old.machine_id, item.old.start, item.old.end, s.plant, s.tariff) : 0;
    const newCost = item.new ? computePlacementCost(item.new.machine_id, item.new.start, item.new.end, s.plant, s.tariff) : 0;
    const costDelta = newCost - oldCost;

    return { ...item, category, costDelta };
  });

  const countRerouted = allItems.filter(i => i.category === "rerouted").length;
  const countMoved = allItems.filter(i => i.category === "moved").length;
  const countUnplaced = allItems.filter(i => i.category === "unplaced").length;

  updateFilterCounts(allItems.length, countRerouted, countMoved, countUnplaced);

  const filtered = activeDiffFilter === "all" ? allItems : allItems.filter(i => i.category === activeDiffFilter);

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:1.5rem 0;">No jobs match the selected filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(item => {
    const oldStr = item.old ? `<span class="font-mono"><b>${item.old.machine_id}</b> · ${slotRangeToClock(item.old.start, item.old.end, slotMin)}</span>` : `<em>None</em>`;
    const newStr = item.new ? `<span class="font-mono" style="color:#38bdf8;"><b>${item.new.machine_id}</b> · ${slotRangeToClock(item.new.start, item.new.end, slotMin)}</span>` : `<span class="badge badge-danger font-mono">UNPLACED</span>`;

    let badgeClass = "badge-warning";
    let badgeText = "Moved by re-plan";
    if (item.category === "rerouted") {
      badgeClass = "badge-danger";
      badgeText = `Rerouted (${item.old?.machine_id || "Machine"} down)`;
    } else if (item.category === "unplaced") {
      badgeClass = "badge-danger";
      badgeText = "Capacity deficit";
    }

    let costDeltaHtml = `<span style="color:var(--text-secondary);">₹0</span>`;
    if (item.costDelta > 1) {
      costDeltaHtml = `<span style="color:#f87171; font-weight:700;">+${formatMoney(item.costDelta)}</span>`;
    } else if (item.costDelta < -1) {
      costDeltaHtml = `<span style="color:#34d399; font-weight:700;">−${formatMoney(Math.abs(item.costDelta))}</span>`;
    }

    return `
      <tr>
        <td class="font-mono"><b>${item.job_id}</b></td>
        <td><span class="badge ${badgeClass}">${badgeText}</span></td>
        <td>${oldStr}</td>
        <td>${newStr}</td>
        <td style="text-align:right;" class="font-mono">${costDeltaHtml}</td>
      </tr>
    `;
  }).join("");
}

function updateFilterCounts(all, rerouted, moved, unplaced) {
  const cAll = document.getElementById("count-diff-all");
  const cRerouted = document.getElementById("count-diff-rerouted");
  const cMoved = document.getElementById("count-diff-moved");
  const cUnplaced = document.getElementById("count-diff-unplaced");

  if (cAll) cAll.textContent = String(all);
  if (cRerouted) cRerouted.textContent = String(rerouted);
  if (cMoved) cMoved.textContent = String(moved);
  if (cUnplaced) cUnplaced.textContent = String(unplaced);
}
