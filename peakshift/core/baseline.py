"""Baseline algorithms B0 and B1."""

from typing import Dict, List, Optional, Tuple
from peakshift.core.cost import ScheduleMetrics, compute_schedule_metrics
from peakshift.core.machine_cost import MachineScheduleResult, evaluate_machine_schedule
from peakshift.core.models import Job, Machine, Placement, Plant, Schedule, Segment, Tariff
from peakshift.core.tariff import get_rate_profile


def find_earliest_start(
    machine: Machine,
    busy_intervals: List[Tuple[int, int]],
    duration: int,
    release: int,
    deadline: int,
    horizon: int,
) -> Optional[int]:
    """Finds the earliest slot s >= release where [s, s + duration) is free and <= deadline."""
    min_start = release
    # If machine starts off and has warmup_slots, no job can start before warmup_slots
    if machine.initial_state == "off":
        min_start = max(min_start, machine.warmup_slots)

    # Check downtime intervals
    downtime = machine.downtime

    # Try every candidate start slot
    for s in range(min_start, deadline - duration + 1):
        if s + duration > horizon:
            break

        # Check collision with existing jobs on this machine
        collision = False
        for b_start, b_end in busy_intervals:
            if not (s + duration <= b_start or s >= b_end):
                collision = True
                break
        if collision:
            continue

        # Check collision with downtime
        for dt_start, dt_end in downtime:
            if not (s + duration <= dt_start or s >= dt_end):
                collision = True
                break
        if collision:
            continue

        # Valid slot found!
        return s

    return None


def compute_baseline_placements(plant: Plant) -> Tuple[List[Placement], List[str]]:
    """Generates placements for B0 and B1 using earliest-feasible greedy dispatch.

    Jobs are sorted by Earliest-Deadline-First (EDF) to guarantee that
    tight-deadline jobs claim machine slots before loose-deadline jobs can
    block them.  Any jobs still unplaced after the EDF pass are retried in
    slack-ascending order as a fallback, ensuring every feasible job is placed.
    """
    machine_busy: Dict[str, List[Tuple[int, int]]] = {m.id: [] for m in plant.machines}
    machine_map = {m.id: m for m in plant.machines}
    placements: List[Placement] = []

    def _try_place(jobs_to_try: List) -> List[str]:
        """Greedily places jobs in given order; returns list of unplaced job IDs."""
        remaining: List[str] = []
        for job in jobs_to_try:
            allowed = job.allowed_machines if job.allowed_machines else [m.id for m in plant.machines]
            best_machine_id = None
            best_start = None

            for m_id in allowed:
                if m_id not in machine_map:
                    continue
                m = machine_map[m_id]
                s = find_earliest_start(
                    machine=m,
                    busy_intervals=machine_busy[m_id],
                    duration=job.duration_slots,
                    release=job.release_slot,
                    deadline=job.deadline_slot,
                    horizon=plant.horizon_slots,
                )
                if s is not None:
                    if best_start is None or s < best_start:
                        best_start = s
                        best_machine_id = m_id

            if best_machine_id is not None and best_start is not None:
                end = best_start + job.duration_slots
                placements.append(
                    Placement(
                        job_id=job.id,
                        machine_id=best_machine_id,
                        start=best_start,
                        end=end,
                    )
                )
                machine_busy[best_machine_id].append((best_start, end))
                machine_busy[best_machine_id].sort(key=lambda x: x[0])
            else:
                remaining.append(job.id)
        return remaining

    # Pass 1: Earliest-Deadline-First ordering
    horizon = plant.horizon_slots
    edf_order = sorted(
        plant.jobs,
        key=lambda j: (
            j.deadline_slot if j.deadline_slot is not None else horizon,
            j.release_slot,
            -j.priority,
            j.id,
        ),
    )
    after_pass1 = _try_place(edf_order)

    # Pass 2 (fallback): retry remaining jobs in slack-ascending order
    unplaced: List[str] = []
    if after_pass1:
        job_map = {j.id: j for j in plant.jobs}
        slack_order = sorted(
            [job_map[jid] for jid in after_pass1 if jid in job_map],
            key=lambda j: (
                (j.deadline_slot if j.deadline_slot is not None else horizon)
                - j.release_slot
                - j.duration_slots,
                j.deadline_slot if j.deadline_slot is not None else horizon,
                j.release_slot,
                -j.priority,
                j.id,
            ),
        )
        unplaced = _try_place(slack_order)

    return placements, unplaced


def build_b0_schedule(
    plant: Plant,
    tariff: Tariff,
    placements: Optional[List[Placement]] = None,
    unplaced: Optional[List[str]] = None,
) -> Tuple[Schedule, ScheduleMetrics]:
    """Builds B0 schedule: machines stay on all day (no switch-off)."""
    if placements is None or unplaced is None:
        placements, unplaced = compute_baseline_placements(plant)

    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)
    horizon = plant.horizon_slots
    h = plant.slot_minutes / 60.0

    machine_placements: Dict[str, List[Placement]] = {m.id: [] for m in plant.machines}
    for p in placements:
        machine_placements[p.machine_id].append(p)

    segments_by_machine: Dict[str, List[Segment]] = {}
    machine_results: Dict[str, MachineScheduleResult] = {}

    for m in plant.machines:
        m_places = sorted(machine_placements[m.id], key=lambda x: x.start)
        w = m.warmup_slots

        # If machine has no jobs: off all day
        if not m_places:
            segments = [Segment(state="off", start=0, end=horizon, job_id=None)]
            segments_by_machine[m.id] = segments
            machine_results[m.id] = MachineScheduleResult(
                segments=segments,
                kwh=0.0,
                cost=0.0,
                idle_kwh=0.0,
                switch_offs=0,
                slot_kw=[0.0] * horizon,
            )
            continue

        # Machine runs jobs and stays on all day (no switch-off in gaps)
        segments: List[Segment] = []
        slot_kw = [0.0] * horizon
        total_kwh = 0.0
        total_cost = 0.0
        idle_kwh = 0.0

        first_start = m_places[0].start
        # Handle leading slots
        if first_start > 0:
            if m.initial_state == "off":
                if first_start - w > 0:
                    segments.append(Segment(state="off", start=0, end=first_start - w, job_id=None))
                if w > 0:
                    segments.append(Segment(state="warmup", start=first_start - w, end=first_start, job_id=None))
                    warmup_kw = m.restart_kwh / (w * h)
                    for t in range(first_start - w, first_start):
                        slot_kw[t] = warmup_kw
                restart_slot = first_start - w if w > 0 else first_start
                rate_t = rate_profile[restart_slot] if restart_slot < horizon else rate_profile[-1]
                total_kwh += m.restart_kwh
                total_cost += m.restart_kwh * rate_t
            else:
                segments.append(Segment(state="idle", start=0, end=first_start, job_id=None))
                for t in range(first_start):
                    slot_kw[t] = m.idle_kw
                    e = m.idle_kw * h
                    total_kwh += e
                    idle_kwh += e
                    total_cost += e * rate_profile[t]

        # Jobs and gaps
        for idx, p in enumerate(m_places):
            segments.append(Segment(state="run", start=p.start, end=p.end, job_id=p.job_id))
            for t in range(p.start, p.end):
                slot_kw[t] = m.run_kw
                e = m.run_kw * h
                total_kwh += e
                total_cost += e * rate_profile[t]

            # Gap to next job
            if idx < len(m_places) - 1:
                next_start = m_places[idx + 1].start
                if next_start > p.end:
                    # B0 stays idle!
                    segments.append(Segment(state="idle", start=p.end, end=next_start, job_id=None))
                    for t in range(p.end, next_start):
                        slot_kw[t] = m.idle_kw
                        e = m.idle_kw * h
                        total_kwh += e
                        idle_kwh += e
                        total_cost += e * rate_profile[t]

        # Trailing slots
        last_end = m_places[-1].end
        if last_end < horizon:
            # In B0, machine stays on all day (idle)
            segments.append(Segment(state="idle", start=last_end, end=horizon, job_id=None))
            for t in range(last_end, horizon):
                slot_kw[t] = m.idle_kw
                e = m.idle_kw * h
                total_kwh += e
                idle_kwh += e
                total_cost += e * rate_profile[t]

        segments_by_machine[m.id] = segments
        machine_results[m.id] = MachineScheduleResult(
            segments=segments,
            kwh=total_kwh,
            cost=total_cost,
            idle_kwh=idle_kwh,
            switch_offs=0,
            slot_kw=slot_kw,
        )

    schedule = Schedule(
        segments=segments_by_machine,
        placements=placements,
        unplaced=unplaced,
    )
    metrics = compute_schedule_metrics(
        plant=plant,
        tariff=tariff,
        schedule=schedule,
        machine_results=machine_results,
    )
    return schedule, metrics


def build_b1_schedule(
    plant: Plant,
    tariff: Tariff,
    placements: Optional[List[Placement]] = None,
    unplaced: Optional[List[str]] = None,
) -> Tuple[Schedule, ScheduleMetrics]:
    """Builds B1 schedule: identical placements to B0, but applies tariff-blind break-even on/off rule."""
    if placements is None or unplaced is None:
        placements, unplaced = compute_baseline_placements(plant)

    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)

    machine_placements: Dict[str, List[Tuple[int, int, Optional[str]]]] = {
        m.id: [] for m in plant.machines
    }
    for p in placements:
        machine_placements[p.machine_id].append((p.start, p.end, p.job_id))

    segments_by_machine: Dict[str, List[Segment]] = {}
    machine_results: Dict[str, MachineScheduleResult] = {}

    for m in plant.machines:
        busy = sorted(machine_placements[m.id], key=lambda x: x[0])
        res = evaluate_machine_schedule(
            machine=m,
            busy_intervals=busy,
            rate_profile=rate_profile,
            slot_minutes=plant.slot_minutes,
            tariff_blind=True,
            horizon_slots=plant.horizon_slots,
        )
        segments_by_machine[m.id] = res.segments
        machine_results[m.id] = res

    schedule = Schedule(
        segments=segments_by_machine,
        placements=placements,
        unplaced=unplaced,
    )
    metrics = compute_schedule_metrics(
        plant=plant,
        tariff=tariff,
        schedule=schedule,
        machine_results=machine_results,
    )
    return schedule, metrics
