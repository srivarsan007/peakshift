"""Tests for Milestone M2: Generator, Presets, and Determinism."""

import pytest
from peakshift.core.generator import PRESET_CONFIGS, generate_plant, save_demo_presets
from peakshift.core.loaders import load_plant_from_json


def test_generator_determinism():
    """Non-negotiable #4: Same input plus same seed gives the same output."""
    plant1 = generate_plant("machining", utilization=0.5, seed=123)
    plant2 = generate_plant("machining", utilization=0.5, seed=123)
    assert plant1.model_dump() == plant2.model_dump()

    # Different seeds give different job sequences
    plant3 = generate_plant("machining", utilization=0.5, seed=456)
    assert plant1.model_dump() != plant3.model_dump()


def test_preset_shapes_and_power_ratios():
    """Verify that presets adhere to the specifications in Section 9."""
    for preset_name, cfg in PRESET_CONFIGS.items():
        ratio = cfg["idle_kw"] / cfg["run_kw"]
        assert 0.40 <= ratio <= 0.60, f"Preset {preset_name} ratio {ratio} out of [0.4, 0.6]"

        plant = generate_plant(preset_name, utilization=0.5, seed=42)
        assert len(plant.machines) == cfg["num_machines"]
        for m in plant.machines:
            assert m.run_kw == cfg["run_kw"]
            assert m.idle_kw == cfg["idle_kw"]
            assert m.restart_kwh == cfg["restart_kwh"]
            assert m.warmup_slots == cfg["warmup_slots"]
            assert m.min_off_slots == cfg["min_off_slots"]

        for j in plant.jobs:
            assert cfg["duration_min"] <= j.duration_slots <= cfg["duration_max"] or j == plant.jobs[-1]
            assert j.release_slot >= 0
            assert j.deadline_slot <= plant.horizon_slots
            assert j.duration_slots <= (j.deadline_slot - j.release_slot)


def test_utilization_accuracy():
    """Verify that generated jobs match the requested utilization target."""
    for util in [0.3, 0.5, 0.7, 0.85]:
        plant = generate_plant("textile", utilization=util, seed=10)
        total_capacity = len(plant.machines) * plant.horizon_slots
        actual_busy = sum(j.duration_slots for j in plant.jobs)
        actual_util = actual_busy / total_capacity
        assert actual_util == pytest.approx(util, abs=0.01)


def test_priority_and_window_distributions():
    """Verify that about 15% have priority 1 and about 30% have tight windows."""
    plant = generate_plant("machining", utilization=0.85, seed=999)
    total_jobs = len(plant.jobs)

    p1_jobs = sum(1 for j in plant.jobs if j.priority == 1)
    p1_ratio = p1_jobs / total_jobs
    # Allow statistical spread on ~60 jobs
    assert 0.05 <= p1_ratio <= 0.25

    narrow_window_jobs = sum(
        1 for j in plant.jobs if (j.deadline_slot - j.release_slot) < plant.horizon_slots
    )
    narrow_ratio = narrow_window_jobs / total_jobs
    assert 0.15 <= narrow_ratio <= 0.45


def test_saved_demo_presets_loadable():
    """Verify that saved demo preset JSON files match Section 14 and load cleanly."""
    saved_files = save_demo_presets()
    assert len(saved_files) == 3

    expected_names = {"textile_mid_load.json", "machining_high_load.json", "food_low_load.json"}
    actual_names = {f.name for f in saved_files}
    assert expected_names == actual_names

    for file_path in saved_files:
        plant, errors = load_plant_from_json(file_path)
        assert errors == []
        assert plant is not None
        assert len(plant.machines) > 0
        assert len(plant.jobs) > 0
