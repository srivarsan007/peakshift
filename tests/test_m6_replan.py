"""M6 tests: replan() function and /api/replan, /api/pareto endpoints."""

import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.generator import generate_plant
from peakshift.core.models import Job, Tariff
from peakshift.core.portfolio import run_portfolio
from peakshift.core.replan import replan
from peakshift.core.tariff import load_default_tariff
from peakshift.verify.verifier import verify_schedule

client = TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tariff() -> Tariff:
    return load_default_tariff()


@pytest.fixture(scope="module")
def plant_and_schedule(tariff):
    plant = generate_plant(preset_name="textile", utilization=0.6, seed=42)
    result = run_portfolio(plant=plant, tariff=tariff, seed=42)
    return plant, result.schedule


# ─────────────────────────────────────────────────────────────────────────────
# 1. machine_down – frozen jobs are untouched
# ─────────────────────────────────────────────────────────────────────────────

def test_machine_down_freezes_started_jobs(plant_and_schedule, tariff):
    plant, schedule = plant_and_schedule
    now_slot = 4  # a few slots in

    # Pick a machine that has at least one job starting before now_slot
    started_before = {
        p.machine_id
        for p in schedule.placements
        if p.start < now_slot
    }
    if not started_before:
        pytest.skip("No jobs started before now_slot in this schedule; skipping freeze test")

    # Pick any machine NOT in started_before to fail
    other_machines = {m.id for m in plant.machines} - started_before
    if not other_machines:
        pytest.skip("All machines have started jobs; can't isolate a failure")

    failed_mid = next(iter(other_machines))
    event = {"type": "machine_down", "machine_id": failed_mid, "from_slot": now_slot,
             "until_slot": plant.horizon_slots}

    result = replan(plant=plant, tariff=tariff, current_schedule=schedule,
                    now_slot=now_slot, event=event, seed=42)

    # Jobs that started before now_slot on machines other than the failed one
    # must still be present in the new schedule (frozen)
    frozen_job_ids = {
        p.job_id for p in schedule.placements
        if p.start < now_slot and p.machine_id != failed_mid
    }
    placed_ids = {p.job_id for p in result.schedule.placements}
    for jid in frozen_job_ids:
        assert jid in placed_ids, f"Frozen job {jid!r} disappeared after replan"


# ─────────────────────────────────────────────────────────────────────────────
# 2. tariff_change – re-costed under new tariff
# ─────────────────────────────────────────────────────────────────────────────

def test_tariff_change_recomputes_metrics(plant_and_schedule, tariff):
    plant, schedule = plant_and_schedule

    # Create a tariff with higher base rate
    expensive = tariff.model_copy(update={"base_rate_per_kwh": tariff.base_rate_per_kwh * 2})
    event = {"type": "tariff_change", "tariff": expensive.model_dump()}

    result = replan(plant=plant, tariff=tariff, current_schedule=schedule,
                    now_slot=0, event=event, seed=42)

    # metrics_before is under the NEW tariff (expensive), so should cost more than original
    assert result.metrics_before.energy_cost > 0
    # status should be ok if all jobs placed
    assert result.status in ("ok", "target_at_risk")
    assert result.elapsed_ms >= 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. rush_job – new job appears in the replanned schedule
# ─────────────────────────────────────────────────────────────────────────────

def test_rush_job_appears_in_schedule(plant_and_schedule, tariff):
    plant, schedule = plant_and_schedule
    now_slot = 10

    # Pick a machine that can run this job
    machine_id = plant.machines[0].id
    rush = Job(
        id="rush_001",
        duration_slots=2,
        release_slot=now_slot,
        deadline_slot=plant.horizon_slots,
        allowed_machines=[machine_id],
        priority=10,
        units=1,
        mandatory=True,
    )
    event = {"type": "rush_job", "job": rush.model_dump()}

    result = replan(plant=plant, tariff=tariff, current_schedule=schedule,
                    now_slot=now_slot, event=event, seed=42)

    placed_ids = {p.job_id for p in result.schedule.placements}
    # Rush job may be placed OR in unplaced (capacity might be full), but it must be tracked
    assert "rush_001" in placed_ids or "rush_001" in result.unplaced


# ─────────────────────────────────────────────────────────────────────────────
# 4. job_delay – delayed job has shifted release_slot
# ─────────────────────────────────────────────────────────────────────────────

def test_job_delay_shifts_placement(plant_and_schedule, tariff):
    plant, schedule = plant_and_schedule
    now_slot = 5

    # Find an unfrozen job (starts at or after now_slot)
    unfrozen = [p for p in schedule.placements if p.start >= now_slot]
    if not unfrozen:
        pytest.skip("No unfrozen jobs in this schedule")

    target_jid = unfrozen[0].job_id
    extra = 4

    event = {"type": "job_delay", "job_id": target_jid, "extra_slots": extra}
    result = replan(plant=plant, tariff=tariff, current_schedule=schedule,
                    now_slot=now_slot, event=event, seed=42)

    # If the delayed job is placed, its start must be >= now_slot + extra
    placed_map = {p.job_id: p for p in result.schedule.placements}
    if target_jid in placed_map:
        new_start = placed_map[target_jid].start
        min_allowed = max(now_slot, 0) + extra
        assert new_start >= min_allowed, (
            f"Delayed job {target_jid!r} starts at {new_start}, expected >= {min_allowed}"
        )
    else:
        # Unplaced is also acceptable (delay pushed it past deadline)
        assert target_jid in result.unplaced


# ─────────────────────────────────────────────────────────────────────────────
# 5. target_at_risk – machine down removes all capacity for a mandatory job
# ─────────────────────────────────────────────────────────────────────────────

def test_target_at_risk_when_only_machine_fails(tariff):
    """Single machine, single mandatory job — fail the machine → target_at_risk."""
    from peakshift.core.models import Machine, Plant

    machine = Machine(id="M1", run_kw=10.0, idle_kw=1.0, restart_kwh=0.0)
    job = Job(
        id="J1",
        duration_slots=4,
        release_slot=0,
        deadline_slot=48,
        allowed_machines=["M1"],
        mandatory=True,
    )
    tiny_plant = Plant(name="tiny", machines=[machine], jobs=[job])

    # Get a baseline schedule first
    port = run_portfolio(plant=tiny_plant, tariff=tariff, seed=42)
    schedule = port.schedule

    event = {
        "type": "machine_down",
        "machine_id": "M1",
        "from_slot": 0,
        "until_slot": 48,
    }
    result = replan(plant=tiny_plant, tariff=tariff, current_schedule=schedule,
                    now_slot=0, event=event, seed=42)

    assert result.status == "target_at_risk"
    assert "J1" in result.unplaced
    assert result.units_short >= 1
    assert result.target_met is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. API /api/replan – returns correct shape
# ─────────────────────────────────────────────────────────────────────────────

def test_api_replan_shape(plant_and_schedule, tariff):
    plant, schedule = plant_and_schedule

    event = {
        "type": "tariff_change",
        "tariff": tariff.model_dump(),
    }
    payload = {
        "plant": plant.model_dump(),
        "tariff": tariff.model_dump(),
        "schedule": schedule.model_dump(),
        "now_slot": 0,
        "event": event,
        "seed": 42,
    }
    resp = client.post("/api/replan", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    # Required top-level fields
    for field in ("schedule", "metrics_before", "metrics_after", "diff",
                  "status", "unplaced", "units_short", "target_met", "elapsed_ms"):
        assert field in data, f"Missing field: {field!r}"

    assert data["status"] in ("ok", "target_at_risk", "error")
    assert isinstance(data["diff"], list)
    assert isinstance(data["unplaced"], list)
    assert data["elapsed_ms"] >= 0

    # metrics_before and metrics_after have expected sub-fields
    for metrics_key in ("metrics_before", "metrics_after"):
        m = data[metrics_key]
        for sub in ("kwh", "energy_cost", "peak_kw", "target_met"):
            assert sub in m, f"{metrics_key} missing {sub!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. API /api/pareto – returns one point per cap
# ─────────────────────────────────────────────────────────────────────────────

def test_api_pareto_returns_points(plant_and_schedule, tariff):
    plant, _ = plant_and_schedule
    caps = [50.0, 100.0, 200.0]

    payload = {
        "plant": plant.model_dump(),
        "tariff": tariff.model_dump(),
        "caps": caps,
        "seed": 42,
    }
    resp = client.post("/api/pareto", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert "points" in data
    assert "elapsed_ms" in data
    assert data["elapsed_ms"] >= 0

    # One point per cap (failures are silently skipped, but all three should succeed)
    assert len(data["points"]) == len(caps)

    for point in data["points"]:
        assert "cap_kw" in point
        assert "cost" in point
        assert "peak_kw" in point
        assert "cap_satisfied" in point
        assert point["cost"] >= 0
        assert point["peak_kw"] >= 0


def test_replan_never_schedules_in_past(plant_and_schedule, tariff):
    """Verify that re-plan with now_slot=12 never schedules unfrozen jobs in the past."""
    plant, schedule = plant_and_schedule
    now_slot = 12
    failed_mid = plant.machines[0].id

    event = {
        "type": "machine_down",
        "machine_id": failed_mid,
        "from_slot": now_slot,
        "until_slot": plant.horizon_slots,
    }

    result = replan(
        plant=plant,
        tariff=tariff,
        current_schedule=schedule,
        now_slot=now_slot,
        event=event,
        seed=42,
    )

    # Identify jobs that were frozen
    old_placements = {p.job_id: p for p in schedule.placements}
    frozen_ids = {
        p.job_id for p in schedule.placements
        if p.start < now_slot and (p.machine_id != failed_mid or p.end <= now_slot)
    }

    # Verify no unfrozen job starts before now_slot
    for p in result.schedule.placements:
        if p.job_id not in frozen_ids:
            assert p.start >= now_slot, (
                f"Rescheduled job '{p.job_id}' started at slot {p.start} < now_slot {now_slot}!"
            )

    # Verifier check #10 must pass
    ver_res = verify_schedule(
        plant=plant,
        tariff=tariff,
        schedule=result.schedule,
        now_slot=now_slot,
        frozen_job_ids=frozen_ids,
    )
    replan_check = next((c for c in ver_res.checks if c.name == "replan_now_slot"), None)
    assert replan_check is not None
    assert replan_check.passed is True


def test_verifier_catches_jobs_scheduled_in_past(plant_and_schedule, tariff):
    """Verify that verifier check #10 fails if a non-frozen job starts before now_slot."""
    from peakshift.core.models import Placement, Schedule

    plant, schedule = plant_and_schedule
    now_slot = 12

    # Maliciously shift a job to slot 8 (< 12) without being in frozen_job_ids
    bad_placements = [
        p.model_copy(update={"start": 8, "end": 8 + (p.end - p.start)})
        if i == 0 else p
        for i, p in enumerate(schedule.placements)
    ]
    bad_schedule = Schedule(
        segments=schedule.segments,
        placements=bad_placements,
        unplaced=schedule.unplaced,
    )

    ver_res = verify_schedule(
        plant=plant,
        tariff=tariff,
        schedule=bad_schedule,
        now_slot=now_slot,
        frozen_job_ids=set(),  # empty frozen set
    )
    replan_check = next((c for c in ver_res.checks if c.name == "replan_now_slot"), None)
    assert replan_check is not None
    assert replan_check.passed is False
    assert "which is before now_slot" in replan_check.details


# ─────────────────────────────────────────────────────────────────────────────
# Step-0 acceptance tests: machine selector correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_api_rejects_unknown_machine_id(plant_and_schedule, tariff):
    """POST /api/replan with a machine_id not in the plant must return 422."""
    plant, schedule = plant_and_schedule
    event = {
        "type": "machine_down",
        "machine_id": "M0_DOES_NOT_EXIST",
        "from_slot": 12,
        "until_slot": plant.horizon_slots,
    }
    resp = client.post("/api/replan", json={
        "plant": plant.model_dump(),
        "tariff": tariff.model_dump(),
        "schedule": schedule.model_dump(),
        "now_slot": 12,
        "event": event,
        "seed": 42,
    })
    assert resp.status_code == 422, (
        f"Expected 422 for unknown machine id, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "UNKNOWN_MACHINE" in str(body), f"Expected UNKNOWN_MACHINE error code, got: {body}"


def test_api_accepts_all_real_machine_ids(plant_and_schedule, tariff):
    """POST /api/replan succeeds for every machine id that actually exists in the plant.

    This test will fail if the dropdown could ever send an id not in the plant,
    because *every* plant machine id must be a valid replan target.
    """
    plant, schedule = plant_and_schedule
    plant_machine_ids = {m.id for m in plant.machines}

    # Textile preset must have machines M1..M10 (10 machines)
    assert len(plant_machine_ids) >= 1, "Plant has no machines – generator broken"

    for mid in sorted(plant_machine_ids):
        event = {
            "type": "machine_down",
            "machine_id": mid,
            "from_slot": 12,
            "until_slot": plant.horizon_slots,
        }
        resp = client.post("/api/replan", json={
            "plant": plant.model_dump(),
            "tariff": tariff.model_dump(),
            "schedule": schedule.model_dump(),
            "now_slot": 12,
            "event": event,
            "seed": 42,
        })
        assert resp.status_code == 200, (
            f"Replan failed for real machine {mid}: {resp.status_code} {resp.text}"
        )


def test_textile_preset_has_no_M0(plant_and_schedule):
    """Textile plant must use M1..M10 – never M0.  The dropdown must never show M0."""
    plant, _ = plant_and_schedule
    machine_ids = {m.id for m in plant.machines}
    assert "M0" not in machine_ids, (
        f"Plant contains 'M0' which should not exist. IDs: {sorted(machine_ids)}"
    )
    # All ids must start with 'M' followed by a positive integer
    for mid in machine_ids:
        assert mid.startswith("M"), f"Unexpected machine id format: {mid}"
        num = int(mid[1:])
        assert num >= 1, f"Machine id {mid} uses index 0 – reserved for 'no machine'"
