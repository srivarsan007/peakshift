"""Re-planning algorithm for disruption events."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from peakshift.core.cost import ScheduleMetrics, compute_schedule_metrics
from peakshift.core.machine_cost import evaluate_machine_schedule
from peakshift.core.models import Job, Machine, Placement, Plant, Schedule, Tariff
from peakshift.core.portfolio import run_portfolio
from peakshift.core.tariff import get_rate_profile

logger = logging.getLogger(__name__)

MOVE_PENALTY_INR = 25.0  # Per moved job; consistent with SPEC §5.8


class JobDiff(BaseModel):
    job_id: str
    old: Optional[Dict[str, Any]] = None   # {machine_id, start, end}
    new: Optional[Dict[str, Any]] = None   # {machine_id, start, end} or None if unplaced
    reason: str


class ReplanResult(BaseModel):
    schedule: Schedule
    metrics_before: ScheduleMetrics
    metrics_after: ScheduleMetrics
    diff: List[JobDiff]
    status: str                   # "ok" | "target_at_risk"
    unplaced: List[str]
    units_short: int
    target_met: bool
    elapsed_ms: float = 0.0


def _placement_to_dict(p: Placement) -> Dict[str, Any]:
    return {"machine_id": p.machine_id, "start": p.start, "end": p.end}


def replan(
    plant: Plant,
    tariff: Tariff,
    current_schedule: Schedule,
    now_slot: int,
    event: Dict[str, Any],
    move_penalty_inr: float = MOVE_PENALTY_INR,
    seed: int = 42,
    max_iterations: Optional[int] = None,
) -> ReplanResult:
    """Re-plans after a disruption event.

    Events (dict must have "type"):
      machine_down  {machine_id, from_slot, until_slot}
      tariff_change {tariff}         (new Tariff object)
      rush_job      {job}            (new Job object)
      job_delay     {job_id, extra_slots}

    Returns ReplanResult with before/after metrics, diff list, and target status.
    Never raises an unhandled exception.
    """
    import time
    t0 = time.perf_counter()

    event_type = event.get("type", "")

    # ── Re-cost the current schedule under the (possibly new) tariff ──────────
    active_tariff = tariff
    if event_type == "tariff_change":
        try:
            if isinstance(event["tariff"], dict):
                active_tariff = Tariff.model_validate(event["tariff"])
            else:
                active_tariff = event["tariff"]
        except Exception as e:
            logger.warning(f"tariff_change: invalid tariff payload: {e}")

    metrics_before = compute_schedule_metrics(plant, active_tariff, current_schedule)

    # ── Snapshot of current placements (before re-plan) ───────────────────────
    old_placements: Dict[str, Placement] = {p.job_id: p for p in current_schedule.placements}

    # ── Build the modified plant for re-planning ──────────────────────────────
    try:
        modified_plant, modified_jobs_to_place, frozen_placements = _apply_event(
            plant=plant,
            current_schedule=current_schedule,
            now_slot=now_slot,
            event=event,
            event_type=event_type,
        )
    except Exception as e:
        logger.error(f"replan: _apply_event failed: {e}")
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        return ReplanResult(
            schedule=current_schedule,
            metrics_before=metrics_before,
            metrics_after=metrics_before,
            diff=[],
            status="error",
            unplaced=[],
            units_short=0,
            target_met=metrics_before.target_met,
            elapsed_ms=elapsed,
        )

    # ── Run portfolio to place unfrozen jobs ──────────────────────────────────
    try:
        port_result = run_portfolio(
            plant=modified_plant,
            tariff=active_tariff,
            seed=seed,
            max_iterations=max_iterations,
        )
        new_schedule_free = port_result.schedule
    except Exception as e:
        logger.error(f"replan: portfolio failed: {e}")
        new_schedule_free = current_schedule

    # ── Merge frozen placements back into the new schedule ────────────────────
    merged_placements = list(frozen_placements.values()) + list(new_schedule_free.placements)
    unplaced_ids = list(new_schedule_free.unplaced)

    # ── Build merged Schedule segments ────────────────────────────────────────
    rate_profile = get_rate_profile(active_tariff, plant.horizon_slots, plant.slot_minutes)

    machine_map = {m.id: m for m in plant.machines}
    machine_busy: Dict[str, List[Tuple[int, int, Optional[str]]]] = {
        m.id: [] for m in plant.machines
    }
    for p in merged_placements:
        if p.machine_id in machine_busy:
            machine_busy[p.machine_id].append((p.start, p.end, p.job_id))
    for mid in machine_busy:
        machine_busy[mid].sort(key=lambda x: x[0])

    segments_by_machine = {}
    machine_results = {}
    for m in plant.machines:
        busy = machine_busy[m.id]
        res = evaluate_machine_schedule(
            machine=m,
            busy_intervals=busy,
            rate_profile=rate_profile,
            slot_minutes=plant.slot_minutes,
            tariff_blind=False,
            horizon_slots=plant.horizon_slots,
        )
        segments_by_machine[m.id] = res.segments
        machine_results[m.id] = res

    final_schedule = Schedule(
        segments=segments_by_machine,
        placements=merged_placements,
        unplaced=unplaced_ids,
    )
    metrics_after = compute_schedule_metrics(
        plant=plant,
        tariff=active_tariff,
        schedule=final_schedule,
        machine_results=machine_results,
    )

    # ── Build diff list ───────────────────────────────────────────────────────
    new_placements: Dict[str, Placement] = {p.job_id: p for p in merged_placements}
    diff: List[JobDiff] = []

    all_job_ids = set(old_placements.keys()) | set(new_placements.keys()) | set(unplaced_ids)
    for jid in all_job_ids:
        old_p = old_placements.get(jid)
        new_p = new_placements.get(jid)

        old_d = _placement_to_dict(old_p) if old_p else None
        new_d = _placement_to_dict(new_p) if new_p else None

        if old_d == new_d:
            continue  # Unchanged

        failed_mid = event.get("machine_id") if event_type == "machine_down" else None

        if new_p is None and jid in unplaced_ids:
            reason = (
                "REROUTED_MACHINE_DOWN"
                if (event_type == "machine_down" and old_p and old_p.machine_id == failed_mid)
                else "MOVED_BY_REPLAN"
            )
            diff.append(JobDiff(job_id=jid, old=old_d, new=None, reason=reason))
        elif old_p is None and new_p is not None:
            diff.append(JobDiff(job_id=jid, old=None, new=new_d, reason="MOVED_BY_REPLAN"))
        elif old_p and new_p:
            if old_p.machine_id != new_p.machine_id:
                reason = (
                    "REROUTED_MACHINE_DOWN"
                    if (event_type == "machine_down" and old_p.machine_id == failed_mid)
                    else "MOVED_BY_REPLAN"
                )
            else:
                reason = "MOVED_BY_REPLAN"
            diff.append(JobDiff(job_id=jid, old=old_d, new=new_d, reason=reason))

    # ── Target status ─────────────────────────────────────────────────────────
    job_map_all = {j.id: j for j in plant.jobs}
    if event_type == "rush_job" and "job" in event:
        rj = event["job"]
        if isinstance(rj, dict):
            rj = Job.model_validate(rj)
        job_map_all[rj.id] = rj

    mandatory_jobs = [j for j in job_map_all.values() if j.mandatory]
    placed_set = set(p.job_id for p in merged_placements)
    unplaced_mandatory = [j.id for j in mandatory_jobs if j.id not in placed_set]
    units_short = sum(job_map_all[jid].units for jid in unplaced_mandatory if jid in job_map_all)

    target_met = len(unplaced_mandatory) == 0
    status = "ok" if target_met else "target_at_risk"

    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    return ReplanResult(
        schedule=final_schedule,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        diff=diff,
        status=status,
        unplaced=unplaced_ids,
        units_short=units_short,
        target_met=target_met,
        elapsed_ms=elapsed,
    )


def _apply_event(
    plant: Plant,
    current_schedule: Schedule,
    now_slot: int,
    event: Dict[str, Any],
    event_type: str,
) -> Tuple["Plant", List[Job], Dict[str, Placement]]:
    """Returns (modified_plant_for_portfolio, jobs_to_freely_place, frozen_placement_map)."""
    import copy

    frozen: Dict[str, Placement] = {}
    job_map = {j.id: j for j in plant.jobs}
    all_jobs = list(plant.jobs)
    all_machines = list(plant.machines)

    if event_type == "machine_down":
        failed_mid = event["machine_id"]
        from_slot = event.get("from_slot", now_slot)
        until_slot = event.get("until_slot", plant.horizon_slots)

        # Freeze jobs that started before now_slot and are NOT on the failed machine
        for p in current_schedule.placements:
            job = job_map.get(p.job_id)
            if job is None:
                continue
            on_failed = p.machine_id == failed_mid
            started_before_now = p.start < now_slot

            if started_before_now and (not on_failed or p.end <= from_slot):
                frozen[p.job_id] = p  # finished before breakdown or running on good machine

        # Jobs running on the failed machine at now_slot must be rescheduled
        # Jobs that hadn't started on good machines: also reschedule freely
        unfrozen_job_ids = set(job_map.keys()) - set(frozen.keys())

        # Add downtime to the failed machine
        modified_machines = []
        for m in all_machines:
            if m.id == failed_mid:
                mc = copy.deepcopy(m)
                mc.downtime = list(m.downtime) + [(from_slot, until_slot)]
                modified_machines.append(mc)
            else:
                modified_machines.append(m)

        # Clamp unfrozen jobs so they cannot start before now_slot
        unfrozen_jobs = [
            j.model_copy(update={"release_slot": max(j.release_slot, now_slot)})
            if j.release_slot < now_slot
            else j
            for j in all_jobs
            if j.id in unfrozen_job_ids
        ]
        modified_plant = Plant(
            name=plant.name,
            horizon_slots=plant.horizon_slots,
            slot_minutes=plant.slot_minutes,
            contract_kva=plant.contract_kva,
            power_factor=plant.power_factor,
            machines=modified_machines,
            jobs=unfrozen_jobs if unfrozen_jobs else [all_jobs[0]],  # prevent empty jobs
        )
        return modified_plant, unfrozen_jobs, frozen

    elif event_type == "tariff_change":
        # No layout change. Recompute everything from scratch for fairness.
        # Clamp release slots so re-inserted jobs cannot start before now_slot.
        clamped_jobs = [
            j.model_copy(update={"release_slot": max(j.release_slot, now_slot)})
            if j.release_slot < now_slot
            else j
            for j in all_jobs
        ]
        clamped_plant = Plant(
            name=plant.name,
            horizon_slots=plant.horizon_slots,
            slot_minutes=plant.slot_minutes,
            contract_kva=plant.contract_kva,
            power_factor=plant.power_factor,
            machines=all_machines,
            jobs=clamped_jobs,
        )
        return clamped_plant, clamped_jobs, {}

    elif event_type == "rush_job":
        rj_raw = event.get("job", {})
        if isinstance(rj_raw, dict):
            rush_job = Job.model_validate(rj_raw)
        else:
            rush_job = rj_raw

        # Freeze jobs that started before now_slot
        for p in current_schedule.placements:
            if p.start < now_slot:
                frozen[p.job_id] = p

        unfrozen_job_ids = set(job_map.keys()) - set(frozen.keys())
        unfrozen_jobs = [
            j.model_copy(update={"release_slot": max(j.release_slot, now_slot)})
            if j.release_slot < now_slot
            else j
            for j in all_jobs
            if j.id in unfrozen_job_ids
        ]
        # Add the rush job
        if rush_job.id not in {j.id for j in unfrozen_jobs}:
            unfrozen_jobs.append(rush_job)

        new_all_jobs = unfrozen_jobs if unfrozen_jobs else [rush_job]
        modified_plant = Plant(
            name=plant.name,
            horizon_slots=plant.horizon_slots,
            slot_minutes=plant.slot_minutes,
            contract_kva=plant.contract_kva,
            power_factor=plant.power_factor,
            machines=all_machines,
            jobs=new_all_jobs,
        )
        return modified_plant, unfrozen_jobs, frozen

    elif event_type == "job_delay":
        jid = event["job_id"]
        extra = int(event.get("extra_slots", 0))

        # Freeze jobs that started before now_slot
        for p in current_schedule.placements:
            if p.start < now_slot:
                frozen[p.job_id] = p

        unfrozen_job_ids = set(job_map.keys()) - set(frozen.keys())

        # Extend the delayed job's release_slot
        modified_jobs = []
        for j in all_jobs:
            if j.id == jid and j.id in unfrozen_job_ids:
                import copy as cp
                jc = cp.deepcopy(j)
                jc.release_slot = max(jc.release_slot, now_slot) + extra
                # Ensure release < deadline
                if jc.deadline_slot is not None and jc.release_slot >= jc.deadline_slot:
                    jc.deadline_slot = jc.release_slot + j.duration_slots
                    if jc.deadline_slot > plant.horizon_slots:
                        jc.deadline_slot = plant.horizon_slots
                modified_jobs.append(jc)
            elif j.id in unfrozen_job_ids:
                modified_jobs.append(j)

        new_all_jobs = modified_jobs if modified_jobs else all_jobs[:1]
        modified_plant = Plant(
            name=plant.name,
            horizon_slots=plant.horizon_slots,
            slot_minutes=plant.slot_minutes,
            contract_kva=plant.contract_kva,
            power_factor=plant.power_factor,
            machines=all_machines,
            jobs=new_all_jobs,
        )
        return modified_plant, modified_jobs, frozen

    else:
        raise ValueError(f"Unknown event type: '{event_type}'")
