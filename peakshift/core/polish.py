"""Exact CP-SAT Polish using OR-Tools (optional refine)."""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from peakshift.core.cost import compute_schedule_metrics
from peakshift.core.machine_cost import evaluate_machine_schedule
from peakshift.core.models import Placement, Plant, Schedule, Tariff
from peakshift.core.tariff import get_rate_profile

logger = logging.getLogger(__name__)


class PolishResult(BaseModel):
    status: str  # "improved" | "optimal" | "unchanged" | "disabled" | "skipped"
    message: str
    schedule: Schedule
    proven_lower_bound: Optional[float] = None
    gap_percent: Optional[float] = None
    elapsed_ms: float = 0.0


def can_refine_plant(plant: Plant) -> Tuple[bool, str]:
    """Validates whether the plant meets CP-SAT polish requirements per SPEC §5.7."""
    if os.environ.get("PEAKSHIFT_REFINE", "on").lower() == "off":
        return False, "Refine disabled via PEAKSHIFT_REFINE=off environment variable."

    if len(plant.machines) > 5:
        return False, f"Plant has {len(plant.machines)} machines (max 5 supported for CP-SAT)."

    if len(plant.jobs) > 40:
        return False, f"Plant has {len(plant.jobs)} jobs (max 40 supported for CP-SAT)."

    for m in plant.machines:
        if m.warmup_slots > 0:
            return False, f"Machine {m.id} has warmup_slots > 0 (not supported by CP-SAT formulation)."
        if m.min_off_slots > 0:
            return False, f"Machine {m.id} has min_off_slots > 0 (not supported by CP-SAT formulation)."
        if m.max_switch_cycles is not None:
            return False, f"Machine {m.id} has cycle limits (not supported by CP-SAT formulation)."
        if m.downtime and len(m.downtime) > 0:
            return False, f"Machine {m.id} has downtime intervals (not supported by CP-SAT formulation)."

    return True, "Plant is eligible for CP-SAT refine."


def refine_schedule(
    plant: Plant,
    tariff: Tariff,
    current_schedule: Schedule,
    peak_cap_kw: Optional[float] = None,
    time_limit_sec: float = 20.0,
    num_workers: int = 8,
) -> PolishResult:
    """Refines a schedule using OR-Tools CP-SAT solver if available and eligible."""
    t0 = time.perf_counter()

    eligible, reason = can_refine_plant(plant)
    if not eligible:
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        return PolishResult(
            status="disabled",
            message=reason,
            schedule=current_schedule,
            proven_lower_bound=None,
            elapsed_ms=elapsed,
        )

    # Check for OR-Tools availability
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        return PolishResult(
            status="disabled",
            message="OR-Tools is not installed in runtime environment. Refine is optional.",
            schedule=current_schedule,
            proven_lower_bound=None,
            elapsed_ms=elapsed,
        )

    # Build CP-SAT Model per SPEC §5.7
    try:
        model = cp_model.CpModel()
        horizon = plant.horizon_slots
        h_hours = plant.slot_minutes / 60.0
        rate_profile = get_rate_profile(tariff, horizon, plant.slot_minutes)

        machines = plant.machines
        jobs = plant.jobs
        machine_map = {m.id: m for m in machines}

        # Decision variables: x[j, m, s] = 1 if job j starts on machine m at slot s
        x: Dict[Tuple[str, str, int], Any] = {}
        # Hint lookup from current_schedule
        current_placements = {p.job_id: (p.machine_id, p.start) for p in current_schedule.placements}

        for j in jobs:
            allowed = j.allowed_machines or [m.id for m in machines]
            deadline = j.deadline_slot or horizon
            feasible_vars = []

            for m_id in allowed:
                if m_id not in machine_map:
                    continue
                latest_start = deadline - j.duration_slots
                for s in range(j.release_slot, latest_start + 1):
                    var = model.NewBoolVar(f"x_{j.id}_{m_id}_{s}")
                    x[(j.id, m_id, s)] = var
                    feasible_vars.append(var)

                    # Add warm-start hint if matches current schedule
                    if j.id in current_placements:
                        c_mid, c_start = current_placements[j.id]
                        if c_mid == m_id and c_start == s:
                            model.AddHint(var, 1)
                        else:
                            model.AddHint(var, 0)

            # Exactly one placement per job
            model.AddExactlyOne(feasible_vars)

        # Machine non-overlap and run indicators
        # run[m, t] is sum of jobs running on m at t <= 1
        run_vars: Dict[Tuple[str, int], Any] = {}
        on_vars: Dict[Tuple[str, int], Any] = {}
        switch_vars: Dict[Tuple[str, int], Any] = {}

        for m in machines:
            for t in range(horizon):
                running_on_m = []
                for j in jobs:
                    allowed = j.allowed_machines or [m_id for m_id in machine_map]
                    if m.id not in allowed:
                        continue
                    deadline = j.deadline_slot or horizon
                    min_s = max(j.release_slot, t - j.duration_slots + 1)
                    max_s = min(t, deadline - j.duration_slots)
                    for s in range(min_s, max_s + 1):
                        if (j.id, m.id, s) in x:
                            running_on_m.append(x[(j.id, m.id, s)])

                # At most 1 job running at time t
                model.Add(sum(running_on_m) <= 1)
                run_var = model.NewBoolVar(f"run_{m.id}_{t}")
                model.Add(run_var == sum(running_on_m))
                run_vars[(m.id, t)] = run_var

                # Machine on state: on[m, t] >= run[m, t]
                on_var = model.NewBoolVar(f"on_{m.id}_{t}")
                model.Add(on_var >= run_var)
                on_vars[(m.id, t)] = on_var

                # Switch-on indicator: switch_on[m, t] >= on[m, t] - on[m, t-1]
                sw_var = model.NewBoolVar(f"sw_{m.id}_{t}")
                if t == 0:
                    init_on = 1 if m.initial_state != "off" else 0
                    model.Add(sw_var >= on_var - init_on)
                else:
                    model.Add(sw_var >= on_var - on_vars[(m.id, t - 1)])
                switch_vars[(m.id, t)] = sw_var

        # Optional Peak Cap
        if peak_cap_kw is not None and peak_cap_kw > 0:
            for t in range(horizon):
                slot_kw = []
                for m in machines:
                    # Power is run_kw if running, idle_kw if on but not running
                    # i.e., run_var * (run_kw - idle_kw) + on_var * idle_kw
                    slot_kw.append(
                        run_vars[(m.id, t)] * int(round(m.run_kw - m.idle_kw))
                        + on_vars[(m.id, t)] * int(round(m.idle_kw))
                    )
                model.Add(sum(slot_kw) <= int(round(peak_cap_kw)))

        # Objective: minimize energy cost (scaled to integer cents/paise)
        # Cost = sum over m, t of:
        # run_kw * rate[t] * h on run slots + idle_kw * rate[t] * h on (on - run) slots + restart_kwh * rate[t] on switch_on
        obj_terms = []
        scale = 100  # Paise

        for m in machines:
            run_diff = m.run_kw - m.idle_kw
            for t in range(horizon):
                rate = rate_profile[t]
                # run incremental cost
                run_coeff = int(round(run_diff * h_hours * rate * scale))
                # idle cost when on
                idle_coeff = int(round(m.idle_kw * h_hours * rate * scale))
                # restart cost
                restart_coeff = int(round(m.restart_kwh * rate * scale))

                obj_terms.append(run_vars[(m.id, t)] * run_coeff)
                obj_terms.append(on_vars[(m.id, t)] * idle_coeff)
                obj_terms.append(switch_vars[(m.id, t)] * restart_coeff)

        model.Minimize(sum(obj_terms))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_sec
        solver.parameters.num_workers = num_workers

        status = solver.Solve(model)
        elapsed = round((time.perf_counter() - t0) * 1000, 2)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Extract placements
            new_placements = []
            for (j_id, m_id, s), var in x.items():
                if solver.Value(var) == 1:
                    job = next(j for j in jobs if j.id == j_id)
                    new_placements.append(
                        Placement(
                            job_id=j_id,
                            machine_id=m_id,
                            start=s,
                            end=s + job.duration_slots,
                        )
                    )

            # Reconstruct segments using evaluate_machine_schedule
            busy_by_m = {m.id: [] for m in machines}
            for p in new_placements:
                busy_by_m[p.machine_id].append((p.start, p.end, p.job_id))
            for mid in busy_by_m:
                busy_by_m[mid].sort(key=lambda x: x[0])

            segments_by_m = {}
            for m in machines:
                res = evaluate_machine_schedule(
                    machine=m,
                    busy_intervals=busy_by_m[m.id],
                    rate_profile=rate_profile,
                    slot_minutes=plant.slot_minutes,
                    horizon_slots=horizon,
                )
                segments_by_m[m.id] = res.segments

            candidate_schedule = Schedule(
                segments=segments_by_m,
                placements=new_placements,
                unplaced=[],
            )

            current_m = compute_schedule_metrics(plant, tariff, current_schedule)
            cand_m = compute_schedule_metrics(plant, tariff, candidate_schedule)

            lower_bound_inr = solver.BestObjectiveBound() / scale if status == cp_model.FEASIBLE else cand_m.energy_cost
            gap_pct = round(((cand_m.energy_cost - lower_bound_inr) / cand_m.energy_cost) * 100, 2) if cand_m.energy_cost > 0 else 0.0

            if cand_m.energy_cost < current_m.energy_cost:
                return PolishResult(
                    status="improved",
                    message=f"CP-SAT improved schedule energy cost from ₹{current_m.energy_cost:.1f} to ₹{cand_m.energy_cost:.1f}.",
                    schedule=candidate_schedule,
                    proven_lower_bound=round(lower_bound_inr, 2),
                    gap_percent=gap_pct,
                    elapsed_ms=elapsed,
                )
            else:
                return PolishResult(
                    status="optimal" if status == cp_model.OPTIMAL else "unchanged",
                    message="Portfolio solution proved optimal or no cheaper schedule exists within time limit.",
                    schedule=current_schedule,
                    proven_lower_bound=round(lower_bound_inr, 2),
                    gap_percent=gap_pct,
                    elapsed_ms=elapsed,
                )
        else:
            return PolishResult(
                status="skipped",
                message=f"CP-SAT solver returned status {solver.StatusName(status)}.",
                schedule=current_schedule,
                proven_lower_bound=None,
                elapsed_ms=elapsed,
            )

    except Exception as e:
        logger.error(f"CP-SAT polish failed with error: {e}")
        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        return PolishResult(
            status="skipped",
            message=f"CP-SAT failed: {str(e)}",
            schedule=current_schedule,
            proven_lower_bound=None,
            elapsed_ms=elapsed,
        )
