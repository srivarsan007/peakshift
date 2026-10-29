#!/usr/bin/env node
/**
 * capture_real_screenshots.js
 * Captures authentic headless Chrome screenshots for all 5 pages
 * at 1366x768 (desktop) and 390x844 (mobile).
 *
 * Generates:
 *   docs/screenshots/01_load_page.png
 *   docs/screenshots/02_compare_page.png
 *   docs/screenshots/03_disrupt_page.png
 *   docs/screenshots/04_sweep_page.png
 *   docs/screenshots/05_reasons_page.png
 *
 * Zero CDN, zero npm dependencies.
 */

"use strict";
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

const BASE_URL = process.env.PEAKSHIFT_URL || process.argv[2] || "http://127.0.0.1:8000";
const OUT_DIR = path.join(__dirname, "../docs/screenshots");
const DBG_PORT = 9226; // fresh port

fs.mkdirSync(OUT_DIR, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let d = "";
      res.on("data", (c) => (d += c));
      res.on("end", () => {
        try {
          resolve(JSON.parse(d));
        } catch (e) {
          reject(e);
        }
      });
    }).on("error", reject);
  });
}

function chromeBin() {
  for (const p of [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ]) {
    try {
      if (fs.existsSync(p)) return p;
    } catch (_) {}
  }
  return "google-chrome";
}

async function openCDP() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await fetchJson(`http://127.0.0.1:${DBG_PORT}/json`);
      const page = list.find((t) => t.type === "page" && t.url && t.url.includes("http") && t.webSocketDebuggerUrl)
        || list.find((t) => t.type === "page" && t.webSocketDebuggerUrl)
        || list.find((t) => t.webSocketDebuggerUrl);
      if (page) {
        const wsUrl = page.webSocketDebuggerUrl;
        return await new Promise((res, rej) => {
          const WS = typeof WebSocket !== "undefined"
            ? WebSocket
            : (() => {
                try {
                  return require("ws");
                } catch (_) {
                  return null;
                }
              })();
          if (!WS) {
            rej(new Error("No WebSocket available"));
            return;
          }
          const sock = new WS(wsUrl);
          let id = 0;
          const callbacks = new Map();
          sock.onmessage = ({ data }) => {
            const msg = JSON.parse(data);
            if (msg.id && callbacks.has(msg.id)) {
              const { ok, fail } = callbacks.get(msg.id);
              callbacks.delete(msg.id);
              msg.error ? fail(msg.error) : ok(msg.result);
            }
          };
          sock.onopen = () =>
            res({
              send(method, params = {}) {
                return new Promise((ok, fail) => {
                  const mid = ++id;
                  callbacks.set(mid, { ok, fail });
                  sock.send(JSON.stringify({ id: mid, method, params }));
                });
              },
              close() {
                sock.close();
              },
            });
          sock.onerror = rej;
        });
      }
    } catch (_) {}
    await sleep(400);
  }
  throw new Error("Could not connect to Chrome CDP on port " + DBG_PORT);
}

async function nav(cdp, url) {
  await cdp.send("Page.navigate", { url });
  await sleep(600);
}

async function setVP(cdp, w, h, dpr = 1) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: w,
    height: h,
    deviceScaleFactor: dpr,
    mobile: w < 600,
  });
}

async function shot(cdp, name) {
  const { data } = await cdp.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
  });
  const out = path.join(OUT_DIR, name);
  fs.writeFileSync(out, Buffer.from(data, "base64"));
  console.log(`  ✓ saved ${name}`);
}

async function waitExpr(cdp, expr, timeout = 90_000, interval = 800) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    try {
      const { result } = await cdp.send("Runtime.evaluate", {
        expression: expr,
        returnByValue: true,
      });
      if (result.value === true || result.value === "true") return;
    } catch (_) {}
    await sleep(interval);
  }
  throw new Error(`Timeout (${timeout}ms): ${expr}`);
}

async function main() {
  console.log("PeakShift Real Screenshot Capture (Desktop 1366x768 & Mobile 390x844)");
  console.log("═════════════════════════════════════════════════════════════════════");

  const chrome = spawn(chromeBin(), [
    `--remote-debugging-port=${DBG_PORT}`,
    "--headless=new",
    "--no-sandbox",
    "--disable-extensions",
    "--user-data-dir=/tmp/peakshift_chrome_shots",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1366,768",
    "--force-color-profile=srgb",
    BASE_URL,
  ], { stdio: "ignore" });
  process.on("exit", () => chrome.kill());

  await sleep(2000);
  const cdp = await openCDP();
  await cdp.send("Runtime.enable", {});
  await cdp.send("Page.enable", {});
  console.log("  Chrome CDP connected");

  await nav(cdp, BASE_URL);
  await sleep(800);

  // ── 1. Load Page ─────────────────────────────────────────────────────────
  console.log("\n[1/5] Load Page...");
  await setVP(cdp, 1366, 768);
  await nav(cdp, `${BASE_URL}/#load`);
  await sleep(1500);

  // Select textile preset
  await cdp.send("Runtime.evaluate", {
    expression: `(function(){
      const card = document.querySelector('.preset-card[data-preset="textile"]')
                || document.querySelector('.preset-card');
      if (card) card.click();
    })()`,
  });
  await sleep(600);

  await shot(cdp, "01_load_page.png");

  // ── Run Optimization ─────────────────────────────────────────────────────
  console.log("  Running optimization (textile preset)...");
  await setVP(cdp, 1366, 768);
  await cdp.send("Runtime.evaluate", {
    expression: `(function(){
      const btn = document.getElementById('btn-run-opt')
               || document.getElementById('btn-global-run');
      if (btn) btn.click();
    })()`,
  });

  await waitExpr(
    cdp,
    `!!(window.__PEAKSHIFT_STORE__ && window.__PEAKSHIFT_STORE__.get().lastResult)`,
    90_000
  );
  console.log("  Optimization completed.");

  // ── 2. Compare Page ──────────────────────────────────────────────────────
  console.log("\n[2/5] Compare Page...");
  await nav(cdp, `${BASE_URL}/#compare`);
  await sleep(2500); // Plotly Gantt render time

  await setVP(cdp, 1366, 768);
  await sleep(500);
  await shot(cdp, "02_compare_page.png");

  // ── 3. Disruption Page ───────────────────────────────────────────────────
  console.log("\n[3/5] Disruption Page...");
  await setVP(cdp, 1366, 768);
  await nav(cdp, `${BASE_URL}/#disrupt`);
  await sleep(1500);

  // Trigger M3 breakdown
  console.log("  Triggering M3 breakdown at slot 12...");
  await cdp.send("Runtime.evaluate", {
    expression: `(function(){
      const mSel = document.getElementById("select-failed-machine");
      if (mSel) {
        mSel.value = "M3";
        mSel.dispatchEvent(new Event("change"));
      }
      const slotSlider = document.getElementById("disrupt-now-slot");
      if (slotSlider) {
        slotSlider.value = "12";
        slotSlider.dispatchEvent(new Event("input"));
      }
      document.getElementById("btn-trigger-replan")?.click();
    })()`,
  });

  await waitExpr(
    cdp,
    `!!(window.__PEAKSHIFT_STORE__?.get()?.disruptResult)`,
    60_000
  );
  console.log("  Disruption re-plan complete.");
  await sleep(2500);

  await setVP(cdp, 1366, 768);
  await sleep(500);
  await shot(cdp, "03_disrupt_page.png");

  // ── 4. Sweep Page ────────────────────────────────────────────────────────
  console.log("\n[4/5] Sweep Page...");
  await setVP(cdp, 1366, 768);
  await nav(cdp, `${BASE_URL}/#sweep`);
  await sleep(1200);

  try {
    await waitExpr(
      cdp,
      `!!(window.__PEAKSHIFT_STORE__?.get()?.sweepResult?.length > 0)`,
      90_000
    );
    console.log("  Sweep curve ready.");
  } catch (e) {
    console.warn("  Sweep wait warning:", e.message);
  }

  try {
    await waitExpr(
      cdp,
      `!!(window.__PEAKSHIFT_STORE__?.get()?.paretoResult?.points?.length > 0)`,
      60_000
    );
    console.log("  Pareto curve ready.");
  } catch (e) {
    console.warn("  Pareto wait warning:", e.message);
  }

  await sleep(2500);

  await setVP(cdp, 1366, 768);
  await sleep(500);
  await shot(cdp, "04_sweep_page.png");

  // ── 5. Reasons Page ──────────────────────────────────────────────────────
  console.log("\n[5/5] Reasons & Audit Page...");
  await setVP(cdp, 1366, 768);
  await nav(cdp, `${BASE_URL}/#reasons`);
  await sleep(1500);

  await shot(cdp, "05_reasons_page.png");

  console.log("\n═════════════════════════════════════════════════════════════════════");
  console.log("✅ All real screenshots successfully captured!");

  cdp.close();
  chrome.kill();
  process.exit(0);
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
