/**
 * PeakShift Formatting Helpers
 * Indian currency (en-IN), power, energy, clock times from slot indices.
 */

export function formatMoney(inr, showSign = false) {
  if (inr === null || inr === undefined || isNaN(inr)) return "₹0";
  const abs = Math.round(Math.abs(inr));
  const formatted = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(abs);

  if (inr < 0) return `-${formatted}`;
  if (showSign && inr > 0) return `+${formatted}`;
  return formatted;
}

export function formatNumber(val, decimals = 1) {
  if (val === null || val === undefined || isNaN(val)) return "0.0";
  return Number(val).toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatPercent(val, decimals = 1, showSign = false) {
  if (val === null || val === undefined || isNaN(val)) return "0.0%";
  const num = Number(val);
  const sign = showSign && num > 0 ? "+" : "";
  return `${sign}${num.toFixed(decimals)}%`;
}

export function slotToClock(slot, slotMinutes = 30) {
  const totalMinutes = slot * slotMinutes;
  const hours = Math.floor(totalMinutes / 60) % 24;
  const minutes = totalMinutes % 60;
  const hh = String(hours).padStart(2, "0");
  const mm = String(minutes).padStart(2, "0");
  return `${hh}:${mm}`;
}

export function slotRangeToClock(start, end, slotMinutes = 30) {
  return `${slotToClock(start, slotMinutes)} – ${slotToClock(end, slotMinutes)}`;
}

export function formatSavingsText(pct, baselineName = "baseline") {
  if (pct === null || pct === undefined || isNaN(pct)) return `0.0% vs ${baselineName}`;
  const num = Number(pct);
  if (num < -0.01) {
    return `${Math.abs(num).toFixed(1)}% higher than ${baselineName}`;
  }
  return `${num.toFixed(1)}% reduction vs ${baselineName}`;
}
