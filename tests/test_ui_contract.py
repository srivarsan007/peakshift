"""UI Contract tests asserting that every field the frontend reads exists in backend responses."""

import io
import json
import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.generator import generate_plant
from peakshift.core.tariff import load_default_tariff

client = TestClient(app)


def test_openapi_schema_available():
    """Assert /openapi.json exists and defines required paths."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema["paths"]

    assert "/api/health" in paths
    assert "/api/tariffs" in paths
    assert "/api/plants/generate" in paths
    assert "/api/plants/upload" in paths
    assert "/api/optimize" in paths
    assert "/api/verify" in paths
    assert "/api/replan" in paths
    assert "/api/pareto" in paths
    assert "/api/sweep" in paths
    assert "/api/refine" in paths


def test_ui_contract_load_page():
    """Load Page reads: plant (machines, jobs, horizon_slots, slot_minutes), tariff (windows, base_rate_per_kwh)."""
    # 1. tariffs
    resp_t = client.get("/api/tariffs")
    assert resp_t.status_code == 200
    t_data = resp_t.json()["default_tariff"]
    assert "windows" in t_data
    assert "base_rate_per_kwh" in t_data

    # 2. plants/generate
    resp_g = client.post("/api/plants/generate", json={"preset": "machining", "utilization": 0.5, "seed": 42})
    assert resp_g.status_code == 200
    plant = resp_g.json()["plant"]
    assert "machines" in plant
    assert "jobs" in plant
    assert "horizon_slots" in plant
    assert "slot_minutes" in plant

    # 3. plants/upload error reporting
    resp_u = client.post("/api/plants/upload", files={"file": ("bad.csv", io.BytesIO(b"bad"), "text/csv")})
    assert resp_u.status_code == 422
    err_data = resp_u.json()
    assert "errors" in err_data or "details" in err_data


def test_ui_contract_compare_and_reasons_pages():
    """Compare and Reasons Pages read:
    baseline_b0, baseline_b1, schedule, metrics (kwh, energy_cost, tax, peak_kw, peak_kva,
    switch_offs, idle_kwh, makespan, jobs_placed, target_met, co2_kg),
    savings_vs_b0, savings_vs_b1, winner, bound, verifier, elapsed_ms, decisions.
    """
    plant = generate_plant("food_lines", utilization=0.3, seed=42)
    tariff = load_default_tariff()

    resp = client.post(
        "/api/optimize",
        json={"plant": plant.model_dump(), "tariff": tariff.model_dump()},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Top-level fields
    for field in [
        "baseline_b0", "baseline_b1", "schedule", "metrics",
        "savings_vs_b0", "savings_vs_b1", "winner", "bound",
        "verifier", "elapsed_ms", "decisions",
    ]:
        assert field in data, f"Missing binding field in /api/optimize: {field}"

    # Schedule fields
    sched = data["schedule"]
    assert "segments" in sched
    assert "placements" in sched
    for p in sched["placements"]:
        assert "job_id" in p
        assert "machine_id" in p
        assert "start" in p
        assert "end" in p

    # Metrics fields
    metrics = data["metrics"]
    for m_field in [
        "kwh", "energy_cost", "tax", "peak_kw", "peak_kva",
        "switch_offs", "idle_kwh", "makespan", "jobs_placed",
        "target_met", "co2_kg",
    ]:
        assert m_field in metrics, f"Missing metric in /api/optimize: {m_field}"

    # Decisions fields
    for d in data["decisions"]:
        assert "code" in d
        assert "text" in d
        assert "saving_inr" in d

    # Verifier fields
    verifier = data["verifier"]
    assert "passed" in verifier
    assert "checks" in verifier
    for c in verifier["checks"]:
        assert "name" in c
        assert "passed" in c
        assert "details" in c


def test_ui_contract_disruption_page():
    """Disruption Page reads:
    schedule, metrics_before, metrics_after, diff (job_id, old, new, reason),
    status, unplaced, units_short, target_met, elapsed_ms.
    """
    plant = generate_plant("textile", utilization=0.5, seed=42)
    tariff = load_default_tariff()

    # Get initial schedule
    opt_resp = client.post(
        "/api/optimize",
        json={"plant": plant.model_dump(), "tariff": tariff.model_dump()},
    )
    schedule = opt_resp.json()["schedule"]

    replan_resp = client.post(
        "/api/replan",
        json={
            "plant": plant.model_dump(),
            "tariff": tariff.model_dump(),
            "schedule": schedule,
            "now_slot": 6,
            "event": {
                "type": "machine_down",
                "machine_id": plant.machines[0].id,
                "from_slot": 6,
                "until_slot": plant.horizon_slots,
            },
            "seed": 42,
        },
    )
    assert replan_resp.status_code == 200
    data = replan_resp.json()

    for field in [
        "schedule", "metrics_before", "metrics_after",
        "diff", "status", "unplaced", "units_short", "target_met", "elapsed_ms",
    ]:
        assert field in data, f"Missing field in /api/replan response: {field}"


def test_ui_contract_pareto_page():
    """Pareto / Cap Page reads: points (cap_kw, cost, peak_kw, cap_satisfied), elapsed_ms."""
    plant = generate_plant("food_lines", utilization=0.4, seed=42)
    tariff = load_default_tariff()

    resp = client.post(
        "/api/pareto",
        json={
            "plant": plant.model_dump(),
            "tariff": tariff.model_dump(),
            "caps": [100.0, 150.0],
            "seed": 42,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "points" in data
    assert "elapsed_ms" in data
    for pt in data["points"]:
        assert "cap_kw" in pt
        assert "cost" in pt
        assert "peak_kw" in pt
        assert "cap_satisfied" in pt


def test_static_files_served():
    """Assert index.html, tokens.css, app.css, and vendor/plotly.min.js are served offline."""
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert "PeakShift" in resp_index.text

    resp_tokens = client.get("/css/tokens.css")
    assert resp_tokens.status_code == 200
    assert "--bg-base" in resp_tokens.text

    resp_app_css = client.get("/css/app.css")
    assert resp_app_css.status_code == 200

    resp_plotly = client.get("/vendor/plotly.min.js")
    assert resp_plotly.status_code == 200
    assert len(resp_plotly.content) > 1000000
