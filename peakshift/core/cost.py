"""Schedule cost and metrics calculations across the entire plant."""

from typing import Dict, List, Optional
from pydantic import BaseModel

from peakshift.core.machine_cost import MachineScheduleResult
from peakshift.core.models import Job, Plant, Schedule, Segment, Tariff
from peakshift.core.timegrid import slot_hours


class ScheduleMetrics(BaseModel):
    kwh: float
    energy_cost: float
    tax: float
    peak_kw: float
    peak_kva: float
    billed_kva: float
    demand_charge: Optional[float] = None
    switch_offs: int
    idle_kwh: float
    makespan: int
    jobs_placed: int
    target_met: bool
    co2_kg: float


def compute_schedule_metrics(
    plant: Plant,
    tariff: Tariff,
    schedule: Schedule,
    machine_results: Optional[Dict[str, MachineScheduleResult]] = None,
    co2_factor_kg_per_kwh: float = 0.73,
) -> ScheduleMetrics:
    """Computes all exact metrics specified in Section 4 for a complete schedule."""
    h = slot_hours(plant.slot_minutes)
    horizon = plant.horizon_slots
    slots_total_kw = [0.0] * horizon

    machine_map = {m.id: m for m in plant.machines}

    total_kwh = 0.0
    total_energy_cost = 0.0
    total_idle_kwh = 0.0
    total_switch_offs = 0

    # If pre-computed machine_results are supplied, use them directly
    if machine_results:
        for mid, m_res in machine_results.items():
            total_kwh += m_res.kwh
            total_energy_cost += m_res.cost
            total_idle_kwh += m_res.idle_kwh
            total_switch_offs += m_res.switch_offs
            for t in range(horizon):
                slots_total_kw[t] += m_res.slot_kw[t]
    else:
        # Recompute from segments directly
        from peakshift.core.tariff import get_rate_profile

        rate_profile = get_rate_profile(tariff, horizon, plant.slot_minutes)

        for mid, segs in schedule.segments.items():
            m = machine_map[mid]
            w = m.warmup_slots
            warmup_kw = (m.restart_kwh / (w * h)) if w > 0 else 0.0

            for seg in segs:
                seg_len = seg.end - seg.start
                if seg.state == "run":
                    for t in range(seg.start, seg.end):
                        slots_total_kw[t] += m.run_kw
                        e = m.run_kw * h
                        total_kwh += e
                        total_energy_cost += e * rate_profile[t]
                elif seg.state == "idle":
                    for t in range(seg.start, seg.end):
                        slots_total_kw[t] += m.idle_kw
                        e = m.idle_kw * h
                        total_kwh += e
                        total_idle_kwh += e
                        total_energy_cost += e * rate_profile[t]
                elif seg.state == "warmup":
                    for t in range(seg.start, seg.end):
                        slots_total_kw[t] += warmup_kw
                    # Restart energy charged once at the start of warmup
                    rate_t = rate_profile[seg.start]
                    total_kwh += m.restart_kwh
                    total_energy_cost += m.restart_kwh * rate_t
                elif seg.state == "off":
                    # If off segment has restart without warmup
                    # Check if next segment is run
                    pass

            # Count switch-offs: transitions to off state (excluding leading off if started off, and trailing off)
            # A switch-off is when machine turns off during the schedule
            for i in range(len(segs)):
                if segs[i].state == "off":
                    # Check if it was a switch-off event:
                    # Not initial off if initial_state == 'off' and start == 0
                    if segs[i].start == 0 and m.initial_state == "off":
                        continue
                    # Not trailing off if end == horizon and no further jobs
                    if segs[i].end == horizon:
                        continue
                    total_switch_offs += 1

    tax = plant.power_factor  # placeholder before formula
    tax = tariff.tax_rate * total_energy_cost

    peak_kw = max(slots_total_kw) if slots_total_kw else 0.0
    peak_kva = peak_kw / plant.power_factor if plant.power_factor > 0 else peak_kw

    if plant.contract_kva is not None:
        billed_kva = max(peak_kva, tariff.demand_floor_fraction * plant.contract_kva)
    else:
        billed_kva = peak_kva

    demand_charge = (
        tariff.demand_rate_per_kva * billed_kva
        if tariff.demand_rate_per_kva is not None
        else None
    )

    # Makespan: max end slot of placed jobs
    makespan = max((p.end for p in schedule.placements), default=0)
    jobs_placed = len(schedule.placements)

    # Target met check: all mandatory jobs placed and total units placed >= target units
    placed_job_ids = {p.job_id for p in schedule.placements}
    mandatory_jobs = [j for j in plant.jobs if j.mandatory]
    target_units_total = sum(j.units for j in plant.jobs)
    placed_units_total = sum(j.units for j in plant.jobs if j.id in placed_job_ids)

    all_mandatory_placed = all(j.id in placed_job_ids for j in mandatory_jobs)
    target_met = all_mandatory_placed and (placed_units_total >= target_units_total)

    co2_kg = total_kwh * co2_factor_kg_per_kwh

    return ScheduleMetrics(
        kwh=round(total_kwh, 4),
        energy_cost=round(total_energy_cost, 4),
        tax=round(tax, 4),
        peak_kw=round(peak_kw, 4),
        peak_kva=round(peak_kva, 4),
        billed_kva=round(billed_kva, 4),
        demand_charge=round(demand_charge, 4) if demand_charge is not None else None,
        switch_offs=total_switch_offs,
        idle_kwh=round(total_idle_kwh, 4),
        makespan=makespan,
        jobs_placed=jobs_placed,
        target_met=target_met,
        co2_kg=round(co2_kg, 4),
    )
