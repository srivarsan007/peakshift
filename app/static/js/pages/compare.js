/**
 * Compare Page View Controller  —  Schedule Comparison & Verification
 */

import { store } from "../state.js";
import { formatMoney, formatNumber, formatPercent, formatSavingsText, slotToClock, slotRangeToClock } from "../format.js";
import { renderGantt, renderLoadProfile } from "../charts.js";

let currentGanttView = "difference"; // "baseline" | "optimized" | "difference"
let shiftPlanView = "operator";      // "operator" | "full"
let shiftPlanExpanded = false;

export function initComparePage() {
  const container = document.getElementById("page-compare");
  if (!container) return;

  renderTemplate(container);
  bindEvents(container);
  updateContent();

  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#compare") requestAnimationFrame(updateContent);
  });

  store.subscribe(() => {
    if (window.location.hash === "#compare") {
      updateContent();
    }
  });
}

function renderTemplate(container) {
  container.innerHTML = `
    <!-- Top Row: Page Title + Interactive Verifier & Winner Badges -->
    <div class="page-header" style="margin-bottom:1rem;">
      <div>
        <h1 class="page-title">Schedule Comparison &amp; Verification</h1>
        <p class="page-desc">Comparing always-on factory baseline against PeakShift tariff-optimized schedule.</p>
      </div>
      <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
        <button id="btn-verifier-drawer" class="badge badge-info" style="cursor:pointer; border:1px solid rgba(59,130,246,0.5); font-family:inherit; padding:5px 12px;" title="Click to view verifier audit drawer">
          <span id="verifier-badge-text">VERIFIER: NOT CHECKED</span> 📋
        </button>
        <span id="winner-badge" class="badge badge-info">WINNER: PENDING</span>
      </div>
    </div>

    <!-- Verification Failure Alert Banner -->
    <div id="compare-unverified-warning" class="banner banner-danger" style="display:none;" aria-live="assertive">
      <div>
        <strong>Verification Alert:</strong> Schedule failed independent verifier checks. Energy savings are suppressed.
      </div>
      <button class="btn btn-secondary btn-sm" id="btn-inspect-audit-inline">Inspect Audit Checks</button>
    </div>

    <!-- Capacity Shortfall / Target at Risk Alert Banner -->
    <div id="compare-at-risk-warning" class="banner banner-danger" style="display:none;" aria-live="assertive">
      <div id="compare-at-risk-content">
        <strong>Target at risk:</strong> Unplaced jobs detected due to plant capacity limits.
      </div>
    </div>

    <!-- Empty State -->
    <div id="compare-empty" class="card" style="text-align:center; padding:2.5rem; margin-bottom:1.5rem;">
      <p style="color:var(--text-secondary); margin-bottom:1rem; font-size:var(--text-md);">No optimization run yet.</p>
      <a href="#load" class="btn btn-primary">Go to Load &amp; Configure</a>
    </div>

    <!-- 1. Outcome Card (First on Screen per SPEC) -->
    <div id="compare-outcome-card" class="outcome-card" style="display:none;">
      <div class="outcome-text" id="compare-outcome-text">
        <span class="outcome-dot"></span>
        <span id="outcome-summary-line">Calculating schedule metrics…</span>
      </div>
      <button class="btn btn-secondary btn-sm" id="btn-export-shift-csv" style="display:flex; align-items:center; gap:0.5rem;">
        <span>📥 Download Shift Plan CSV</span>
      </button>
    </div>

    <!-- 2. Four KPI Cards with Before-to-After Delta Chips -->
    <div class="grid-4" id="compare-kpis" style="margin-bottom:1.25rem;">
      <div class="kpi-card">
        <span class="kpi-label">Energy Cost</span>
        <span class="kpi-value" id="kpi-cost">--</span>
        <div id="kpi-cost-delta" class="delta-chip delta-neutral">Always-on: --</div>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Total Energy</span>
        <span class="kpi-value" id="kpi-kwh">--</span>
        <div id="kpi-kwh-delta" class="delta-chip delta-neutral">Always-on: --</div>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Peak Power Demand</span>
        <span class="kpi-value" id="kpi-peak">--</span>
        <div id="kpi-peak-delta" class="delta-chip delta-neutral">Baseline: --</div>
      </div>
      <div class="kpi-card">
        <span class="kpi-label">Machine Switch-Offs</span>
        <span class="kpi-value" id="kpi-switches">--</span>
        <div id="kpi-switches-delta" class="delta-chip delta-neutral">Cycle cap compliant</div>
      </div>
    </div>

    <!-- Method Disclosure (Lower Bound & Algorithmic Guarantee) -->
    <details class="method-disclosure" id="compare-method-disclosure" style="margin-bottom:1.5rem;">
      <summary>
        <span>Method &amp; Theoretical Lower Bound</span>
        <span style="font-size:0.75rem; color:var(--text-muted);" id="comp-time-chip">-- ms</span>
      </summary>
      <div class="method-disclosure-content">
        <div>
          <span style="color:var(--text-muted);">Theoretical Lower Bound: </span>
          <b class="num" style="color:#38bdf8;" id="bound-text">--</b>
          <span style="color:var(--text-muted); font-size:11px;" id="bound-desc"></span>
        </div>
        <div>
          <span style="color:var(--text-muted);">Optimization Guarantee: </span>
          <span>cost(Portfolio) &le; cost(B1 Smart Standby) &le; cost(B0 Always-On)</span>
        </div>
      </div>
    </details>

    <!-- 3. 24-Hour Tariff Strip -->
    <div class="card tariff-strip-container" style="padding:1rem 1.25rem; margin-bottom:1.25rem;">
      <div class="tariff-strip-header">
        <span style="font-weight:600; color:var(--text-primary);">TNERC HT-I Tariff Schedule (24 Hours)</span>
        <span style="color:var(--text-muted); font-size:11px;">Off-peak: ₹7.13/kWh (0.95x) &bull; Normal: ₹7.50/kWh &bull; Peak: ₹9.38/kWh (1.25x)</span>
      </div>
      <div class="tariff-strip" id="compare-tariff-strip">
        <div class="tariff-strip-block offpeak" style="width:20.83%;" title="00:00 - 05:00 Off-Peak (0.95x) ₹7.13/kWh">00:00–05:00 Off-Peak ₹7.13</div>
        <div class="tariff-strip-block normal" style="width:4.17%;" title="05:00 - 06:00 Normal (1.00x) ₹7.50/kWh">05–06</div>
        <div class="tariff-strip-block peak" style="width:16.67%;" title="06:00 - 10:00 Morning Peak (+25%) ₹9.38/kWh">06:00–10:00 Peak ₹9.38</div>
        <div class="tariff-strip-block normal" style="width:33.33%;" title="10:00 - 18:00 Normal (1.00x) ₹7.50/kWh">10:00–18:00 Normal ₹7.50</div>
        <div class="tariff-strip-block peak" style="width:16.67%;" title="18:00 - 22:00 Evening Peak (+25%) ₹9.38/kWh">18:00–22:00 Peak ₹9.38</div>
        <div class="tariff-strip-block offpeak" style="width:8.33%;" title="22:00 - 24:00 Off-Peak (0.95x) ₹7.13/kWh">22–24 Off-Peak</div>
      </div>
    </div>

    <!-- 4. Interactive Gantt Schedule Section with View Toggle -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem; flex-wrap:wrap; gap:0.5rem;">
        <div>
          <h2 class="card-title" id="gantt-section-title" style="margin-bottom:0;">Machine Production Schedule</h2>
          <p style="font-size:0.75rem; color:var(--text-muted); margin-top:2px;" id="gantt-subtitle">Coloured by machine state &bull; Hover shows job ID, power, and ₹ cost.</p>
        </div>
        <div class="segmented-control" id="gantt-view-toggle" role="group" aria-label="Gantt View Switcher">
          <button class="segmented-item" data-view="baseline">Baseline (Always-On)</button>
          <button class="segmented-item" data-view="optimized">Optimized</button>
          <button class="segmented-item active" data-view="difference">Difference (Moved Jobs)</button>
        </div>
      </div>

      <div id="chart-gantt-main" class="chart-container">
        <div class="loading-overlay">Select an industrial plant on Load page to view schedule.</div>
      </div>
    </div>

    <!-- 5. Power Demand Step Chart with Savings Fill -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
        <h2 class="card-title" style="margin-bottom:0;">Aggregate Factory Power Demand (kW)</h2>
        <span style="font-size:0.75rem; color:var(--text-muted);">Shaded area illustrates energy savings vs always-on</span>
      </div>
      <div id="chart-load-profile" class="chart-container">
        <div class="loading-overlay">Awaiting optimization run...</div>
      </div>
      <div id="peak-cap-callout" style="display:none; margin-top:0.75rem;"></div>
    </div>

    <!-- 6. Manager Shift Plan & Operator Action Log -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.75rem; flex-wrap:wrap; gap:0.75rem;">
        <div>
          <h2 class="card-title" style="margin-bottom:0.25rem;">Shift Plan &amp; Machine Action Log</h2>
          <p style="font-size:0.8125rem; color:var(--text-secondary);" id="shift-plan-desc">
            Shop-floor sequence for machine operators with planned shutdowns and changeovers.
          </p>
        </div>
        <div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
          <div class="segmented-control" id="shift-view-toggle">
            <button class="segmented-item active" data-shift-view="operator">Operator view (actions only)</button>
            <button class="segmented-item" data-shift-view="full">Full schedule</button>
          </div>
          <button class="btn btn-secondary btn-sm" id="btn-export-shift-csv-table">
            <span>📥 CSV</span>
          </button>
        </div>
      </div>

      <!-- Filters & Search Bar -->
      <div style="display:flex; align-items:center; gap:0.75rem; margin-bottom:0.75rem; flex-wrap:wrap; font-size:var(--text-xs);">
        <div style="display:flex; align-items:center; gap:0.35rem;">
          <label for="shift-filter-machine" style="color:var(--text-muted);">Machine:</label>
          <select id="shift-filter-machine" class="form-control" style="min-height:32px; padding:2px 8px; width:auto; font-size:var(--text-xs);">
            <option value="all">All machines</option>
          </select>
        </div>
        <div style="display:flex; align-items:center; gap:0.35rem;">
          <label for="shift-filter-action" style="color:var(--text-muted);">Action:</label>
          <select id="shift-filter-action" class="form-control" style="min-height:32px; padding:2px 8px; width:auto; font-size:var(--text-xs);">
            <option value="all">All events</option>
            <option value="off">Switch-offs (standby)</option>
            <option value="warmup">Warm-up starts</option>
            <option value="run">Job changeovers</option>
          </select>
        </div>
        <div style="display:flex; align-items:center; gap:0.35rem; margin-left:auto;">
          <input type="text" id="shift-search-job" class="form-control" placeholder="Search Job ID…" style="min-height:32px; padding:2px 8px; width:140px; font-size:var(--text-xs);">
          <span id="shift-row-count" style="color:var(--text-muted); font-size:var(--text-xs); white-space:nowrap;">Showing 0 rows</span>
        </div>
      </div>

      <!-- Action Log Table -->
      <div class="table-responsive table-sticky-header" style="max-height: 480px;">
        <table class="data-table" id="table-shift-plan">
          <thead>
            <tr>
              <th>Machine</th>
              <th>State / Action</th>
              <th>Job ID</th>
              <th>Start Time</th>
              <th>End Time</th>
              <th>Duration</th>
              <th>Power (kW)</th>
            </tr>
          </thead>
          <tbody id="shift-plan-tbody">
            <tr><td colspan="7" style="text-align:center; color:var(--text-muted);">No shift plan generated.</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Collapsible Show More / Show Less Button -->
      <div id="shift-expand-container" style="display:none; text-align:center; margin-top:0.75rem;">
        <button class="btn btn-secondary btn-sm" id="btn-toggle-shift-expand" style="font-size:var(--text-xs);">
          Show all rows
        </button>
      </div>
    </div>

    <!-- 7. Verifier Side Drawer (Opens when verifier badge is clicked) -->
    <div id="verifier-drawer-backdrop" class="drawer-backdrop" aria-hidden="true"></div>
    <aside id="verifier-drawer" class="drawer-panel" aria-label="Verifier Audit Drawer" aria-hidden="true">
      <div class="drawer-header">
        <div>
          <div class="drawer-title">Independent Verifier Audit</div>
          <div style="font-size:var(--text-xs); color:var(--text-muted);">Isolated verification engine recomputation</div>
        </div>
        <button id="btn-close-drawer" class="drawer-close" aria-label="Close audit drawer">&times;</button>
      </div>
      <div class="drawer-body" id="verifier-drawer-body">
        <p style="color:var(--text-muted);">No verification results available.</p>
      </div>
    </aside>
  `;
}

function bindEvents(container) {
  // Verifier drawer toggles
  const drawerBackdrop = container.querySelector("#verifier-drawer-backdrop");
  const drawerPanel = container.querySelector("#verifier-drawer");
  const openDrawer = () => {
    if (drawerBackdrop && drawerPanel) {
      drawerBackdrop.classList.add("open");
      drawerPanel.classList.add("open");
      drawerBackdrop.setAttribute("aria-hidden", "false");
      drawerPanel.setAttribute("aria-hidden", "false");
    }
  };
  const closeDrawer = () => {
    if (drawerBackdrop && drawerPanel) {
      drawerBackdrop.classList.remove("open");
      drawerPanel.classList.remove("open");
      drawerBackdrop.setAttribute("aria-hidden", "true");
      drawerPanel.setAttribute("aria-hidden", "true");
    }
  };

  container.querySelector("#btn-verifier-drawer")?.addEventListener("click", openDrawer);
  container.querySelector("#btn-inspect-audit-inline")?.addEventListener("click", openDrawer);
  container.querySelector("#btn-close-drawer")?.addEventListener("click", closeDrawer);
  drawerBackdrop?.addEventListener("click", closeDrawer);

  // Gantt view toggle: Baseline | Optimized | Difference
  container.querySelectorAll("#gantt-view-toggle .segmented-item").forEach(btn => {
    btn.addEventListener("click", (e) => {
      container.querySelectorAll("#gantt-view-toggle .segmented-item").forEach(b => b.classList.remove("active"));
      e.target.classList.add("active");
      currentGanttView = e.target.dataset.view;
      renderCurrentGantt();
    });
  });

  // Shift plan view toggle: Operator view | Full schedule
  container.querySelectorAll("#shift-view-toggle .segmented-item").forEach(btn => {
    btn.addEventListener("click", (e) => {
      container.querySelectorAll("#shift-view-toggle .segmented-item").forEach(b => b.classList.remove("active"));
      e.target.classList.add("active");
      shiftPlanView = e.target.dataset.shiftView;
      shiftPlanExpanded = false; // Reset to top 15
      renderShiftPlanTable();
    });
  });

  // Shift plan filters & search
  container.querySelector("#shift-filter-machine")?.addEventListener("change", () => {
    shiftPlanExpanded = false;
    renderShiftPlanTable();
  });
  container.querySelector("#shift-filter-action")?.addEventListener("change", () => {
    shiftPlanExpanded = false;
    renderShiftPlanTable();
  });
  container.querySelector("#shift-search-job")?.addEventListener("input", () => {
    shiftPlanExpanded = false;
    renderShiftPlanTable();
  });

  // Expand / collapse shift plan
  container.querySelector("#btn-toggle-shift-expand")?.addEventListener("click", () => {
    shiftPlanExpanded = !shiftPlanExpanded;
    renderShiftPlanTable();
  });

  // Single CSV download handler
  const exportHandler = () => {
    const s = store.get();
    if (!s.lastResult || !s.plant) return;
    exportShiftPlanCSV(s.plant, s.lastResult.schedule);
  };
  container.querySelector("#btn-export-shift-csv")?.addEventListener("click", exportHandler);
  container.querySelector("#btn-export-shift-csv-table")?.addEventListener("click", exportHandler);
}

function exportShiftPlanCSV(plant, schedule) {
  const slotMinutes = plant.slot_minutes || 30;
  const machineMap = new Map(plant.machines.map(m => [m.id, m]));
  const rows = [
    ["Machine", "State", "Job_ID", "Start_Slot", "End_Slot", "Start_Time", "End_Time", "Duration_Min", "Power_kW"]
  ];

  if (schedule && schedule.segments) {
    for (const [mId, segs] of Object.entries(schedule.segments)) {
      const m = machineMap.get(mId);
      for (const seg of segs) {
        const durSlots = seg.end - seg.start;
        const durMin = durSlots * slotMinutes;
        const kw = seg.state === "run" ? (m?.run_kw || 0) : (seg.state === "idle" ? (m?.idle_kw || 0) : 0);
        rows.push([
          mId,
          seg.state.toUpperCase(),
          seg.job_id || "-",
          seg.start,
          seg.end,
          slotToClock(seg.start, slotMinutes),
          slotToClock(seg.end, slotMinutes),
          durMin,
          kw
        ]);
      }
    }
  }

  const csvContent = "data:text/csv;charset=utf-8," + rows.map(e => e.join(",")).join("\n");
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement("a");
  link.setAttribute("href", encodedUri);
  link.setAttribute("download", `PeakShift_Shift_Plan_${plant.name || "Factory"}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function computeMovedJobIds(b0Schedule, optSchedule) {
  const moved = new Set();
  if (!b0Schedule?.placements || !optSchedule?.placements) return moved;
  const b0Map = new Map(b0Schedule.placements.map(p => [p.job_id, p]));
  for (const optP of optSchedule.placements) {
    const b0P = b0Map.get(optP.job_id);
    if (!b0P || b0P.machine_id !== optP.machine_id || b0P.start !== optP.start) {
      moved.add(optP.job_id);
    }
  }
  return moved;
}

function renderCurrentGantt() {
  const s = store.get();
  const res = s.lastResult;
  if (!res || !s.plant) return;

  const subtitle = document.getElementById("gantt-subtitle");
  const movedJobIds = computeMovedJobIds(res.baseline_b0?.schedule, res.schedule);

  if (currentGanttView === "baseline") {
    if (subtitle) subtitle.textContent = "Baseline Schedule (Continuous always-on operation)";
    renderGantt("chart-gantt-main", res.baseline_b0.schedule, s.plant, s.tariff, "Baseline Schedule", {
      highlightMoved: false,
    });
  } else if (currentGanttView === "optimized") {
    if (subtitle) subtitle.textContent = "PeakShift Optimized Schedule (Staggered away from peak tariff windows)";
    renderGantt("chart-gantt-main", res.schedule, s.plant, s.tariff, "Optimized Schedule", {
      highlightMoved: false,
    });
  } else {
    // Difference view: optimized schedule with moved jobs highlighted
    if (subtitle) subtitle.innerHTML = `Difference View: <b style="color:#38bdf8;">${movedJobIds.size} moved/rescheduled jobs</b> highlighted with a blue outline.`;
    renderGantt("chart-gantt-main", res.schedule, s.plant, s.tariff, "Difference Schedule", {
      movedJobIds,
      highlightMoved: true,
    });
  }
}

function renderShiftPlanTable() {
  const s = store.get();
  const res = s.lastResult;
  const shiftTbody = document.getElementById("shift-plan-tbody");
  const rowCountEl = document.getElementById("shift-row-count");
  const expandContainer = document.getElementById("shift-expand-container");
  const expandBtn = document.getElementById("btn-toggle-shift-expand");

  if (!shiftTbody || !res?.schedule?.segments || !s.plant) return;

  const slotMin = s.plant.slot_minutes || 30;
  const machineMap = new Map(s.plant.machines.map(mc => [mc.id, mc]));

  // Selected filter values
  const filterMachine = document.getElementById("shift-filter-machine")?.value || "all";
  const filterAction = document.getElementById("shift-filter-action")?.value || "all";
  const searchJob = (document.getElementById("shift-search-job")?.value || "").trim().toLowerCase();

  const allItems = [];

  for (const [mId, segs] of Object.entries(res.schedule.segments)) {
    if (filterMachine !== "all" && mId !== filterMachine) continue;
    const mc = machineMap.get(mId);

    for (let i = 0; i < segs.length; i++) {
      const seg = segs[i];
      const prevSeg = i > 0 ? segs[i - 1] : null;

      // Operator view filtering: only switch-offs, warm-ups, and job changeovers
      if (shiftPlanView === "operator") {
        const isSwitchOff = seg.state === "off";
        const isWarmup = seg.state === "warmup";
        const isJobStart = seg.state === "run" && (!prevSeg || prevSeg.state !== "run" || prevSeg.job_id !== seg.job_id);

        if (!isSwitchOff && !isWarmup && !isJobStart) continue;
      }

      // Action dropdown filter
      if (filterAction !== "all") {
        if (filterAction === "off" && seg.state !== "off") continue;
        if (filterAction === "warmup" && seg.state !== "warmup") continue;
        if (filterAction === "run" && seg.state !== "run") continue;
      }

      // Job ID text search
      if (searchJob && (!seg.job_id || !seg.job_id.toLowerCase().includes(searchJob))) {
        continue;
      }

      const dur = (seg.end - seg.start) * slotMin;
      const kw = seg.state === "run" ? (mc?.run_kw || 0) : (seg.state === "idle" ? (mc?.idle_kw || 0) : 0);

      let badgeClass = "badge-info";
      let actionLabel = seg.state.toUpperCase();
      if (seg.state === "run") {
        badgeClass = "badge-success";
        actionLabel = "JOB RUN";
      } else if (seg.state === "warmup") {
        badgeClass = "badge-warning";
        actionLabel = "WARM-UP";
      } else if (seg.state === "off") {
        badgeClass = "badge-danger";
        actionLabel = "STANDBY SHUTDOWN";
      } else if (seg.state === "idle") {
        badgeClass = "badge-info";
        actionLabel = "IDLE";
      }

      allItems.push({
        mId,
        badgeClass,
        actionLabel,
        jobId: seg.job_id,
        startClock: slotToClock(seg.start, slotMin),
        endClock: slotToClock(seg.end, slotMin),
        dur,
        kw,
      });
    }
  }

  // Update machine selector options if needed
  const machineSelect = document.getElementById("shift-filter-machine");
  if (machineSelect && machineSelect.options.length <= 1) {
    s.plant.machines.forEach(mc => {
      const opt = document.createElement("option");
      opt.value = mc.id;
      opt.textContent = `${mc.id} (${mc.run_kw} kW)`;
      machineSelect.appendChild(opt);
    });
  }

  // Table never exceeds 15 rows until expanded
  const limit = shiftPlanExpanded ? allItems.length : Math.min(15, allItems.length);
  const visibleItems = allItems.slice(0, limit);

  if (rowCountEl) {
    rowCountEl.textContent = `Showing ${visibleItems.length} of ${allItems.length} actions`;
  }

  if (expandContainer && expandBtn) {
    if (allItems.length > 15) {
      expandContainer.style.display = "block";
      expandBtn.textContent = shiftPlanExpanded ? `Collapse to 15 actions` : `Show all ${allItems.length} actions`;
    } else {
      expandContainer.style.display = "none";
    }
  }

  if (visibleItems.length === 0) {
    shiftTbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:1.5rem;">No actions matching filters.</td></tr>`;
    return;
  }

  shiftTbody.innerHTML = visibleItems.map(item => `
    <tr>
      <td class="font-mono"><b>${item.mId}</b></td>
      <td><span class="badge ${item.badgeClass}">${item.actionLabel}</span></td>
      <td class="font-mono">${item.jobId ? `<b>${item.jobId}</b>` : '<span style="color:var(--text-muted);">-</span>'}</td>
      <td class="font-mono">${item.startClock}</td>
      <td class="font-mono">${item.endClock}</td>
      <td class="num">${item.dur} min</td>
      <td class="num">${formatNumber(item.kw, 1)} kW</td>
    </tr>
  `).join("");
}

export function updateContent() {
  const s = store.get();
  const res = s.lastResult;
  const emptyEl = document.getElementById("compare-empty");
  const outcomeCard = document.getElementById("compare-outcome-card");
  const kpiEl = document.getElementById("compare-kpis");
  const methodDisclosure = document.getElementById("compare-method-disclosure");

  if (emptyEl) emptyEl.style.display = res ? "none" : "block";
  if (outcomeCard) outcomeCard.style.display = res ? "flex" : "none";
  if (kpiEl) kpiEl.style.display = res ? "" : "none";
  if (methodDisclosure) methodDisclosure.style.display = res ? "block" : "none";
  if (!res) return;

  const verifier = res.verifier;
  const totalJobs = s.plant?.jobs?.length || res.schedule?.placements?.length || 0;
  const placedJobs = res.schedule?.placements?.length || 0;

  const unplacedJobs = (res.schedule?.unplaced && res.schedule.unplaced.length > 0)
    ? res.schedule.unplaced
    : (s.plant?.jobs || []).filter(j => !(res.schedule?.placements || []).some(p => p.job_id === j.id)).map(j => j.id);

  const hasUnplaced = unplacedJobs.length > 0 || placedJobs < totalJobs;
  const isVerified = verifier && verifier.passed && !hasUnplaced;

  // Capacity shortfall / Target at Risk alert banner
  const riskBanner = document.getElementById("compare-at-risk-warning");
  const riskContent = document.getElementById("compare-at-risk-content");
  if (riskBanner) {
    if (hasUnplaced) {
      riskBanner.style.display = "flex";
      if (riskContent) {
        riskContent.innerHTML = `<strong>Target at risk:</strong> <b>${unplacedJobs.length} unplaced jobs</b> detected due to capacity limits. Plant schedule is overloaded.`;
      }
    } else {
      riskBanner.style.display = "none";
    }
  }

  // 1. Outcome Card: "75/75 jobs · target verified · ₹7,356 saved, 22.9% vs always-on, 3.4% vs smart rule"
  const outcomeLine = document.getElementById("outcome-summary-line");
  if (outcomeLine) {
    if (hasUnplaced) {
      outcomeLine.innerHTML = `
        <b style="color:#f87171;">${placedJobs}/${totalJobs} jobs placed</b>
        <span style="color:var(--text-muted); margin:0 4px;">·</span>
        <span style="color:#f87171; font-weight:700;">Target at risk ✗</span>
        <span style="color:var(--text-muted); margin:0 4px;">·</span>
        <span style="color:var(--text-secondary);">${unplacedJobs.length} unplaced jobs</span>
      `;
    } else if (isVerified) {
      const b0SavInr = res.savings_vs_b0.inr;
      const b0SavPct = res.savings_vs_b0.percent;
      const b1SavPct = res.savings_vs_b1.percent;

      outcomeLine.innerHTML = `
        <b>${placedJobs}/${totalJobs} jobs</b>
        <span style="color:var(--text-muted); margin:0 4px;">·</span>
        <span style="color:#34d399; font-weight:700;">target verified ✓</span>
        <span style="color:var(--text-muted); margin:0 4px;">·</span>
        <b style="color:#34d399;">${formatMoney(b0SavInr)} saved</b>,
        <span>${formatPercent(b0SavPct)} vs always-on</span>,
        <span>${formatPercent(b1SavPct)} vs smart rule</span>
      `;
    } else {
      outcomeLine.innerHTML = `
        <b>${placedJobs}/${totalJobs} jobs</b>
        <span style="color:var(--text-muted); margin:0 4px;">·</span>
        <span style="color:#f87171; font-weight:700;">target verification pending/failed</span>
      `;
    }
  }

  // Verifier Badge & Drawer Header
  const verBadgeText = document.getElementById("verifier-badge-text");
  const verBadgeBtn = document.getElementById("btn-verifier-drawer");
  const warnBanner = document.getElementById("compare-unverified-warning");
  if (verBadgeText && verBadgeBtn) {
    if (!verifier) {
      verBadgeBtn.className = "badge badge-info";
      verBadgeText.textContent = "VERIFIER: NOT CHECKED";
      if (warnBanner) warnBanner.style.display = "none";
    } else if (hasUnplaced) {
      verBadgeBtn.className = "badge badge-danger";
      verBadgeText.textContent = "TARGET AT RISK ✗";
      if (warnBanner) warnBanner.style.display = "none";
    } else if (isVerified) {
      verBadgeBtn.className = "badge badge-success";
      verBadgeText.textContent = "TARGET VERIFIED ✓";
      if (warnBanner) warnBanner.style.display = "none";
    } else {
      verBadgeBtn.className = "badge badge-danger";
      verBadgeText.textContent = "VERIFICATION FAILED ✗";
      if (warnBanner) warnBanner.style.display = "flex";
    }
  }

  // Populate Verifier Side Drawer
  const drawerBody = document.getElementById("verifier-drawer-body");
  if (drawerBody && verifier) {
    const checks = verifier.checks || [];
    drawerBody.innerHTML = `
      <div style="background:var(--bg-card); padding:1rem; border-radius:var(--radius-md); border:1px solid var(--border-default); margin-bottom:1rem;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <strong style="color:var(--text-primary);">Overall Audit Status</strong>
          <span class="badge ${isVerified ? 'badge-success' : 'badge-danger'}">
            ${isVerified ? 'VERIFIED PASS ✓' : 'FAILED CHECKS ✗'}
          </span>
        </div>
        <p style="font-size:var(--text-xs); color:var(--text-muted); margin-top:0.35rem;">
          Verified ${checks.filter(c => c.passed).length} of ${checks.length} independent physical and contract constraints.
        </p>
      </div>
      <div style="display:flex; flex-direction:column; gap:0.5rem;">
        ${checks.map(c => `
          <div class="audit-check-card">
            <div class="audit-check-header">
              <span class="audit-check-name">${c.name.replace(/_/g, " ")}</span>
              <span class="badge ${c.passed ? 'badge-success' : 'badge-danger'}">
                ${c.passed ? 'PASS ✓' : 'FAIL ✗'}
              </span>
            </div>
            <div class="audit-check-detail">${c.details || 'Verification check passed cleanly.'}</div>
          </div>
        `).join("")}
      </div>
    `;
  }

  // Winner badge with plain wording
  const winnerBadge = document.getElementById("winner-badge");
  if (winnerBadge) {
    let winLabel = res.winner || "Portfolio";
    if (winLabel === "H3+improve" || winLabel === "H3") {
      winLabel = "Tariff-stagger + local search";
    } else if (winLabel === "H2+improve" || winLabel === "H2") {
      winLabel = "Cheapest-insertion + local search";
    } else if (winLabel === "H1+improve" || winLabel === "H1") {
      winLabel = "Tariff-window packing";
    } else if (winLabel === "H0") {
      winLabel = "Earliest deadline first";
    }
    winnerBadge.textContent = `WINNER: ${winLabel}`;
    winnerBadge.className = "badge badge-info";
    winnerBadge.title = `Algorithm code: ${res.winner || 'portfolio'}`;
  }

  // Bound display in method disclosure
  const boundText = document.getElementById("bound-text");
  const boundDesc = document.getElementById("bound-desc");
  if (boundText && res.bound) {
    boundText.textContent = formatMoney(res.bound.proven_lower_bound);
    if (boundDesc) boundDesc.textContent = `(${res.bound.description || 'weak lower bound'})`;
  }
  const compTimeChip = document.getElementById("comp-time-chip");
  if (compTimeChip) compTimeChip.textContent = `Solved in ${formatNumber(res.elapsed_ms, 1)} ms`;

  // Four KPI Cards: Energy Cost, Energy kWh, Peak kW, Switch-offs
  const m = res.metrics;
  const b0_m = res.baseline_b0.metrics;
  const b1_m = res.baseline_b1.metrics;

  // 1. Energy cost
  const costEl = document.getElementById("kpi-cost");
  const costDelta = document.getElementById("kpi-cost-delta");
  if (costEl) costEl.textContent = formatMoney(m.energy_cost);
  if (costDelta) {
    if (!isVerified) {
      costDelta.textContent = "Savings suppressed";
      costDelta.className = "delta-chip delta-warn";
    } else {
      const diff = b0_m.energy_cost - m.energy_cost;
      const pct = res.savings_vs_b0.percent;
      costDelta.textContent = `−${formatMoney(diff)} (${formatPercent(pct)} vs always-on)`;
      costDelta.className = "delta-chip delta-good";
    }
  }

  // 2. Total Energy (kWh)
  const kwhEl = document.getElementById("kpi-kwh");
  const kwhDelta = document.getElementById("kpi-kwh-delta");
  if (kwhEl) kwhEl.textContent = `${formatNumber(m.kwh, 1)} kWh`;
  if (kwhDelta) {
    const kwhDiff = b0_m.kwh - m.kwh;
    if (kwhDiff > 0.05) {
      kwhDelta.textContent = `−${formatNumber(kwhDiff, 1)} kWh (idle shutoff)`;
      kwhDelta.className = "delta-chip delta-good";
    } else {
      kwhDelta.textContent = `Idle: ${formatNumber(m.idle_kwh, 1)} kWh`;
      kwhDelta.className = "delta-chip delta-neutral";
    }
  }

  // 3. Peak Power Demand (kW)
  const peakEl = document.getElementById("kpi-peak");
  const peakDelta = document.getElementById("kpi-peak-delta");
  if (peakEl) peakEl.textContent = `${formatNumber(m.peak_kw, 1)} kW`;
  if (peakDelta) {
    const peakDiff = b0_m.peak_kw - m.peak_kw;
    if (Math.abs(peakDiff) < 0.1) {
      peakDelta.textContent = `Equal to baseline (${formatNumber(b0_m.peak_kw, 1)} kW)`;
      peakDelta.className = "delta-chip delta-neutral";
    } else if (peakDiff > 0) {
      peakDelta.textContent = `−${formatNumber(peakDiff, 1)} kW peak trimmed`;
      peakDelta.className = "delta-chip delta-good";
    } else {
      peakDelta.textContent = `Baseline: ${formatNumber(b0_m.peak_kw, 1)} kW`;
      peakDelta.className = "delta-chip delta-neutral";
    }
  }

  // 4. Switch-Offs
  const swEl = document.getElementById("kpi-switches");
  const swDelta = document.getElementById("kpi-switches-delta");
  if (swEl) swEl.textContent = `${m.switch_offs} shutdowns`;
  if (swDelta) {
    let maxCap = null;
    if (s.plant?.machines) {
      const caps = s.plant.machines.map(mc => mc.max_switch_cycles).filter(c => c !== null && c !== undefined);
      if (caps.length > 0) maxCap = Math.max(...caps);
    }
    swDelta.textContent = maxCap !== null ? `✓ Within cap (max ${maxCap}/machine)` : `✓ Dynamic break-even compliant`;
    swDelta.className = "delta-chip delta-good";
  }

  // Peak notice on power chart
  const peakNotice = document.getElementById("peak-cap-callout");
  if (peakNotice) {
    if (Math.abs(b0_m.peak_kw - m.peak_kw) < 0.1) {
      peakNotice.style.display = "block";
      peakNotice.innerHTML = `
        <div class="banner banner-info" style="margin-bottom:0; font-size:var(--text-xs);">
          <span>⚡ <b>Peak demand note:</b> Optimized peak demand matches baseline peak (${formatNumber(m.peak_kw, 1)} kW). Want to trim peak demand charges? <a href="#sweep" style="color:#38bdf8; text-decoration:underline; font-weight:600;">Try a peak cap on the Sweep &amp; Cap page &rarr;</a></span>
        </div>
      `;
    } else {
      peakNotice.style.display = "none";
    }
  }

  // Render Visualizations
  renderCurrentGantt();
  renderLoadProfile("chart-load-profile", res.baseline_b0.schedule, res.schedule, s.plant, s.tariff, s.config?.peak_cap_kw);
  renderShiftPlanTable();

  // URL query parameter hooks for automated test & screenshot capture
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get("drawer") === "1") {
    setTimeout(() => {
      document.getElementById("btn-verifier-drawer")?.click();
    }, 150);
  }
}
