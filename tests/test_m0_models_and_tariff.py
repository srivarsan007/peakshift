"""Tests for Milestone M0: models, validation, tariff lookup, timegrid, and loaders."""

import io
import json
import logging
from pathlib import Path
import pytest
import pandas as pd

from peakshift.core.models import Job, Machine, Plant, Tariff, TariffWindow
from peakshift.core.tariff import (
    get_multiplier,
    get_rate_at_slot,
    get_rate_profile,
    load_default_tariff,
    load_tariff_from_json,
)
from peakshift.core.timegrid import (
    slot_hours,
    slot_to_day_and_hour,
    slot_to_hour_of_day,
    time_to_slot,
)
from peakshift.core.loaders import (
    load_plant_from_dataframes,
    load_plant_from_excel,
    load_plant_from_csv_files,
    load_plant_from_json,
    load_tariff_from_hourly_csv,
)


def sample_valid_plant_dict():
    return {
        "name": "Test Plant",
        "horizon_slots": 48,
        "slot_minutes": 30,
        "contract_kva": 200.0,
        "power_factor": 0.95,
        "machines": [
            {
                "id": "M1",
                "run_kw": 12.0,
                "idle_kw": 5.0,
                "restart_kwh": 3.0,
                "warmup_slots": 1,
                "min_off_slots": 2,
            },
            {
                "id": "M2",
                "run_kw": 20.0,
                "idle_kw": 8.0,
                "restart_kwh": 5.0,
                "warmup_slots": 0,
                "min_off_slots": 0,
            },
        ],
        "jobs": [
            {
                "id": "J1",
                "duration_slots": 4,
                "release_slot": 0,
                "deadline_slot": 24,
                "allowed_machines": ["M1", "M2"],
                "priority": 1,
                "units": 2,
            },
            {
                "id": "J2",
                "duration_slots": 6,
                "release_slot": 10,
                "deadline_slot": 48,
            },
        ],
    }


# ==========================================
# 1. Models & Validation Tests
# ==========================================

def test_valid_plant_defaults():
    plant_dict = sample_valid_plant_dict()
    plant = Plant.model_validate(plant_dict)
    assert plant.name == "Test Plant"
    assert len(plant.machines) == 2
    assert len(plant.jobs) == 2
    # J2 should have received default allowed_machines (all machines)
    j2 = next(j for j in plant.jobs if j.id == "J2")
    assert set(j2.allowed_machines) == {"M1", "M2"}
    assert j2.deadline_slot == 48


def test_reject_empty_machines():
    data = sample_valid_plant_dict()
    data["machines"] = []
    with pytest.raises(ValueError, match="at least one machine"):
        Plant.model_validate(data)


def test_reject_empty_jobs():
    data = sample_valid_plant_dict()
    data["jobs"] = []
    with pytest.raises(ValueError, match="at least one job"):
        Plant.model_validate(data)


def test_reject_duplicate_machine_ids():
    data = sample_valid_plant_dict()
    data["machines"].append(data["machines"][0])
    with pytest.raises(ValueError, match="Duplicate machine id"):
        Plant.model_validate(data)


def test_reject_duplicate_job_ids():
    data = sample_valid_plant_dict()
    data["jobs"].append(data["jobs"][0])
    with pytest.raises(ValueError, match="Duplicate job id"):
        Plant.model_validate(data)


def test_reject_unknown_machine_in_allowed_machines():
    data = sample_valid_plant_dict()
    data["jobs"][0]["allowed_machines"] = ["M1", "M_NONEXISTENT"]
    with pytest.raises(ValueError, match="unknown machine in allowed_machines"):
        Plant.model_validate(data)


def test_reject_invalid_job_duration():
    data = sample_valid_plant_dict()
    data["jobs"][0]["duration_slots"] = 0
    with pytest.raises(ValueError):
        Plant.model_validate(data)


def test_reject_release_ge_deadline():
    data = sample_valid_plant_dict()
    data["jobs"][0]["release_slot"] = 20
    data["jobs"][0]["deadline_slot"] = 20
    with pytest.raises(ValueError, match="release_slot.*>=.*deadline_slot"):
        Plant.model_validate(data)


def test_reject_deadline_gt_horizon():
    data = sample_valid_plant_dict()
    data["jobs"][0]["deadline_slot"] = 50
    with pytest.raises(ValueError, match="deadline_slot.*>.*horizon"):
        Plant.model_validate(data)


def test_reject_duration_larger_than_window():
    data = sample_valid_plant_dict()
    data["jobs"][0]["duration_slots"] = 10
    data["jobs"][0]["release_slot"] = 5
    data["jobs"][0]["deadline_slot"] = 12  # window = 7 < duration 10
    with pytest.raises(ValueError, match="duration.*larger than feasible window"):
        Plant.model_validate(data)


def test_reject_negative_power():
    data = sample_valid_plant_dict()
    data["machines"][0]["run_kw"] = -5.0
    with pytest.raises(ValueError):
        Plant.model_validate(data)


def test_warn_idle_kw_gt_run_kw(caplog):
    caplog.set_level(logging.WARNING)
    m = Machine(id="M_WARN", run_kw=10.0, idle_kw=15.0, restart_kwh=2.0)
    assert "idle_kw (15.0) > run_kw (10.0)" in caplog.text


def test_reject_slot_minutes_not_dividing_60():
    data = sample_valid_plant_dict()
    data["slot_minutes"] = 25  # Does not divide 60
    with pytest.raises(ValueError, match="must divide 60 evenly"):
        Plant.model_validate(data)


def test_reject_horizon_longer_than_3_days():
    data = sample_valid_plant_dict()
    data["slot_minutes"] = 30
    data["horizon_slots"] = 150  # 150 * 30 = 4500 min > 4320 min (3 days)
    with pytest.raises(ValueError, match="exceeds maximum 3 days"):
        Plant.model_validate(data)


# ==========================================
# 2. Tariff & Timegrid Tests
# ==========================================

def test_tariff_window_crossing_midnight_rejected():
    with pytest.raises(ValueError, match="cannot cross midnight"):
        TariffWindow(start_hour=22.0, end_hour=5.0, multiplier=1.2)


def test_tariff_overlapping_windows_rejected():
    with pytest.raises(ValueError, match="Overlapping tariff windows"):
        Tariff(
            name="Bad Tariff",
            base_rate_per_kwh=7.0,
            windows=[
                TariffWindow(start_hour=6.0, end_hour=10.0, multiplier=1.2),
                TariffWindow(start_hour=9.0, end_hour=12.0, multiplier=1.5),
            ],
        )


def test_tn_ht1_default_tariff_lookup():
    tariff = load_default_tariff()
    assert tariff.name == "TN HT-I"
    assert tariff.base_rate_per_kwh == 7.50

    # Test hours:
    # 0 to 5 -> 0.95 (rate = 7.50 * 0.95 = 7.125)
    assert get_multiplier(tariff, 0.0) == 0.95
    assert get_multiplier(tariff, 2.5) == 0.95
    assert get_multiplier(tariff, 4.9) == 0.95
    assert get_rate_at_slot(tariff, slot=0, slot_minutes=30) == pytest.approx(7.125)

    # 5 to 6 -> default 1.0 (rate = 7.50)
    assert get_multiplier(tariff, 5.0) == 1.0
    assert get_multiplier(tariff, 5.5) == 1.0
    assert get_rate_at_slot(tariff, slot=10, slot_minutes=30) == pytest.approx(7.50)  # 10 * 0.5 = 5.0h

    # 6 to 10 -> 1.25 (rate = 7.50 * 1.25 = 9.375)
    assert get_multiplier(tariff, 6.0) == 1.25
    assert get_multiplier(tariff, 8.0) == 1.25
    assert get_rate_at_slot(tariff, slot=12, slot_minutes=30) == pytest.approx(9.375)  # 12 * 0.5 = 6.0h

    # 10 to 18 -> default 1.0 (rate = 7.50)
    assert get_multiplier(tariff, 10.0) == 1.0
    assert get_multiplier(tariff, 14.0) == 1.0
    assert get_rate_at_slot(tariff, slot=20, slot_minutes=30) == pytest.approx(7.50)  # 20 * 0.5 = 10.0h

    # 18 to 22 -> 1.25 (rate = 9.375)
    assert get_multiplier(tariff, 18.0) == 1.25
    assert get_multiplier(tariff, 21.5) == 1.25
    assert get_rate_at_slot(tariff, slot=36, slot_minutes=30) == pytest.approx(9.375)  # 36 * 0.5 = 18.0h

    # 22 to 24 -> 0.95 (rate = 7.125)
    assert get_multiplier(tariff, 22.0) == 0.95
    assert get_multiplier(tariff, 23.5) == 0.95
    assert get_rate_at_slot(tariff, slot=44, slot_minutes=30) == pytest.approx(7.125)  # 44 * 0.5 = 22.0h


def test_timegrid_helpers():
    assert slot_hours(30) == 0.5
    assert slot_hours(15) == 0.25
    assert slot_to_hour_of_day(0, 30) == 0.0
    assert slot_to_hour_of_day(48, 30) == 0.0  # wraps around midnight
    assert slot_to_hour_of_day(25, 30) == 12.5

    day, hour = slot_to_day_and_hour(50, 30)  # 25 hours = day 1, hour 1.0
    assert day == 1
    assert hour == 1.0
    assert time_to_slot(day, hour, 30) == 50


# ==========================================
# 3. Loaders Tests
# ==========================================

def test_load_plant_from_json():
    p_dict = sample_valid_plant_dict()
    json_str = json.dumps(p_dict)
    plant, errors = load_plant_from_json(json_str)
    assert errors == []
    assert plant is not None
    assert plant.name == "Test Plant"


def test_load_plant_from_csv_dataframes():
    m_df = pd.DataFrame([
        {"id": "M1", "run_kw": 10, "idle_kw": 4, "restart_kwh": 2, "warmup_slots": 1, "min_off_slots": 1},
        {"id": "M2", "run_kw": 15, "idle_kw": 6, "restart_kwh": 3, "warmup_slots": 0, "min_off_slots": 0},
    ])
    j_df = pd.DataFrame([
        {"id": "J1", "duration_slots": 2, "release_slot": 0, "deadline_slot": 20, "allowed_machines": "M1, M2"},
        {"id": "J2", "duration_slots": 4, "release_slot": 2, "deadline_slot": 48},
    ])
    plant, errors = load_plant_from_dataframes(m_df, j_df)
    assert errors == []
    assert plant is not None
    assert len(plant.machines) == 2
    assert len(plant.jobs) == 2


def test_load_plant_from_csv_files_with_semicolon_and_bom():
    # Test CSV with semicolon delimiter and UTF-8 BOM
    m_csv = "\ufeffid;run_kw;idle_kw;restart_kwh\nM1;12.0;5.0;2.0\nM2;15.0;6.0;3.0\n"
    j_csv = "id;duration_slots;release_slot;deadline_slot\nJ1;3;0;24\nJ2;5;2;48\n"

    plant, errors = load_plant_from_csv_files(m_csv.encode("utf-8-sig"), j_csv.encode("utf-8"))
    assert errors == []
    assert plant is not None
    assert len(plant.machines) == 2
    assert len(plant.jobs) == 2


def test_loader_reports_row_numbers_on_error():
    m_df = pd.DataFrame([
        {"id": "M1", "run_kw": 10, "idle_kw": 4, "restart_kwh": 2},
        {"id": "M2", "run_kw": "invalid_number", "idle_kw": 6, "restart_kwh": 3},
    ])
    j_df = pd.DataFrame([
        {"id": "J1", "duration_slots": 0},  # Invalid duration < 1
    ])
    plant, errors = load_plant_from_dataframes(m_df, j_df)
    assert plant is None
    assert any("Machines row 3" in e for e in errors)
    assert any("Jobs row 2" in e for e in errors)


def test_load_plant_from_excel(tmp_path):
    excel_path = tmp_path / "test_plant.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        m_df = pd.DataFrame([
            {"id": "M1", "run_kw": 10.0, "idle_kw": 4.0, "restart_kwh": 2.0},
        ])
        j_df = pd.DataFrame([
            {"id": "J1", "duration_slots": 3, "release_slot": 0, "deadline_slot": 48},
        ])
        m_df.to_excel(writer, sheet_name="machines", index=False)
        j_df.to_excel(writer, sheet_name="jobs", index=False)

    plant, errors = load_plant_from_excel(excel_path)
    assert errors == []
    assert plant is not None
    assert len(plant.machines) == 1
    assert len(plant.jobs) == 1


def test_load_tariff_from_hourly_csv():
    # 24 hours CSV
    hours = list(range(24))
    multipliers = [1.0] * 24
    # Peak from 6 to 10
    for h in range(6, 10):
        multipliers[h] = 1.3

    df = pd.DataFrame({"hour": hours, "multiplier": multipliers})
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    tariff, errors = load_tariff_from_hourly_csv(csv_bytes, base_rate_per_kwh=8.0, name="Hourly Tariff")
    assert errors == []
    assert tariff is not None
    assert tariff.base_rate_per_kwh == 8.0
    # Window 6 to 10 should be created
    assert any(w.start_hour == 6.0 and w.end_hour == 10.0 and w.multiplier == 1.3 for w in tariff.windows)
    assert get_multiplier(tariff, 7.0) == 1.3
    assert get_multiplier(tariff, 12.0) == 1.0
