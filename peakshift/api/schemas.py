"""API Request and Response schemas."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from peakshift.core.cost import ScheduleMetrics
from peakshift.core.models import Plant, Schedule, Tariff
from peakshift.core.polish import PolishResult
from peakshift.core.reasons import DecisionReason
from peakshift.core.replan import JobDiff
from peakshift.core.sweep import SweepResult
from peakshift.verify.verifier import VerificationCheck, VerificationResult


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: List[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "peakshift"
    elapsed_ms: float = 0.0


class TariffListResponse(BaseModel):
    tariffs: List[Tariff]
    default_tariff: Tariff
    elapsed_ms: float = 0.0


class GeneratePlantRequest(BaseModel):
    preset: str
    utilization: float = Field(..., ge=0.05, le=1.0)
    seed: int = 42
    machines: Optional[int] = Field(default=None, ge=1)
    overrides: Optional[Dict[str, Any]] = None


class GeneratePlantResponse(BaseModel):
    plant: Plant
    elapsed_ms: float = 0.0


class UploadPlantResponse(BaseModel):
    plant: Optional[Plant] = None
    errors: List[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class OptimizeConfig(BaseModel):
    peak_cap_kw: Optional[float] = None
    iterations: Optional[int] = None
    seed: int = 42


class OptimizeRequest(BaseModel):
    plant: Plant
    tariff: Tariff
    config: OptimizeConfig = Field(default_factory=OptimizeConfig)


class SavingsInfo(BaseModel):
    inr: float
    percent: float


class BaselineInfo(BaseModel):
    schedule: Schedule
    metrics: ScheduleMetrics


class OptimizeResponse(BaseModel):
    baseline_b0: BaselineInfo
    baseline_b1: BaselineInfo
    schedule: Schedule
    metrics: ScheduleMetrics
    savings_vs_b0: SavingsInfo
    savings_vs_b1: SavingsInfo
    winner: str
    decisions: List[DecisionReason]
    verifier: VerificationResult
    bound: Optional[Dict[str, Any]] = None
    cap_satisfied: bool = True
    cap_excess_kw: float = 0.0
    elapsed_ms: float = 0.0


class VerifyRequest(BaseModel):
    plant: Plant
    tariff: Tariff
    schedule: Schedule
    reported_metrics: Optional[Dict[str, Any]] = None
    peak_cap_kw: Optional[float] = None


class VerifyResponse(BaseModel):
    passed: bool
    checks: List[VerificationCheck]
    recomputed_metrics: Dict[str, float] = Field(default_factory=dict)
    elapsed_ms: float = 0.0


# ── M6: Replan and Pareto ────────────────────────────────────────────────────

class ReplanRequest(BaseModel):
    plant: Plant
    tariff: Tariff
    schedule: Schedule
    now_slot: int = Field(..., ge=0)
    event: Dict[str, Any]
    seed: int = 42


class ReplanResponse(BaseModel):
    schedule: Schedule
    metrics_before: ScheduleMetrics
    metrics_after: ScheduleMetrics
    diff: List[JobDiff]
    status: str                 # "ok" | "target_at_risk" | "error"
    unplaced: List[str]
    units_short: int
    target_met: bool
    elapsed_ms: float = 0.0


class ParetoRequest(BaseModel):
    plant: Plant
    tariff: Tariff
    caps: List[float] = Field(..., min_length=1)
    seed: int = 42


class ParetoPoint(BaseModel):
    cap_kw: float
    cost: float
    peak_kw: float
    cap_satisfied: bool


class ParetoResponse(BaseModel):
    points: List[ParetoPoint]
    elapsed_ms: float = 0.0


# ── M8: Sweep & Refine ───────────────────────────────────────────────────────

class SweepRequest(BaseModel):
    preset: str
    tariff: Tariff
    utilizations: Optional[List[float]] = None
    seeds: Optional[List[int]] = None
    max_iterations: Optional[int] = None


class SweepResponse(BaseModel):
    result: SweepResult
    elapsed_ms: float = 0.0


class RefineRequest(BaseModel):
    plant: Plant
    tariff: Tariff
    schedule: Schedule
    peak_cap_kw: Optional[float] = None
    time_limit_sec: float = 20.0


class RefineResponse(BaseModel):
    result: PolishResult
    elapsed_ms: float = 0.0
