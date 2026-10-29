"""Per-machine exact cost calculation and gap decisions."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from peakshift.core.models import Machine, Segment
from peakshift.core.timegrid import slot_hours


@dataclass
class GapCandidate:
    gap_type: str  # "leading" or "internal"
    start_slot: int
    end_slot: int
    warmup_start: int
    warmup_slots: int
    idle_energy: float
    idle_cost: float
    off_cost: float
    saving_cost: float
    saving_energy: float
    rate_at_restart: float


@dataclass
class MachineScheduleResult:
    segments: List[Segment]
    kwh: float
    cost: float
    idle_kwh: float
    switch_offs: int
    slot_kw: List[float]


def evaluate_machine_schedule(
    machine: Machine,
    busy_intervals: List[Tuple[int, int, Optional[str]]],
    rate_profile: List[float],
    slot_minutes: int,
    tariff_blind: bool = False,
    horizon_slots: Optional[int] = None,
) -> MachineScheduleResult:
    """Evaluates exact cost, segments, energy, and power profile for a single machine.
    
    busy_intervals: list of (start_slot, end_slot, job_id) sorted by start_slot.
    rate_profile: list of rate[t] per kWh for each slot.
    tariff_blind: if True, break-even uses energy comparison (idle_kwh >= restart_kwh),
                  otherwise uses cost comparison (idle_cost > off_cost).
    """
    total_slots = horizon_slots if horizon_slots is not None else len(rate_profile)
    h = slot_hours(slot_minutes)
    w = machine.warmup_slots
    min_off = machine.min_off_slots

    # Case: Machine has no jobs all day
    if not busy_intervals:
        segments = [Segment(state="off", start=0, end=total_slots, job_id=None)]
        return MachineScheduleResult(
            segments=segments,
            kwh=0.0,
            cost=0.0,
            idle_kwh=0.0,
            switch_offs=0,
            slot_kw=[0.0] * total_slots,
        )

    # Sort busy intervals just in case
    sorted_busy = sorted(busy_intervals, key=lambda x: x[0])

    # Identify candidate gaps for switching off
    gap_candidates: List[GapCandidate] = []

    # 1. Leading gap
    first_start = sorted_busy[0][0]
    if first_start > 0:
        if machine.initial_state == "off":
            # Initial off: off for free, restart charged at first use
            # Must satisfy first_start >= w
            pass  # Handled directly in segment construction
        else:
            # Starts idle: can turn off if gap length >= w + min_off
            gap_len = first_start
            if gap_len >= w + min_off:
                idle_cost = sum(
                    machine.idle_kw * h * rate_profile[t] for t in range(first_start)
                )
                idle_energy = gap_len * machine.idle_kw * h
                restart_slot = first_start - w if w > 0 else first_start
                rate_restart = rate_profile[restart_slot] if restart_slot < total_slots else rate_profile[-1]
                off_cost = machine.restart_kwh * rate_restart
                saving_cost = idle_cost - off_cost
                saving_energy = idle_energy - machine.restart_kwh

                gap_candidates.append(
                    GapCandidate(
                        gap_type="leading",
                        start_slot=0,
                        end_slot=first_start,
                        warmup_start=first_start - w,
                        warmup_slots=w,
                        idle_energy=idle_energy,
                        idle_cost=idle_cost,
                        off_cost=off_cost,
                        saving_cost=saving_cost,
                        saving_energy=saving_energy,
                        rate_at_restart=rate_restart,
                    )
                )

    # 2. Internal gaps
    for i in range(len(sorted_busy) - 1):
        g_start = sorted_busy[i][1]
        g_end = sorted_busy[i + 1][0]
        gap_len = g_end - g_start
        if gap_len > 0:
            if gap_len >= w + min_off:
                idle_cost = sum(
                    machine.idle_kw * h * rate_profile[t] for t in range(g_start, g_end)
                )
                idle_energy = gap_len * machine.idle_kw * h
                restart_slot = g_end - w if w > 0 else g_end
                rate_restart = rate_profile[restart_slot] if restart_slot < total_slots else rate_profile[-1]
                off_cost = machine.restart_kwh * rate_restart
                saving_cost = idle_cost - off_cost
                saving_energy = idle_energy - machine.restart_kwh

                gap_candidates.append(
                    GapCandidate(
                        gap_type="internal",
                        start_slot=g_start,
                        end_slot=g_end,
                        warmup_start=g_end - w,
                        warmup_slots=w,
                        idle_energy=idle_energy,
                        idle_cost=idle_cost,
                        off_cost=off_cost,
                        saving_cost=saving_cost,
                        saving_energy=saving_energy,
                        rate_at_restart=rate_restart,
                    )
                )

    # Decide which candidate gaps switch off
    # Filter by saving > 0 (or >= 0 for tariff blind break-even)
    beneficial_gaps: List[GapCandidate] = []
    for g in gap_candidates:
        if tariff_blind:
            if g.saving_energy >= 0:
                beneficial_gaps.append(g)
        else:
            if g.saving_cost > 0:
                beneficial_gaps.append(g)

    # Apply max_switch_cycles cap if set
    if machine.max_switch_cycles is not None and machine.max_switch_cycles >= 0:
        if tariff_blind:
            beneficial_gaps.sort(key=lambda x: x.saving_energy, reverse=True)
        else:
            beneficial_gaps.sort(key=lambda x: x.saving_cost, reverse=True)
        off_gaps_set = set(
            (g.start_slot, g.end_slot)
            for g in beneficial_gaps[: machine.max_switch_cycles]
        )
    else:
        off_gaps_set = set((g.start_slot, g.end_slot) for g in beneficial_gaps)

    # Build segments, energy, cost, and slot kW vector
    segments: List[Segment] = []
    total_kwh = 0.0
    total_cost = 0.0
    total_idle_kwh = 0.0
    switch_offs = 0
    slot_kw = [0.0] * total_slots

    # Leading section
    if first_start > 0:
        if machine.initial_state == "off":
            # Off from 0 to first_start - w
            if first_start - w > 0:
                segments.append(
                    Segment(state="off", start=0, end=first_start - w, job_id=None)
                )
            # Warmup from first_start - w to first_start
            if w > 0:
                segments.append(
                    Segment(
                        state="warmup",
                        start=first_start - w,
                        end=first_start,
                        job_id=None,
                    )
                )
                warmup_kw = machine.restart_kwh / (w * h)
                for t in range(first_start - w, first_start):
                    slot_kw[t] = warmup_kw
            # Restart energy charged once at rate[first_start - w]
            restart_slot = first_start - w if w > 0 else first_start
            rate_restart = rate_profile[restart_slot] if restart_slot < total_slots else rate_profile[-1]
            total_kwh += machine.restart_kwh
            total_cost += machine.restart_kwh * rate_restart
        else:
            # Initial state idle
            if (0, first_start) in off_gaps_set:
                # Turn off!
                switch_offs += 1
                if first_start - w > 0:
                    segments.append(
                        Segment(
                            state="off", start=0, end=first_start - w, job_id=None
                        )
                    )
                if w > 0:
                    segments.append(
                        Segment(
                            state="warmup",
                            start=first_start - w,
                            end=first_start,
                            job_id=None,
                        )
                    )
                    warmup_kw = machine.restart_kwh / (w * h)
                    for t in range(first_start - w, first_start):
                        slot_kw[t] = warmup_kw
                restart_slot = first_start - w if w > 0 else first_start
                rate_restart = rate_profile[restart_slot] if restart_slot < total_slots else rate_profile[-1]
                total_kwh += machine.restart_kwh
                total_cost += machine.restart_kwh * rate_restart
            else:
                # Stay idle
                segments.append(
                    Segment(state="idle", start=0, end=first_start, job_id=None)
                )
                for t in range(first_start):
                    slot_kw[t] = machine.idle_kw
                    e = machine.idle_kw * h
                    total_kwh += e
                    total_idle_kwh += e
                    total_cost += e * rate_profile[t]

    # Process jobs and internal gaps
    for idx, (b_start, b_end, j_id) in enumerate(sorted_busy):
        # Run segment
        segments.append(
            Segment(state="run", start=b_start, end=b_end, job_id=j_id)
        )
        for t in range(b_start, b_end):
            slot_kw[t] = machine.run_kw
            e = machine.run_kw * h
            total_kwh += e
            total_cost += e * rate_profile[t]

        # Gap to next job if not last job
        if idx < len(sorted_busy) - 1:
            next_start = sorted_busy[idx + 1][0]
            g_len = next_start - b_end
            if g_len > 0:
                if (b_end, next_start) in off_gaps_set:
                    # Switch off!
                    switch_offs += 1
                    if next_start - w > b_end:
                        segments.append(
                            Segment(
                                state="off",
                                start=b_end,
                                end=next_start - w,
                                job_id=None,
                            )
                        )
                    if w > 0:
                        segments.append(
                            Segment(
                                state="warmup",
                                start=next_start - w,
                                end=next_start,
                                job_id=None,
                            )
                        )
                        warmup_kw = machine.restart_kwh / (w * h)
                        for t in range(next_start - w, next_start):
                            slot_kw[t] = warmup_kw
                    restart_slot = next_start - w if w > 0 else next_start
                    rate_restart = rate_profile[restart_slot] if restart_slot < total_slots else rate_profile[-1]
                    total_kwh += machine.restart_kwh
                    total_cost += machine.restart_kwh * rate_restart
                else:
                    # Stay idle
                    segments.append(
                        Segment(
                            state="idle",
                            start=b_end,
                            end=next_start,
                            job_id=None,
                        )
                    )
                    for t in range(b_end, next_start):
                        slot_kw[t] = machine.idle_kw
                        e = machine.idle_kw * h
                        total_kwh += e
                        total_idle_kwh += e
                        total_cost += e * rate_profile[t]

    # Trailing gap after last job: off (cost 0)
    last_end = sorted_busy[-1][1]
    if last_end < total_slots:
        segments.append(
            Segment(state="off", start=last_end, end=total_slots, job_id=None)
        )

    return MachineScheduleResult(
        segments=segments,
        kwh=total_kwh,
        cost=total_cost,
        idle_kwh=total_idle_kwh,
        switch_offs=switch_offs,
        slot_kw=slot_kw,
    )
