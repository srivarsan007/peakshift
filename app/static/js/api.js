/**
 * PeakShift API Client
 * Centralized fetch handler with AbortController, 60s timeout, and 422 error parsing.
 */

import { getTariffForRequest } from "./state.js";

let activeControllers = new Map();

async function request(endpoint, options = {}, cancelKey = null) {
  if (cancelKey && activeControllers.has(cancelKey)) {
    activeControllers.get(cancelKey).abort();
  }

  const controller = new AbortController();
  if (cancelKey) {
    activeControllers.set(cancelKey, controller);
  }

  const timeoutId = setTimeout(() => {
    controller.abort();
  }, 60000);

  const fetchOptions = {
    ...options,
    signal: controller.signal,
    headers: {
      "Accept": "application/json",
      ...(options.headers || {}),
    },
  };

  try {
    const res = await fetch(endpoint, fetchOptions);
    clearTimeout(timeoutId);
    if (cancelKey) activeControllers.delete(cancelKey);

    const data = await res.json().catch(() => null);

    if (!res.ok) {
      if (res.status === 422 && data) {
        throw {
          code: data.code || "VALIDATION_ERROR",
          message: data.message || "Validation failed",
          details: Array.isArray(data.details) ? data.details : [data.message || "Unknown error"],
          elapsed_ms: data.elapsed_ms || 0,
        };
      }
      throw {
        code: `HTTP_${res.status}`,
        message: data?.message || res.statusText || "Request failed",
        details: data?.details || [res.statusText],
        elapsed_ms: 0,
      };
    }

    return data;
  } catch (err) {
    clearTimeout(timeoutId);
    if (cancelKey) activeControllers.delete(cancelKey);

    if (err.name === "AbortError") {
      throw {
        code: "TIMEOUT_OR_ABORT",
        message: "Request timed out or was superseded by a newer request",
        details: ["Operation aborted after 60 seconds"],
      };
    }
    throw err;
  }
}

export const api = {
  async health() {
    return request("/api/health", { method: "GET" }, "health");
  },

  async getTariffs() {
    return request("/api/tariffs", { method: "GET" }, "tariffs");
  },

  async tariffs() {
    return request("/api/tariffs", { method: "GET" }, "tariffs");
  },

  async generatePlant({ preset, utilization, seed = 42, machines = null, overrides = null }) {
    return request(
      "/api/plants/generate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset, utilization, seed, machines, overrides }),
      },
      "generate"
    );
  },

  async uploadPlant(formData) {
    return request(
      "/api/plants/upload",
      {
        method: "POST",
        body: formData,
      },
      "upload"
    );
  },

  async optimize({ plant, tariff, config = {} }) {
    const validTariff = getTariffForRequest(tariff);
    return request(
      "/api/optimize",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plant, tariff: validTariff, config }),
      },
      "optimize"
    );
  },

  async verify({ plant, tariff, schedule, reported_metrics = null, peak_cap_kw = null }) {
    const validTariff = getTariffForRequest(tariff);
    return request(
      "/api/verify",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plant, tariff: validTariff, schedule, reported_metrics, peak_cap_kw }),
      },
      "verify"
    );
  },

  async replan({ plant, tariff, schedule, now_slot, event, seed = 42 }) {
    const validTariff = getTariffForRequest(tariff);
    let finalEvent = event;
    if (event && event.type === "tariff_change" && event.tariff) {
      finalEvent = { ...event, tariff: getTariffForRequest(event.tariff) };
    }
    return request(
      "/api/replan",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plant, tariff: validTariff, schedule, now_slot, event: finalEvent, seed }),
      },
      "replan"
    );
  },

  async pareto({ plant, tariff, caps, seed = 42 }) {
    const validTariff = getTariffForRequest(tariff);
    return request(
      "/api/pareto",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plant, tariff: validTariff, caps, seed }),
      },
      "pareto"
    );
  },

  async sweep({ preset, tariff, utilizations = null, seeds = null, max_iterations = null }) {
    const validTariff = getTariffForRequest(tariff);
    return request(
      "/api/sweep",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset, tariff: validTariff, utilizations, seeds, max_iterations }),
      },
      "sweep"
    );
  },

  async refine({ plant, tariff, schedule, peak_cap_kw = null, time_limit_sec = 20.0 }) {
    const validTariff = getTariffForRequest(tariff);
    return request(
      "/api/refine",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plant, tariff: validTariff, schedule, peak_cap_kw, time_limit_sec }),
      },
      "refine"
    );
  },
};
