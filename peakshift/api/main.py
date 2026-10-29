"""FastAPI main application with endpoints and error handling."""

import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

from peakshift.api.schemas import (
    BaselineInfo,
    ErrorResponse,
    GeneratePlantRequest,
    GeneratePlantResponse,
    HealthResponse,
    OptimizeRequest,
    OptimizeResponse,
    ParetoPoint,
    ParetoRequest,
    ParetoResponse,
    RefineRequest,
    RefineResponse,
    ReplanRequest,
    ReplanResponse,
    SavingsInfo,
    SweepRequest,
    SweepResponse,
    TariffListResponse,
    UploadPlantResponse,
    VerifyRequest,
    VerifyResponse,
)
from peakshift.core.generator import generate_plant
from peakshift.core.loaders import (
    load_plant_from_csv_files,
    load_plant_from_excel,
    load_plant_from_json,
)
from peakshift.core.models import Plant, Tariff
from peakshift.core.portfolio import run_portfolio
from peakshift.core.polish import refine_schedule
from peakshift.core.reasons import generate_schedule_reasons
from peakshift.core.replan import replan
from peakshift.core.sweep import run_load_sweep
from peakshift.core.tariff import get_rate_profile, load_default_tariff
from peakshift.verify.verifier import verify_schedule

app = FastAPI(
    title="PeakShift API",
    description="Factory Energy Optimization Scheduler",
    version="0.1.0",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        content = dict(exc.detail)
        if "code" not in content:
            content["code"] = "ERROR"
        if "message" not in content:
            content["message"] = "An error occurred"
        if "details" not in content:
            content["details"] = []
        if "elapsed_ms" not in content:
            content["elapsed_ms"] = 0.0
        return JSONResponse(status_code=exc.status_code, content=content)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "HTTP_ERROR",
            "message": str(exc.detail),
            "details": [str(exc.detail)],
            "elapsed_ms": 0.0,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Custom error handler returning 422 with {code, message, details}."""
    errors = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "")
        errors.append(f"{loc}: {msg}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": errors,
            "elapsed_ms": 0.0,
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "BAD_DATA",
            "message": str(exc),
            "details": [str(exc)],
            "elapsed_ms": 0.0,
        },
    )


def compute_trivial_lower_bound(plant: Plant, tariff: Tariff) -> Dict[str, Any]:
    """Computes a trivial weak lower bound assuming zero idle and cheapest run slots."""
    h = plant.slot_minutes / 60.0
    rate_profile = get_rate_profile(tariff, plant.horizon_slots, plant.slot_minutes)
    machine_map = {m.id: m for m in plant.machines}
    total_min_run_cost = 0.0

    for j in plant.jobs:
        allowed = j.allowed_machines or list(machine_map.keys())
        deadline = j.deadline_slot or plant.horizon_slots
        best_cost = None
        for mid in allowed:
            if mid not in machine_map:
                continue
            m = machine_map[mid]
            for s in range(j.release_slot, deadline - j.duration_slots + 1):
                if s + j.duration_slots > plant.horizon_slots:
                    break
                c = sum(m.run_kw * h * rate_profile[t] for t in range(s, s + j.duration_slots))
                if best_cost is None or c < best_cost:
                    best_cost = c
        if best_cost is not None:
            total_min_run_cost += best_cost

    return {
        "type": "trivial_weak",
        "proven_lower_bound": round(total_min_run_cost, 2),
        "description": "Trivial lower bound: each job placed at its cheapest feasible slots with zero idle energy.",
    }


@app.get("/api/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Lightweight health check endpoint."""
    return HealthResponse(status="ok", service="peakshift", elapsed_ms=0.0)


@app.get("/api/tariffs", response_model=TariffListResponse)
def get_tariffs() -> TariffListResponse:
    """Returns available electricity tariffs with the default TN HT-I tariff."""
    t0 = time.perf_counter()
    default_t = load_default_tariff()
    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    return TariffListResponse(
        tariffs=[default_t],
        default_tariff=default_t,
        elapsed_ms=elapsed,
    )


@app.post("/api/plants/generate", response_model=GeneratePlantResponse)
def api_generate_plant(req: GeneratePlantRequest) -> GeneratePlantResponse:
    """Generates a synthetic plant configuration from preset."""
    t0 = time.perf_counter()
    try:
        plant = generate_plant(
            preset_name=req.preset,
            utilization=req.utilization,
            seed=req.seed,
            num_machines=req.machines,
            overrides=req.overrides,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "GENERATE_FAILED", "message": str(e), "details": [str(e)]},
        )
    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    return GeneratePlantResponse(plant=plant, elapsed_ms=elapsed)


@app.post("/api/plants/upload", response_model=UploadPlantResponse)
async def api_upload_plant(
    file: Optional[UploadFile] = File(None),
    machines_file: Optional[UploadFile] = File(None),
    jobs_file: Optional[UploadFile] = File(None),
) -> UploadPlantResponse:
    """Uploads plant via single JSON/Excel file or two CSV files."""
    t0 = time.perf_counter()
    plant: Optional[Plant] = None
    errors: List[str] = []

    if file is not None:
        filename = (file.filename or "").lower()
        content = await file.read()
        if filename.endswith(".json"):
            plant, errors = load_plant_from_json(content.decode("utf-8-sig"))
        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            plant, errors = load_plant_from_excel(io.BytesIO(content))
        elif filename.endswith(".csv"):
            try:
                first_line = content.decode("utf-8-sig", errors="ignore").splitlines()[0].lower()
                is_m = "run_kw" in first_line or "idle_kw" in first_line or "machine" in first_line or "machine" in filename
                is_j = "duration" in first_line or "deadline" in first_line or "job" in first_line or "job" in filename
                if is_m and not is_j:
                    errors = ["Only machines CSV provided. Please upload both machines_file and jobs_file (jobs file is missing)."]
                elif is_j and not is_m:
                    errors = ["Only jobs CSV provided. Please upload both machines_file and jobs_file (machines file is missing)."]
                else:
                    errors = ["Single CSV uploaded. CSV uploads require both machines_file and jobs_file."]
            except Exception:
                errors = ["Single CSV uploaded. CSV uploads require both machines_file and jobs_file."]
        else:
            errors = [f"Unsupported single file type for '{file.filename}'. Must be .json or .xlsx"]
    elif machines_file is not None and jobs_file is not None:
        m_content = await machines_file.read()
        j_content = await jobs_file.read()
        plant, errors = load_plant_from_csv_files(m_content, j_content)
    else:
        errors = ["Must provide either 'file' (JSON/Excel) or both 'machines_file' and 'jobs_file' (CSV)"]

    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    if errors:
        primary_msg = errors[0] if len(errors) == 1 else "Failed to parse plant file"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": "UPLOAD_VALIDATION_ERROR",
                "message": primary_msg,
                "details": errors,
                "elapsed_ms": elapsed,
            },
        )

    return UploadPlantResponse(plant=plant, errors=errors, elapsed_ms=elapsed)


@app.post("/api/optimize", response_model=OptimizeResponse)
def api_optimize(req: OptimizeRequest) -> OptimizeResponse:
    """Runs the portfolio optimization, verification, and reasons generation."""
    t0 = time.perf_counter()

    port_res = run_portfolio(
        plant=req.plant,
        tariff=req.tariff,
        peak_cap_kw=req.config.peak_cap_kw,
        seed=req.config.seed,
        max_iterations=req.config.iterations,
    )

    # Verifier call
    ver = verify_schedule(
        plant=req.plant,
        tariff=req.tariff,
        schedule=port_res.schedule,
        reported_metrics=port_res.metrics.model_dump(),
        peak_cap_kw=req.config.peak_cap_kw,
    )

    # Decisions reasons
    decisions = generate_schedule_reasons(
        plant=req.plant,
        tariff=req.tariff,
        schedule=port_res.schedule,
        baseline_b0=port_res.baseline_b0[0],
    )

    # Bound calculation
    bound = compute_trivial_lower_bound(req.plant, req.tariff)

    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)

    return OptimizeResponse(
        baseline_b0=BaselineInfo(
            schedule=port_res.baseline_b0[0],
            metrics=port_res.baseline_b0[1],
        ),
        baseline_b1=BaselineInfo(
            schedule=port_res.baseline_b1[0],
            metrics=port_res.baseline_b1[1],
        ),
        schedule=port_res.schedule,
        metrics=port_res.metrics,
        savings_vs_b0=SavingsInfo(
            inr=port_res.savings_vs_b0_inr,
            percent=port_res.savings_vs_b0_pct,
        ),
        savings_vs_b1=SavingsInfo(
            inr=port_res.savings_vs_b1_inr,
            percent=port_res.savings_vs_b1_pct,
        ),
        winner=port_res.winner,
        decisions=decisions,
        verifier=ver,
        bound=bound,
        cap_satisfied=port_res.cap_satisfied,
        cap_excess_kw=port_res.cap_excess_kw,
        elapsed_ms=elapsed,
    )


@app.post("/api/verify", response_model=VerifyResponse)
def api_verify(req: VerifyRequest) -> VerifyResponse:
    """Verifies a schedule against all 9 physical and contract checks."""
    t0 = time.perf_counter()

    result = verify_schedule(
        plant=req.plant,
        tariff=req.tariff,
        schedule=req.schedule,
        reported_metrics=req.reported_metrics,
        peak_cap_kw=req.peak_cap_kw,
    )

    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    return VerifyResponse(
        passed=result.passed,
        checks=result.checks,
        recomputed_metrics=result.recomputed_metrics,
        elapsed_ms=elapsed,
    )


@app.post("/api/replan", response_model=ReplanResponse)
def api_replan(req: ReplanRequest) -> ReplanResponse:
    """Re-plans a schedule after a disruption event.

    Supported event types:
    - ``machine_down``: ``{type, machine_id, from_slot, until_slot}``
    - ``tariff_change``: ``{type, tariff}`` (full Tariff object)
    - ``rush_job``: ``{type, job}`` (full Job object)
    - ``job_delay``: ``{type, job_id, extra_slots}``
    """
    t0 = time.perf_counter()
    # Validate machine_id exists in plant for machine_down events
    if req.event.get("type") == "machine_down":
        mid = req.event.get("machine_id")
        plant_machine_ids = {m.id for m in req.plant.machines}
        if mid not in plant_machine_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "UNKNOWN_MACHINE",
                    "message": f"Machine '{mid}' not found in plant. Valid machine IDs: {sorted(plant_machine_ids)}",
                    "details": [f"Received machine_id='{mid}'"],
                },
            )
    try:
        result = replan(
            plant=req.plant,
            tariff=req.tariff,
            current_schedule=req.schedule,
            now_slot=req.now_slot,
            event=req.event,
            seed=req.seed,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "REPLAN_FAILED", "message": str(e), "details": [str(e)]},
        )

    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    result.elapsed_ms = elapsed
    return ReplanResponse(
        schedule=result.schedule,
        metrics_before=result.metrics_before,
        metrics_after=result.metrics_after,
        diff=result.diff,
        status=result.status,
        unplaced=result.unplaced,
        units_short=result.units_short,
        target_met=result.target_met,
        elapsed_ms=elapsed,
    )


@app.post("/api/pareto", response_model=ParetoResponse)
def api_pareto(req: ParetoRequest) -> ParetoResponse:
    """Returns a Pareto frontier of cost vs peak-kW for a list of cap values.

    For each ``cap_kw`` in ``caps``, runs the portfolio optimizer with that
    peak cap and records the resulting cost and actual peak.
    """
    t0 = time.perf_counter()
    points: list[ParetoPoint] = []

    for cap in req.caps:
        try:
            res = run_portfolio(
                plant=req.plant,
                tariff=req.tariff,
                peak_cap_kw=cap,
                seed=req.seed,
            )
            points.append(
                ParetoPoint(
                    cap_kw=cap,
                    cost=res.metrics.energy_cost,
                    peak_kw=res.metrics.peak_kw,
                    cap_satisfied=res.cap_satisfied,
                )
            )
        except Exception as e:
            logger.warning(f"pareto: cap={cap} failed: {e}")

    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    return ParetoResponse(points=points, elapsed_ms=elapsed)


@app.post("/api/sweep", response_model=SweepResponse)
def api_sweep(req: SweepRequest) -> SweepResponse:
    """Executes a factory utilization load sweep across preset and seeds."""
    t0 = time.perf_counter()
    try:
        result = run_load_sweep(
            preset_name=req.preset,
            tariff=req.tariff,
            utilizations=req.utilizations,
            seeds=req.seeds,
            max_iterations=req.max_iterations,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SWEEP_FAILED", "message": str(e), "details": [str(e)]},
        )
    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    return SweepResponse(result=result, elapsed_ms=elapsed)


@app.post("/api/refine", response_model=RefineResponse)
def api_refine(req: RefineRequest) -> RefineResponse:
    """Refines a schedule using OR-Tools CP-SAT exact formulation."""
    t0 = time.perf_counter()
    try:
        result = refine_schedule(
            plant=req.plant,
            tariff=req.tariff,
            current_schedule=req.schedule,
            peak_cap_kw=req.peak_cap_kw,
            time_limit_sec=req.time_limit_sec,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "REFINE_FAILED", "message": str(e), "details": [str(e)]},
        )
    elapsed = round((time.perf_counter() - t0) * 1000.0, 2)
    return RefineResponse(result=result, elapsed_ms=elapsed)


# ── Test Cases Directory Mounting ────────────────────────────────────────────
test_cases_dir = Path(__file__).resolve().parent.parent.parent / "test_cases"
if test_cases_dir.exists():
    app.mount("/test-cases", StaticFiles(directory=str(test_cases_dir), html=True), name="test_cases")

# ── Static UI Mounting ───────────────────────────────────────────────────────
static_dir = Path(__file__).resolve().parent.parent.parent / "app" / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
