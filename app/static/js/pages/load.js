/**
 * Load & Configure Page View Controller  —  PeakShift
 * Provides industrial preset selection, plant utilization adjustment,
 * electricity tariff review, and custom plant file upload (.json, .xlsx, or CSV pair).
 */

import { api } from "../api.js";
import { store, getTariffForRequest } from "../state.js";
import { formatMoney, formatNumber } from "../format.js";

export function initLoadPage() {
  const container = document.getElementById("page-load");
  if (!container) return;

  renderTemplate(container);
  bindEvents(container);
  fetchInitialData();
}

function renderTemplate(container) {
  const s = store.get();
  container.innerHTML = `
    <!-- Page Header & Run CTA -->
    <div class="page-header" style="margin-bottom:1rem;">
      <div>
        <h1 class="page-title">Factory &amp; Tariff Configuration</h1>
        <p class="page-desc">Select an industrial preset, upload plant data, or review electricity tariff rates.</p>
      </div>
      <div>
        <button id="btn-run-opt" class="btn btn-primary" style="font-size:var(--text-sm); font-weight:700;">
          <span>Run Optimization</span>
          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Three-Step Workflow Guidance Bar -->
    <div class="workflow-steps-bar" role="navigation" aria-label="Optimization workflow steps">
      <div class="workflow-step active">
        <span class="workflow-step-num">1</span>
        <span><b>Step 1:</b> Choose Plant</span>
      </div>
      <span class="workflow-step-arrow" aria-hidden="true">&rarr;</span>
      <div class="workflow-step">
        <span class="workflow-step-num">2</span>
        <span><b>Step 2:</b> Review Tariff</span>
      </div>
      <span class="workflow-step-arrow" aria-hidden="true">&rarr;</span>
      <div class="workflow-step">
        <span class="workflow-step-num">3</span>
        <span><b>Step 3:</b> Run &amp; Compare</span>
      </div>
    </div>

    <!-- Inline Error Banner -->
    <div id="load-error-banner" class="alert alert-danger" style="display:none; margin-bottom:1.5rem; align-items:flex-start; justify-content:space-between;" role="alert">
      <div id="load-error-content" style="flex:1;"></div>
      <button type="button" class="btn-close" id="btn-dismiss-load-error" aria-label="Dismiss error" style="background:transparent; border:none; color:inherit; font-size:1.25rem; line-height:1; cursor:pointer; padding:0 0 0 0.75rem;">&times;</button>
    </div>

    <div class="grid-2" style="margin-bottom:1.5rem;">
      <!-- ── Left Column: Plant Configuration ── -->
      <div class="card">
        <h2 class="card-title">1. Plant Architecture &amp; Machines</h2>

        <!-- Industrial Preset Selector -->
        <div class="form-group" style="margin-bottom:1.25rem;">
          <label class="form-label" style="margin-bottom:0.5rem;">Select Industry Preset</label>
          <div class="preset-selector" id="preset-selector" role="radiogroup" aria-label="Industry Preset">
            <!-- Textile -->
            <div class="preset-card ${s.preset === 'textile' ? 'active' : ''}" data-preset="textile" role="radio" aria-checked="${s.preset === 'textile'}" tabindex="0">
              <div class="preset-card-title">Textile Mill</div>
              <div class="preset-card-meta">
                <b>10</b> machines &bull; <b>75</b> jobs<br>
                Typical Load: <b>60%</b>
              </div>
            </div>
            <!-- Machining -->
            <div class="preset-card ${s.preset === 'machining' ? 'active' : ''}" data-preset="machining" role="radio" aria-checked="${s.preset === 'machining'}" tabindex="0">
              <div class="preset-card-title">CNC Machining</div>
              <div class="preset-card-meta">
                <b>8</b> machines &bull; <b>69</b> jobs<br>
                Typical Load: <b>70%</b>
              </div>
            </div>
            <!-- Food Lines -->
            <div class="preset-card ${s.preset === 'food_lines' ? 'active' : ''}" data-preset="food_lines" role="radio" aria-checked="${s.preset === 'food_lines'}" tabindex="0">
              <div class="preset-card-title">Food Processing</div>
              <div class="preset-card-meta">
                <b>6</b> machines &bull; <b>35</b> jobs<br>
                Typical Load: <b>50%</b>
              </div>
            </div>
          </div>
        </div>

        <!-- Plant Utilization Slider with Visible Value -->
        <div class="form-group" style="margin-bottom:1.25rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.35rem;">
            <label class="form-label" for="input-utilization" style="margin-bottom:0;">Plant Utilization Target</label>
            <span id="label-utilization" class="num" style="font-weight:700; color:#38bdf8; font-size:var(--text-sm);">${Math.round(s.utilization * 100)}% load</span>
          </div>
          <input type="range" class="form-range" id="input-utilization" min="0.2" max="0.95" step="0.05" value="${s.utilization}" aria-label="Plant utilization target">
          <p style="font-size:var(--text-xs); color:var(--text-muted); margin-top:0.25rem;">Adjusts job density across the production horizon (0.20 to 0.95).</p>
        </div>

        <!-- Scenario Seed Input -->
        <div class="form-group" style="margin-bottom:1.25rem;">
          <label class="form-label" for="input-seed">Scenario Seed</label>
          <input type="number" class="form-control num" id="input-seed" value="${s.seed}" min="1" max="99999" placeholder="42">
          <p style="font-size:var(--text-xs); color:var(--text-muted); margin-top:0.25rem;">Controls repeatable job release times and deadlines.</p>
        </div>

        <!-- File Upload Section -->
        <div style="margin: 1.25rem 0; border-top: 1px solid var(--border-default); padding-top: 1rem;">
          <label class="form-label">Or Upload Custom Plant File (.json, .xlsx, or CSV pair)</label>
          <div class="dropzone" id="file-dropzone" tabindex="0" role="button" aria-label="Upload custom plant file">
            <svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin:0 auto 0.5rem auto; color:var(--text-muted);">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
            </svg>
            <p style="font-weight:600; color:var(--text-primary);">Click or drag file here to upload</p>
            <p style="font-size:var(--text-xs); color:var(--text-muted); margin-top:0.25rem;">Supports JSON, Excel (.xlsx) or CSV pair</p>
            <input type="file" id="input-file-upload" accept=".json,.xlsx,.csv" multiple style="display:none;">
          </div>
          <div id="upload-status" style="margin-top:0.5rem; font-size:var(--text-xs);"></div>
        </div>

        <!-- Plant Summary Badge Card -->
        <div id="plant-summary" style="padding:0.75rem 1rem; background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-md);">
          <div style="font-weight:700; font-size:var(--text-sm); margin-bottom:0.25rem;">Active Plant: <span id="summary-plant-name" style="color:#38bdf8;">Loading...</span></div>
          <div style="font-size:var(--text-xs); color:var(--text-secondary);" id="summary-plant-stats">Fetching configuration...</div>
        </div>
      </div>

      <!-- ── Right Column: Tariff Configuration ── -->
      <div class="card">
        <h2 class="card-title">2. Electricity Tariff (TNERC HT-I)</h2>

        <!-- Tariff Rate Inputs -->
        <div class="grid-2" style="margin-bottom:1rem;">
          <div class="form-group">
            <label class="form-label" for="tariff-base-rate">Base Energy Rate (₹/kWh)</label>
            <input type="number" step="0.05" class="form-control num" id="tariff-base-rate" value="7.50">
          </div>
          <div class="form-group">
            <label class="form-label" for="tariff-tax-rate">Electricity Tax</label>
            <input type="text" class="form-control num" id="tariff-tax-rate" value="5%" readonly title="Statutory 5% electricity tax">
          </div>
        </div>

        <!-- 24-Hour Tariff Visual Strip -->
        <div style="margin-bottom:0.75rem;">
          <div style="display:flex; justify-content:space-between; margin-bottom:0.35rem; font-size:var(--text-xs); color:var(--text-secondary);">
            <span>24-Hour Rate Timeline</span>
            <span>00:00 &rarr; 24:00</span>
          </div>
          <div class="tariff-strip">
            <div class="tariff-strip-block offpeak" style="width:20.83%;" title="00:00 - 05:00 Off-Peak (0.95x) ₹7.13/kWh">00–05 Off-Peak</div>
            <div class="tariff-strip-block normal" style="width:4.17%;" title="05:00 - 06:00 Normal (1.00x) ₹7.50/kWh">05–06</div>
            <div class="tariff-strip-block peak" style="width:16.67%;" title="06:00 - 10:00 Peak (+25%) ₹9.38/kWh">06–10 Peak</div>
            <div class="tariff-strip-block normal" style="width:33.33%;" title="10:00 - 18:00 Normal (1.00x) ₹7.50/kWh">10–18 Normal</div>
            <div class="tariff-strip-block peak" style="width:16.67%;" title="18:00 - 22:00 Peak (+25%) ₹9.38/kWh">18–22 Peak</div>
            <div class="tariff-strip-block offpeak" style="width:8.33%;" title="22:00 - 24:00 Off-Peak (0.95x) ₹7.13/kWh">22–24</div>
          </div>
        </div>

        <!-- Complete 24-Hour Tariff Table -->
        <div class="table-responsive" style="margin-bottom:1rem;">
          <table class="data-table" id="tariff-windows-table">
            <thead>
              <tr>
                <th>Window Band</th>
                <th>Clock Hours</th>
                <th>Multiplier</th>
                <th>Effective Rate</th>
              </tr>
            </thead>
            <tbody id="tariff-windows-body">
              <tr><td colspan="4" style="text-align:center; color:var(--text-muted);">Loading tariff structure...</td></tr>
            </tbody>
          </table>
        </div>

        <!-- Tariff Legal Source Note -->
        <div style="font-size:var(--text-xs); color:var(--text-muted); line-height:1.5; padding:0.6rem 0.75rem; background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-md);">
          <strong style="color:var(--text-secondary);">Tariff Source:</strong> Tamil Nadu Electricity Regulatory Commission (TNERC HT-I Industrial Tariff), Comprehensive Tariff Order No. 6 of 2025.<br>
          <span style="color:var(--text-muted);">* Peak windows (06:00–10:00, 18:00–22:00) incur a 25% surcharge (1.25x = ₹9.38/kWh). Off-peak (22:00–05:00) carries a 5% rebate (0.95x = ₹7.13/kWh).</span>
        </div>
      </div>
    </div>
  `;
}

export function applyPlantObject(plant) {
  if (!plant || !plant.machines || !plant.jobs) {
    throw new Error("Invalid plant configuration: 'machines' and 'jobs' arrays are required.");
  }
  store.set({ plant });

  const statusEl = document.getElementById("upload-status");
  if (statusEl) {
    statusEl.innerHTML = `<span style="color:#34d399; font-weight:600;">✓ Successfully loaded ${plant.name || "Custom Plant"} (${plant.machines.length} machines, ${plant.jobs.length} jobs)</span>`;
  }

  const summaryName = document.getElementById("summary-plant-name");
  const summaryStats = document.getElementById("summary-plant-stats");
  if (summaryName) summaryName.textContent = plant.name || "Custom Plant";
  if (summaryStats) {
    const totalUnits = (plant.jobs || []).reduce((acc, j) => acc + (j.units || 1), 0);
    summaryStats.innerHTML = `
      <b>${plant.machines?.length || 0}</b> Machines &bull;
      <b>${plant.jobs?.length || 0}</b> Jobs &bull;
      <b>${totalUnits}</b> Total Units &bull;
      Horizon: <b>${plant.horizon_slots || 48}</b> slots (${plant.slot_minutes || 30} min/slot)
    `;
  }

  const eb = document.getElementById("load-error-banner");
  if (eb) eb.style.display = "none";

  const notice = document.getElementById("summary-rejection-notice");
  if (notice) notice.remove();
}

function bindEvents(container) {
  // Preset selector cards
  const presetSelector = container.querySelector("#preset-selector");
  presetSelector?.addEventListener("click", (e) => {
    const card = e.target.closest(".preset-card");
    if (!card) return;
    const preset = card.dataset.preset;
    presetSelector.querySelectorAll(".preset-card").forEach(c => {
      c.classList.remove("active");
      c.setAttribute("aria-checked", "false");
    });
    card.classList.add("active");
    card.setAttribute("aria-checked", "true");
    store.set({ preset });
    triggerGenerate();
  });

  // Preset keyboard accessibility (Enter / Space to select)
  presetSelector?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const card = e.target.closest(".preset-card");
      if (card) {
        e.preventDefault();
        card.click();
      }
    }
  });

  // Utilization slider
  const utilInput = container.querySelector("#input-utilization");
  const utilLabel = container.querySelector("#label-utilization");
  utilInput?.addEventListener("input", (e) => {
    const val = parseFloat(e.target.value);
    const pct = Math.round(val * 100);
    if (utilLabel) {
      let descriptor = "Moderate";
      if (pct < 45) descriptor = "Low load";
      else if (pct > 75) descriptor = "High stress";
      else descriptor = "Standard load";
      utilLabel.textContent = `${pct}% (${descriptor})`;
    }
    store.set({ utilization: val });
  });
  utilInput?.addEventListener("change", () => {
    triggerGenerate();
  });

  // Scenario seed input
  const seedInput = container.querySelector("#input-seed");
  seedInput?.addEventListener("change", (e) => {
    const seed = parseInt(e.target.value, 10) || 42;
    store.set({ seed });
    triggerGenerate();
  });

  // Base energy rate input
  const baseRateInput = container.querySelector("#tariff-base-rate");
  baseRateInput?.addEventListener("input", (e) => {
    const rate = parseFloat(e.target.value) || 7.50;
    const s = store.get();
    if (s.tariff) {
      const updatedTariff = getTariffForRequest({ ...s.tariff, base_rate_per_kwh: rate, base_rate: rate });
      store.set({ tariff: updatedTariff });
      renderTariffTable(updatedTariff);
    }
  });

  // Dropzone drag-and-drop
  const dropzone = container.querySelector("#file-dropzone");
  const fileInput = container.querySelector("#input-file-upload");

  dropzone?.addEventListener("click", () => fileInput?.click());
  dropzone?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput?.click();
    }
  });

  dropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone?.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone?.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files?.length) {
      handleFileUpload(e.dataTransfer.files);
    }
  });

  fileInput?.addEventListener("change", (e) => {
    if (e.target.files?.length) {
      handleFileUpload(e.target.files);
      e.target.value = "";
    }
  });

  // Run Optimization button
  const runBtn = container.querySelector("#btn-run-opt");
  runBtn?.addEventListener("click", runOptimization);

  // Dismiss error button
  container.querySelector("#btn-dismiss-load-error")?.addEventListener("click", () => {
    const eb = document.getElementById("load-error-banner");
    if (eb) eb.style.display = "none";
    const notice = document.getElementById("summary-rejection-notice");
    if (notice) notice.remove();
  });
}

async function fetchInitialData() {
  const s = store.get();

  // 1. Fetch tariff
  try {
    const tariffRes = await api.tariffs();
    const rawTariff = tariffRes.default_tariff || (tariffRes.tariffs && tariffRes.tariffs[0]) || tariffRes.tariff || tariffRes;
    const tariff = getTariffForRequest(rawTariff);
    store.set({ tariff, defaultTariff: tariff });
    renderTariffTable(tariff);
  } catch (err) {
    showError("Tariff Load Error", err);
  }

  // 2. Generate initial plant
  await triggerGenerate();
}

async function triggerGenerate() {
  const s = store.get();
  const summaryName = document.getElementById("summary-plant-name");
  const summaryStats = document.getElementById("summary-plant-stats");
  if (summaryName) summaryName.textContent = "Generating scenario…";

  try {
    const res = await api.generatePlant({
      preset: s.preset,
      utilization: s.utilization,
      seed: s.seed,
    });
    store.set({ plant: res.plant });

    if (summaryName) summaryName.textContent = res.plant.name || s.preset;
    if (summaryStats) {
      const totalUnits = (res.plant.jobs || []).reduce((acc, j) => acc + (j.units || 1), 0);
      summaryStats.innerHTML = `
        <b>${res.plant.machines?.length || 0}</b> Machines &bull;
        <b>${res.plant.jobs?.length || 0}</b> Jobs &bull;
        <b>${totalUnits}</b> Total Units &bull;
        Horizon: <b>${res.plant.horizon_slots}</b> slots (${res.plant.slot_minutes} min/slot)
      `;
    }

    const eb = document.getElementById("load-error-banner");
    if (eb) eb.style.display = "none";
    const notice = document.getElementById("summary-rejection-notice");
    if (notice) notice.remove();
  } catch (err) {
    showError("Plant Generation Failed", err);
  }
}

function validatePlantJson(p) {
  const errors = [];
  if (!p || typeof p !== "object" || Array.isArray(p)) {
    return ["Invalid plant data: expected a JSON object containing 'machines' and 'jobs'."];
  }
  const slotMinutes = p.slot_minutes || 30;
  if (60 % slotMinutes !== 0) {
    errors.push(`slot_minutes (${slotMinutes}) must divide 60 evenly`);
  }
  const horizonSlots = p.horizon_slots || 48;
  if (horizonSlots < 1) {
    errors.push("horizon_slots must be at least 1");
  }
  if (horizonSlots * slotMinutes > 4320) {
    errors.push(`Horizon length (${horizonSlots * slotMinutes} mins) exceeds maximum 3 days (4320 mins)`);
  }
  if (!Array.isArray(p.machines) || p.machines.length === 0) {
    errors.push("Plant must contain at least one machine");
  }
  if (!Array.isArray(p.jobs) || p.jobs.length === 0) {
    errors.push("Plant must contain at least one job");
  }

  const machineIds = new Set();
  if (Array.isArray(p.machines)) {
    p.machines.forEach((m, idx) => {
      const row = idx + 1;
      if (!m || typeof m !== "object") {
        errors.push(`Machine at index ${row} is not an object`);
        return;
      }
      const mid = String(m.id || "").trim();
      if (!mid) {
        errors.push(`Machine at index ${row}: missing required 'id'`);
      } else if (machineIds.has(mid)) {
        errors.push(`Duplicate machine id: '${mid}'`);
      } else {
        machineIds.add(mid);
      }
      if (m.run_kw === undefined || m.run_kw === null || isNaN(Number(m.run_kw)) || Number(m.run_kw) < 0) {
        errors.push(`Machine '${mid || row}': run_kw must be a non-negative number`);
      }
      if (m.idle_kw === undefined || m.idle_kw === null || isNaN(Number(m.idle_kw)) || Number(m.idle_kw) < 0) {
        errors.push(`Machine '${mid || row}': idle_kw must be a non-negative number`);
      }
      if (m.restart_kwh === undefined || m.restart_kwh === null || isNaN(Number(m.restart_kwh)) || Number(m.restart_kwh) < 0) {
        errors.push(`Machine '${mid || row}': restart_kwh must be a non-negative number`);
      }
    });
  }

  const jobIds = new Set();
  if (Array.isArray(p.jobs)) {
    p.jobs.forEach((j, idx) => {
      const row = idx + 1;
      if (!j || typeof j !== "object") {
        errors.push(`Job at index ${row} is not an object`);
        return;
      }
      const jid = String(j.id || "").trim();
      if (!jid) {
        errors.push(`Job at index ${row}: missing required 'id'`);
      } else if (jobIds.has(jid)) {
        errors.push(`Duplicate job id: '${jid}'`);
      } else {
        jobIds.add(jid);
      }
      const dur = Number(j.duration_slots);
      if (isNaN(dur) || dur < 1) {
        errors.push(`Job '${jid || row}': duration_slots must be at least 1`);
      }
      const rel = Number(j.release_slot !== undefined ? j.release_slot : 0);
      const dead = Number(j.deadline_slot !== undefined ? j.deadline_slot : horizonSlots);
      if (dead > horizonSlots) {
        errors.push(`Job ${jid}: deadline_slot (${dead}) > horizon (${horizonSlots})`);
      }
      if (rel >= dead) {
        errors.push(`Job ${jid}: release slot ${rel} is after deadline slot ${dead}`);
      }
      if (dur > (dead - rel)) {
        errors.push(`Job ${jid}: duration (${dur}) larger than feasible window (${dead - rel})`);
      }
      if (Array.isArray(j.allowed_machines)) {
        for (const mid of j.allowed_machines) {
          if (!machineIds.has(String(mid).trim())) {
            errors.push(`Job ${jid}: machine '${mid}' does not exist`);
          }
        }
      }
    });
  }

  return errors;
}

async function handleFileUpload(files) {
  if (!files) return;
  const fileList = Array.isArray(files) ? files : (files instanceof FileList ? Array.from(files) : [files]);
  if (fileList.length === 0) return;

  const statusEl = document.getElementById("upload-status");
  const fileNames = fileList.map(f => f.name).join(", ");
  if (statusEl) statusEl.innerHTML = `<span style="color:#38bdf8;">Loading and parsing ${fileNames}...</span>`;

  // Direct client-side JSON parse with full schema & integrity validation
  if (fileList.length === 1 && fileList[0].name.toLowerCase().endsWith(".json")) {
    try {
      const text = await fileList[0].text();
      let p;
      try {
        p = JSON.parse(text);
      } catch (parseErr) {
        const msg = `Invalid JSON syntax: ${parseErr.message}`;
        if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">${msg}</span>`;
        showError("Invalid JSON File", { message: msg, details: [msg] });
        return;
      }

      const valErrors = validatePlantJson(p);
      if (valErrors.length > 0) {
        const primary = valErrors[0];
        if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">Validation Error: ${primary}</span>`;
        showError("Plant Validation Error", { message: primary, details: valErrors }, true);
        return;
      }

      applyPlantObject(p);
      return;
    } catch (err) {
      if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">JSON Error: ${err.message}</span>`;
      showError("Invalid JSON File", { message: err.message, details: [err.message] }, true);
      return;
    }
  }

  const fd = new FormData();
  if (fileList.length === 1) {
    const f = fileList[0];
    const nameLower = f.name.toLowerCase();
    if (nameLower.endsWith(".csv")) {
      try {
        const text = await f.text();
        const firstLine = (text.split(/\r?\n/)[0] || "").toLowerCase();
        const isMachines = firstLine.includes("run_kw") || firstLine.includes("idle_kw") || firstLine.includes("machine") || nameLower.includes("machine");
        const isJobs = firstLine.includes("duration") || firstLine.includes("deadline") || firstLine.includes("job") || nameLower.includes("job");
        let msg = "";
        if (isMachines && !isJobs) {
          msg = "Only the machines CSV was selected. Please select both machines and jobs CSV files together (the jobs file is missing).";
        } else if (isJobs && !isMachines) {
          msg = "Only the jobs CSV was selected. Please select both machines and jobs CSV files together (the machines file is missing).";
        } else {
          msg = "CSV uploads require both machines and jobs files. Please select both CSV files together.";
        }
        if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">${msg}</span>`;
        showError("File Upload Validation Error", { message: msg, details: [msg] }, true);
        return;
      } catch (_) {
        const msg = "CSV uploads require both machines and jobs files. Please select both files together.";
        if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">${msg}</span>`;
        showError("File Upload Validation Error", { message: msg, details: [msg] }, true);
        return;
      }
    }
    fd.append("file", f);
  } else {
    let machinesFile = null;
    let jobsFile = null;
    for (const f of fileList) {
      const lower = f.name.toLowerCase();
      if (lower.includes("machine")) {
        machinesFile = f;
      } else if (lower.includes("job")) {
        jobsFile = f;
      }
    }
    if (!machinesFile || !jobsFile) {
      machinesFile = machinesFile || fileList[0];
      jobsFile = jobsFile || fileList[1];
    }
    fd.append("machines_file", machinesFile);
    fd.append("jobs_file", jobsFile);
  }

  try {
    const res = await api.uploadPlant(fd);
    applyPlantObject(res.plant);
  } catch (err) {
    let mainMsg = (err.details && err.details.length === 1) ? err.details[0] : (err.message || 'Validation error');
    if (mainMsg === "Failed to fetch") {
      // If network unreachable, but it's a JSON file, fallback to client-side parse
      if (fileList.length === 1 && fileList[0].name.toLowerCase().endsWith(".json")) {
        try {
          const text = await fileList[0].text();
          const p = JSON.parse(text);
          if (p.machines && p.jobs) {
            applyPlantObject(p);
            return;
          }
        } catch (_) {}
      }
      mainMsg = "Cannot connect to backend server. Ensure FastAPI is running on port 8000.";
    }
    if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">Upload failed: ${mainMsg}</span>`;
    showError("File Upload Validation Error", { ...err, message: mainMsg }, true);
  }
}

function renderTariffTable(tariff) {
  const tbody = document.getElementById("tariff-windows-body");
  if (!tbody || !tariff) return;

  const baseRate = tariff.base_rate_per_kwh || tariff.base_rate || 7.50;

  // Complete 24-hour tariff schedule covering all slots
  const fullWindows = [
    { name: "OFF-PEAK (Night)", start: 0, end: 5, multiplier: 0.95 },
    { name: "NORMAL (Early Morning)", start: 5, end: 6, multiplier: 1.00 },
    { name: "PEAK (Morning Window)", start: 6, end: 10, multiplier: 1.25 },
    { name: "NORMAL (Daytime)", start: 10, end: 18, multiplier: 1.00 },
    { name: "PEAK (Evening Window)", start: 18, end: 22, multiplier: 1.25 },
    { name: "OFF-PEAK (Late Night)", start: 22, end: 24, multiplier: 0.95 },
  ];

  tbody.innerHTML = fullWindows.map(w => {
    const effRate = baseRate * w.multiplier;
    const isPeak = w.multiplier > 1.0;
    const isOffPeak = w.multiplier < 1.0;
    const badgeClass = isPeak ? "badge-danger" : (isOffPeak ? "badge-success" : "badge-info");
    const badgeText = isPeak ? "PEAK (+25%)" : (isOffPeak ? "OFF-PEAK (-5%)" : "NORMAL (BASE)");

    return `
      <tr>
        <td><span class="badge ${badgeClass}">${badgeText}</span></td>
        <td class="font-mono"><b>${String(w.start).padStart(2, '0')}:00 &ndash; ${String(w.end).padStart(2, '0')}:00</b></td>
        <td class="num">${w.multiplier.toFixed(2)}x</td>
        <td class="num"><b>₹${effRate.toFixed(2)}</b> / kWh</td>
      </tr>
    `;
  }).join("");
}

async function runOptimization() {
  const s = store.get();
  if (!s.plant) {
    showError("Optimization Error", { message: "Plant data must be loaded before running optimization." });
    return;
  }

  let requestTariff;
  try {
    requestTariff = getTariffForRequest(s.tariff);
  } catch (err) {
    showError("Tariff Configuration Error", err);
    return;
  }

  const runBtn = document.getElementById("btn-run-opt");
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.innerHTML = `<span class="spinner"></span> <span>Optimizing...</span>`;
  }

  try {
    const result = await api.optimize({
      plant: s.plant,
      tariff: requestTariff,
      config: s.config,
    });
    store.set({ lastResult: result });
    if (window.location.hash === "#load" || !window.location.hash) {
      window.location.hash = "#compare";
    }
  } catch (err) {
    showError("Optimization Failed", err);
  } finally {
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.innerHTML = `<span>Run Optimization</span> <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>`;
    }
  }
}

function showError(title, err, isUploadRejection = false) {
  const eb = document.getElementById("load-error-banner");
  const ec = document.getElementById("load-error-content");
  if (!eb || !ec) return;

  let detailsHtml = "";
  if (err.details && Array.isArray(err.details) && (err.details.length > 1 || err.details[0] !== err.message)) {
    detailsHtml = `<ul class="error-list" style="margin-top:0.5rem;">${err.details.map((d) => {
      const isRow = typeof d === "string" && d.toLowerCase().includes("row");
      return `<li>${isRow ? `<b>${d}</b>` : d}</li>`;
    }).join("")}</ul>`;
  }

  ec.innerHTML = `<strong>${title}:</strong> ${err.message || 'Unknown error'}${detailsHtml}`;
  eb.style.display = "flex";

  if (isUploadRejection) {
    const s = store.get();
    const currentPlantName = s.plant?.name || "Textile Mill Demo";
    const summaryCard = document.getElementById("plant-summary");
    let notice = document.getElementById("summary-rejection-notice");
    if (!notice && summaryCard) {
      notice = document.createElement("div");
      notice.id = "summary-rejection-notice";
      notice.style.marginTop = "0.35rem";
      notice.style.fontSize = "var(--text-xs)";
      notice.style.color = "#f87171";
      notice.style.fontWeight = "600";
      summaryCard.appendChild(notice);
    }
    if (notice) {
      notice.textContent = `Upload rejected. The previous plant (${currentPlantName}) is still loaded.`;
      notice.style.display = "block";
    }
  }
}
