"""tests/test_test_cases.py
Authoritative automated API test suite for PeakShift test cases TC01 through TC10.

Verifies upload, parsing, optimization, independent verification,
baseline bounds, outage isolation, tight priority windows, capacity alerts,
and human-readable validation errors.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from peakshift.api.main import app
from peakshift.core.tariff import load_default_tariff

client = TestClient(app)
TC_DIR = Path(__file__).resolve().parent.parent / "test_cases"
DEFAULT_TARIFF = load_default_tariff().model_dump()


def _get_costs(res: dict):
    b0 = res["baseline_b0"]["metrics"]
    b1 = res["baseline_b1"]["metrics"]
    opt = res["metrics"]
    b0_cost = b0["energy_cost"] + b0["tax"] + b0["demand_charge"]
    b1_cost = b1["energy_cost"] + b1["tax"] + b1["demand_charge"]
    opt_cost = opt["energy_cost"] + opt["tax"] + opt["demand_charge"]
    return opt_cost, b1_cost, b0_cost


def test_tc01_textile_mill_standard():
    """TC01: Textile preset JSON uploads, optimizes, and respects constraints."""
    path = TC_DIR / "TC01_textile_mill_standard.json"
    assert path.exists(), "TC01 file must exist"

    with open(path, "rb") as f:
        resp = client.post("/api/plants/upload", files={"file": ("TC01_textile_mill_standard.json", f, "application/json")})
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 10
    assert len(plant["jobs"]) > 0

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 42}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True
    opt_cost, b1_cost, b0_cost = _get_costs(res)
    assert opt_cost <= b1_cost <= b0_cost + 1e-4
    assert res["savings_vs_b0"]["percent"] > 15.0


def test_tc02_machining_shop_excel():
    """TC02: Multi-sheet Excel workbook uploads and optimizes cleanly."""
    path = TC_DIR / "TC02_machining_shop.xlsx"
    assert path.exists(), "TC02 file must exist"

    with open(path, "rb") as f:
        resp = client.post(
            "/api/plants/upload",
            files={"file": ("TC02_machining_shop.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 8
    assert len(plant["jobs"]) > 0

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 7}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True
    opt_cost, b1_cost, b0_cost = _get_costs(res)
    assert opt_cost <= b1_cost <= b0_cost + 1e-4


def test_tc03_food_lines_csv_pair():
    """TC03: Dual CSV pair (machines + jobs) uploads and optimizes cleanly."""
    m_path = TC_DIR / "TC03_food_lines_machines.csv"
    j_path = TC_DIR / "TC03_food_lines_jobs.csv"
    assert m_path.exists() and j_path.exists()

    with open(m_path, "rb") as mf, open(j_path, "rb") as jf:
        resp = client.post(
            "/api/plants/upload",
            files={
                "machines_file": ("TC03_food_lines_machines.csv", mf, "text/csv"),
                "jobs_file": ("TC03_food_lines_jobs.csv", jf, "text/csv"),
            },
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 6
    assert len(plant["jobs"]) > 0

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 11}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True
    opt_cost, b1_cost, b0_cost = _get_costs(res)
    assert opt_cost <= b1_cost <= b0_cost + 1e-4


def test_tc04_semicolon_bom_csv_pair():
    """TC04: Semicolon-delimited CSV with UTF-8 BOM parses and optimizes."""
    m_path = TC_DIR / "TC04_excel_export_machines.csv"
    j_path = TC_DIR / "TC04_excel_export_jobs.csv"
    assert m_path.exists() and j_path.exists()

    with open(m_path, "rb") as mf, open(j_path, "rb") as jf:
        resp = client.post(
            "/api/plants/upload",
            files={
                "machines_file": ("TC04_excel_export_machines.csv", mf, "text/csv"),
                "jobs_file": ("TC04_excel_export_jobs.csv", jf, "text/csv"),
            },
        )
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 6

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 3}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True


def test_tc05_small_demo_all_features():
    """TC05: Hand-written 3-machine 10-job scenario testing all constraint types."""
    path = TC_DIR / "TC05_small_demo_all_features.json"
    assert path.exists()

    with open(path, "rb") as f:
        resp = client.post("/api/plants/upload", files={"file": ("TC05_small_demo_all_features.json", f, "application/json")})
    assert resp.status_code == 200
    plant = resp.json()["plant"]
    assert len(plant["machines"]) == 3
    assert len(plant["jobs"]) == 10

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 42}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True

    # Assert M3 outage [20, 26) is strictly respected
    placements = res["schedule"]["placements"]
    m3_clashes = [
        p for p in placements
        if p["machine_id"] == "M3" and not (p["end"] <= 20 or p["start"] >= 26)
    ]
    assert len(m3_clashes) == 0, f"Found placements on M3 during outage: {m3_clashes}"


def test_tc06_high_load_with_outage():
    """TC06: High load plant with M3 outage and tight priority windows."""
    path = TC_DIR / "TC06_high_load_with_outage.json"
    assert path.exists()

    with open(path, "rb") as f:
        resp = client.post("/api/plants/upload", files={"file": ("TC06_high_load_with_outage.json", f, "application/json")})
    assert resp.status_code == 200
    plant = resp.json()["plant"]

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 5}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True

    # Assert zero jobs scheduled on M3 during slots 20 to 28
    placements = res["schedule"]["placements"]
    m3_clashes = [
        p for p in placements
        if p["machine_id"] == "M3" and not (p["end"] <= 20 or p["start"] >= 28)
    ]
    assert len(m3_clashes) == 0, f"Found placements on M3 during outage: {m3_clashes}"


def test_tc07_low_load_big_savings():
    """TC07: Low load (0.3) plant achieving substantial energy cost reduction."""
    path = TC_DIR / "TC07_low_load_big_savings.json"
    assert path.exists()

    with open(path, "rb") as f:
        resp = client.post("/api/plants/upload", files={"file": ("TC07_low_load_big_savings.json", f, "application/json")})
    assert resp.status_code == 200
    plant = resp.json()["plant"]

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 8}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    assert res["metrics"]["target_met"] is True
    assert res["verifier"]["passed"] is True
    assert res["savings_vs_b0"]["percent"] > 40.0


def test_tc08_overloaded_target_at_risk():
    """TC08: Overloaded plant flags Target at Risk with unplaced jobs."""
    path = TC_DIR / "TC08_overloaded_target_at_risk.json"
    assert path.exists()

    with open(path, "rb") as f:
        resp = client.post("/api/plants/upload", files={"file": ("TC08_overloaded_target_at_risk.json", f, "application/json")})
    assert resp.status_code == 200
    plant = resp.json()["plant"]

    opt_resp = client.post("/api/optimize", json={"plant": plant, "tariff": DEFAULT_TARIFF, "config": {"seed": 42}})
    assert opt_resp.status_code == 200
    res = opt_resp.json()

    # Target cannot be met because plant is over capacity
    assert res["metrics"]["target_met"] is False
    assert len(res["schedule"]["unplaced"]) > 0


def test_tc09_missing_column_csv():
    """TC09: CSV missing required column returns HTTP 422 naming restart_kwh."""
    m_path = TC_DIR / "TC09_invalid_missing_column_machines.csv"
    j_path = TC_DIR / "TC09_invalid_missing_column_jobs.csv"
    assert m_path.exists() and j_path.exists()

    with open(m_path, "rb") as mf, open(j_path, "rb") as jf:
        resp = client.post(
            "/api/plants/upload",
            files={
                "machines_file": ("TC09_invalid_missing_column_machines.csv", mf, "text/csv"),
                "jobs_file": ("TC09_invalid_missing_column_jobs.csv", jf, "text/csv"),
            },
        )
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "UPLOAD_VALIDATION_ERROR"
    error_text = " ".join(data.get("details", [])) + " " + data.get("message", "")
    assert "restart_kwh" in error_text
    assert "pydantic" not in error_text.lower()


def test_tc10_unknown_machine_json():
    """TC10: JSON referencing non-existent machine returns HTTP 422 naming J5 and M99."""
    path = TC_DIR / "TC10_invalid_unknown_machine.json"
    assert path.exists()

    with open(path, "rb") as f:
        resp = client.post(
            "/api/plants/upload",
            files={"file": ("TC10_invalid_unknown_machine.json", f, "application/json")},
        )
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "UPLOAD_VALIDATION_ERROR"
    error_text = " ".join(data.get("details", [])) + " " + data.get("message", "")
    assert "M99" in error_text
    assert "J5" in error_text
    assert "pydantic" not in error_text.lower()


def test_test_cases_static_serving():
    """Verify that test_cases folder is served read-only at /test-cases/."""
    resp = client.get("/test-cases/TC01_textile_mill_standard.json")
    assert resp.status_code == 200
    assert "machines" in resp.json()

    resp_readme = client.get("/test-cases/README.md")
    assert resp_readme.status_code == 200
    assert "PeakShift Official Test Cases" in resp_readme.text

    resp_results = client.get("/test-cases/RESULTS.md")
    assert resp_results.status_code == 200
    assert "PeakShift Benchmark Execution Results" in resp_results.text


def test_results_md_savings_subtraction_consistency():
    """Verify that in RESULTS.md, for every test case, B0 cost minus Optimized cost equals Savings."""
    import re
    results_path = TC_DIR / "RESULTS.md"
    assert results_path.exists(), "RESULTS.md must exist"
    content = results_path.read_text(encoding="utf-8")

    # Match rows like: | **TC01** | ... | ₹32,141.06 | ₹25,650.00 | ₹24,883.50 | ₹7,257.56 | 22.6% | ...
    pattern = re.compile(
        r"\|\s*\*\*(TC\d+)\*\*\s*\|[^|]+\|[^|]+\|\s*₹([\d,]+\.\d{2})\s*\|\s*₹([\d,]+\.\d{2})\s*\|\s*₹([\d,]+\.\d{2})\s*\|\s*₹([\d,]+\.\d{2})\s*\|\s*([\d\.]+)%"
    )
    matches = pattern.findall(content)
    assert len(matches) == 8, f"Expected 8 test case rows in RESULTS.md, found {len(matches)}"

    for case_id, b0_str, b1_str, opt_str, sav_str, pct_str in matches:
        b0 = float(b0_str.replace(",", ""))
        opt = float(opt_str.replace(",", ""))
        sav = float(sav_str.replace(",", ""))
        expected_sav = round(b0 - opt, 2)
        assert abs(expected_sav - sav) < 1e-6, (
            f"{case_id}: B0 ({b0}) - Optimized ({opt}) = {expected_sav}, "
            f"but Savings column has {sav}"
        )

