/**
 * Rationale & Audit Page — Step 5 upgrade
 *
 * - Verifier checks as a checklist with a progress bar
 * - Decisions grouped by reason code with total ₹ saved per group
 * - Method accordion explaining baselines, tariff source, limits
 */

import { store } from "../state.js";
import { formatMoney, formatNumber } from "../format.js";

export function initReasonsPage() {
  const container = document.getElementById("page-reasons");
  if (!container) return;

  renderTemplate(container);
  bindEvents(container);
  updateContent();

  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#reasons") requestAnimationFrame(updateContent);
  });

  store.subscribe(() => {
    if (window.location.hash === "#reasons") updateContent();
  });
}

function renderTemplate(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h1 class="page-title">Rationale &amp; audit</h1>
        <p class="page-desc">Independent verification of every constraint, and a plain-English record of every scheduling decision.</p>
      </div>
      <div id="reasons-overall-badge" class="badge badge-info" aria-live="polite">Audit pending</div>
    </div>

    <!-- ── Section 1: Verifier checklist ─────────────────── -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;flex-wrap:wrap;gap:0.5rem;">
        <h2 class="card-title" style="margin-bottom:0;">Independent verifier — integrity checks</h2>
        <div style="display:flex;align-items:center;gap:0.75rem;">
          <span id="verifier-count" style="font-size:0.8125rem;color:var(--text-secondary);"></span>
        </div>
      </div>
      <p style="font-size:0.8125rem;color:var(--text-secondary);margin-bottom:1rem;">
        The verifier recomputes all energy, makespan, machine states, and constraints slot-by-slot, independently of the optimizer.
      </p>

      <!-- Progress bar -->
      <div id="verifier-progress-wrap" style="margin-bottom:1rem;display:none;" aria-label="Verifier progress">
        <div style="display:flex;justify-content:space-between;margin-bottom:0.25rem;">
          <span style="font-size:0.75rem;color:var(--text-muted);">Checks passed</span>
          <span id="verifier-progress-label" style="font-size:0.75rem;color:var(--text-muted);font-variant-numeric:tabular-nums;">0 / 0</span>
        </div>
        <div class="progress-track">
          <div id="verifier-progress-bar" class="progress-fill" style="width:0%;" role="progressbar"
            aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>
        </div>
      </div>

      <!-- Checklist -->
      <div id="verifier-checklist" class="verifier-checklist" aria-label="Verifier checks">
        <div style="padding:2rem;text-align:center;color:var(--text-muted);">
          No verification results yet. Run an optimization first.
        </div>
      </div>
    </div>

    <!-- ── Section 2: Decision log ────────────────────────── -->
    <div class="card" style="margin-bottom:1.5rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;flex-wrap:wrap;gap:0.5rem;">
        <div>
          <h2 class="card-title" style="margin-bottom:0.125rem;">Optimization decisions</h2>
          <p style="font-size:0.8125rem;color:var(--text-secondary);margin:0;">
            Every departure from the baseline, grouped by reason code with total ₹ saved per group.
          </p>
        </div>
        <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap;">
          <button class="btn btn-secondary btn-sm" id="btn-sort-savings"
            style="padding:0.25rem 0.625rem;min-height:32px;" aria-label="Sort by savings">
            Sort: highest savings first
          </button>
          <button class="btn btn-secondary btn-sm" id="btn-expand-all"
            style="padding:0.25rem 0.625rem;min-height:32px;" aria-label="Expand all groups">
            Expand all
          </button>
        </div>
      </div>

      <div id="decisions-grouped" aria-label="Grouped decisions">
        <div style="padding:2rem;text-align:center;color:var(--text-muted);">
          No optimization decisions recorded yet.
        </div>
      </div>
    </div>

    <!-- ── Section 3: Method accordion ───────────────────── -->
    <div class="card">
      <details class="method-disclosure" open>
        <summary>Method — how the optimizer and verifier work</summary>
        <div class="method-body">
          <p><strong>Baselines</strong></p>
          <ul>
            <li><strong>B0 — Always-On:</strong> All machines run for the full 24-hour horizon at rated power. No scheduling whatsoever. Upper bound on energy cost.</li>
            <li><strong>B1 — Smart Standby (EDF):</strong> Earliest-Deadline-First dispatch. Machines turn off when idle but do not shift jobs across tariff windows. Represents a disciplined human scheduler.</li>
          </ul>

          <p><strong>Tariff source</strong></p>
          <p>
            Tamil Nadu Electricity Regulatory Commission (TNERC) HT-I Industrial Tariff,
            <em>Comprehensive Tariff Order No. 6 of 2025</em>.
            Base rate: ₹7.50/kWh. Peak multiplier: 1.25× (₹9.38/kWh, 06:00–10:00 &amp; 18:00–22:00).
            Off-peak multiplier: 0.95× (₹7.13/kWh, 22:00–05:00). Electricity tax: 5%.
          </p>

          <p><strong>Limits of this simulation</strong></p>
          <ul>
            <li><strong>Simulated plant:</strong> Jobs and machines are generated from a preset; real plants vary significantly.</li>
            <li><strong>Simplified restart model:</strong> Warm-up energy and minimum off-time penalties are modelled; thermal losses and mechanical start loads are not.</li>
            <li><strong>Single-shift horizon:</strong> The 24-hour window assumes a single production day with no carryover jobs.</li>
            <li><strong>No demand-charge model:</strong> The tariff does not include a capacity charge on peak kVA; some real tariffs do. The Cap feature estimates its value but does not bill it.</li>
          </ul>

          <p><strong>Verifier independence</strong></p>
          <p>
            The verifier runs as a separate Python module (<code>peakshift.core.verify</code>) that does not share any state with the optimizer.
            It re-reads the returned schedule and recomputes every constraint from scratch.
            A green badge means every check passed on the exact output the API returned.
          </p>
        </div>
      </details>
    </div>
  `;
}

let _sortDescending = true;
let _allExpanded = false;

function bindEvents(container) {
  container.querySelector("#btn-sort-savings")?.addEventListener("click", () => {
    _sortDescending = !_sortDescending;
    const btn = container.querySelector("#btn-sort-savings");
    if (btn) btn.textContent = _sortDescending ? "Sort: highest savings first" : "Sort: lowest savings first";
    const s = store.get();
    if (s.lastResult?.decisions) renderGroupedDecisions(s.lastResult.decisions);
  });

  container.querySelector("#btn-expand-all")?.addEventListener("click", () => {
    _allExpanded = !_allExpanded;
    const btn = container.querySelector("#btn-expand-all");
    if (btn) btn.textContent = _allExpanded ? "Collapse all" : "Expand all";
    document.querySelectorAll(".decision-group-details").forEach(d => {
      d.open = _allExpanded;
    });
  });
}

export function updateContent() {
  const s = store.get();
  const res = s.lastResult;
  if (!res) return;

  const verifier = res.verifier;
  const overallBadge = document.getElementById("reasons-overall-badge");

  if (verifier) {
    const checks = verifier.checks || [];
    const passed = checks.filter(c => c.passed).length;
    const total  = checks.length;

    if (overallBadge) {
      if (verifier.passed) {
        overallBadge.className = "badge badge-success";
        overallBadge.textContent = `All ${total} checks passed ✓`;
      } else {
        overallBadge.className = "badge badge-danger";
        overallBadge.textContent = `${total - passed} of ${total} checks failed ✗`;
      }
    }

    renderVerifierChecklist(checks, passed, total);
  }

  if (res.decisions) {
    renderGroupedDecisions(res.decisions);
  }
}

function renderVerifierChecklist(checks, passed, total) {
  const wrap = document.getElementById("verifier-progress-wrap");
  const bar  = document.getElementById("verifier-progress-bar");
  const lbl  = document.getElementById("verifier-progress-label");
  const list = document.getElementById("verifier-checklist");
  const count = document.getElementById("verifier-count");

  if (!checks.length) return;

  const pct = total > 0 ? Math.round((passed / total) * 100) : 0;

  if (wrap) wrap.style.display = "block";
  if (bar) {
    bar.style.width = `${pct}%`;
    bar.setAttribute("aria-valuenow", pct);
    bar.className = `progress-fill ${passed === total ? "progress-fill-ok" : "progress-fill-warn"}`;
  }
  if (lbl) lbl.textContent = `${passed} / ${total}`;
  if (count) count.textContent = `${passed} of ${total} passed`;

  if (!list) return;
  list.innerHTML = checks.map((c, i) => `
    <div class="verifier-check-item ${c.passed ? "check-pass" : "check-fail"}"
      role="listitem">
      <div class="check-icon" aria-hidden="true">${c.passed ? "✓" : "✗"}</div>
      <div class="check-body">
        <div class="check-name">
          <span class="check-num">${i + 1}.</span>
          <strong>${escapeHtml(c.name)}</strong>
        </div>
        <div class="check-detail">${escapeHtml(c.details || (c.passed ? "Satisfied" : "Violation detected"))}</div>
      </div>
      <div class="check-badge ${c.passed ? "badge badge-success" : "badge badge-danger"}">
        ${c.passed ? "PASS" : "FAIL"}
      </div>
    </div>
  `).join("");
}

/** Groups decisions by reason code; shows total ₹ saved per group. */
function renderGroupedDecisions(decisions) {
  const container = document.getElementById("decisions-grouped");
  if (!container) return;

  if (!decisions?.length) {
    container.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-muted);">Baseline schedule was optimal — zero differences.</div>`;
    return;
  }

  // Group by code
  const groups = {};
  for (const d of decisions) {
    const code = d.code || "OTHER";
    if (!groups[code]) groups[code] = { code, total: 0, items: [] };
    groups[code].items.push(d);
    groups[code].total += (d.saving_inr || 0);
  }

  // Sort groups by total savings
  const sortedGroups = Object.values(groups).sort((a, b) =>
    _sortDescending ? b.total - a.total : a.total - b.total
  );

  container.innerHTML = sortedGroups.map(g => {
    const sortedItems = [...g.items].sort((a, b) =>
      _sortDescending ? b.saving_inr - a.saving_inr : a.saving_inr - b.saving_inr
    );

    const rows = sortedItems.map(d => {
      const ref = [
        d.job_id     ? `<span class="font-mono">${escapeHtml(d.job_id)}</span>` : null,
        d.machine_id ? `<span class="font-mono">${escapeHtml(d.machine_id)}</span>` : null,
      ].filter(Boolean).join(" · ") || "<em style='color:var(--text-muted)'>Global</em>";

      const savColor = d.saving_inr >= 0 ? "#34d399" : "#f87171";
      return `
        <tr>
          <td>${ref}</td>
          <td style="color:var(--text-primary);">${escapeHtml(d.text || "")}</td>
          <td class="font-mono" style="color:${savColor};font-weight:600;text-align:right;">
            ${d.saving_inr >= 0 ? "+" : ""}${formatMoney(d.saving_inr, true)}
          </td>
        </tr>`;
    }).join("");

    const totalColor = g.total >= 0 ? "#34d399" : "#f87171";
    return `
      <details class="decision-group-details" ${_allExpanded ? "open" : ""}>
        <summary class="decision-group-summary">
          <div style="display:flex;justify-content:space-between;align-items:center;width:100%;gap:0.5rem;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:0.625rem;">
              <span class="badge badge-info">${escapeHtml(g.code)}</span>
              <span style="font-size:0.875rem;color:var(--text-secondary);">${g.items.length} decision${g.items.length !== 1 ? "s" : ""}</span>
            </div>
            <span class="font-mono" style="color:${totalColor};font-weight:700;font-size:0.9375rem;">
              ${g.total >= 0 ? "+" : ""}${formatMoney(g.total, true)} total
            </span>
          </div>
        </summary>
        <div class="table-responsive" style="margin-top:0.5rem;">
          <table class="data-table">
            <thead>
              <tr>
                <th>Job / Machine</th>
                <th>Explanation</th>
                <th style="text-align:right;">Savings (₹)</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </details>
    `;
  }).join("");
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
