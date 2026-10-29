"""Tests for Milestone M3: Verifier and Negative Tests."""

import copy
import pytest

from peakshift.core.baseline import build_b0_schedule, build_b1_schedule
from peakshift.core.generator import generate_plant
from peakshift.core.models import Job, Machine, Placement, Plant, Schedule, Segment, Tariff
from peakshift.core.tariff import load_default_tariff
from peakshift.verify.verifier import verify_schedule


@pytest.fixture
def fully_placed_plant_and_tariff():
    tariff = load_default_tariff()
    # Food lines preset at 0.30 utilization has all jobs successfully placed by B0 and B1
    plant = generate_plant("food_lines", utilization=0.30, seed=42)
    return plant, tariff


# =========================================================
# 1. Positive Tests (Good Schedules Pass All 9 Checks)
# =========================================================

def test_verifier_passes_b0_schedule(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    assert len(schedule.unplaced) == 0, "Prerequisite: all jobs must be placed for positive test"

    result = verify_schedule(
        plant=plant,
        tariff=tariff,
        schedule=schedule,
        reported_metrics=metrics.model_dump(),
    )
    assert result.passed is True
    for c in result.checks:
        assert c.passed is True, f"Check failed: {c.name} - {c.details}"


def test_verifier_passes_b1_schedule(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b1_schedule(plant, tariff)
    assert len(schedule.unplaced) == 0, "Prerequisite: all jobs must be placed for positive test"

    result = verify_schedule(
        plant=plant,
        tariff=tariff,
        schedule=schedule,
        reported_metrics=metrics.model_dump(),
    )
    assert result.passed is True
    for c in result.checks:
        assert c.passed is True, f"Check failed: {c.name} - {c.details}"


# =========================================================
# 2. Negative Tests (Each Broken Schedule Fails With Exact Check Name)
# =========================================================

def test_negative_horizon_coverage_gap(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_schedule = copy.deepcopy(schedule)

    # Shorten segment 0 on M1 to create a 1-slot gap
    m1_segs = broken_schedule.segments["M1"]
    m1_segs[0].end -= 1

    result = verify_schedule(plant, tariff, broken_schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "horizon_coverage")
    assert check.passed is False
    assert "gap" in check.details.lower() or "overlap" in check.details.lower()


def test_negative_missed_deadline(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_schedule = copy.deepcopy(schedule)

    # Shift a job's deadline so placement violates it
    p = broken_schedule.placements[0]
    job = next(j for j in plant.jobs if j.id == p.job_id)
    job.deadline_slot = p.end - 1  # forces end > deadline

    result = verify_schedule(plant, tariff, broken_schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "job_constraints")
    assert check.passed is False
    assert "deadline" in check.details.lower()


def test_negative_shortened_job(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_schedule = copy.deepcopy(schedule)

    # Shorten placement duration
    p = broken_schedule.placements[0]
    p.end = p.start + 1

    result = verify_schedule(plant, tariff, broken_schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "job_constraints")
    assert check.passed is False
    assert "duration" in check.details.lower()


def test_negative_overlapping_jobs(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_schedule = copy.deepcopy(schedule)

    # Force two placements on the same machine to overlap
    m1_places = [p for p in broken_schedule.placements if p.machine_id == "M1"]
    if len(m1_places) >= 2:
        m1_places[1].start = m1_places[0].start
    else:
        other = [p for p in broken_schedule.placements if p.machine_id != "M1"][0]
        other.machine_id = "M1"
        other.start = m1_places[0].start
        other.end = m1_places[0].end

    result = verify_schedule(plant, tariff, broken_schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "no_job_overlap")
    assert check.passed is False
    assert "overlapping" in check.details.lower()


def test_negative_run_in_downtime(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_plant = copy.deepcopy(plant)

    # Add downtime to machine M1 that overlaps a placed job
    m1_place = [p for p in schedule.placements if p.machine_id == "M1"][0]
    broken_m1 = next(m for m in broken_plant.machines if m.id == "M1")
    broken_m1.downtime = [(m1_place.start, m1_place.end)]

    result = verify_schedule(broken_plant, tariff, schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "no_run_in_downtime")
    assert check.passed is False
    assert "downtime" in check.details.lower()


def test_negative_missing_warmup_after_off():
    tariff = load_default_tariff()
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=10.0, warmup_slots=2)
    j = Job(id="J1", duration_slots=4, release_slot=10, deadline_slot=48)
    plant = Plant(name="TestWarmup", horizon_slots=48, machines=[m], jobs=[j])

    # Off [0, 10), then run [10, 14) -> MISSING warmup [8, 10)!
    segs = {
        "M1": [
            Segment(state="off", start=0, end=10, job_id=None),
            Segment(state="run", start=10, end=14, job_id="J1"),
            Segment(state="off", start=14, end=48, job_id=None),
        ]
    }
    placements = [Placement(job_id="J1", machine_id="M1", start=10, end=14)]
    schedule = Schedule(segments=segs, placements=placements)

    result = verify_schedule(plant, tariff, schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "warmup_and_min_off")
    assert check.passed is False


def test_negative_exceeded_switch_cycles():
    tariff = load_default_tariff()
    m = Machine(id="M1", run_kw=20.0, idle_kw=8.0, restart_kwh=2.0, max_switch_cycles=1)
    j1 = Job(id="J1", duration_slots=2, release_slot=0, deadline_slot=10)
    j2 = Job(id="J2", duration_slots=2, release_slot=10, deadline_slot=20)
    j3 = Job(id="J3", duration_slots=2, release_slot=20, deadline_slot=48)
    plant = Plant(name="TestCycles", horizon_slots=48, machines=[m], jobs=[j1, j2, j3])

    # 2 switch cycles > limit of 1
    segs = {
        "M1": [
            Segment(state="run", start=0, end=2, job_id="J1"),
            Segment(state="off", start=2, end=10, job_id=None),
            Segment(state="run", start=10, end=12, job_id="J2"),
            Segment(state="off", start=12, end=20, job_id=None),
            Segment(state="run", start=20, end=22, job_id="J3"),
            Segment(state="off", start=22, end=48, job_id=None),
        ]
    }
    placements = [
        Placement(job_id="J1", machine_id="M1", start=0, end=2),
        Placement(job_id="J2", machine_id="M1", start=10, end=12),
        Placement(job_id="J3", machine_id="M1", start=20, end=22),
    ]
    schedule = Schedule(segments=segs, placements=placements)

    result = verify_schedule(plant, tariff, schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "max_switch_cycles")
    assert check.passed is False
    assert "exceeding max_switch_cycles" in check.details.lower()


def test_negative_peak_cap_violation(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)

    strict_cap = metrics.peak_kw / 2.0
    result = verify_schedule(plant, tariff, schedule, peak_cap_kw=strict_cap)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "peak_cap")
    assert check.passed is False
    assert "exceeds cap" in check.details.lower()


def test_negative_unplaced_mandatory_job(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_schedule = copy.deepcopy(schedule)

    # Remove placement for job 0
    removed_job_id = broken_schedule.placements[0].job_id
    broken_schedule.placements = broken_schedule.placements[1:]
    # Replace run segment with idle segment
    m_id = None
    for mid, segs in broken_schedule.segments.items():
        for s in segs:
            if s.job_id == removed_job_id:
                s.state = "idle"
                s.job_id = None
                m_id = mid
                break

    result = verify_schedule(plant, tariff, broken_schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "mandatory_and_target")
    assert check.passed is False
    assert f"Mandatory job '{removed_job_id}' is not placed" in check.details


def test_negative_duplicate_job_placement(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)
    broken_schedule = copy.deepcopy(schedule)

    # Place the same job twice
    broken_schedule.placements.append(broken_schedule.placements[0])

    result = verify_schedule(plant, tariff, broken_schedule)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "mandatory_and_target")
    assert check.passed is False
    assert "duplicate" in check.details.lower()


def test_negative_wrong_reported_cost(fully_placed_plant_and_tariff):
    plant, tariff = fully_placed_plant_and_tariff
    schedule, metrics = build_b0_schedule(plant, tariff)

    bad_metrics = metrics.model_dump()
    bad_metrics["energy_cost"] = bad_metrics["energy_cost"] - 50.0

    result = verify_schedule(plant, tariff, schedule, reported_metrics=bad_metrics)
    assert result.passed is False
    check = next(c for c in result.checks if c.name == "metrics_match")
    assert check.passed is False
    assert "energy cost mismatch" in check.details.lower()
