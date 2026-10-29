/**
 * Sweep & Peak Limit Page — Step 5 upgrade
 *
 * - Auto-runs sweep on first visit when plant has ≤ 10 machines (small plant)
 * - Skeleton loaders instead of spinners
 * - Sweep chart: both baselines + spread band, generated insight sentence,
 *   current plant utilization marked
 * - Pareto: auto-runs with 5 caps, recommended point marked, summary table beside
 * - Slider label explains what the cap means in plain English
 */

import { api } from "../api.js";
import { store } from "../state.js";
import { formatNumber, formatMoney } from "../format.js";

let _autoSweepDone = false;
let _autoPaRetoDone = false;
let _sweepAbort = null;
let _paretoAbort = null;

export function initSweepPage() {
  const container = document.getElementById("page-sweep");
  if (!container) return;

  renderTemplate(container);
  bindEvents(container);
  updateContent();

  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#sweep") {
      requestAnimationFrame(() => {
        updateContent();
        maybeAutoRun(container);
      });
    }
  });

  store.subscribe(() => {
    if (window.location.hash === "#sweep") {
      updateContent();
    }
  });

  if (window.location.hash === "#sweep") {
    requestAnimationFrame(() => maybeAutoRun(container));
  }
}

async function maybeAutoRun(container) {
  const s = store.get();
  if (!s.plant || !s.tariff) return;

  const machineCount = s.plant.machines?.length ?? 0;
  const isSmall = machineCount <= 10;

  if (isSmall && !_autoSweepDone) {
    _autoSweepDone = true;
    await runSweep(container);
  }
  if (isSmall && !_autoPaRetoDone) {
    _autoPaRetoDone = true;
    await runPareto(container);
  }
}

function renderTemplate(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1 class="page-title">Utilization sweep &amp; peak cap</h1>
        <p class="page-desc">How savings change with factory load, and what capping peak demand costs.</p>
      </div>
      <button id="btn-run-sweep" class="btn btn-secondary" aria-label="Re-run utilization sweep">
        <span>Re-run sweep</span>
      </button>
    </div>

    <!-- ── Pareto section ──────────────────────────────────── -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:0.75rem;">
        <div>
          <h2 class="card-title" style="margin-bottom:0.25rem;">Peak cap — cost vs demand trade-off</h2>
          <p id="pareto-cap-description" style="font-size:0.8125rem;color:var(--text-secondary);margin:0;">
            Move the slider to see how a power cap changes cost.
          </p>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:0.5rem;">
          <div style="display:flex;gap:0.5rem;align-items:center;">
            <label class="form-label" for="slider-peak-cap" style="margin:0;white-space:nowrap;">Cap limit:</label>
            <span id="label-peak-cap" class="chip chip-info"
              style="min-width:80px;text-align:center;font-variant-numeric:tabular-nums;">
              No cap
            </span>
          </div>
          <input type="range" class="form-range" id="slider-peak-cap"
            min="50" max="350" step="10" value="200" style="width:220px;"
            aria-label="Peak power cap in kilowatts">
          <p style="font-size:0.6875rem;color:var(--text-muted);margin:0;">
            Re-optimizes on release, not on every tick.
          </p>
        </div>
      </div>

      <div style="display:flex;gap:1.5rem;align-items:flex-start;flex-wrap:wrap;">
        <div id="chart-pareto-frontier" class="chart-container"
          style="flex:1 1 420px;min-height:340px;">
          <div class="skeleton-chart" aria-label="Loading Pareto chart"></div>
        </div>
        <div id="pareto-table-aside" style="flex:0 0 240px;min-width:180px;">
          <div class="skeleton-block" style="height:200px;border-radius:8px;" aria-label="Loading"></div>
        </div>
      </div>

      <div id="pareto-status"
        style="font-size:0.8125rem;color:var(--text-muted);margin-top:0.5rem;"
        aria-live="polite"></div>
    </div>

    <!-- ── Sweep section ──────────────────────────────────── -->
    <div class="card">
      <h2 class="card-title">Savings across factory load levels</h2>
      <p id="sweep-insight"
        style="font-size:0.9375rem;color:var(--text-primary);font-weight:500;margin-bottom:1rem;min-height:1.4em;"
        aria-live="polite"></p>

      <div id="chart-sweep-curve" class="chart-container" style="min-height:380px;">
        <div class="skeleton-chart" aria-label="Running sweep"></div>
      </div>

      <details class="method-disclosure" style="margin-top:1.25rem;">
        <summary>Method — how these numbers are computed</summary>
        <div class="method-body">
          <p><strong>Baselines</strong></p>
          <ul>
            <li><strong>B0 — Always-On:</strong> Every machine runs continuously for the full 24-hour horizon. No scheduling. Maximum possible energy cost.</li>
            <li><strong>B1 — Smart Standby:</strong> Machines switch off only when no job is queued. Represents an attentive human operator.</li>
            <li><strong>PeakShift:</strong> The optimizer. Moves jobs to off-peak windows, groups shutdowns to exceed the break-even threshold, and obeys all machine constraints.</li>
          </ul>
          <p><strong>Spread band</strong> — shaded area covers the min and max savings across 5 random seeds at each utilization level.</p>
          <p><strong>Plant</strong> — simulated with the selected preset and TNERC HT-I Industrial Tariff (Tariff Order No. 6 of 2025). Treat as directional guidance.</p>
          <p><strong>Simplified restart model</strong> — warm-up energy and minimum off-time penalties included; thermal losses and mechanical start loads are not modelled.</p>
        </div>
      </details>
    </div>
  `;
}

function bindEvents(container) {
  const capSlider = container.querySelector("#slider-peak-cap");
  const capLabel  = container.querySelector("#label-peak-cap");
  const capDesc   = container.querySelector("#pareto-cap-description");

  capSlider?.addEventListener("input", (e) => {
    const val = parseFloat(e.target.value);
    if (capLabel) capLabel.textContent = `${formatNumber(val, 0)} kW`;
    if (capDesc) capDesc.textContent = describeCapEffect(val, store.get());
  });

  capSlider?.addEventListener("change", async (e) => {
    const val = parseFloat(e.target.value);
    const s = store.get();
    if (!s.plant || !s.tariff) {
      window.location.hash = "#load";
      return;
    }
    if (capLabel) capLabel.textContent = `${formatNumber(val, 0)} kW`;
    if (capDesc) capDesc.textContent = describeCapEffect(val, s);

    const paretoEl = document.getElementById("chart-pareto-frontier");
    if (paretoEl) showSkeletonChart(paretoEl);

    try {
      const optRes = await api.optimize({
        plant: s.plant,
        tariff: s.tariff,
        config: { ...s.config, peak_cap_kw: val },
      });
      store.set({ lastResult: optRes });
      const cur = store.get();
      renderParetoPlotEnhanced("chart-pareto-frontier", cur.paretoResult?.points || [], val, optRes.metrics);
      renderParetoTable("pareto-table-aside", cur.paretoResult?.points || [], val);
    } catch (err) {
      const el = document.getElementById("chart-pareto-frontier");
      if (el) el.innerHTML = `<div class="loading-overlay" style="color:#f87171;">Optimization failed: ${err.message || err}</div>`;
    }
  });

  container.querySelector("#btn-run-sweep")?.addEventListener("click", () => {
    _autoSweepDone = false;
    runSweep(container);
  });
}

async function runSweep(container) {
  const s = store.get();
  if (!s.tariff) return;

  const btn = container.querySelector("#btn-run-sweep");
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span><span>Sweeping…</span>`; }

  const sweepEl = document.getElementById("chart-sweep-curve");
  if (sweepEl) showSkeletonChart(sweepEl, "Running sweep across 6 utilization levels (30–83%)…");

  try {
    if (_sweepAbort) _sweepAbort.abort();
    _sweepAbort = new AbortController();

    const sweepRes = await api.sweep({
      preset: s.preset || "textile",
      tariff: s.tariff,
      utilizations: [0.30, 0.40, 0.50, 0.60, 0.70, 0.83],
    });

    const sweepPoints = sweepRes.result.levels.map(lvl => ({
      utilization:       lvl.utilization,
      savings_vs_b0_pct: lvl.mean_savings_b0_pct,
      savings_vs_b1_pct: lvl.mean_savings_b1_pct,
      min_b0: lvl.min_savings_b0_pct,
      max_b0: lvl.max_savings_b0_pct,
      min_b1: lvl.min_savings_b1_pct ?? lvl.mean_savings_b1_pct,
      max_b1: lvl.max_savings_b1_pct ?? lvl.mean_savings_b1_pct,
    }));

    store.set({ sweepResult: sweepPoints });
    const currentUtil = s.config?.utilization ?? null;
    renderSweepChartEnhanced("chart-sweep-curve", sweepPoints, currentUtil);
    renderSweepInsight(sweepPoints, currentUtil);
  } catch (err) {
    if (err.name === "AbortError") return;
    const el = document.getElementById("chart-sweep-curve");
    if (el) el.innerHTML = `<div class="loading-overlay" style="color:#f87171;">Sweep failed: ${err.message || "Unknown error"}</div>`;
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `<span>Re-run sweep</span>`; }
  }
}

async function runPareto(container) {
  const s = store.get();
  if (!s.plant || !s.tariff) return;

  const statusEl = container.querySelector("#pareto-status");
  if (statusEl) statusEl.textContent = "Tracing Pareto frontier across 5 peak caps…";

  const paretoEl = document.getElementById("chart-pareto-frontier");
  if (paretoEl) showSkeletonChart(paretoEl, "Solving 5 multi-objective schedule variants…");

  const basePeak = s.lastResult?.metrics?.peak_kw
    ?? (s.plant.machines ?? []).reduce((a, m) => a + (m.run_kw ?? 0), 0);

  const caps = [
    Math.max(50, Math.round(basePeak * 0.65)),
    Math.max(50, Math.round(basePeak * 0.75)),
    Math.max(50, Math.round(basePeak * 0.85)),
    Math.max(50, Math.round(basePeak * 0.95)),
    Math.max(50, Math.round(basePeak * 1.10)),
  ].filter((v, i, a) => a.indexOf(v) === i);

  try {
    if (_paretoAbort) _paretoAbort.abort();
    _paretoAbort = new AbortController();

    const res = await api.pareto({
      plant: s.plant,
      tariff: s.tariff,
      caps,
      seed: s.seed,
    });
    store.set({ paretoResult: res });

    const elapsed = typeof res.elapsed_ms === "number"
      ? res.elapsed_ms.toFixed(0) : res.elapsed_ms;
    if (statusEl) statusEl.textContent = `Completed in ${elapsed} ms`;

    const currentCap = s.config?.peak_cap_kw ?? null;
    renderParetoPlotEnhanced("chart-pareto-frontier", res.points, currentCap, s.lastResult?.metrics ?? null);
    renderParetoTable("pareto-table-aside", res.points, currentCap);

    // Update slider range
    const capSlider = document.getElementById("slider-peak-cap");
    if (capSlider && res.points.length > 0) {
      const mn = Math.max(30, Math.floor(Math.min(...res.points.map(p => p.cap_kw)) * 0.85));
      const mx = Math.ceil(Math.max(...res.points.map(p => p.cap_kw)) * 1.15);
      capSlider.min = mn;
      capSlider.max = mx;
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    if (statusEl) statusEl.textContent = "Pareto calculation failed.";
    const el = document.getElementById("chart-pareto-frontier");
    if (el) el.innerHTML = `<div class="loading-overlay" style="color:#f87171;">Pareto failed: ${err.message || "Unknown error"}</div>`;
  }
}

export function updateContent() {
  const s = store.get();

  if (s.paretoResult) {
    const currentCap = s.config?.peak_cap_kw ?? null;
    renderParetoPlotEnhanced("chart-pareto-frontier", s.paretoResult.points, currentCap, s.lastResult?.metrics ?? null);
    renderParetoTable("pareto-table-aside", s.paretoResult.points, currentCap);
  }
  if (s.sweepResult) {
    const currentUtil = s.config?.utilization ?? null;
    renderSweepChartEnhanced("chart-sweep-curve", s.sweepResult, currentUtil);
    renderSweepInsight(s.sweepResult, currentUtil);
  }

  const capSlider = document.getElementById("slider-peak-cap");
  const capLabel  = document.getElementById("label-peak-cap");
  const capDesc   = document.getElementById("pareto-cap-description");
  if (capSlider && capLabel) {
    const storedCap = s.config?.peak_cap_kw;
    if (storedCap) {
      capSlider.value = storedCap;
      capLabel.textContent = `${formatNumber(storedCap, 0)} kW`;
    }
    if (capDesc) capDesc.textContent = describeCapEffect(parseFloat(capSlider.value), s);
  }
}

// ─── Chart renderers ──────────────────────────────────────────────────────────

function renderSweepChartEnhanced(containerId, sweepPoints, currentUtil = null) {
  if (!window.Plotly) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!sweepPoints?.length) {
    container.innerHTML = `<div class="loading-overlay">No sweep data. Click "Re-run sweep".</div>`;
    return;
  }

  const xUtil  = sweepPoints.map(p => p.utilization * 100);
  const yB0    = sweepPoints.map(p => p.savings_vs_b0_pct);
  const yB1    = sweepPoints.map(p => p.savings_vs_b1_pct);
  const yB0min = sweepPoints.map(p => p.min_b0 ?? p.savings_vs_b0_pct);
  const yB0max = sweepPoints.map(p => p.max_b0 ?? p.savings_vs_b0_pct);
  const yB1min = sweepPoints.map(p => p.min_b1 ?? p.savings_vs_b1_pct);
  const yB1max = sweepPoints.map(p => p.max_b1 ?? p.savings_vs_b1_pct);

  const xFill  = [...xUtil, ...xUtil.slice().reverse()];

  const traces = [
    { x: xFill, y: [...yB0max, ...yB0min.slice().reverse()],
      fill: "toself", fillcolor: "rgba(16,185,129,0.13)",
      line: { color: "transparent" }, name: "B0 spread",
      hoverinfo: "skip", showlegend: false, type: "scatter" },
    { x: xFill, y: [...yB1max, ...yB1min.slice().reverse()],
      fill: "toself", fillcolor: "rgba(56,189,248,0.10)",
      line: { color: "transparent" }, name: "B1 spread",
      hoverinfo: "skip", showlegend: false, type: "scatter" },
    { x: xUtil, y: yB0, mode: "lines+markers", type: "scatter",
      name: "vs Always-On (B0)",
      line: { color: "#10b981", width: 2.5 }, marker: { size: 8 },
      hovertemplate: "Utilization %{x:.0f}%<br>Savings vs B0: %{y:.1f}%<extra></extra>" },
    { x: xUtil, y: yB1, mode: "lines+markers", type: "scatter",
      name: "vs Smart Standby (B1)",
      line: { color: "#38bdf8", width: 2, dash: "dash" }, marker: { size: 8 },
      hovertemplate: "Utilization %{x:.0f}%<br>Savings vs B1: %{y:.1f}%<extra></extra>" },
  ];

  const shapes = [];
  const annotations = [];
  if (currentUtil !== null) {
    const pct = currentUtil * 100;
    shapes.push({ type: "line", x0: pct, x1: pct, y0: 0, y1: 1, yref: "paper",
      line: { color: "#f59e0b", width: 2, dash: "dot" } });
    annotations.push({ x: pct, y: 1, yref: "paper",
      text: `Current<br>${pct.toFixed(0)}%`,
      showarrow: false, font: { color: "#f59e0b", size: 11 },
      xanchor: "left", yanchor: "bottom" });
  }

  const layout = {
    ...BASE_LAYOUT,
    shapes, annotations,
    xaxis: { title: { text: "Factory utilization (%)", font: { size: 12 } },
      gridcolor: "#334155", dtick: 10 },
    yaxis: { title: { text: "Savings vs baseline (%)", font: { size: 12 } },
      gridcolor: "#334155", ticksuffix: "%" },
    legend: { orientation: "h", y: 1.18, x: 0.01 },
  };

  container.innerHTML = "";
  window.Plotly.newPlot(container, traces, layout, { responsive: true, displayModeBar: false });
}

function renderParetoPlotEnhanced(containerId, points, currentCap, metrics) {
  if (!window.Plotly) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!points?.length) {
    container.innerHTML = `<div class="loading-overlay">No Pareto data — auto-running…</div>`;
    return;
  }

  const satisfied = points.filter(p => p.cap_satisfied);
  const recommended = satisfied.length
    ? satisfied.sort((a, b) => b.cap_kw - a.cap_kw)[0]
    : [...points].sort((a, b) => a.cost - b.cost)[0];

  const colors = points.map(p =>
    p === recommended ? "#f59e0b"
    : p.cap_satisfied ? "#34d399"
    : "#f87171"
  );

  const traces = [
    {
      x: points.map(p => p.peak_kw),
      y: points.map(p => p.cost),
      mode: "lines+markers", type: "scatter",
      name: "Pareto frontier",
      line: { color: "#475569", width: 1.5 },
      marker: {
        size: points.map(p => p === recommended ? 16 : 10),
        color: colors,
        symbol: points.map(p => p === recommended ? "star" : "circle"),
        line: { color: "#0f172a", width: 1 },
      },
      text: points.map(p =>
        `Cap: ${formatNumber(p.cap_kw, 0)} kW` +
        `<br>Peak: ${formatNumber(p.peak_kw, 1)} kW` +
        `<br>Cost: ${formatMoney(p.cost)}` +
        `<br>Satisfied: ${p.cap_satisfied ? "✓" : "✗"}` +
        (p === recommended ? "<br><b>★ Recommended</b>" : "")
      ),
      hoverinfo: "text",
    },
  ];

  if (currentCap !== null) {
    const match = points.find(p => Math.abs(p.cap_kw - currentCap) < 5);
    if (match) {
      traces.push({
        x: [match.peak_kw], y: [match.cost],
        mode: "markers", type: "scatter",
        name: `Active cap (${formatNumber(currentCap, 0)} kW)`,
        marker: { size: 20, color: "#a78bfa", symbol: "diamond",
          line: { color: "#fff", width: 1.5 } },
        hoverinfo: "name",
      });
    }
  }

  const layout = {
    ...BASE_LAYOUT,
    xaxis: { title: { text: "Peak power demand (kW)", font: { size: 12 } }, gridcolor: "#334155" },
    yaxis: { title: { text: "Energy cost (₹)", font: { size: 12 } }, gridcolor: "#334155" },
    legend: { orientation: "h", y: 1.18, x: 0.01 },
    annotations: [{
      x: recommended.peak_kw, y: recommended.cost,
      text: "★ Recommended",
      showarrow: true, arrowhead: 2, arrowcolor: "#f59e0b",
      font: { color: "#f59e0b", size: 11 },
      bgcolor: "rgba(15,23,42,0.85)", bordercolor: "#f59e0b",
      borderwidth: 1, borderpad: 4, ax: 40, ay: -36,
    }],
  };

  container.innerHTML = "";
  window.Plotly.newPlot(container, traces, layout, { responsive: true, displayModeBar: false });
}

function renderParetoTable(containerId, points, currentCap) {
  const el = document.getElementById(containerId);
  if (!el || !points?.length) return;

  const sorted = [...points].sort((a, b) => a.cap_kw - b.cap_kw);
  const recommended = (sorted.filter(p => p.cap_satisfied).sort((a, b) => b.cap_kw - a.cap_kw)[0])
    ?? sorted[0];

  el.innerHTML = `
    <div class="pareto-table-card">
      <div class="pareto-table-header">Cap scenarios</div>
      <table class="pareto-aside-table">
        <thead>
          <tr><th>Cap</th><th>Peak</th><th>Cost</th><th></th></tr>
        </thead>
        <tbody>
          ${sorted.map(p => {
            const active = currentCap !== null && Math.abs(p.cap_kw - currentCap) < 5;
            const rec    = p === recommended;
            return `<tr class="${active ? "pareto-row-active" : ""} ${rec ? "pareto-row-rec" : ""}">
              <td class="font-mono">${formatNumber(p.cap_kw, 0)} kW</td>
              <td class="font-mono">${formatNumber(p.peak_kw, 1)} kW</td>
              <td class="font-mono">${formatMoney(p.cost, false)}</td>
              <td>${rec ? '<span title="Recommended">★</span>' : ""}${p.cap_satisfied ? " ✓" : ' <span style="color:#f87171">✗</span>'}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
      <p class="pareto-table-note">✓ cap satisfied &nbsp;·&nbsp; ★ recommended<br>✗ cap could not be met</p>
    </div>
  `;
}

function renderSweepInsight(sweepPoints, currentUtil) {
  const el = document.getElementById("sweep-insight");
  if (!el || !sweepPoints?.length) return;

  const maxB0 = Math.max(...sweepPoints.map(p => p.savings_vs_b0_pct));
  const minB0 = Math.min(...sweepPoints.map(p => p.savings_vs_b0_pct));
  const crossover = sweepPoints.find(p => p.savings_vs_b0_pct < 5);

  let s = `PeakShift saves <strong>${formatNumber(minB0, 1)}%–${formatNumber(maxB0, 1)}%</strong> versus always-on across all load levels.`;
  if (crossover) {
    s += ` Savings fall below 5% above <strong>${formatNumber(crossover.utilization * 100, 0)}% utilization</strong> — peak-shift headroom shrinks as the plant runs fuller.`;
  } else {
    s += ` Savings remain meaningful even at high utilization because off-peak windows span 8 hours.`;
  }
  if (currentUtil !== null) {
    const nearest = sweepPoints.reduce((best, p) =>
      Math.abs(p.utilization - currentUtil) < Math.abs(best.utilization - currentUtil) ? p : best
    );
    s += ` At the current load (${formatNumber(currentUtil * 100, 0)}%), the model predicts <strong>${formatNumber(nearest.savings_vs_b0_pct, 1)}% savings</strong> vs always-on.`;
  }
  el.innerHTML = s;
}

function describeCapEffect(val, s) {
  const basePeak = s?.lastResult?.metrics?.peak_kw
    ?? (s?.plant?.machines ?? []).reduce((a, m) => a + (m.run_kw ?? 0), 0)
    ?? val;
  const ratio = val / basePeak;
  if (ratio >= 1.1) return `No real restriction — cap is above the unconstrained peak of ${formatNumber(basePeak, 0)} kW.`;
  if (ratio >= 0.95) return `Mild cap — scheduler has under 5% headroom to reduce peak demand.`;
  if (ratio >= 0.85) return `Moderate cap — restricts concurrency; some jobs delay into off-peak windows.`;
  if (ratio >= 0.75) return `Tight cap — enforces significant job serialisation; cost rises from reduced flexibility.`;
  return `Very tight cap (${formatNumber(val, 0)} kW) — scheduler will struggle; some jobs may be unplaced.`;
}

function showSkeletonChart(el, msg = "") {
  el.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:0.75rem;padding:1.5rem;height:100%;box-sizing:border-box;">
      ${msg ? `<p style="font-size:0.8125rem;color:var(--text-muted);margin:0;">${msg}</p>` : ""}
      <div class="skeleton-block" style="height:180px;border-radius:6px;"></div>
      <div class="skeleton-block" style="height:20px;width:60%;border-radius:4px;"></div>
      <div class="skeleton-block" style="height:20px;width:40%;border-radius:4px;"></div>
    </div>`;
}

const BASE_LAYOUT = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { family: "Inter, ui-sans-serif, system-ui, sans-serif", color: "#f1f5f9", size: 12 },
  margin: { l: 110, r: 24, t: 28, b: 38 },
  autosize: true,
};
