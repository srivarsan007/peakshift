"""tests/test_templates.py
Automated tests for PeakShift template files in templates/.
Asserts that every provided template uploads via the API, parses without error,
satisfies optimization, and achieves verifier green status.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.tariff import load_default_tariff

client = TestClient(app)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DEFAULT_TARIFF = load_default_tariff().model_dump()


def _optimize_and_verify(plant: dict):
    resp = client.post(
        "/api/optimize",
        json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 42}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics"]["target_met"] is True, f"Target not met: {data['metrics']}"
    assert data["verifier"]["passed"] is True, f"Verifier failed: {data['verifier']}"


def test_template_minimal_plant_json():
    """Verify templates/minimal_plant.json uploads, optimizes, and passes verifier."""
    path = TEMPLATES_DIR / "minimal_plant.json"
    assert path.exists(), "minimal_plant.json must exist"

    with open(path, "rb") as f:
        resp = client.post(
            "/api/plants/upload",
            files={"file": ("minimal_plant.json", f, "application/json")},
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 2
    assert len(plant["jobs"]) == 4

    _optimize_and_verify(plant)


def test_template_minimal_csv_pair():
    """Verify templates/minimal_machines.csv + minimal_jobs.csv upload, optimize, and pass verifier."""
    m_path = TEMPLATES_DIR / "minimal_machines.csv"
    j_path = TEMPLATES_DIR / "minimal_jobs.csv"
    assert m_path.exists() and j_path.exists()

    with open(m_path, "rb") as mf, open(j_path, "rb") as jf:
        resp = client.post(
            "/api/plants/upload",
            files={
                "machines_file": ("minimal_machines.csv", mf, "text/csv"),
                "jobs_file": ("minimal_jobs.csv", jf, "text/csv"),
            },
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 3
    assert len(plant["jobs"]) == 6

    _optimize_and_verify(plant)


def test_template_full_csv_pair():
    """Verify templates/full_machines.csv + full_jobs.csv upload, optimize, and pass verifier."""
    m_path = TEMPLATES_DIR / "full_machines.csv"
    j_path = TEMPLATES_DIR / "full_jobs.csv"
    assert m_path.exists() and j_path.exists()

    with open(m_path, "rb") as mf, open(j_path, "rb") as jf:
        resp = client.post(
            "/api/plants/upload",
            files={
                "machines_file": ("full_machines.csv", mf, "text/csv"),
                "jobs_file": ("full_jobs.csv", jf, "text/csv"),
            },
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 3
    assert len(plant["jobs"]) == 6

    _optimize_and_verify(plant)


def test_template_minimal_plant_xlsx():
    """Verify templates/minimal_plant.xlsx uploads, optimizes, and passes verifier."""
    path = TEMPLATES_DIR / "minimal_plant.xlsx"
    assert path.exists(), "minimal_plant.xlsx must exist"

    with open(path, "rb") as f:
        resp = client.post(
            "/api/plants/upload",
            files={
                "file": (
                    "minimal_plant.xlsx",
                    f,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 3
    assert len(plant["jobs"]) == 6

    _optimize_and_verify(plant)
