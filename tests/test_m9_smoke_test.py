"""M9 End-to-End Smoke Test Checklist per SPEC §16."""

import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.tariff import load_default_tariff

client = TestClient(app)


def test_smoke_item_1_url_opens():
    """Item 1: Web application root opens and returns complete HTML shell."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<!DOCTYPE html>" in resp.text
    assert "PeakShift" in resp.text
    assert 'id="page-load"' in resp.text
    assert 'id="page-compare"' in resp.text
    assert 'id="page-disrupt"' in resp.text
    assert 'id="page-sweep"' in resp.text
    assert 'id="page-reasons"' in resp.text


def test_smoke_item_2_health_ok():
    """Item 2: /api/health returns ok status."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "peakshift"


def test_smoke_item_3_load_presets():
    """Item 3: All 3 standard demo presets generate valid plants."""
    for preset in ["machining", "textile", "food_lines"]:
        resp = client.post(
            "/api/plants/generate",
            json={"preset": preset, "utilization": 0.5, "seed": 42},
        )
        assert resp.status_code == 200, f"Preset {preset} generation failed"
        plant = resp.json()["plant"]
        assert len(plant["machines"]) >= 6
        assert len(plant["jobs"]) >= 20


def test_smoke_item_4_optimize_and_verifier_green():
    """Item 4: Optimize returns and the verifier badge is green (passed = True)."""
    gen_resp = client.post(
        "/api/plants/generate",
        json={"preset": "textile", "utilization": 0.6, "seed": 42},
    )
    plant = gen_resp.json()["plant"]
    tariff = load_default_tariff()

    opt_resp = client.post(
        "/api/optimize",
        json={"plant": plant, "tariff": tariff.model_dump()},
    )
    assert opt_resp.status_code == 200
    data = opt_resp.json()

    # Verifier passed
    assert data["verifier"]["passed"] is True, "Expected verifier to pass on portfolio schedule"
    # Cost ordering guarantee
    assert data["metrics"]["energy_cost"] <= data["baseline_b0"]["metrics"]["energy_cost"]
    # All 9 checks passed
    assert len(data["verifier"]["checks"]) == 9
    assert all(c["passed"] for c in data["verifier"]["checks"])


def test_smoke_item_5_disruption_console_events():
    """Item 5: Disruption console executes all 4 events and returns new schedules and diffs."""
    gen_resp = client.post(
        "/api/plants/generate",
        json={"preset": "food_lines", "utilization": 0.5, "seed": 42},
    )
    plant = gen_resp.json()["plant"]
    tariff = load_default_tariff()

    opt_resp = client.post(
        "/api/optimize",
        json={"plant": plant, "tariff": tariff.model_dump()},
    )
    schedule = opt_resp.json()["schedule"]

    events = [
        ("machine_down", {"type": "machine_down", "machine_id": plant["machines"][0]["id"], "from_slot": 10, "until_slot": 48}),
        ("tariff_change", {"type": "tariff_change", "tariff": tariff.model_dump()}),
        ("rush_job", {"type": "rush_job", "job": {"id": "RUSH_SMOKE", "duration_slots": 3, "release_slot": 10, "deadline_slot": 30, "mandatory": True, "units": 1}}),
        ("job_delay", {"type": "job_delay", "job_id": plant["jobs"][0]["id"], "extra_slots": 2}),
    ]

    for ev_name, ev_payload in events:
        replan_resp = client.post(
            "/api/replan",
            json={
                "plant": plant,
                "tariff": tariff.model_dump(),
                "schedule": schedule,
                "now_slot": 10,
                "event": ev_payload,
                "seed": 42,
            },
        )
        assert replan_resp.status_code == 200, f"Replan failed for event: {ev_name}"
        data = replan_resp.json()
        assert "schedule" in data
        assert "diff" in data
        assert data["status"] in ("ok", "target_at_risk")


def test_smoke_item_6_pareto_curve():
    """Item 6: Pareto frontier calculates trade-off points."""
    gen_resp = client.post(
        "/api/plants/generate",
        json={"preset": "machining", "utilization": 0.4, "seed": 42},
    )
    plant = gen_resp.json()["plant"]
    tariff = load_default_tariff()

    pareto_resp = client.post(
        "/api/pareto",
        json={"plant": plant, "tariff": tariff.model_dump(), "caps": [60.0, 90.0, 120.0]},
    )
    assert pareto_resp.status_code == 200
    points = pareto_resp.json()["points"]
    assert len(points) == 3


def test_smoke_item_7_sweep_completes():
    """Item 7: Sweep executes across levels within allowable time."""
    tariff = load_default_tariff()
    sweep_resp = client.post(
        "/api/sweep",
        json={"preset": "food_lines", "tariff": tariff.model_dump(), "utilizations": [0.35, 0.65], "seeds": [42]},
    )
    assert sweep_resp.status_code == 200
    assert len(sweep_resp.json()["result"]["levels"]) == 2


def test_smoke_item_8_offline_assets():
    """Item 8: All vendor, CSS, and JS assets load without CDN dependency."""
    for asset_path in [
        "/vendor/plotly.min.js",
        "/css/tokens.css",
        "/css/app.css",
        "/js/api.js",
        "/js/state.js",
        "/js/format.js",
        "/js/charts.js",
        "/js/pages/load.js",
        "/js/pages/compare.js",
        "/js/pages/disrupt.js",
        "/js/pages/sweep.js",
        "/js/pages/reasons.js",
    ]:
        resp = client.get(asset_path)
        assert resp.status_code == 200, f"Failed to serve asset: {asset_path}"
