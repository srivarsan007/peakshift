/**
 * PeakShift Plotly Chart Builders
 * Zero CDN: uses local vendor/plotly.min.js
 */

import { formatMoney, formatNumber, slotToClock, slotRangeToClock } from "./format.js";

const STATE_COLORS = {
  run: "#34d399",     // Emerald green
  idle: "#60a5fa",    // Sky blue
  off: "#475569",     // Slate grey
  warmup: "#fbbf24",  // Amber
  failed: "#f87171",  // Red
};

const COMMON_LAYOUT = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: {
    family: "Inter, ui-sans-serif, system-ui, sans-serif",
    color: "#f1f5f9",
    size: 12,
  },
  margin: { l: 110, r: 24, t: 28, b: 38 },
  autosize: true,
};

function ensurePlotly() {
  if (typeof window.Plotly === "undefined") {
    console.warn("Plotly is not yet loaded");
    return false;
  }
  return true;
}

function getRateAtSlot(slot, tariff, slotMinutes) {
  if (!tariff || !tariff.windows) return 7.50;
  const hour = (slot * slotMinutes) / 60;
  for (const w of tariff.windows) {
    if (hour >= w.start_hour && hour < w.end_hour) {
      return (tariff.base_rate || 7.50) * (w.multiplier || 1.0);
    }
  }
  return tariff.base_rate || 7.50;
}

/**
 * Builds Gantt chart segments for a schedule.
 * - Colours by machine state (running, idle, warmup, off)
 * - Machine labels include run kW: e.g. M1 (22 kW)
 * - Job IDs drawn only on bars wide enough to read (duration >= 2 slots)
 * - Hover shows job ID, clock times, kW, and calculated INR energy cost
 * - Highlights moved/relocated jobs with distinct outline in Difference mode
 */
export function renderGantt(containerId, schedule, plant, tariff, title = "Machine Schedule", options = {}) {
  if (!ensurePlotly()) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  const slotMinutes = plant?.slot_minutes || 30;
  const horizonSlots = plant?.horizon_slots || 48;
  const machines = plant?.machines || [];
  const machineMap = new Map(machines.map(m => [m.id, m]));
  const movedJobIds = options.movedJobIds || new Set();
  const highlightAllMoved = options.highlightMoved || false;

  const shapes = [];
  const annotations = [];
  const traces = [];

  // Machine labels with run kW
  const machineLabels = machines.map(m => `${m.id} (${m.run_kw} kW)`);
  const machineIdToLabel = new Map(machines.map(m => [m.id, `${m.id} (${m.run_kw} kW)`]));

  // Ghost bars: Prior plan rendered behind the rescheduled schedule
  if (options.ghostSchedule && options.ghostSchedule.segments) {
    const ghostTrace = {
      x: [],
      y: [],
      base: [],
      name: "Prior Plan (Ghost)",
      type: "bar",
      orientation: "h",
      textposition: "none",
      marker: {
        color: "rgba(148, 163, 184, 0.12)",
        line: {
          color: "rgba(148, 163, 184, 0.55)",
          width: 1.5,
          dash: "dot",
        },
      },
      hoverinfo: "text",
      hovertext: [],
      showlegend: true,
    };
    for (const [mId, segs] of Object.entries(options.ghostSchedule.segments)) {
      const mLabel = machineIdToLabel.get(mId) || mId;
      for (const seg of segs) {
        if (seg.state === "run") {
          ghostTrace.y.push(mLabel);
          ghostTrace.base.push(seg.start);
          ghostTrace.x.push(seg.end - seg.start);
          ghostTrace.hovertext.push(`Prior Plan: Job <b>${seg.job_id || ''}</b> [${slotRangeToClock(seg.start, seg.end, slotMinutes)}]`);
        }
      }
    }
    if (ghostTrace.x.length > 0) {
      traces.push(ghostTrace);
    }
  }

  // Vertical "NOW" line marker
  if (options.nowSlot !== null && options.nowSlot !== undefined) {
    shapes.push({
      type: "line",
      xref: "x",
      yref: "paper",
      x0: options.nowSlot,
      x1: options.nowSlot,
      y0: 0,
      y1: 1,
      line: {
        color: "#38bdf8",
        width: 2.5,
        dash: "solid",
      },
      layer: "above",
    });

    annotations.push({
      x: options.nowSlot,
      y: 1.06,
      xref: "x",
      yref: "paper",
      text: `NOW (${slotToClock(options.nowSlot, slotMinutes)})`,
      showarrow: false,
      font: { size: 10, color: "#38bdf8", weight: 700 },
      xanchor: "center",
      yanchor: "bottom",
    });
  }

  // Failed machine hatching (explicitly passed)
  if (options.failedMachineId) {
    const failedLabel = machineIdToLabel.get(options.failedMachineId) || options.failedMachineId;
    const failStart = options.failedFromSlot !== undefined ? options.failedFromSlot : 0;
    const failDur = horizonSlots - failStart;
    if (failDur > 0) {
      const failedTrace = {
        x: [failDur],
        y: [failedLabel],
        base: [failStart],
        name: "Breakdown (Failed)",
        type: "bar",
        orientation: "h",
        text: ["FAILED (OFFLINE)"],
        textposition: "inside",
        insidetextanchor: "middle",
        textfont: { size: 10, color: "#ffffff", family: "Inter, sans-serif" },
        marker: {
          color: "rgba(239, 68, 68, 0.55)",
          pattern: {
            shape: "/",
            bgcolor: "rgba(239, 68, 68, 0.20)",
            fgcolor: "#ef4444",
            size: 8,
          },
          line: {
            color: "#ef4444",
            width: 2,
          },
        },
        hoverinfo: "text",
        hovertext: [`<b>${options.failedMachineId} Breakdown</b><br>Offline from ${slotToClock(failStart, slotMinutes)} to end of shift`],
        showlegend: true,
      };
      traces.push(failedTrace);
    }
  }

  // 1. Tariff background shading with faint labels
  if (tariff && tariff.windows) {
    for (const w of tariff.windows) {
      const startSlot = (w.start_hour * 60) / slotMinutes;
      const endSlot = (w.end_hour * 60) / slotMinutes;
      const isPeak = w.multiplier > 1.0;
      const isOffPeak = w.multiplier < 1.0;
      const fillColor = isPeak ? "rgba(239, 68, 68, 0.10)" : (isOffPeak ? "rgba(16, 185, 129, 0.07)" : "transparent");

      if (fillColor !== "transparent") {
        shapes.push({
          type: "rect",
          xref: "x",
          yref: "paper",
          x0: startSlot,
          x1: endSlot,
          y0: 0,
          y1: 1,
          fillcolor: fillColor,
          line: { width: 0 },
          layer: "below",
        });

        // Add small text label for peak windows
        if (isPeak && (endSlot - startSlot) >= 4) {
          annotations.push({
            x: (startSlot + endSlot) / 2,
            y: 1.05,
            xref: "x",
            yref: "paper",
            text: `PEAK (${w.multiplier}x)`,
            showarrow: false,
            font: { size: 10, color: "rgba(239, 68, 68, 0.8)", weight: 700 },
          });
        }
      }
    }
  }

  // 2. Machine states and traces
  const stateLabels = { run: "Running", idle: "Idle Standby", off: "Switched Off", warmup: "Warmup", failed: "Failed" };
  const categories = ["run", "idle", "off", "warmup", "failed"];
  const categoryTraces = {};

  categories.forEach(st => {
    categoryTraces[st] = {
      x: [],
      y: [],
      base: [],
      text: [],
      hovertext: [],
      textposition: "inside",
      insidetextanchor: "middle",
      textfont: { size: 11, color: st === "warmup" ? "#000" : "#fff", family: "Inter, sans-serif" },
      name: stateLabels[st],
      type: "bar",
      orientation: "h",
      marker: {
        color: [],
        line: {
          color: [],
          width: [],
        },
      },
      hoverinfo: "text",
    };
  });

  if (schedule && schedule.segments) {
    for (const [mId, segs] of Object.entries(schedule.segments)) {
      const machine = machineMap.get(mId);
      const mLabel = machineIdToLabel.get(mId) || mId;

      for (const seg of segs) {
        const dur = seg.end - seg.start;
        const kw = seg.state === "run" ? (machine?.run_kw || 0) : (seg.state === "idle" ? (machine?.idle_kw || 0) : 0);
        const clockStr = slotRangeToClock(seg.start, seg.end, slotMinutes);

        // Calculate segment energy cost in INR
        let segCost = 0;
        const hoursPerSlot = slotMinutes / 60;
        for (let s = seg.start; s < seg.end && s < horizonSlots; s++) {
          const rate = getRateAtSlot(s, tariff, slotMinutes);
          segCost += kw * hoursPerSlot * rate;
        }

        const isMoved = seg.job_id && movedJobIds.has(seg.job_id);
        const jobStr = seg.job_id ? `Job: <b>${seg.job_id}</b><br>` : "";

        const hover = `
          <b>${mLabel}</b> — ${stateLabels[seg.state]}<br>
          ${jobStr}Clock Time: ${clockStr} (${dur * slotMinutes} min)<br>
          Demand: ${formatNumber(kw, 1)} kW · Energy Cost: ${formatMoney(segCost)}
          ${isMoved ? "<br><span style='color:#38bdf8;'>● MOVED BY OPTIMIZER</span>" : ""}
        `;

        // Only draw job ID text if duration >= 2 slots (sufficient bar width)
        const barText = (seg.job_id && dur >= 2) ? String(seg.job_id) : "";

        const tr = categoryTraces[seg.state] || categoryTraces.idle;
        tr.y.push(mLabel);
        tr.base.push(seg.start);
        tr.x.push(dur);
        tr.text.push(barText);
        tr.hovertext.push(hover);

        // Marker styling: outline for moved jobs in difference view
        tr.marker.color.push(STATE_COLORS[seg.state]);
        if (isMoved && highlightAllMoved) {
          tr.marker.line.color.push("#38bdf8");
          tr.marker.line.width.push(2.5);
        } else if (seg.state === "off") {
          tr.marker.line.color.push("#64748b");
          tr.marker.line.width.push(1);
        } else {
          tr.marker.line.color.push("rgba(0,0,0,0.2)");
          tr.marker.line.width.push(0.5);
        }
      }
    }
  }

  for (const st of categories) {
    if (categoryTraces[st].x.length > 0) {
      // Map hovertext to text field for Plotly hover
      categoryTraces[st].text = categoryTraces[st].text;
      categoryTraces[st].hoverinfo = "text";
      categoryTraces[st].hovertext = categoryTraces[st].hovertext;
      // In Plotly bar traces, custom hover info is provided via hovertext
      traces.push(categoryTraces[st]);
    }
  }

  // Consistent dynamic row height for identical row spacing on both charts
  const calcHeight = Math.max(260, machines.length * 34 + 80);

  const layout = {
    ...COMMON_LAYOUT,
    height: calcHeight,
    barmode: "stack",
    shapes,
    annotations,
    xaxis: {
      range: [0, horizonSlots],
      tickmode: "array",
      tickvals: Array.from({ length: Math.floor(horizonSlots / 4) + 1 }, (_, i) => i * 4),
      ticktext: Array.from({ length: Math.floor(horizonSlots / 4) + 1 }, (_, i) => slotToClock(i * 4, slotMinutes)),
      gridcolor: "rgba(55, 65, 81, 0.6)",
      zerolinecolor: "rgba(55, 65, 81, 0.6)",
      linecolor: "rgba(55, 65, 81, 0.6)",
    },
    yaxis: {
      categoryorder: "array",
      categoryarray: [...machineLabels].reverse(),
      gridcolor: "rgba(55, 65, 81, 0.4)",
      linecolor: "rgba(55, 65, 81, 0.6)",
    },
    legend: {
      orientation: "h",
      y: 1.14,
      x: 0.02,
      font: { size: 11, color: "#94a3b8" },
    },
  };

  const config = { responsive: true, displayModeBar: false };
  container.innerHTML = "";
  window.Plotly.newPlot(container, traces, layout, config);
}

/**
 * Builds kW Load Profile Step Chart comparing Baseline vs Optimized.
 * - Labels peak windows
 * - Dashed horizontal line at baseline peak kW
 * - Cap line when peakCapKw is set
 * - Fills area between baseline and optimized to make energy savings visible
 */
export function renderLoadProfile(containerId, b0Schedule, optSchedule, plant, tariff, peakCapKw = null) {
  if (!ensurePlotly()) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  const slotMinutes = plant?.slot_minutes || 30;
  const horizonSlots = plant?.horizon_slots || 48;
  const machines = plant?.machines || [];
  const machineMap = new Map(machines.map(m => [m.id, m]));

  const computeSlotsKw = (sched) => {
    const kwPerSlot = new Array(horizonSlots).fill(0);
    if (!sched || !sched.segments) return kwPerSlot;

    for (const [mId, segs] of Object.entries(sched.segments)) {
      const m = machineMap.get(mId);
      if (!m) continue;
      for (const seg of segs) {
        let kw = 0;
        if (seg.state === "run") kw = m.run_kw;
        else if (seg.state === "idle") kw = m.idle_kw;
        for (let s = seg.start; s < seg.end && s < horizonSlots; s++) {
          kwPerSlot[s] += kw;
        }
      }
    }
    return kwPerSlot;
  };

  const b0Kw = computeSlotsKw(b0Schedule);
  const optKw = computeSlotsKw(optSchedule);
  const b0Peak = Math.max(...b0Kw, 0);
  const xSlots = Array.from({ length: horizonSlots }, (_, i) => i);

  const traces = [
    {
      x: xSlots,
      y: b0Kw,
      name: "Baseline (Always-On)",
      type: "scatter",
      mode: "lines",
      line: { shape: "hv", color: "#ef4444", width: 2, dash: "dot" },
      hoverinfo: "x+y+name",
    },
    {
      x: xSlots,
      y: optKw,
      name: "Optimized (PeakShift)",
      type: "scatter",
      mode: "lines",
      line: { shape: "hv", color: "#34d399", width: 2.5 },
      fill: "tonexty",
      fillcolor: "rgba(16, 185, 129, 0.14)",
      hoverinfo: "x+y+name",
    },
  ];

  const shapes = [];
  const annotations = [];

  // Tariff peak shading and window labels
  if (tariff && tariff.windows) {
    for (const w of tariff.windows) {
      if (w.multiplier > 1.0) {
        const x0 = (w.start_hour * 60) / slotMinutes;
        const x1 = (w.end_hour * 60) / slotMinutes;
        shapes.push({
          type: "rect",
          xref: "x",
          yref: "paper",
          x0: x0,
          x1: x1,
          y0: 0,
          y1: 1,
          fillcolor: "rgba(239, 68, 68, 0.12)",
          line: { width: 0 },
          layer: "below",
        });

        annotations.push({
          x: (x0 + x1) / 2,
          y: 0.96,
          xref: "x",
          yref: "paper",
          text: `PEAK (${w.multiplier}x)`,
          showarrow: false,
          font: { size: 10, color: "rgba(239, 68, 68, 0.8)", weight: 700 },
        });
      }
    }
  }

  // Dashed line at baseline peak kW
  if (b0Peak > 0) {
    shapes.push({
      type: "line",
      xref: "paper",
      yref: "y",
      x0: 0,
      x1: 1,
      y0: b0Peak,
      y1: b0Peak,
      line: { color: "rgba(239, 68, 68, 0.6)", width: 1.5, dash: "dash" },
    });
    annotations.push({
      x: 0.98,
      y: b0Peak,
      xref: "paper",
      yref: "y",
      text: `Peak: ${formatNumber(b0Peak, 0)} kW`,
      showarrow: false,
      xanchor: "right",
      yanchor: "bottom",
      font: { size: 10, color: "#f87171" },
    });
  }

  // Peak Cap horizontal line
  if (peakCapKw && peakCapKw > 0) {
    shapes.push({
      type: "line",
      xref: "paper",
      yref: "y",
      x0: 0,
      x1: 1,
      y0: peakCapKw,
      y1: peakCapKw,
      line: { color: "#fbbf24", width: 2, dash: "dashdot" },
    });
    annotations.push({
      x: 0.02,
      y: peakCapKw,
      xref: "paper",
      yref: "y",
      text: `Peak Cap: ${formatNumber(peakCapKw, 0)} kW`,
      showarrow: false,
      xanchor: "left",
      yanchor: "bottom",
      font: { size: 10, color: "#fbbf24", weight: 700 },
    });
  }

  const layout = {
    ...COMMON_LAYOUT,
    height: 320,
    shapes,
    annotations,
    xaxis: {
      range: [0, horizonSlots],
      tickmode: "array",
      tickvals: Array.from({ length: Math.floor(horizonSlots / 4) + 1 }, (_, i) => i * 4),
      ticktext: Array.from({ length: Math.floor(horizonSlots / 4) + 1 }, (_, i) => slotToClock(i * 4, slotMinutes)),
      gridcolor: "rgba(55, 65, 81, 0.6)",
      zerolinecolor: "rgba(55, 65, 81, 0.6)",
      linecolor: "rgba(55, 65, 81, 0.6)",
    },
    yaxis: {
      title: { text: "Power (kW)", font: { size: 12 } },
      gridcolor: "rgba(55, 65, 81, 0.4)",
      linecolor: "rgba(55, 65, 81, 0.6)",
    },
    legend: {
      orientation: "h",
      y: 1.15,
      x: 0.02,
      font: { size: 11, color: "#94a3b8" },
    },
  };

  const config = { responsive: true, displayModeBar: false };
  container.innerHTML = "";
  window.Plotly.newPlot(container, traces, layout, config);
}

/**
 * Builds Pareto Frontier Scatter (Cost vs Peak kW).
 */
export function renderParetoPlot(containerId, paretoPoints, currentCap = null) {
  if (!ensurePlotly()) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!paretoPoints || paretoPoints.length === 0) {
    container.innerHTML = `<div class="loading-overlay">No Pareto data available. Adjust caps and run.</div>`;
    return;
  }

  const xPeaks = paretoPoints.map(p => p.peak_kw);
  const yCosts = paretoPoints.map(p => p.cost);
  const capLabels = paretoPoints.map(p => `Cap: ${formatNumber(p.cap_kw, 0)} kW<br>Peak: ${formatNumber(p.peak_kw, 1)} kW<br>Cost: ${formatMoney(p.cost)}<br>Satisfied: ${p.cap_satisfied}`);

  const traces = [
    {
      x: xPeaks,
      y: yCosts,
      mode: "lines+markers",
      type: "scatter",
      name: "Pareto Trade-off",
      marker: { size: 10, color: "#3b82f6" },
      line: { color: "#3b82f6", width: 2 },
      text: capLabels,
      hoverinfo: "text",
    },
  ];

  // Highlight chosen cap
  if (currentCap !== null) {
    const match = paretoPoints.find(p => Math.abs(p.cap_kw - currentCap) < 0.1);
    if (match) {
      traces.push({
        x: [match.peak_kw],
        y: [match.cost],
        mode: "markers",
        type: "scatter",
        name: `Active Cap (${formatNumber(currentCap, 0)} kW)`,
        marker: { size: 16, color: "#f59e0b", symbol: "diamond" },
        hoverinfo: "name",
      });
    }
  }

  const layout = {
    ...COMMON_LAYOUT,
    xaxis: {
      title: { text: "Peak Power Demand (kW)", font: { size: 12 } },
      gridcolor: "#374151",
    },
    yaxis: {
      title: { text: "Energy Cost (₹)", font: { size: 12 } },
      gridcolor: "#374151",
    },
    legend: { orientation: "h", y: 1.15, x: 0.05 },
  };

  const config = { responsive: true, displayModeBar: false };
  container.innerHTML = "";
  window.Plotly.newPlot(container, traces, layout, config);
}

/**
 * Builds Load Sweep chart (Utilization vs Savings %).
 */
export function renderSweepChart(containerId, sweepPoints) {
  if (!ensurePlotly()) return;
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!sweepPoints || sweepPoints.length === 0) {
    container.innerHTML = `<div class="loading-overlay">No Sweep data available. Click "Run Utilization Sweep".</div>`;
    return;
  }

  const xUtil = sweepPoints.map(p => (p.utilization * 100));
  const yB0 = sweepPoints.map(p => p.savings_vs_b0_pct);
  const yB1 = sweepPoints.map(p => p.savings_vs_b1_pct);

  const traces = [
    {
      x: xUtil,
      y: yB0,
      mode: "lines+markers",
      type: "scatter",
      name: "Savings vs Always-On (B0)",
      line: { color: "#10b981", width: 2.5 },
      marker: { size: 8 },
    },
    {
      x: xUtil,
      y: yB1,
      mode: "lines+markers",
      type: "scatter",
      name: "Savings vs Smart Baseline (B1)",
      line: { color: "#38bdf8", width: 2, dash: "dash" },
      marker: { size: 8 },
    },
  ];

  const layout = {
    ...COMMON_LAYOUT,
    xaxis: {
      title: { text: "Factory Utilization (%)", font: { size: 12 } },
      gridcolor: "#374151",
      dtick: 10,
    },
    yaxis: {
      title: { text: "Savings (%)", font: { size: 12 } },
      gridcolor: "#374151",
      ticksuffix: "%",
    },
    legend: { orientation: "h", y: 1.15, x: 0.05 },
  };

  const config = { responsive: true, displayModeBar: false };
  container.innerHTML = "";
  window.Plotly.newPlot(container, traces, layout, config);
}
