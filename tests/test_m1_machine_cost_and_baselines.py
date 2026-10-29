"""Tests for Milestone M1: machine_cost, B0 and B1 baselines, and metrics."""

import pytest
import random
from peakshift.core.machine_cost import evaluate_machine_schedule
from peakshift.core.baseline import build_b0_schedule, build_b1_schedule
from peakshift.core.cost import compute_schedule_metrics
from peakshift.core.generator import generate_plant
from peakshift.core.models import Job, Machine, Plant, Tariff, TariffWindow
from peakshift.core.tariff import load_default_tariff


# =========================================================
# 8 Hand-Worked Cases for machine_cost
# =========================================================

def test_case_1_no_gap():
    """Case 1: Machine runs continuously for the full horizon (slots 0..48)."""
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0)
    rate_profile = [7.50] * 48
    busy = [(0, 48, "J1")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    # 48 slots * 0.5 h = 24 h
    # kWh = 20 kW * 24 h = 480 kWh
    # cost = 480 * 7.50 = 3600.0
    assert len(res.segments) == 1
    assert res.segments[0].state == "run"
    assert res.segments[0].start == 0
    assert res.segments[0].end == 48
    assert res.kwh == pytest.approx(480.0)
    assert res.cost == pytest.approx(3600.0)
    assert res.idle_kwh == pytest.approx(0.0)
    assert res.switch_offs == 0


def test_case_2_one_short_gap():
    """Case 2: Short gap where idle energy < restart energy; stays idle."""
    # run: [0, 4), gap [4, 6) of len 2 (1 h), run: [6, 10), trailing off: [10, 48)
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0)
    rate_profile = [7.50] * 48
    busy = [(0, 4, "J1"), (6, 10, "J2")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    # Gap [4, 6): len = 2 slots = 1 h.
    # Idle cost = 8 kW * 1 h * 7.50 = 60.0
    # Off cost = 10 kWh * 7.50 = 75.0
    # Since 60.0 < 75.0, gap stays idle!
    # Trailing gap [10, 48) is off (cost 0)
    # Total kWh = (4 + 4) * 0.5 * 20 (run) + 2 * 0.5 * 8 (idle) = 80 + 8 = 88 kWh
    # Cost = 88 * 7.50 = 660.0
    assert any(s.state == "idle" and s.start == 4 and s.end == 6 for s in res.segments)
    assert res.switch_offs == 0
    assert res.kwh == pytest.approx(88.0)
    assert res.idle_kwh == pytest.approx(8.0)
    assert res.cost == pytest.approx(660.0)


def test_case_3_one_long_gap():
    """Case 3: Long gap where idle energy > restart energy; switches off."""
    # run: [0, 4), gap [4, 12) of len 8 (4 h), run: [12, 16)
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0)
    rate_profile = [7.50] * 48
    busy = [(0, 4, "J1"), (12, 16, "J2")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    # Gap [4, 12): len = 8 slots = 4 h.
    # Idle cost = 8 kW * 4 h * 7.50 = 240.0
    # Off cost = 10 kWh * 7.50 = 75.0
    # Since 75.0 < 240.0, switches off!
    # Segments: run [0, 4), off [4, 12), run [12, 16), off [16, 48)
    # Total run kWh = 8 slots * 0.5 * 20 = 80 kWh
    # Restart kWh = 10 kWh
    # Total kWh = 90 kWh
    # Total cost = 90 * 7.50 = 675.0
    # Saving vs idle = 240 - 75 = 165.0
    assert any(s.state == "off" and s.start == 4 and s.end == 12 for s in res.segments)
    assert res.switch_offs == 1
    assert res.kwh == pytest.approx(90.0)
    assert res.idle_kwh == pytest.approx(0.0)
    assert res.cost == pytest.approx(675.0)


def test_case_4_leading_gap():
    """Case 4: Leading gap on machine starting 'idle' switches off when beneficial."""
    # First job starts at slot 6. Gap [0, 6) of length 6 (3 h).
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0, initial_state="idle")
    rate_profile = [7.50] * 48
    busy = [(6, 10, "J1")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    # Gap [0, 6): idle energy = 3 h * 8 kW = 24 kWh.
    # Restart energy = 10 kWh.
    # Off is cheaper -> switches off from 0 to 6.
    assert any(s.state == "off" and s.start == 0 and s.end == 6 for s in res.segments)
    assert res.switch_offs == 1
    # Run = 4 slots = 2 h -> 40 kWh. Restart = 10 kWh. Total = 50 kWh.
    assert res.kwh == pytest.approx(50.0)
    assert res.cost == pytest.approx(50.0 * 7.50)


def test_case_5_trailing_gap():
    """Case 5: Trailing gap after last job is always off at cost 0."""
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0)
    rate_profile = [7.50] * 48
    busy = [(0, 10, "J1")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    # Trailing gap [10, 48) is off
    assert any(s.state == "off" and s.start == 10 and s.end == 48 for s in res.segments)
    # Total kWh = 10 * 0.5 * 20 = 100 kWh
    assert res.kwh == pytest.approx(100.0)
    assert res.cost == pytest.approx(750.0)


def test_case_6_warmup_gt_zero():
    """Case 6: Warmup > 0; gap creates off segment and warmup segment before next run."""
    # warmup_slots = 2, min_off_slots = 2
    # Gap [4, 12): length 8 >= 2 + 2 = 4
    # Off segment [4, 10), Warmup segment [10, 12)
    # Restart energy charged at rate[12 - 2] = rate[10]
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0, warmup_slots=2, min_off_slots=2)
    rate_profile = [7.50] * 48
    rate_profile[10] = 9.0  # rate at warmup start
    busy = [(0, 4, "J1"), (12, 16, "J2")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    assert any(s.state == "off" and s.start == 4 and s.end == 10 for s in res.segments)
    assert any(s.state == "warmup" and s.start == 10 and s.end == 12 for s in res.segments)
    # Warmup power = restart_kwh / (w * h) = 10 / (2 * 0.5) = 10 kW
    assert res.slot_kw[10] == pytest.approx(10.0)
    assert res.slot_kw[11] == pytest.approx(10.0)
    # Restart cost = 10 kWh * 9.0 = 90.0
    # Run cost = (4 + 4) * 0.5 * 20 * 7.50 = 600.0
    # Total cost = 690.0
    assert res.cost == pytest.approx(690.0)


def test_case_7_cycle_cap():
    """Case 7: max_switch_cycles restricts the number of switch-offs to the top K savings."""
    # Machine with max_switch_cycles = 1
    # 3 jobs, creating 2 gaps:
    # Gap 1: [4, 10) len 6 (3 h) -> idle cost = 3 * 8 * 7.5 = 180. Off cost = 10 * 7.5 = 75. Saving = 105.
    # Gap 2: [14, 24) len 10 (5 h) -> idle cost = 5 * 8 * 7.5 = 300. Off cost = 75. Saving = 225.
    # With cap = 1, Gap 2 must turn off, but Gap 1 must stay idle!
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0, max_switch_cycles=1)
    rate_profile = [7.50] * 48
    busy = [(0, 4, "J1"), (10, 14, "J2"), (24, 28, "J3")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    assert res.switch_offs == 1
    # Gap 1 stays idle
    assert any(s.state == "idle" and s.start == 4 and s.end == 10 for s in res.segments)
    # Gap 2 turns off
    assert any(s.state == "off" and s.start == 14 and s.end == 24 for s in res.segments)


def test_case_8_initial_state_off():
    """Case 8: Machine starting 'off' has free leading off gap, pays restart once on first use."""
    # First job starts at slot 8, warmup_slots = 2
    # Leading off [0, 6), warmup [6, 8)
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0, warmup_slots=2, initial_state="off")
    rate_profile = [7.50] * 48
    busy = [(8, 12, "J1")]

    res = evaluate_machine_schedule(m, busy, rate_profile, slot_minutes=30)
    assert any(s.state == "off" and s.start == 0 and s.end == 6 for s in res.segments)
    assert any(s.state == "warmup" and s.start == 6 and s.end == 8 for s in res.segments)
    assert res.switch_offs == 0  # Did not switch off from running/idle state
    # Restart energy = 10, Run energy = 4 * 0.5 * 20 = 40. Total = 50.
    assert res.kwh == pytest.approx(50.0)
    assert res.cost == pytest.approx(50.0 * 7.50)


# =========================================================
# Property Test: B1 <= B0 on 50 Random Seeds
# =========================================================

def test_b1_le_b0_on_50_seeds():
    """Verify that cost(B1) <= cost(B0) across 50 seeded random plants."""
    tariff = load_default_tariff()

    for seed in range(50):
        rng = random.Random(seed)
        num_machines = rng.randint(2, 6)
        num_jobs = rng.randint(5, 20)

        machines = []
        for i in range(num_machines):
            run_kw = round(rng.uniform(15.0, 35.0), 1)
            idle_kw = round(rng.uniform(4.0, run_kw * 0.6), 1)
            restart_kwh = round(rng.uniform(3.0, 12.0), 1)
            warmup = rng.choice([0, 1, 2])
            min_off = rng.choice([0, 1, 2])
            initial_state = rng.choice(["idle", "off"])
            machines.append(
                Machine(
                    id=f"M{i}",
                    run_kw=run_kw,
                    idle_kw=idle_kw,
                    restart_kwh=restart_kwh,
                    warmup_slots=warmup,
                    min_off_slots=min_off,
                    initial_state=initial_state,
                )
            )

        jobs = []
        for j in range(num_jobs):
            dur = rng.randint(2, 6)
            rel = rng.randint(0, 10)
            dead = rng.randint(rel + dur, 48)
            jobs.append(
                Job(
                    id=f"J{j}",
                    duration_slots=dur,
                    release_slot=rel,
                    deadline_slot=dead,
                )
            )

        plant = Plant(
            name=f"Random Plant {seed}",
            horizon_slots=48,
            slot_minutes=30,
            machines=machines,
            jobs=jobs,
        )

        _, m0 = build_b0_schedule(plant, tariff)
        _, m1 = build_b1_schedule(plant, tariff)

        assert m1.energy_cost <= m0.energy_cost + 1e-4, (
            f"Seed {seed} failed: B1 cost ({m1.energy_cost}) > B0 cost ({m0.energy_cost})"
        )
        assert m1.kwh <= m0.kwh + 1e-4, (
            f"Seed {seed} failed: B1 kWh ({m1.kwh}) > B0 kWh ({m0.kwh})"
        )


@pytest.mark.parametrize("preset,util", [
    ("textile", 0.6),
    ("textile", 0.8),
    ("machining", 0.6),
    ("machining", 0.85),
    ("food_lines", 0.5),
    ("food_lines", 0.75),
])
def test_baselines_place_all_jobs(preset, util):
    """Verify that B0 and B1 baselines place 100% of jobs across all presets."""
    plant = generate_plant(preset_name=preset, utilization=util, seed=42)
    tariff = load_default_tariff()

    b0_sched, b0_metrics = build_b0_schedule(plant, tariff)
    assert len(b0_sched.unplaced) == 0, (
        f"B0 left {len(b0_sched.unplaced)}/{len(plant.jobs)} jobs unplaced on {preset} (util={util}): {b0_sched.unplaced}"
    )
    assert b0_metrics.jobs_placed == len(plant.jobs)

    b1_sched, b1_metrics = build_b1_schedule(plant, tariff)
    assert len(b1_sched.unplaced) == 0, (
        f"B1 left {len(b1_sched.unplaced)}/{len(plant.jobs)} jobs unplaced on {preset} (util={util}): {b1_sched.unplaced}"
    )
    assert b1_metrics.jobs_placed == len(plant.jobs)
