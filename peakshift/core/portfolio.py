"""Portfolio scheduling algorithm: runs H1, H2, H3, improves each, and enforces cost <= B1."""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from peakshift.core.baseline import build_b0_schedule, build_b1_schedule
from peakshift.core.construct import construct_h1, construct_h2, construct_h3
from peakshift.core.cost import ScheduleMetrics
from peakshift.core.improve import improve_schedule
from peakshift.core.machine_cost import MachineScheduleResult
from peakshift.core.models import Plant, Schedule, Tariff


@dataclass
class PortfolioResult:
    schedule: Schedule
    metrics: ScheduleMetrics
    baseline_b0: Tuple[Schedule, ScheduleMetrics]
    baseline_b1: Tuple[Schedule, ScheduleMetrics]
    savings_vs_b0_inr: float
    savings_vs_b0_pct: float
    savings_vs_b1_inr: float
    savings_vs_b1_pct: float
    winner: str
    cap_satisfied: bool = True
    cap_excess_kw: float = 0.0


def run_portfolio(
    plant: Plant,
    tariff: Tariff,
    peak_cap_kw: Optional[float] = None,
    seed: int = 42,
    max_iterations: Optional[int] = None,
) -> PortfolioResult:
    """Runs the full portfolio algorithm with the guarantee: result cost <= B1 cost."""
    # 1. Compute Baselines B0 and B1
    s_b0, m_b0 = build_b0_schedule(plant, tariff)
    s_b1, m_b1 = build_b1_schedule(plant, tariff)

    # 2. Run Constructive Heuristics
    candidates: List[Tuple[str, Schedule, ScheduleMetrics, Dict[str, MachineScheduleResult]]] = []

    # H1
    s_h1, m_h1, res_h1 = construct_h1(plant, tariff, peak_cap_kw=peak_cap_kw)
    candidates.append(("H1", s_h1, m_h1, res_h1))

    # H2
    s_h2, m_h2, res_h2 = construct_h2(plant, tariff, peak_cap_kw=peak_cap_kw)
    candidates.append(("H2", s_h2, m_h2, res_h2))

    # H3
    s_h3, m_h3, res_h3 = construct_h3(plant, tariff, peak_cap_kw=peak_cap_kw)
    candidates.append(("H3", s_h3, m_h3, res_h3))

    # 3. Apply Improvement to Each Constructive Heuristic
    improved_candidates: List[Tuple[str, Schedule, ScheduleMetrics]] = []
    # Always include B1 as candidate so guarantee holds
    improved_candidates.append(("B1", s_b1, m_b1))

    for name, cand_s, cand_m, cand_res in candidates:
        improved_candidates.append((name, cand_s, cand_m))
        s_imp, m_imp, _ = improve_schedule(
            plant=plant,
            tariff=tariff,
            schedule=cand_s,
            machine_results=cand_res,
            peak_cap_kw=peak_cap_kw,
            seed=seed,
            max_iterations=max_iterations,
        )
        improved_candidates.append((f"{name}+improve", s_imp, m_imp))

    # 4. Selection:
    # First priority: Target met (all mandatory jobs placed)
    # Second priority: Lowest energy cost
    # Guarantee: result cost <= B1 cost
    mandatory_candidates = [c for c in improved_candidates if c[2].target_met]
    if not mandatory_candidates:
        # If no candidate placed all mandatory jobs, sort by (unplaced count, cost)
        sorted_candidates = sorted(
            improved_candidates,
            key=lambda c: (len(c[1].unplaced), c[2].energy_cost),
        )
        winner_name, best_schedule, best_metrics = sorted_candidates[0]
    else:
        # Sort by energy cost ascending
        sorted_candidates = sorted(
            mandatory_candidates,
            key=lambda c: c[2].energy_cost,
        )
        winner_name, best_schedule, best_metrics = sorted_candidates[0]

        # Enforce guarantee: result cost <= B1 cost (if B1 met target)
        if m_b1.target_met and best_metrics.energy_cost > m_b1.energy_cost:
            winner_name = "B1"
            best_schedule = s_b1
            best_metrics = m_b1

    # 5. Peak Cap Check
    cap_satisfied = True
    cap_excess = 0.0
    if peak_cap_kw is not None:
        if best_metrics.peak_kw > peak_cap_kw + 1e-4:
            cap_satisfied = False
            cap_excess = round(best_metrics.peak_kw - peak_cap_kw, 4)

    # 6. Savings Calculations
    b0_cost = m_b0.energy_cost
    b1_cost = m_b1.energy_cost
    best_cost = best_metrics.energy_cost

    savings_vs_b0_inr = round(b0_cost - best_cost, 4)
    savings_vs_b0_pct = round((savings_vs_b0_inr / b0_cost * 100.0) if b0_cost > 0 else 0.0, 2)

    savings_vs_b1_inr = round(b1_cost - best_cost, 4)
    savings_vs_b1_pct = round((savings_vs_b1_inr / b1_cost * 100.0) if b1_cost > 0 else 0.0, 2)

    return PortfolioResult(
        schedule=best_schedule,
        metrics=best_metrics,
        baseline_b0=(s_b0, m_b0),
        baseline_b1=(s_b1, m_b1),
        savings_vs_b0_inr=savings_vs_b0_inr,
        savings_vs_b0_pct=savings_vs_b0_pct,
        savings_vs_b1_inr=savings_vs_b1_inr,
        savings_vs_b1_pct=savings_vs_b1_pct,
        winner=winner_name,
        cap_satisfied=cap_satisfied,
        cap_excess_kw=cap_excess,
    )
