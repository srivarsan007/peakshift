"""Improvement search: local search over relocate and swap moves."""

import os
import random
from typing import Dict, List, Optional, Tuple

from peakshift.core.construct import _is_slot_free
from peakshift.core.cost import ScheduleMetrics, compute_schedule_metrics
from peakshift.core.machine_cost import MachineScheduleResult, evaluate_machine_schedule
from peakshift.core.models import Job, Machine, Placement, Plant, Schedule, Segment, Tariff
from peakshift.core.tariff import get_rate_profile


def improve_schedule(
    plant: Plant,
    tariff: Tariff,
    schedule: Schedule,
    machine_results: Dict[str, MachineScheduleResult],
    peak_cap_kw: Optional[float] = None,
    seed: int = 42,
    max_iterations: Optional[int] = None,
) -> Tuple[Schedule, ScheduleMetrics, Dict[str, MachineScheduleResult]]:
    """Applies local search over relocate and swap moves to strictly improve cost.
    
    Uses seeded RNG and iteration limit for complete reproducibility.
    """
    if max_iterations is None:
        try:
            max_iterations = int(os.getenv("PEAKSHIFT_ITER", "150000"))
        except ValueError:
            max_iterations = 150000

    if not schedule.placements:
        metrics = compute_schedule_metrics(
            plant=plant,
            tariff=tariff,
            schedule=schedule,
            machine_results=machine_results,
        )
        return schedule, metrics, machine_results

    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)
    horizon = plant.horizon_slots
    slot_min = plant.slot_minutes
    job_map = {j.id: j for j in plant.jobs}
    machine_map = {m.id: m for m in plant.machines}

    # Internal state
    placements_map: Dict[str, Placement] = {p.job_id: p.model_copy() for p in schedule.placements}
    machine_busy: Dict[str, List[Tuple[int, int, str]]] = {m.id: [] for m in plant.machines}
    for p in placements_map.values():
        machine_busy[p.machine_id].append((p.start, p.end, p.job_id))
    for m in plant.machines:
        machine_busy[m.id].sort(key=lambda x: x[0])

    curr_results = dict(machine_results)
    total_slot_kw = [0.0] * horizon
    for m in plant.machines:
        for t in range(horizon):
            total_slot_kw[t] += curr_results[m.id].slot_kw[t]

    rng = random.Random(seed)
    placed_job_ids = list(placements_map.keys())

    # Limit search iterations
    # Cap iterations to keep portfolio <= 3s while exploring effectively
    iter_limit = min(max_iterations, 30000)
    no_improve_limit = 3000
    no_improve_count = 0

    for _ in range(iter_limit):
        if no_improve_count >= no_improve_limit:
            break

        move_type = rng.random()

        if move_type < 0.70 or len(placed_job_ids) < 2:
            # Relocate move
            j_id = rng.choice(placed_job_ids)
            job = job_map[j_id]
            curr_p = placements_map[j_id]
            src_m_id = curr_p.machine_id

            allowed = job.allowed_machines if job.allowed_machines else [m.id for m in plant.machines]
            target_m_id = rng.choice(allowed)
            target_m = machine_map[target_m_id]

            min_start = job.release_slot
            if target_m.initial_state == "off":
                min_start = max(min_start, target_m.warmup_slots)

            deadline = job.deadline_slot if job.deadline_slot is not None else horizon
            max_start = deadline - job.duration_slots
            if max_start < min_start:
                no_improve_count += 1
                continue

            new_start = rng.randint(min_start, max_start)
            if new_start + job.duration_slots > horizon:
                no_improve_count += 1
                continue

            # If identical placement, skip
            if src_m_id == target_m_id and new_start == curr_p.start:
                no_improve_count += 1
                continue

            # Candidate busy for target machine
            target_busy_base = [b for b in machine_busy[target_m_id] if b[2] != j_id]
            if not _is_slot_free(target_m, target_busy_base, new_start, job.duration_slots, job.release_slot, deadline, horizon):
                no_improve_count += 1
                continue

            new_target_busy = target_busy_base + [(new_start, new_start + job.duration_slots, j_id)]
            new_target_busy.sort(key=lambda x: x[0])

            new_target_res = evaluate_machine_schedule(
                machine=target_m,
                busy_intervals=new_target_busy,
                rate_profile=rate_profile,
                slot_minutes=slot_min,
                tariff_blind=False,
                horizon_slots=horizon,
            )

            if src_m_id == target_m_id:
                cost_delta = new_target_res.cost - curr_results[src_m_id].cost
                # Peak cap check
                if peak_cap_kw is not None:
                    violates = False
                    for t in range(horizon):
                        nkw = total_slot_kw[t] - curr_results[src_m_id].slot_kw[t] + new_target_res.slot_kw[t]
                        if nkw > peak_cap_kw + 1e-4:
                            violates = True
                            break
                    if violates:
                        no_improve_count += 1
                        continue

                if cost_delta < -1e-6:
                    # Accept!
                    for t in range(horizon):
                        total_slot_kw[t] += new_target_res.slot_kw[t] - curr_results[src_m_id].slot_kw[t]
                    curr_results[src_m_id] = new_target_res
                    machine_busy[src_m_id] = new_target_busy
                    placements_map[j_id].start = new_start
                    placements_map[j_id].end = new_start + job.duration_slots
                    no_improve_count = 0
                else:
                    no_improve_count += 1
            else:
                src_m = machine_map[src_m_id]
                new_src_busy = [b for b in machine_busy[src_m_id] if b[2] != j_id]
                new_src_res = evaluate_machine_schedule(
                    machine=src_m,
                    busy_intervals=new_src_busy,
                    rate_profile=rate_profile,
                    slot_minutes=slot_min,
                    tariff_blind=False,
                    horizon_slots=horizon,
                )

                cost_delta = (new_src_res.cost + new_target_res.cost) - (curr_results[src_m_id].cost + curr_results[target_m_id].cost)

                if peak_cap_kw is not None:
                    violates = False
                    for t in range(horizon):
                        nkw = (
                            total_slot_kw[t]
                            - curr_results[src_m_id].slot_kw[t]
                            - curr_results[target_m_id].slot_kw[t]
                            + new_src_res.slot_kw[t]
                            + new_target_res.slot_kw[t]
                        )
                        if nkw > peak_cap_kw + 1e-4:
                            violates = True
                            break
                    if violates:
                        no_improve_count += 1
                        continue

                if cost_delta < -1e-6:
                    # Accept!
                    for t in range(horizon):
                        total_slot_kw[t] += (
                            new_src_res.slot_kw[t]
                            + new_target_res.slot_kw[t]
                            - curr_results[src_m_id].slot_kw[t]
                            - curr_results[target_m_id].slot_kw[t]
                        )
                    curr_results[src_m_id] = new_src_res
                    curr_results[target_m_id] = new_target_res
                    machine_busy[src_m_id] = new_src_busy
                    machine_busy[target_m_id] = new_target_busy
                    placements_map[j_id].machine_id = target_m_id
                    placements_map[j_id].start = new_start
                    placements_map[j_id].end = new_start + job.duration_slots
                    no_improve_count = 0
                else:
                    no_improve_count += 1

        else:
            # Swap move
            j1_id, j2_id = rng.sample(placed_job_ids, 2)
            j1 = job_map[j1_id]
            j2 = job_map[j2_id]
            p1 = placements_map[j1_id]
            p2 = placements_map[j2_id]

            m1_id = p1.machine_id
            m2_id = p2.machine_id

            # Allowed machine checks
            allowed1 = j1.allowed_machines if j1.allowed_machines else [m.id for m in plant.machines]
            allowed2 = j2.allowed_machines if j2.allowed_machines else [m.id for m in plant.machines]
            if m2_id not in allowed1 or m1_id not in allowed2:
                no_improve_count += 1
                continue

            # Try swapping starts
            s1_new = p2.start
            s2_new = p1.start

            # Feasibility
            m1 = machine_map[m1_id]
            m2 = machine_map[m2_id]
            d1 = j1.deadline_slot if j1.deadline_slot is not None else horizon
            d2 = j2.deadline_slot if j2.deadline_slot is not None else horizon

            busy1_base = [b for b in machine_busy[m1_id] if b[2] not in (j1_id, j2_id)]
            busy2_base = [b for b in machine_busy[m2_id] if b[2] not in (j1_id, j2_id)]

            if not _is_slot_free(m2, busy2_base, s1_new, j1.duration_slots, j1.release_slot, d1, horizon):
                no_improve_count += 1
                continue
            if not _is_slot_free(m1, busy1_base, s2_new, j2.duration_slots, j2.release_slot, d2, horizon):
                no_improve_count += 1
                continue

            new_busy2 = busy2_base + [(s1_new, s1_new + j1.duration_slots, j1_id)]
            new_busy2.sort(key=lambda x: x[0])
            new_busy1 = busy1_base + [(s2_new, s2_new + j2.duration_slots, j2_id)]
            new_busy1.sort(key=lambda x: x[0])

            if m1_id == m2_id:
                # Ensure j1 and j2 do not overlap with each other
                if not (s1_new + j1.duration_slots <= s2_new or s2_new + j2.duration_slots <= s1_new):
                    no_improve_count += 1
                    continue

                # Same machine swap
                new_same_busy = busy1_base + [
                    (s1_new, s1_new + j1.duration_slots, j1_id),
                    (s2_new, s2_new + j2.duration_slots, j2_id),
                ]
                new_same_busy.sort(key=lambda x: x[0])
                new_res = evaluate_machine_schedule(
                    machine=m1,
                    busy_intervals=new_same_busy,
                    rate_profile=rate_profile,
                    slot_minutes=slot_min,
                    tariff_blind=False,
                    horizon_slots=horizon,
                )
                cost_delta = new_res.cost - curr_results[m1_id].cost
                if cost_delta < -1e-6:
                    for t in range(horizon):
                        total_slot_kw[t] += new_res.slot_kw[t] - curr_results[m1_id].slot_kw[t]
                    curr_results[m1_id] = new_res
                    machine_busy[m1_id] = new_same_busy
                    placements_map[j1_id].start = s1_new
                    placements_map[j1_id].end = s1_new + j1.duration_slots
                    placements_map[j2_id].start = s2_new
                    placements_map[j2_id].end = s2_new + j2.duration_slots
                    no_improve_count = 0
                else:
                    no_improve_count += 1
            else:
                new_res1 = evaluate_machine_schedule(
                    machine=m1,
                    busy_intervals=new_busy1,
                    rate_profile=rate_profile,
                    slot_minutes=slot_min,
                    tariff_blind=False,
                    horizon_slots=horizon,
                )
                new_res2 = evaluate_machine_schedule(
                    machine=m2,
                    busy_intervals=new_busy2,
                    rate_profile=rate_profile,
                    slot_minutes=slot_min,
                    tariff_blind=False,
                    horizon_slots=horizon,
                )
                cost_delta = (new_res1.cost + new_res2.cost) - (curr_results[m1_id].cost + curr_results[m2_id].cost)
                if cost_delta < -1e-6:
                    for t in range(horizon):
                        total_slot_kw[t] += (
                            new_res1.slot_kw[t]
                            + new_res2.slot_kw[t]
                            - curr_results[m1_id].slot_kw[t]
                            - curr_results[m2_id].slot_kw[t]
                        )
                    curr_results[m1_id] = new_res1
                    curr_results[m2_id] = new_res2
                    machine_busy[m1_id] = new_busy1
                    machine_busy[m2_id] = new_busy2
                    placements_map[j1_id].machine_id = m2_id
                    placements_map[j1_id].start = s1_new
                    placements_map[j1_id].end = s1_new + j1.duration_slots
                    placements_map[j2_id].machine_id = m1_id
                    placements_map[j2_id].start = s2_new
                    placements_map[j2_id].end = s2_new + j2.duration_slots
                    no_improve_count = 0
                else:
                    no_improve_count += 1

    # Return improved schedule
    segments_by_machine = {m.id: curr_results[m.id].segments for m in plant.machines}
    final_schedule = Schedule(
        segments=segments_by_machine,
        placements=list(placements_map.values()),
        unplaced=schedule.unplaced,
    )
    final_metrics = compute_schedule_metrics(
        plant=plant,
        tariff=tariff,
        schedule=final_schedule,
        machine_results=curr_results,
    )
    return final_schedule, final_metrics, curr_results
