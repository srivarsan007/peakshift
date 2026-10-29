"""Independent schedule verifier.

Imports only models.py and tariff.py.
Recomputes everything slot by slot from the output segments.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from peakshift.core.models import Plant, Schedule, Segment, Tariff
from peakshift.core.tariff import get_rate_profile


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    details: str = ""


class VerificationResult(BaseModel):
    passed: bool
    checks: List[VerificationCheck]
    recomputed_metrics: Dict[str, float] = Field(default_factory=dict)


def verify_schedule(
    plant: Plant,
    tariff: Tariff,
    schedule: Schedule,
    reported_metrics: Optional[Dict[str, Any]] = None,
    peak_cap_kw: Optional[float] = None,
    co2_factor_kg_per_kwh: float = 0.73,
    now_slot: Optional[int] = None,
    frozen_job_ids: Optional[Any] = None,
) -> VerificationResult:
    """Verifies that a schedule strictly satisfies all physical and contract rules.
    
    Independent implementation importing ONLY models.py and tariff.py.
    """
    checks: List[VerificationCheck] = []
    horizon = plant.horizon_slots
    slot_min = plant.slot_minutes
    h = slot_min / 60.0
    machine_map = {m.id: m for m in plant.machines}
    job_map = {j.id: j for j in plant.jobs}
    rate_profile = get_rate_profile(tariff, horizon, slot_min)

    # -------------------------------------------------------------
    # Check 1: Horizon coverage
    # every machine's segments cover the horizon once, no gaps or overlaps
    # -------------------------------------------------------------
    check1_passed = True
    check1_details = "All machines cover horizon exactly without gaps or overlaps."
    for m in plant.machines:
        if m.id not in schedule.segments:
            check1_passed = False
            check1_details = f"Machine '{m.id}' missing from schedule segments."
            break
        segs = schedule.segments[m.id]
        if not segs:
            check1_passed = False
            check1_details = f"Machine '{m.id}' has empty segments list."
            break
        if segs[0].start != 0:
            check1_passed = False
            check1_details = f"Machine '{m.id}' first segment starts at {segs[0].start}, expected 0."
            break
        for i in range(len(segs) - 1):
            if segs[i].end != segs[i + 1].start:
                check1_passed = False
                check1_details = (
                    f"Machine '{m.id}' segment gap/overlap between [{segs[i].start}, {segs[i].end}) "
                    f"and [{segs[i + 1].start}, {segs[i + 1].end})."
                )
                break
        if not check1_passed:
            break
        if segs[-1].end != horizon:
            check1_passed = False
            check1_details = f"Machine '{m.id}' last segment ends at {segs[-1].end}, expected {horizon}."
            break

    checks.append(VerificationCheck(name="horizon_coverage", passed=check1_passed, details=check1_details))

    # -------------------------------------------------------------
    # Check 2: Job constraints
    # every placed job runs on an allowed machine inside its release and deadline window for exactly its duration
    # -------------------------------------------------------------
    check2_passed = True
    check2_details = "All placed jobs respect allowed machines, release, deadline, and duration."
    for p in schedule.placements:
        if p.job_id not in job_map:
            check2_passed = False
            check2_details = f"Placement references unknown job '{p.job_id}'."
            break
        job = job_map[p.job_id]

        if p.machine_id not in machine_map:
            check2_passed = False
            check2_details = f"Job '{p.job_id}' placed on unknown machine '{p.machine_id}'."
            break

        allowed = job.allowed_machines if job.allowed_machines else list(machine_map.keys())
        if p.machine_id not in allowed:
            check2_passed = False
            check2_details = (
                f"Job '{p.job_id}' placed on machine '{p.machine_id}' which is not in allowed_machines: {allowed}."
            )
            break

        if p.start < job.release_slot:
            check2_passed = False
            check2_details = f"Job '{p.job_id}' starts at {p.start} before release_slot {job.release_slot}."
            break

        deadline = job.deadline_slot if job.deadline_slot is not None else horizon
        if p.end > deadline:
            check2_passed = False
            check2_details = f"Job '{p.job_id}' ends at {p.end} after deadline_slot {deadline}."
            break

        placed_dur = p.end - p.start
        if placed_dur != job.duration_slots:
            check2_passed = False
            check2_details = (
                f"Job '{p.job_id}' duration is {placed_dur}, expected {job.duration_slots}."
            )
            break

        # Verify that the machine's segments contain this run segment
        m_segs = schedule.segments.get(p.machine_id, [])
        run_found = False
        for s in m_segs:
            if s.state == "run" and s.job_id == p.job_id:
                if s.start == p.start and s.end == p.end:
                    run_found = True
                    break
        if not run_found:
            check2_passed = False
            check2_details = f"Job '{p.job_id}' placement [{p.start}, {p.end}) not found in segments of '{p.machine_id}'."
            break

    checks.append(VerificationCheck(name="job_constraints", passed=check2_passed, details=check2_details))

    # -------------------------------------------------------------
    # Check 3: No two jobs overlap on a machine
    # -------------------------------------------------------------
    check3_passed = True
    check3_details = "No jobs overlap on any machine."
    for m in plant.machines:
        m_places = [p for p in schedule.placements if p.machine_id == m.id]
        sorted_places = sorted(m_places, key=lambda x: x.start)
        for i in range(len(sorted_places) - 1):
            if sorted_places[i].end > sorted_places[i + 1].start:
                check3_passed = False
                check3_details = (
                    f"Machine '{m.id}' has overlapping placements: job '{sorted_places[i].job_id}' "
                    f"[{sorted_places[i].start}, {sorted_places[i].end}) and '{sorted_places[i + 1].job_id}' "
                    f"[{sorted_places[i + 1].start}, {sorted_places[i + 1].end})."
                )
                break
        if not check3_passed:
            break

    checks.append(VerificationCheck(name="no_job_overlap", passed=check3_passed, details=check3_details))

    # -------------------------------------------------------------
    # Check 4: No run during downtime
    # -------------------------------------------------------------
    check4_passed = True
    check4_details = "No jobs run during downtime intervals."
    for m in plant.machines:
        for dt_start, dt_end in m.downtime:
            m_places = [p for p in schedule.placements if p.machine_id == m.id]
            for p in m_places:
                if not (p.end <= dt_start or p.start >= dt_end):
                    check4_passed = False
                    check4_details = (
                        f"Machine '{m.id}' runs job '{p.job_id}' at [{p.start}, {p.end}) during downtime [{dt_start}, {dt_end})."
                    )
                    break
            if not check4_passed:
                break
        if not check4_passed:
            break

    checks.append(VerificationCheck(name="no_run_in_downtime", passed=check4_passed, details=check4_details))

    # -------------------------------------------------------------
    # Check 5: Warmup and min_off
    # every off segment is followed by a warmup of the right length before the next run, off length >= min_off_slots
    # -------------------------------------------------------------
    check5_passed = True
    check5_details = "Warmup durations and min_off_slots respected."
    for m in plant.machines:
        segs = schedule.segments.get(m.id, [])
        w = m.warmup_slots
        min_off = m.min_off_slots

        for i, seg in enumerate(segs):
            if seg.state == "off":
                seg_len = seg.end - seg.start
                # Check if this is the trailing off segment (end of day)
                is_trailing = (seg.end == horizon) and not any(s.state == "run" for s in segs[i + 1:])
                # Check if this is initial off
                is_initial = (seg.start == 0 and m.initial_state == "off")

                if not is_trailing:
                    # An off segment during operation must satisfy min_off_slots
                    if not is_initial and seg_len < min_off:
                        check5_passed = False
                        check5_details = (
                            f"Machine '{m.id}' off segment [{seg.start}, {seg.end}) length {seg_len} < min_off_slots {min_off}."
                        )
                        break

                    # After off, before next run, there must be a warmup segment of length w (if w > 0)
                    if w > 0:
                        if i + 1 >= len(segs) or segs[i + 1].state != "warmup":
                            check5_passed = False
                            check5_details = (
                                f"Machine '{m.id}' off segment [{seg.start}, {seg.end}) not followed by warmup segment."
                            )
                            break
                        warmup_seg = segs[i + 1]
                        warmup_len = warmup_seg.end - warmup_seg.start
                        if warmup_len != w:
                            check5_passed = False
                            check5_details = (
                                f"Machine '{m.id}' warmup segment [{warmup_seg.start}, {warmup_seg.end}) length {warmup_len} != {w}."
                            )
                            break
                        # Warmup must be immediately followed by run
                        if i + 2 >= len(segs) or segs[i + 2].state != "run":
                            check5_passed = False
                            check5_details = (
                                f"Machine '{m.id}' warmup segment [{warmup_seg.start}, {warmup_seg.end}) not followed by run."
                            )
                            break
            elif seg.state == "warmup":
                # Warmup can only occur after an off state
                if i == 0 and m.initial_state != "off":
                    check5_passed = False
                    check5_details = f"Machine '{m.id}' started with warmup but initial_state was not 'off'."
                    break
                elif i > 0 and segs[i - 1].state != "off":
                    check5_passed = False
                    check5_details = f"Machine '{m.id}' warmup segment preceded by '{segs[i - 1].state}', expected 'off'."
                    break
        if not check5_passed:
            break

    checks.append(VerificationCheck(name="warmup_and_min_off", passed=check5_passed, details=check5_details))

    # -------------------------------------------------------------
    # Check 6: Switch cycles <= max_switch_cycles
    # -------------------------------------------------------------
    check6_passed = True
    check6_details = "Switch cycles within limit."
    for m in plant.machines:
        if m.max_switch_cycles is not None:
            segs = schedule.segments.get(m.id, [])
            cycles = 0
            for i, seg in enumerate(segs):
                if seg.state == "off":
                    # Do not count initial off if machine started off
                    if seg.start == 0 and m.initial_state == "off":
                        continue
                    # Do not count trailing off
                    if seg.end == horizon and not any(s.state == "run" for s in segs[i + 1:]):
                        continue
                    cycles += 1
            if cycles > m.max_switch_cycles:
                check6_passed = False
                check6_details = (
                    f"Machine '{m.id}' had {cycles} switch cycles, exceeding max_switch_cycles ({m.max_switch_cycles})."
                )
                break

    checks.append(VerificationCheck(name="max_switch_cycles", passed=check6_passed, details=check6_details))

    # -------------------------------------------------------------
    # Check 7 & Metrics Recomputation: Slot by slot
    # -------------------------------------------------------------
    slots_kw = [0.0] * horizon
    total_kwh = 0.0
    total_energy_cost = 0.0
    total_idle_kwh = 0.0

    for m in plant.machines:
        segs = schedule.segments.get(m.id, [])
        w = m.warmup_slots
        warmup_kw = (m.restart_kwh / (w * h)) if w > 0 else 0.0

        for seg in segs:
            if seg.state == "run":
                for t in range(seg.start, seg.end):
                    slots_kw[t] += m.run_kw
                    e = m.run_kw * h
                    total_kwh += e
                    total_energy_cost += e * rate_profile[t]
            elif seg.state == "idle":
                for t in range(seg.start, seg.end):
                    slots_kw[t] += m.idle_kw
                    e = m.idle_kw * h
                    total_kwh += e
                    total_idle_kwh += e
                    total_energy_cost += e * rate_profile[t]
            elif seg.state == "warmup":
                for t in range(seg.start, seg.end):
                    slots_kw[t] += warmup_kw
                # Restart energy is charged once at rate[start]
                restart_rate = rate_profile[seg.start]
                total_kwh += m.restart_kwh
                total_energy_cost += m.restart_kwh * restart_rate
            elif seg.state == "off":
                # If warmup_slots == 0 and machine turns on to run at seg.end
                # check if next is run
                # In that case, restart energy is charged at rate[seg.end]
                idx = segs.index(seg)
                if idx + 1 < len(segs) and segs[idx + 1].state == "run" and w == 0:
                    restart_rate = rate_profile[seg.end]
                    total_kwh += m.restart_kwh
                    total_energy_cost += m.restart_kwh * restart_rate

    peak_kw = max(slots_kw) if slots_kw else 0.0
    peak_kva = peak_kw / plant.power_factor if plant.power_factor > 0 else peak_kw
    tax = tariff.tax_rate * total_energy_cost

    recomputed = {
        "kwh": round(total_kwh, 4),
        "energy_cost": round(total_energy_cost, 4),
        "tax": round(tax, 4),
        "peak_kw": round(peak_kw, 4),
        "peak_kva": round(peak_kva, 4),
        "idle_kwh": round(total_idle_kwh, 4),
    }

    # Check 7: Peak cap respected if set
    check7_passed = True
    check7_details = "Peak kW within cap."
    if peak_cap_kw is not None:
        if peak_kw > peak_cap_kw + 1e-4:
            check7_passed = False
            check7_details = f"Peak kW ({peak_kw:.2f}) exceeds cap ({peak_cap_kw:.2f})."

    checks.append(VerificationCheck(name="peak_cap", passed=check7_passed, details=check7_details))

    # -------------------------------------------------------------
    # Check 8: Every mandatory job placed and total units >= target, no duplicates
    # -------------------------------------------------------------
    check8_passed = True
    check8_details = "All mandatory jobs placed, target units met, no duplicate placements."

    placed_ids = [p.job_id for p in schedule.placements]
    if len(placed_ids) != len(set(placed_ids)):
        check8_passed = False
        check8_details = "Duplicate job placements detected in schedule."
    else:
        placed_set = set(placed_ids)
        for j in plant.jobs:
            if j.mandatory and j.id not in placed_set:
                check8_passed = False
                check8_details = f"Mandatory job '{j.id}' is not placed."
                break

        if check8_passed:
            target_units = sum(j.units for j in plant.jobs)
            placed_units = sum(j.units for j in plant.jobs if j.id in placed_set)
            if placed_units < target_units:
                check8_passed = False
                check8_details = f"Total units placed ({placed_units}) < target units ({target_units})."

    checks.append(VerificationCheck(name="mandatory_and_target", passed=check8_passed, details=check8_details))

    # -------------------------------------------------------------
    # Check 9: Recomputed energy cost, kWh, and peak equal reported values within 0.01
    # -------------------------------------------------------------
    check9_passed = True
    check9_details = "Recomputed metrics match reported metrics within 0.01."
    if reported_metrics is not None:
        rep_kwh = float(reported_metrics.get("kwh", 0.0))
        rep_cost = float(reported_metrics.get("energy_cost", 0.0))
        rep_peak = float(reported_metrics.get("peak_kw", 0.0))

        if abs(total_kwh - rep_kwh) > 0.01:
            check9_passed = False
            check9_details = f"kWh mismatch: recomputed {total_kwh:.4f} vs reported {rep_kwh:.4f}."
        elif abs(total_energy_cost - rep_cost) > 0.01:
            check9_passed = False
            check9_details = f"Energy cost mismatch: recomputed {total_energy_cost:.4f} vs reported {rep_cost:.4f}."
        elif abs(peak_kw - rep_peak) > 0.01:
            check9_passed = False
            check9_details = f"Peak kW mismatch: recomputed {peak_kw:.4f} vs reported {rep_peak:.4f}."

    checks.append(VerificationCheck(name="metrics_match", passed=check9_passed, details=check9_details))

    # -------------------------------------------------------------
    # Check 10: Re-plan execution time - no re-inserted jobs in past
    # -------------------------------------------------------------
    if now_slot is not None:
        check10_passed = True
        check10_details = f"All non-frozen re-planned jobs start at or after now_slot ({now_slot})."
        frozen_set = set(frozen_job_ids) if frozen_job_ids else set()
        for p in schedule.placements:
            if p.job_id in frozen_set:
                continue
            if p.start < now_slot:
                check10_passed = False
                check10_details = (
                    f"Job '{p.job_id}' starts at slot {p.start}, which is before now_slot ({now_slot})."
                )
                break
        checks.append(VerificationCheck(name="replan_now_slot", passed=check10_passed, details=check10_details))

    overall_passed = all(c.passed for c in checks)
    return VerificationResult(
        passed=overall_passed,
        checks=checks,
        recomputed_metrics=recomputed,
    )
