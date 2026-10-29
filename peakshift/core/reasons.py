"""Decision explanation and reason generation."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from peakshift.core.models import Machine, Placement, Plant, Schedule, Segment, Tariff
from peakshift.core.tariff import get_rate_profile
from peakshift.core.timegrid import slot_hours


class DecisionReason(BaseModel):
    code: str
    text: str
    saving_inr: float
    job_id: Optional[str] = None
    machine_id: Optional[str] = None


def generate_schedule_reasons(
    plant: Plant,
    tariff: Tariff,
    schedule: Schedule,
    baseline_b0: Schedule,
    machine_results: Optional[Dict[str, Any]] = None,
) -> List[DecisionReason]:
    """Generates human-readable explanations and savings for scheduling decisions."""
    reasons: List[DecisionReason] = []
    h = slot_hours(plant.slot_minutes)
    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)
    machine_map = {m.id: m for m in plant.machines}

    # 1. Job placement shifts vs B0
    b0_places = {p.job_id: p for p in baseline_b0.placements}
    opt_places = {p.job_id: p for p in schedule.placements}

    for j_id, opt_p in opt_places.items():
        if j_id in b0_places:
            b0_p = b0_places[j_id]
            if opt_p.machine_id != b0_p.machine_id:
                # Machine consolidation or shift
                reasons.append(
                    DecisionReason(
                        code="CONSOLIDATED_TO_FREE_MACHINE",
                        text=f"Job {j_id} moved from machine {b0_p.machine_id} to {opt_p.machine_id} to consolidate load.",
                        saving_inr=0.0,
                        job_id=j_id,
                        machine_id=opt_p.machine_id,
                    )
                )
            elif opt_p.start != b0_p.start:
                # Time shift
                m = machine_map[opt_p.machine_id]
                b0_cost = sum(m.run_kw * h * rate_profile[t] for t in range(b0_p.start, b0_p.end))
                opt_cost = sum(m.run_kw * h * rate_profile[t] for t in range(opt_p.start, opt_p.end))
                saving = round(b0_cost - opt_cost, 2)
                if saving > 0:
                    reasons.append(
                        DecisionReason(
                            code="SHIFT_OFF_PEAK",
                            text=(
                                f"Job {j_id} shifted from slot {b0_p.start} to {opt_p.start} "
                                f"during cheaper tariff hours, saving ₹{saving}."
                            ),
                            saving_inr=saving,
                            job_id=j_id,
                            machine_id=opt_p.machine_id,
                        )
                    )

    # 2. Machine gap decisions (off vs idle)
    for m in plant.machines:
        segs = schedule.segments.get(m.id, [])
        w = m.warmup_slots
        for i, seg in enumerate(segs):
            if seg.state == "off":
                # Check if this was an internal gap or leading gap
                if seg.end < plant.horizon_slots:
                    gap_hours = (seg.end - seg.start) * h
                    # What idle would cost
                    idle_cost = sum(m.idle_kw * h * rate_profile[t] for t in range(seg.start, seg.end))
                    restart_slot = seg.end
                    # If followed by warmup
                    if i + 1 < len(segs) and segs[i + 1].state == "warmup":
                        restart_slot = segs[i + 1].start
                    rate_restart = rate_profile[restart_slot] if restart_slot < len(rate_profile) else rate_profile[-1]
                    restart_cost = m.restart_kwh * rate_restart
                    saving = round(idle_cost - restart_cost, 2)
                    if saving > 0:
                        reasons.append(
                            DecisionReason(
                                code="GAP_OFF_BREAK_EVEN",
                                text=(
                                    f"Machine {m.id} switched off for {gap_hours:.1f} h: "
                                    f"idle would cost ₹{idle_cost:.1f}, restart costs ₹{restart_cost:.1f}, "
                                    f"saving ₹{saving}."
                                ),
                                saving_inr=saving,
                                machine_id=m.id,
                            )
                        )
            elif seg.state == "idle":
                # Kept idle
                if seg.start > 0 and seg.end < plant.horizon_slots:
                    gap_hours = (seg.end - seg.start) * h
                    idle_cost = sum(m.idle_kw * h * rate_profile[t] for t in range(seg.start, seg.end))
                    restart_slot = seg.end - w if w > 0 else seg.end
                    rate_restart = rate_profile[restart_slot] if restart_slot < len(rate_profile) else rate_profile[-1]
                    restart_cost = m.restart_kwh * rate_restart
                    diff = round(restart_cost - idle_cost, 2)
                    if diff > 0:
                        reasons.append(
                            DecisionReason(
                                code="GAP_KEPT_IDLE_RESTART_COSTS_MORE",
                                text=(
                                    f"Machine {m.id} kept idle for {gap_hours:.1f} h: "
                                    f"idle cost ₹{idle_cost:.1f} is less than restart cost ₹{restart_cost:.1f}."
                                ),
                                saving_inr=0.0,
                                machine_id=m.id,
                            )
                        )

    return reasons
