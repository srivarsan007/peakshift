"""Tests for Milestone M4: H1, H2, H3, Improvement Search, Portfolio, Sanity Bands, and Speed."""

import copy
import random
import time
import pytest

from peakshift.core.generator import generate_plant
from peakshift.core.models import Job, Machine, Plant, Tariff, TariffWindow
from peakshift.core.portfolio import run_portfolio
from peakshift.core.tariff import load_default_tariff
from peakshift.verify.verifier import verify_schedule


@pytest.fixture
def default_tariff():
    return load_default_tariff()


# =========================================================
# 1. Determinism and Guarantee Tests
# =========================================================

def test_portfolio_determinism(default_tariff):
    """Same input plus same seed gives identical portfolio result."""
    plant = generate_plant("machining", utilization=0.5, seed=42)
    res1 = run_portfolio(plant, default_tariff, seed=123)
    res2 = run_portfolio(plant, default_tariff, seed=123)

    assert res1.metrics.energy_cost == res2.metrics.energy_cost
    assert res1.winner == res2.winner
    assert len(res1.schedule.placements) == len(res2.schedule.placements)


def test_portfolio_guarantee_cost_le_b1(default_tariff):
    """Portfolio cost must always be <= B1 cost <= B0 cost when B1 meets target."""
    plant = generate_plant("food_lines", utilization=0.3, seed=42)
    res = run_portfolio(plant, default_tariff, seed=42)

    b0_cost = res.baseline_b0[1].energy_cost
    b1_cost = res.baseline_b1[1].energy_cost
    port_cost = res.metrics.energy_cost

    assert res.metrics.target_met is True
    assert port_cost <= b1_cost + 1e-4, f"Portfolio cost {port_cost} > B1 cost {b1_cost}"
    assert b1_cost <= b0_cost + 1e-4, f"B1 cost {b1_cost} > B0 cost {b0_cost}"


def test_zero_idle_power_gives_zero_gap_cost(default_tariff):
    """When idle_kw = 0, gap idle cost is zero."""
    plant = generate_plant("food_lines", utilization=0.3, seed=7)
    for m in plant.machines:
        m.idle_kw = 0.0

    res = run_portfolio(plant, default_tariff, seed=42)
    assert res.metrics.idle_kwh == pytest.approx(0.0)


def test_flat_tariff_behavior():
    """Flat tariff with uniform rates gives no rate-shifting distortion."""
    flat_tariff = Tariff(
        name="Flat Tariff",
        base_rate_per_kwh=7.0,
        windows=[],
    )
    plant = generate_plant("machining", utilization=0.4, seed=15)
    res = run_portfolio(plant, flat_tariff, seed=42)

    # All placements must be valid and verifier must pass
    ver = verify_schedule(plant, flat_tariff, res.schedule, reported_metrics=res.metrics.model_dump())
    assert ver.passed is True


# =========================================================
# 2. Performance Target: 10 machines, 100 jobs <= 3.0s
# =========================================================

def test_performance_target_10_machines_100_jobs(default_tariff):
    """Portfolio runtime for 10 machines and 100 jobs must be <= 3.0 seconds."""
    machines = [
        Machine(
            id=f"M{i + 1}",
            run_kw=20.0,
            idle_kw=8.0,
            restart_kwh=8.0,
            warmup_slots=1,
            min_off_slots=1,
        )
        for i in range(10)
    ]
    # 100 jobs of duration 2 to 4
    rng = random.Random(42)
    jobs = [
        Job(
            id=f"J{j + 1}",
            duration_slots=rng.randint(2, 4),
            release_slot=rng.randint(0, 10),
            deadline_slot=rng.randint(20, 48),
        )
        for j in range(100)
    ]
    plant = Plant(
        name="PerfTestPlant",
        horizon_slots=48,
        slot_minutes=30,
        machines=machines,
        jobs=jobs,
    )

    t0 = time.perf_counter()
    res = run_portfolio(plant, default_tariff, seed=42)
    elapsed = time.perf_counter() - t0

    assert elapsed <= 3.0, f"Portfolio took {elapsed:.2f}s, expected <= 3.0s"
    assert res.metrics.target_met is True
    assert res.metrics.jobs_placed == 100
    if res.baseline_b1[1].target_met:
        assert res.metrics.energy_cost <= res.baseline_b1[1].energy_cost + 1e-4


# =========================================================
# 3. Sanity Bands (Section 10)
# =========================================================

def create_sanity_plant(utilization: float, seed: int = 42) -> Plant:
    """Standard sanity setup: 10 machines, run 20 kW, idle 8 kW, restart 8 kWh, 30 min slots."""
    machines = [
        Machine(
            id=f"M{i + 1}",
            run_kw=20.0,
            idle_kw=8.0,
            restart_kwh=8.0,
            warmup_slots=0,
            min_off_slots=0,
            initial_state="idle",
        )
        for i in range(10)
    ]
    total_slots = 10 * 48
    target_slots = int(round(total_slots * utilization))

    rng = random.Random(seed)
    jobs = []
    curr = 0
    idx = 1
    while curr < target_slots:
        rem = target_slots - curr
        dur = rng.randint(2, 6) if rem >= 6 else rem
        jobs.append(
            Job(
                id=f"J{idx}",
                duration_slots=dur,
                release_slot=0,
                deadline_slot=48,
            )
        )
        curr += dur
        idx += 1

    return Plant(
        name=f"SanityPlant_{int(utilization*100)}",
        horizon_slots=48,
        slot_minutes=30,
        machines=machines,
        jobs=jobs,
    )


def test_sanity_band_30_percent_load(default_tariff):
    """Savings vs B0 at ~30% load should be ~50% (+/- 6 points -> 44% to 56%)."""
    plant = create_sanity_plant(0.30, seed=42)
    res = run_portfolio(plant, default_tariff, seed=42)

    # Sanity band: 50% +/- 6 points
    assert 44.0 <= res.savings_vs_b0_pct <= 56.0, (
        f"At 30% load, savings vs B0 was {res.savings_vs_b0_pct}%, expected between 44% and 56%"
    )


def test_sanity_band_50_percent_load(default_tariff):
    """Savings vs B0 at ~50% load should be ~28 to 30% (+/- 6 points -> 22% to 36%)."""
    plant = create_sanity_plant(0.50, seed=42)
    res = run_portfolio(plant, default_tariff, seed=42)

    assert 22.0 <= res.savings_vs_b0_pct <= 36.0, (
        f"At 50% load, savings vs B0 was {res.savings_vs_b0_pct}%, expected between 22% and 36%"
    )


def test_sanity_band_83_percent_load(default_tariff):
    """Savings vs B0 at ~83% load should be ~7 to 8% (+/- 6 points -> 1% to 14%)."""
    plant = create_sanity_plant(0.83, seed=42)
    res = run_portfolio(plant, default_tariff, seed=42)

    assert 1.0 <= res.savings_vs_b0_pct <= 14.0, (
        f"At 83% load, savings vs B0 was {res.savings_vs_b0_pct}%, expected between 1% and 14%"
    )


# =========================================================
# 4. Property Tests: 50 Seeds
# =========================================================

def test_portfolio_50_seeds_properties(default_tariff):
    """Verifies cost(portfolio) <= cost(B1) <= cost(B0) and verifier validity across 50 seeds."""
    presets = ["machining", "textile", "food_lines"]
    utilizations = [0.3, 0.4, 0.5, 0.6]

    for seed in range(50):
        preset = presets[seed % len(presets)]
        util = utilizations[seed % len(utilizations)]

        plant = generate_plant(preset, utilization=util, seed=seed)
        res = run_portfolio(plant, default_tariff, seed=seed)

        # Check ordering
        b0_cost = res.baseline_b0[1].energy_cost
        b1_cost = res.baseline_b1[1].energy_cost
        port_cost = res.metrics.energy_cost

        if res.baseline_b1[1].target_met:
            assert port_cost <= b1_cost + 1e-4, f"Seed {seed}: portfolio ({port_cost}) > B1 ({b1_cost})"
        assert b1_cost <= b0_cost + 1e-4, f"Seed {seed}: B1 ({b1_cost}) > B0 ({b0_cost})"

        # Check no job placed twice
        placed_ids = [p.job_id for p in res.schedule.placements]
        assert len(placed_ids) == len(set(placed_ids)), f"Seed {seed}: duplicate jobs placed"

        # Check verifier on result
        ver = verify_schedule(plant, default_tariff, res.schedule, reported_metrics=res.metrics.model_dump())
        # Check that physical checks pass
        for c in ver.checks:
            if c.name != "mandatory_and_target" or res.metrics.target_met:
                assert c.passed is True, f"Seed {seed} failed check {c.name}: {c.details}"
