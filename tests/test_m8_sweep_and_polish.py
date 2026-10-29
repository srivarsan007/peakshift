"""M8 tests: Load sweep analysis and exact polish CP-SAT module."""

import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.generator import generate_plant
from peakshift.core.models import Machine, Plant, Job
from peakshift.core.polish import can_refine_plant, refine_schedule
from peakshift.core.portfolio import run_portfolio
from peakshift.core.sweep import run_load_sweep
from peakshift.core.tariff import load_default_tariff

client = TestClient(app)


@pytest.fixture(scope="module")
def default_tariff():
    return load_default_tariff()


def test_sweep_execution_and_metrics(default_tariff):
    """Test sweep across 2 utilizations and 2 seeds."""
    result = run_load_sweep(
        preset_name="textile",
        tariff=default_tariff,
        utilizations=[0.3, 0.7],
        seeds=[42, 43],
        max_iterations=2000,
    )
    assert result.preset == "textile"
    assert len(result.levels) == 2

    lvl0 = result.levels[0]
    assert lvl0.utilization == 0.3
    assert lvl0.seeds_count == 2
    assert lvl0.mean_savings_b0_pct >= lvl0.mean_savings_b1_pct
    assert lvl0.min_savings_b0_pct <= lvl0.max_savings_b0_pct

    lvl1 = result.levels[1]
    assert lvl1.utilization == 0.7
    # 30% load should have higher savings vs B0 than 70% load
    assert lvl0.mean_savings_b0_pct > lvl1.mean_savings_b0_pct


def test_api_sweep_endpoint(default_tariff):
    """Test POST /api/sweep endpoint schema and status."""
    payload = {
        "preset": "food_lines",
        "tariff": default_tariff.model_dump(),
        "utilizations": [0.4, 0.6],
        "seeds": [42],
        "max_iterations": 2000,
    }
    resp = client.post("/api/sweep", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "result" in data
    assert "elapsed_ms" in data
    assert data["result"]["preset"] == "food_lines"
    assert len(data["result"]["levels"]) == 2


def test_can_refine_plant_constraints():
    """Verify can_refine_plant accurately identifies ineligible plants."""
    # Machine with warmup > 0
    m_warmup = Machine(id="M1", run_kw=10.0, idle_kw=2.0, restart_kwh=1.0, warmup_slots=2)
    j = Job(id="J1", duration_slots=2, release_slot=0, deadline_slot=10)
    p_warmup = Plant(name="warmup", machines=[m_warmup], jobs=[j])
    can_ref, reason = can_refine_plant(p_warmup)
    assert not can_ref
    assert "warmup_slots" in reason

    # Plant with 6 machines (> 5)
    six_machines = [
        Machine(id=f"M{i}", run_kw=10.0, idle_kw=2.0, restart_kwh=0.0, warmup_slots=0)
        for i in range(6)
    ]
    p_large = Plant(name="large", machines=six_machines, jobs=[j])
    can_ref, reason = can_refine_plant(p_large)
    assert not can_ref
    assert "machines" in reason

    # Eligible small plant
    m_simple = Machine(id="M1", run_kw=10.0, idle_kw=2.0, restart_kwh=0.0, warmup_slots=0)
    p_simple = Plant(name="simple", machines=[m_simple], jobs=[j])
    can_ref, reason = can_refine_plant(p_simple)
    assert can_ref


def test_polish_refine_schedule_graceful_fallback(default_tariff):
    """Verify polish gracefully returns valid result when ortools is missing or plant is ineligible."""
    plant = generate_plant("machining", utilization=0.5, seed=42)
    port_res = run_portfolio(plant=plant, tariff=default_tariff, seed=42)

    result = refine_schedule(
        plant=plant,
        tariff=default_tariff,
        current_schedule=port_res.schedule,
    )
    assert result.status in ("disabled", "skipped", "improved", "optimal", "unchanged")
    assert result.schedule is not None
    assert result.elapsed_ms >= 0


def test_api_refine_endpoint(default_tariff):
    """Test POST /api/refine endpoint contract."""
    plant = generate_plant("machining", utilization=0.4, seed=42)
    port_res = run_portfolio(plant=plant, tariff=default_tariff, seed=42)

    payload = {
        "plant": plant.model_dump(),
        "tariff": default_tariff.model_dump(),
        "schedule": port_res.schedule.model_dump(),
    }
    resp = client.post("/api/refine", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "result" in data
    assert "status" in data["result"]
    assert "message" in data["result"]
    assert "schedule" in data["result"]
