"""Plant generator and standard presets based on published machine ranges."""

import json
from pathlib import Path
import random
from typing import Any, Dict, List, Optional

from peakshift.core.models import Job, Machine, Plant

PRESET_CONFIGS = {
    "machining": {
        "num_machines": 8,
        "run_kw": 12.0,
        "idle_kw": 7.0,
        "restart_kwh": 3.0,
        "warmup_slots": 1,
        "min_off_slots": 1,
        "duration_min": 2,
        "duration_max": 6,
    },
    "textile": {
        "num_machines": 10,
        "run_kw": 22.0,
        "idle_kw": 9.0,
        "restart_kwh": 10.0,
        "warmup_slots": 2,
        "min_off_slots": 2,
        "duration_min": 2,
        "duration_max": 6,
    },
    "food_lines": {
        "num_machines": 6,
        "run_kw": 30.0,
        "idle_kw": 12.0,
        "restart_kwh": 15.0,
        "warmup_slots": 2,
        "min_off_slots": 2,
        "duration_min": 3,
        "duration_max": 8,
    },
}


def generate_plant(
    preset_name: str,
    utilization: float,
    seed: int = 42,
    num_machines: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
    horizon_slots: int = 48,
    slot_minutes: int = 30,
) -> Plant:
    """Generates a synthetic deterministic Plant from a preset and target utilization.
    
    utilization: share of total machine-slots busy [0.1, 1.0].
    seed: random seed for complete determinism.
    """
    if preset_name not in PRESET_CONFIGS:
        raise ValueError(
            f"Unknown preset '{preset_name}'. Available: {list(PRESET_CONFIGS.keys())}"
        )

    cfg = dict(PRESET_CONFIGS[preset_name])
    if overrides:
        cfg.update(overrides)

    rng = random.Random(seed)

    m_count = num_machines if num_machines is not None else cfg["num_machines"]
    machines: List[Machine] = []
    for i in range(m_count):
        machines.append(
            Machine(
                id=f"M{i + 1}",
                run_kw=cfg["run_kw"],
                idle_kw=cfg["idle_kw"],
                restart_kwh=cfg["restart_kwh"],
                warmup_slots=cfg["warmup_slots"],
                min_off_slots=cfg["min_off_slots"],
                initial_state="idle",
            )
        )

    total_capacity_slots = m_count * horizon_slots
    target_busy_slots = int(round(total_capacity_slots * utilization))

    jobs: List[Job] = []
    current_busy_slots = 0
    job_idx = 1

    d_min = cfg["duration_min"]
    d_max = cfg["duration_max"]

    while current_busy_slots < target_busy_slots:
        remaining = target_busy_slots - current_busy_slots
        if remaining < d_min:
            # If remaining slots < d_min, add them to the previous job or create a final job
            if jobs:
                jobs[-1].duration_slots += remaining
                # Check if deadline window needs expanding
                if jobs[-1].deadline_slot is not None:
                    jobs[-1].deadline_slot = max(
                        jobs[-1].deadline_slot,
                        jobs[-1].release_slot + jobs[-1].duration_slots,
                    )
            else:
                jobs.append(
                    Job(
                        id=f"J{job_idx}",
                        duration_slots=remaining,
                        release_slot=0,
                        deadline_slot=horizon_slots,
                    )
                )
            break

        dur = rng.randint(d_min, min(d_max, remaining))

        # 15% priority 1
        prio = 1 if rng.random() < 0.15 else 0

        # 30% have a narrower deadline window than the full day
        if rng.random() < 0.30:
            extra = rng.randint(2, max(2, (horizon_slots // 3)))
            window_len = min(horizon_slots, dur + extra)
            max_rel = horizon_slots - window_len
            rel = rng.randint(0, max_rel)
            dead = rel + window_len
        else:
            rel = 0
            dead = horizon_slots

        jobs.append(
            Job(
                id=f"J{job_idx}",
                duration_slots=dur,
                release_slot=rel,
                deadline_slot=dead,
                priority=prio,
                units=1,
                mandatory=True,
            )
        )
        current_busy_slots += dur
        job_idx += 1

    plant_name = f"{preset_name}_{int(utilization * 100)}util_seed{seed}"
    return Plant(
        name=plant_name,
        horizon_slots=horizon_slots,
        slot_minutes=slot_minutes,
        machines=machines,
        jobs=jobs,
    )


def save_demo_presets(output_dir: Optional[Path] = None) -> List[Path]:
    """Generates and writes standard demo presets to data/presets/."""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "data" / "presets"
    output_dir.mkdir(parents=True, exist_ok=True)

    demo_specs = [
        ("textile_mid_load", "textile", 0.50, 42),
        ("machining_high_load", "machining", 0.85, 42),
        ("food_low_load", "food_lines", 0.30, 42),
    ]

    saved_paths: List[Path] = []
    for filename, preset, util, seed in demo_specs:
        plant = generate_plant(preset_name=preset, utilization=util, seed=seed)
        dest = output_dir / f"{filename}.json"
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(plant.model_dump(), f, indent=2)
        saved_paths.append(dest)

    return saved_paths
