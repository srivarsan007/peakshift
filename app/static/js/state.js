/**
 * PeakShift Central State Store & Tariff Management
 */

export const DEFAULT_TARIFF = {
  name: "TN HT-I",
  source: "TNERC Order No. 6 of 2025, HT-I",
  base_rate_per_kwh: 7.50,
  windows: [
    { start_hour: 0.0, end_hour: 5.0, multiplier: 0.95 },
    { start_hour: 6.0, end_hour: 10.0, multiplier: 1.25 },
    { start_hour: 18.0, end_hour: 22.0, multiplier: 1.25 },
    { start_hour: 22.0, end_hour: 24.0, multiplier: 0.95 },
  ],
  tax_rate: 0.05,
  demand_rate_per_kva: 608.0,
  demand_floor_fraction: 0.9,
  contract_exceed_penalty_per_kva: 0.0,
};

const defaultState = {
  preset: "textile",
  utilization: 0.6,
  seed: 42,
  plant: null,
  tariff: { ...DEFAULT_TARIFF },
  defaultTariff: { ...DEFAULT_TARIFF },
  config: {
    peak_cap_kw: null,
    iterations: null,
    seed: 42,
  },
  lastResult: null,     // OptimizeResponse
  disruptResult: null,  // ReplanResponse
  paretoResult: null,   // ParetoResponse
  sweepResult: null,    // Sweep data
  isLoading: false,
  loadingMessage: "",
  serverStatus: "checking", // "ok" | "waking" | "error"
};

class StateStore {
  constructor() {
    this.state = { ...defaultState, tariff: { ...DEFAULT_TARIFF } };
    this.listeners = new Set();
    this.loadFromStorage();
  }

  loadFromStorage() {
    try {
      const savedPreset = localStorage.getItem("peakshift_preset");
      if (savedPreset) this.state.preset = savedPreset;
      const savedSeed = localStorage.getItem("peakshift_seed");
      if (savedSeed) this.state.seed = parseInt(savedSeed, 10);
    } catch (e) {
      // localStorage may fail in private mode; silently ignore
    }
  }

  saveToStorage() {
    try {
      if (this.state.preset) localStorage.setItem("peakshift_preset", this.state.preset);
      if (this.state.seed !== undefined) localStorage.setItem("peakshift_seed", String(this.state.seed));
    } catch (e) {
      // ignore
    }
  }

  get() {
    return this.state;
  }

  set(partial) {
    this.state = { ...this.state, ...partial };
    this.saveToStorage();
    this.notify();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    for (const listener of this.listeners) {
      try {
        listener(this.state);
      } catch (err) {
        console.error("State listener error:", err);
      }
    }
  }
}

export const store = new StateStore();

/**
 * Single source of truth for tariff objects in all backend API calls.
 * Ensures the object sent to the server strictly conforms to Pydantic Tariff schema:
 *   - name (required string)
 *   - base_rate_per_kwh (required float > 0)
 *   - windows (list of {start_hour, end_hour, multiplier})
 *   - tax_rate (float >= 0)
 *   - demand_rate_per_kva, demand_floor_fraction, contract_exceed_penalty_per_kva
 */
export function getTariffForRequest(customTariff = null) {
  const current = customTariff || store.get().tariff || store.get().defaultTariff || DEFAULT_TARIFF;

  const baseRate = parseFloat(
    current.base_rate_per_kwh !== undefined && current.base_rate_per_kwh !== null
      ? current.base_rate_per_kwh
      : current.base_rate !== undefined && current.base_rate !== null
      ? current.base_rate
      : DEFAULT_TARIFF.base_rate_per_kwh
  );

  const name = String(current.name || DEFAULT_TARIFF.name).trim();
  const source = current.source !== undefined ? String(current.source) : DEFAULT_TARIFF.source;
  const taxRate = current.tax_rate !== undefined && current.tax_rate !== null
    ? parseFloat(current.tax_rate)
    : DEFAULT_TARIFF.tax_rate;
  const demandRate = current.demand_rate_per_kva !== undefined
    ? current.demand_rate_per_kva
    : DEFAULT_TARIFF.demand_rate_per_kva;
  const demandFloor = current.demand_floor_fraction !== undefined && current.demand_floor_fraction !== null
    ? parseFloat(current.demand_floor_fraction)
    : DEFAULT_TARIFF.demand_floor_fraction;
  const contractPenalty = current.contract_exceed_penalty_per_kva !== undefined && current.contract_exceed_penalty_per_kva !== null
    ? parseFloat(current.contract_exceed_penalty_per_kva)
    : DEFAULT_TARIFF.contract_exceed_penalty_per_kva;

  let windows = [];
  if (Array.isArray(current.windows) && current.windows.length > 0) {
    windows = current.windows.map((w) => ({
      start_hour: parseFloat(w.start_hour !== undefined ? w.start_hour : w.start),
      end_hour: parseFloat(w.end_hour !== undefined ? w.end_hour : w.end),
      multiplier: parseFloat(w.multiplier !== undefined ? w.multiplier : 1.0),
    }));
  } else {
    windows = DEFAULT_TARIFF.windows.map((w) => ({ ...w }));
  }

  // Pre-flight validation checks
  if (!name) {
    throw new Error("Tariff validation error: 'name' is required.");
  }
  if (isNaN(baseRate) || baseRate <= 0) {
    throw new Error("Tariff validation error: 'base_rate_per_kwh' must be a positive number.");
  }

  return {
    name,
    source,
    base_rate_per_kwh: baseRate,
    windows,
    tax_rate: taxRate,
    demand_rate_per_kva: demandRate,
    demand_floor_fraction: demandFloor,
    contract_exceed_penalty_per_kva: contractPenalty,
  };
}
