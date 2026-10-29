import logging
from typing import List, Optional, Tuple, Literal, Dict
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class TariffWindow(BaseModel):
    start_hour: float = Field(..., ge=0.0, le=24.0, description="Start hour [0, 24]")
    end_hour: float = Field(..., ge=0.0, le=24.0, description="End hour [0, 24]")
    multiplier: float = Field(..., gt=0.0, description="Tariff multiplier")

    @model_validator(mode="after")
    def validate_window(self) -> "TariffWindow":
        if self.start_hour >= self.end_hour:
            raise ValueError(
                f"Tariff window cannot cross midnight or have start >= end: "
                f"[{self.start_hour}, {self.end_hour})"
            )
        return self


class Tariff(BaseModel):
    name: str
    source: str = ""
    base_rate_per_kwh: float = Field(..., gt=0.0)
    windows: List[TariffWindow] = Field(default_factory=list)
    tax_rate: float = Field(default=0.05, ge=0.0)
    demand_rate_per_kva: Optional[float] = Field(default=None, ge=0.0)
    demand_floor_fraction: float = Field(default=0.9, ge=0.0, le=1.0)
    contract_exceed_penalty_per_kva: float = Field(default=0.0, ge=0.0)

    @field_validator("windows")
    @classmethod
    def validate_windows_no_overlap(cls, windows: List[TariffWindow]) -> List[TariffWindow]:
        sorted_windows = sorted(windows, key=lambda w: w.start_hour)
        for i in range(len(sorted_windows) - 1):
            curr_w = sorted_windows[i]
            next_w = sorted_windows[i + 1]
            if curr_w.end_hour > next_w.start_hour:
                raise ValueError(
                    f"Overlapping tariff windows detected: [{curr_w.start_hour}, {curr_w.end_hour}) "
                    f"and [{next_w.start_hour}, {next_w.end_hour})"
                )
        return sorted_windows


class Machine(BaseModel):
    id: str
    run_kw: float = Field(..., ge=0.0, description="Power during active run (kW)")
    idle_kw: float = Field(..., ge=0.0, description="Power during idle state (kW)")
    restart_kwh: float = Field(..., ge=0.0, description="Energy to restart from off (kWh)")
    warmup_slots: int = Field(default=0, ge=0, description="Warmup duration before run (slots)")
    min_off_slots: int = Field(default=0, ge=0, description="Minimum slots required to turn off")
    max_switch_cycles: Optional[int] = Field(default=None, ge=0, description="Max switch-off cycles permitted")
    initial_state: Literal["idle", "off"] = Field(default="idle", description="Initial machine state")
    downtime: List[Tuple[int, int]] = Field(default_factory=list, description="List of [start_slot, end_slot) unavailable")

    @model_validator(mode="after")
    def check_machine(self) -> "Machine":
        if self.idle_kw > self.run_kw:
            logger.warning(
                f"Machine {self.id}: idle_kw ({self.idle_kw}) > run_kw ({self.run_kw})"
            )
        for start, end in self.downtime:
            if start >= end or start < 0:
                raise ValueError(
                    f"Machine {self.id} invalid downtime [{start}, {end}): start must be < end and >= 0"
                )
        return self


class Job(BaseModel):
    id: str
    duration_slots: int = Field(..., ge=1, description="Duration in slots")
    release_slot: int = Field(default=0, ge=0, description="Earliest slot available")
    deadline_slot: Optional[int] = Field(default=None, ge=1, description="Latest slot exclusive")
    allowed_machines: Optional[List[str]] = Field(default=None, description="Machines allowed to run this job")
    priority: int = Field(default=0, description="Job priority (higher first)")
    units: int = Field(default=1, ge=1, description="Production units produced")
    mandatory: bool = Field(default=True, description="Whether job is mandatory")

    @model_validator(mode="after")
    def validate_job_bounds(self) -> "Job":
        if self.deadline_slot is not None:
            if self.release_slot >= self.deadline_slot:
                raise ValueError(
                    f"Job {self.id}: release slot {self.release_slot} is after deadline slot {self.deadline_slot} (release_slot >= deadline_slot)"
                )
            window = self.deadline_slot - self.release_slot
            if self.duration_slots > window:
                raise ValueError(
                    f"Job {self.id}: duration ({self.duration_slots}) larger than feasible window ({window})"
                )
        return self


class Plant(BaseModel):
    name: str
    horizon_slots: int = Field(default=48, ge=1, description="Total horizon length in slots")
    slot_minutes: int = Field(default=30, ge=1, description="Minutes per slot")
    contract_kva: Optional[float] = Field(default=None, ge=0.0, description="Contract demand in kVA")
    power_factor: float = Field(default=0.95, gt=0.0, le=1.0, description="Power factor")
    machines: List[Machine] = Field(..., description="List of machines")
    jobs: List[Job] = Field(..., description="List of jobs")

    @model_validator(mode="after")
    def validate_plant_integrity(self) -> "Plant":
        # 1. slot_minutes must divide 60
        if 60 % self.slot_minutes != 0:
            raise ValueError(
                f"slot_minutes ({self.slot_minutes}) must divide 60 evenly"
            )

        # 2. Horizon <= 3 days (3 * 24 * 60 minutes)
        total_minutes = self.horizon_slots * self.slot_minutes
        if total_minutes > 3 * 24 * 60:
            raise ValueError(
                f"Horizon length ({total_minutes} mins) exceeds maximum 3 days (4320 mins)"
            )

        # 3. Non-empty machines and jobs
        if not self.machines:
            raise ValueError("Plant must contain at least one machine")
        if not self.jobs:
            raise ValueError("Plant must contain at least one job")

        # 4. Duplicate machine IDs
        machine_ids = set()
        for m in self.machines:
            if m.id in machine_ids:
                raise ValueError(f"Duplicate machine id: '{m.id}'")
            machine_ids.add(m.id)

        # 5. Duplicate job IDs and job consistency with plant
        job_ids = set()
        for j in self.jobs:
            if j.id in job_ids:
                raise ValueError(f"Duplicate job id: '{j.id}'")
            job_ids.add(j.id)

            # Assign default deadline if not set
            if j.deadline_slot is None:
                j.deadline_slot = self.horizon_slots
            elif j.deadline_slot > self.horizon_slots:
                raise ValueError(
                    f"Job {j.id}: deadline_slot ({j.deadline_slot}) > horizon ({self.horizon_slots})"
                )

            # Re-check release vs deadline in case default was set
            if j.release_slot >= j.deadline_slot:
                raise ValueError(
                    f"Job {j.id}: release slot {j.release_slot} is after deadline slot {j.deadline_slot} (release_slot >= deadline_slot)"
                )
            if j.duration_slots > (j.deadline_slot - j.release_slot):
                raise ValueError(
                    f"Job {j.id}: duration ({j.duration_slots}) larger than feasible window ({j.deadline_slot - j.release_slot})"
                )

            # Assign default allowed_machines if None
            if j.allowed_machines is None or len(j.allowed_machines) == 0:
                j.allowed_machines = list(machine_ids)
            else:
                for mid in j.allowed_machines:
                    if mid not in machine_ids:
                        raise ValueError(
                            f"Job {j.id}: machine '{mid}' does not exist (unknown machine in allowed_machines)"
                        )

        return self


class Segment(BaseModel):
    state: Literal["run", "idle", "off", "warmup"]
    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)
    job_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_segment_bounds(self) -> "Segment":
        if self.start >= self.end:
            raise ValueError(f"Segment start ({self.start}) must be < end ({self.end})")
        if self.state == "run" and not self.job_id:
            raise ValueError("Run segment must have an associated job_id")
        return self


class Placement(BaseModel):
    job_id: str
    machine_id: str
    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_placement_bounds(self) -> "Placement":
        if self.start >= self.end:
            raise ValueError(f"Placement start ({self.start}) must be < end ({self.end})")
        return self


class Schedule(BaseModel):
    segments: Dict[str, List[Segment]] = Field(
        ..., description="Per machine list of non-overlapping contiguous segments covering horizon"
    )
    placements: List[Placement] = Field(
        ..., description="List of job placements"
    )
    unplaced: List[str] = Field(
        default_factory=list, description="IDs of unplaced jobs"
    )
