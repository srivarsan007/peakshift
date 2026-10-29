"""Constructive scheduling heuristics: H1, H2, and H3."""

from typing import Dict, List, Optional, Tuple
from peakshift.core.cost import ScheduleMetrics, compute_schedule_metrics
from peakshift.core.machine_cost import MachineScheduleResult, evaluate_machine_schedule
from peakshift.core.models import Job, Machine, Placement, Plant, Schedule, Segment, Tariff
from peakshift.core.tariff import get_rate_profile


def _is_slot_free(
    machine: Machine,
    busy_intervals: List[Tuple[int, int, Optional[str]]],
    start: int,
    duration: int,
    release: int,
    deadline: int,
    horizon: int,
) -> bool:
    """Checks timing, job collisions, and machine downtime."""
    if start < release:
        return False
    if start + duration > deadline:
        return False
    if start + duration > horizon:
        return False

    if machine.initial_state == "off" and start < machine.warmup_slots:
        return False

    end = start + duration
    # Collision with existing busy intervals
    for b_start, b_end, _ in busy_intervals:
        if not (end <= b_start or start >= b_end):
            return False

    # Collision with downtime
    for dt_start, dt_end in machine.downtime:
        if not (end <= dt_start or start >= dt_end):
            return False

    return True


def construct_h1(
    plant: Plant,
    tariff: Tariff,
    peak_cap_kw: Optional[float] = None,
) -> Tuple[Schedule, ScheduleMetrics, Dict[str, MachineScheduleResult]]:
    """H1: First-come order (B0 order) with exact tariff-aware gap decisions."""
    from peakshift.core.baseline import compute_baseline_placements

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
            tariff_blind=False,
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
    return schedule, metrics, machine_results


def construct_h2(
    plant: Plant,
    tariff: Tariff,
    peak_cap_kw: Optional[float] = None,
) -> Tuple[Schedule, ScheduleMetrics, Dict[str, MachineScheduleResult]]:
    """H2: Cheapest insertion heuristic.
    
    Sort jobs by priority (descending), duration (longest first), and id.
    For each job, evaluate every allowed machine and feasible start, choosing
    the start with minimum marginal machine cost.
    """
    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)
    horizon = plant.horizon_slots
    slot_min = plant.slot_minutes
    machine_map = {m.id: m for m in plant.machines}

    # Sort jobs by priority descending, duration descending, id ascending
    sorted_jobs = sorted(
        plant.jobs,
        key=lambda j: (-j.priority, -j.duration_slots, j.id),
    )

    machine_busy: Dict[str, List[Tuple[int, int, Optional[str]]]] = {
        m.id: [] for m in plant.machines
    }
    machine_results: Dict[str, MachineScheduleResult] = {
        m.id: evaluate_machine_schedule(m, [], rate_profile, slot_min, horizon_slots=horizon)
        for m in plant.machines
    }

    # Track total kw across all machines for incremental peak cap checks
    total_slot_kw = [0.0] * horizon
    for m in plant.machines:
        for t in range(horizon):
            total_slot_kw[t] += machine_results[m.id].slot_kw[t]

    placements: List[Placement] = []
    unplaced: List[str] = []

    for job in sorted_jobs:
        allowed = job.allowed_machines if job.allowed_machines else [m.id for m in plant.machines]
        deadline = job.deadline_slot if job.deadline_slot is not None else horizon

        best_m_id = None
        best_start = None
        best_marginal_cost = None
        best_res = None

        for m_id in allowed:
            m = machine_map[m_id]
            current_busy = machine_busy[m_id]
            base_cost = machine_results[m_id].cost

            min_start = job.release_slot
            if m.initial_state == "off":
                min_start = max(min_start, m.warmup_slots)

            for s in range(min_start, deadline - job.duration_slots + 1):
                if s + job.duration_slots > horizon:
                    break

                if not _is_slot_free(m, current_busy, s, job.duration_slots, job.release_slot, deadline, horizon):
                    continue

                # Candidate busy list
                candidate_busy = current_busy + [(s, s + job.duration_slots, job.id)]
                candidate_busy.sort(key=lambda x: x[0])

                cand_res = evaluate_machine_schedule(
                    machine=m,
                    busy_intervals=candidate_busy,
                    rate_profile=rate_profile,
                    slot_minutes=slot_min,
                    tariff_blind=False,
                    horizon_slots=horizon,
                )

                # Check peak cap if set
                if peak_cap_kw is not None:
                    violates_cap = False
                    for t in range(horizon):
                        new_kw = total_slot_kw[t] - machine_results[m_id].slot_kw[t] + cand_res.slot_kw[t]
                        if new_kw > peak_cap_kw + 1e-4:
                            violates_cap = True
                            break
                    if violates_cap:
                        continue

                marginal_cost = cand_res.cost - base_cost

                # Tie-breaking: minimum marginal cost, then earliest start, then lowest machine id
                if (
                    best_marginal_cost is None
                    or marginal_cost < best_marginal_cost - 1e-6
                    or (abs(marginal_cost - best_marginal_cost) <= 1e-6 and s < best_start)
                    or (abs(marginal_cost - best_marginal_cost) <= 1e-6 and s == best_start and m_id < best_m_id)
                ):
                    best_marginal_cost = marginal_cost
                    best_start = s
                    best_m_id = m_id
                    best_res = cand_res

        if best_m_id is not None and best_start is not None and best_res is not None:
            end = best_start + job.duration_slots
            placements.append(
                Placement(
                    job_id=job.id,
                    machine_id=best_m_id,
                    start=best_start,
                    end=end,
                )
            )
            # Update machine busy, results, and total slot kw
            for t in range(horizon):
                total_slot_kw[t] += best_res.slot_kw[t] - machine_results[best_m_id].slot_kw[t]
            machine_busy[best_m_id].append((best_start, end, job.id))
            machine_busy[best_m_id].sort(key=lambda x: x[0])
            machine_results[best_m_id] = best_res
        else:
            unplaced.append(job.id)

    # Build final schedule
    segments_by_machine = {m.id: machine_results[m.id].segments for m in plant.machines}
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
    return schedule, metrics, machine_results


def construct_h3(
    plant: Plant,
    tariff: Tariff,
    peak_cap_kw: Optional[float] = None,
) -> Tuple[Schedule, ScheduleMetrics, Dict[str, MachineScheduleResult]]:
    """H3: First-fit packing heuristic.
    
    Fill lowest-index machines first, so subsequent machines can stay off entirely.
    """
    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)
    horizon = plant.horizon_slots
    slot_min = plant.slot_minutes
    machine_map = {m.id: m for m in plant.machines}

    sorted_jobs = sorted(
        plant.jobs,
        key=lambda j: (-j.priority, -j.duration_slots, j.id),
    )

    machine_busy: Dict[str, List[Tuple[int, int, Optional[str]]]] = {
        m.id: [] for m in plant.machines
    }
    machine_results: Dict[str, MachineScheduleResult] = {
        m.id: evaluate_machine_schedule(m, [], rate_profile, slot_min, horizon_slots=horizon)
        for m in plant.machines
    }

    total_slot_kw = [0.0] * horizon
    for m in plant.machines:
        for t in range(horizon):
            total_slot_kw[t] += machine_results[m.id].slot_kw[t]

    placements: List[Placement] = []
    unplaced: List[str] = []

    for job in sorted_jobs:
        allowed = job.allowed_machines if job.allowed_machines else [m.id for m in plant.machines]
        deadline = job.deadline_slot if job.deadline_slot is not None else horizon

        # In H3, iterate machines strictly in plant.machines index order
        placed = False
        for m in plant.machines:
            if m.id not in allowed:
                continue

            current_busy = machine_busy[m.id]
            min_start = job.release_slot
            if m.initial_state == "off":
                min_start = max(min_start, m.warmup_slots)

            for s in range(min_start, deadline - job.duration_slots + 1):
                if s + job.duration_slots > horizon:
                    break

                if not _is_slot_free(m, current_busy, s, job.duration_slots, job.release_slot, deadline, horizon):
                    continue

                candidate_busy = current_busy + [(s, s + job.duration_slots, job.id)]
                candidate_busy.sort(key=lambda x: x[0])

                cand_res = evaluate_machine_schedule(
                    machine=m,
                    busy_intervals=candidate_busy,
                    rate_profile=rate_profile,
                    slot_minutes=slot_min,
                    tariff_blind=False,
                    horizon_slots=horizon,
                )

                if peak_cap_kw is not None:
                    violates_cap = False
                    for t in range(horizon):
                        new_kw = total_slot_kw[t] - machine_results[m.id].slot_kw[t] + cand_res.slot_kw[t]
                        if new_kw > peak_cap_kw + 1e-4:
                            violates_cap = True
                            break
                    if violates_cap:
                        continue

                # Placed on this machine!
                end = s + job.duration_slots
                placements.append(
                    Placement(
                        job_id=job.id,
                        machine_id=m.id,
                        start=s,
                        end=end,
                    )
                )
                for t in range(horizon):
                    total_slot_kw[t] += cand_res.slot_kw[t] - machine_results[m.id].slot_kw[t]
                machine_busy[m.id].append((s, end, job.id))
                machine_busy[m.id].sort(key=lambda x: x[0])
                machine_results[m.id] = cand_res
                placed = True
                break

            if placed:
                break

        if not placed:
            unplaced.append(job.id)

    segments_by_machine = {m.id: machine_results[m.id].segments for m in plant.machines}
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
    return schedule, metrics, machine_results
