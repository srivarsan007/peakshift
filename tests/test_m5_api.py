"""Tests for Milestone M5: API endpoints, contract tests, and 422 error structures."""

import io
import json
import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.generator import generate_plant
from peakshift.core.tariff import load_default_tariff

client = TestClient(app)


# =========================================================
# 1. Health and Tariff Endpoints
# =========================================================

def test_api_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "peakshift"
    assert "elapsed_ms" in data


def test_api_tariffs():
    resp = client.get("/api/tariffs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tariffs"]) >= 1
    assert data["default_tariff"]["name"] == "TN HT-I"
    assert data["default_tariff"]["base_rate_per_kwh"] == 7.50


# =========================================================
# 2. Plant Generation Endpoint
# =========================================================

def test_api_generate_plant_success():
    req_payload = {
        "preset": "machining",
        "utilization": 0.5,
        "seed": 42,
    }
    resp = client.post("/api/plants/generate", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "plant" in data
    assert len(data["plant"]["machines"]) == 8
    assert len(data["plant"]["jobs"]) > 0
    assert "elapsed_ms" in data


def test_api_generate_plant_invalid_preset():
    req_payload = {
        "preset": "non_existent_preset",
        "utilization": 0.5,
    }
    resp = client.post("/api/plants/generate", json=req_payload)
    assert resp.status_code == 422
    data = resp.json()
    assert "code" in data
    assert "message" in data


# =========================================================
# 3. Plant Upload Endpoint
# =========================================================

def test_api_upload_json():
    plant = generate_plant("food_lines", utilization=0.3, seed=42)
    json_bytes = json.dumps(plant.model_dump()).encode("utf-8")

    files = {"file": ("plant.json", io.BytesIO(json_bytes), "application/json")}
    resp = client.post("/api/plants/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["plant"]["name"] == plant.name
    assert len(data["plant"]["machines"]) == len(plant.machines)


def test_api_upload_invalid_csv_returns_422():
    # Broken CSV missing required columns
    m_csv = b"id;run_kw\nM1;10.0\n"
    j_csv = b"id\nJ1\n"

    files = {
        "machines_file": ("machines.csv", io.BytesIO(m_csv), "text/csv"),
        "jobs_file": ("jobs.csv", io.BytesIO(j_csv), "text/csv"),
    }
    resp = client.post("/api/plants/upload", files=files)
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "UPLOAD_VALIDATION_ERROR"
    assert len(data["details"]) > 0


def test_api_upload_single_csv_machines_identifies_missing_jobs():
    m_csv = b"id,run_kw,idle_kw,restart_kwh\nM1,10.0,1.0,0.5\n"
    files = {"file": ("machines.csv", io.BytesIO(m_csv), "text/csv")}
    resp = client.post("/api/plants/upload", files=files)
    assert resp.status_code == 422
    data = resp.json()
    assert "jobs file is missing" in data["message"].lower() or "jobs" in data["details"][0].lower()


def test_api_upload_single_csv_jobs_identifies_missing_machines():
    j_csv = b"id,duration_slots,release_slot,deadline_slot\nJ1,4,0,24\n"
    files = {"file": ("jobs.csv", io.BytesIO(j_csv), "text/csv")}
    resp = client.post("/api/plants/upload", files=files)
    assert resp.status_code == 422
    data = resp.json()
    assert "machines file is missing" in data["message"].lower() or "machines" in data["details"][0].lower()


# =========================================================
# 4. Optimize Endpoint (Contract & Data Integrity)
# =========================================================

def test_api_optimize_contract():
    """Verify that POST /api/optimize returns all fields specified in the UI binding table."""
    plant = generate_plant("food_lines", utilization=0.3, seed=42)
    tariff = load_default_tariff()

    payload = {
        "plant": plant.model_dump(),
        "tariff": tariff.model_dump(),
        "config": {
            "seed": 42,
            "iterations": 5000,
        },
    }

    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # Binding table assertions
    assert "baseline_b0" in data
    assert "baseline_b1" in data
    assert "schedule" in data
    assert "metrics" in data
    assert "savings_vs_b0" in data
    assert "savings_vs_b1" in data
    assert "winner" in data
    assert "decisions" in data
    assert "verifier" in data
    assert "bound" in data
    assert "elapsed_ms" in data

    # Metrics fields
    metrics = data["metrics"]
    required_metrics = [
        "kwh", "energy_cost", "tax", "peak_kw", "peak_kva",
        "switch_offs", "idle_kwh", "makespan", "jobs_placed",
        "target_met", "co2_kg",
    ]
    for field in required_metrics:
        assert field in metrics, f"Missing metric field: {field}"

    # Savings fields
    assert "inr" in data["savings_vs_b0"]
    assert "percent" in data["savings_vs_b0"]
    assert "inr" in data["savings_vs_b1"]
    assert "percent" in data["savings_vs_b1"]

    # Verifier checks
    assert "passed" in data["verifier"]
    assert "checks" in data["verifier"]
    assert len(data["verifier"]["checks"]) == 9
    assert data["verifier"]["passed"] is True


def test_api_optimize_invalid_payload_returns_422():
    # Sending malformed payload
    resp = client.post("/api/optimize", json={"bad_field": 123})
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert "message" in data
    assert len(data["details"]) > 0


# =========================================================
# 5. Verify Endpoint
# =========================================================

def test_api_verify():
    plant = generate_plant("food_lines", utilization=0.3, seed=42)
    tariff = load_default_tariff()

    # Optimize first to get valid schedule
    payload = {
        "plant": plant.model_dump(),
        "tariff": tariff.model_dump(),
        "config": {"seed": 42},
    }
    opt_resp = client.post("/api/optimize", json=payload)
    schedule = opt_resp.json()["schedule"]
    metrics = opt_resp.json()["metrics"]

    verify_payload = {
        "plant": plant.model_dump(),
        "tariff": tariff.model_dump(),
        "schedule": schedule,
        "reported_metrics": metrics,
    }
    v_resp = client.post("/api/verify", json=verify_payload)
    assert v_resp.status_code == 200
    v_data = v_resp.json()
    assert v_data["passed"] is True
    assert len(v_data["checks"]) == 9
    assert "recomputed_metrics" in v_data
    assert "elapsed_ms" in v_data
