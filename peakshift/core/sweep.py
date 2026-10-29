"""Load sweep analysis across utilization levels and seeds."""

import logging
import statistics
import time
from typing import List, Optional

from pydantic import BaseModel, Field

from peakshift.core.generator import generate_plant
from peakshift.core.models import Tariff
from peakshift.core.portfolio import run_portfolio

logger = logging.getLogger(__name__)


class SweepLevelResult(BaseModel):
    utilization: float
    seeds_count: int
    # Savings vs B0 (%)
    mean_savings_b0_pct: float
    min_savings_b0_pct: float
    max_savings_b0_pct: float
    std_savings_b0_pct: float
    # Savings vs B1 (%)
    mean_savings_b1_pct: float
    min_savings_b1_pct: float
    max_savings_b1_pct: float
    std_savings_b1_pct: float


class SweepResult(BaseModel):
    preset: str
    levels: List[SweepLevelResult]
    elapsed_ms: float = 0.0


def run_load_sweep(
    preset_name: str,
    tariff: Tariff,
    utilizations: Optional[List[float]] = None,
    seeds: Optional[List[int]] = None,
    max_iterations: Optional[int] = None,
) -> SweepResult:
    """Runs a load sweep across multiple utilization levels and seeds.

    Default: 7 utilization levels (0.3 to 0.9) x 5 seeds = 35 instances.
    """
    t0 = time.perf_counter()

    if utilizations is None:
        utilizations = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    if seeds is None:
        seeds = [42, 43, 44, 45, 46]

    # For fast execution during sweeps, if max_iterations is not provided, use 10,000
    effective_iter = max_iterations if max_iterations is not None else 10000

    levels: List[SweepLevelResult] = []

    for u in utilizations:
        b0_savings: List[float] = []
        b1_savings: List[float] = []

        for s in seeds:
            try:
                plant = generate_plant(preset_name=preset_name, utilization=u, seed=s)
                res = run_portfolio(
                    plant=plant,
                    tariff=tariff,
                    seed=s,
                    max_iterations=effective_iter,
                )
                b0_savings.append(res.savings_vs_b0_pct)
                b1_savings.append(res.savings_vs_b1_pct)
            except Exception as e:
                logger.warning(f"sweep failed for preset={preset_name}, u={u}, seed={s}: {e}")

        if not b0_savings:
            continue

        mean_b0 = statistics.mean(b0_savings)
        min_b0 = min(b0_savings)
        max_b0 = max(b0_savings)
        std_b0 = statistics.stdev(b0_savings) if len(b0_savings) > 1 else 0.0

        mean_b1 = statistics.mean(b1_savings)
        min_b1 = min(b1_savings)
        max_b1 = max(b1_savings)
        std_b1 = statistics.stdev(b1_savings) if len(b1_savings) > 1 else 0.0

        levels.append(
            SweepLevelResult(
                utilization=round(u, 2),
                seeds_count=len(b0_savings),
                mean_savings_b0_pct=round(mean_b0, 2),
                min_savings_b0_pct=round(min_b0, 2),
                max_savings_b0_pct=round(max_b0, 2),
                std_savings_b0_pct=round(std_b0, 2),
                mean_savings_b1_pct=round(mean_b1, 2),
                min_savings_b1_pct=round(min_b1, 2),
                max_savings_b1_pct=round(max_b1, 2),
                std_savings_b1_pct=round(std_b1, 2),
            )
        )

    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    return SweepResult(preset=preset_name, levels=levels, elapsed_ms=elapsed)
