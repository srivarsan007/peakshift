# Contributing to PeakShift

Thank you for your interest in contributing to PeakShift! We welcome bug reports, feature proposals, documentation improvements, and pull requests.

---

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/<owner>/PeakShift.git
   cd PeakShift
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install development dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements-dev.txt
   ```

4. **Verify the installation**:
   ```bash
   pytest
   ```

---

## Running the Application Locally

Start the local FastAPI development server:
```bash
uvicorn peakshift.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The web dashboard is served directly at [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

## Testing & Quality Assurance

### 1. Unit & Property Tests
PeakShift maintains comprehensive unit, contract, property, and negative tests using `pytest`:
```bash
pytest -v
```

### 2. End-to-End Headless Browser Tests
Verify file uploads and optimization across the browser interface using headless Chrome:
```bash
node scripts/e2e_upload_check.js http://127.0.0.1:8000
```

---

## How to Add a Test Case

1. **Create the Scenario File**:
   Add your plant configuration to `test_cases/` in JSON, Excel (`.xlsx`), or dual CSV format (`machines.csv` and `jobs.csv`).

2. **Schema & Integrity Checklist**:
   - Ensure `machines` contains non-empty lists with positive `run_kw`, `idle_kw`, and `restart_kwh`.
   - Ensure all jobs have `duration_slots >= 1` and `release_slot < deadline_slot <= horizon_slots`.
   - If specifying `allowed_machines`, ensure all referenced machine IDs exist.

3. **Add Automated Test Coverage**:
   In `tests/test_test_cases.py`, add a test function that loads your scenario via `/api/plants/upload`, optimizes it via `/api/optimize`, and verifies that `verifier["passed"]` holds.

4. **Update Documentation**:
   Update `test_cases/README.md` and `test_cases/RESULTS.md` with the observed baseline costs, optimized cost, and savings metrics.

---

## Code Style & Guidelines

- **Python**: Follow PEP 8 guidelines. Use type annotations across all function signatures. Models must use Pydantic v2 schemas.
- **Pure Functions**: Optimization and cost functions under `peakshift/core/` should remain pure and deterministic (accepting seeded random generators).
- **Independent Verifier**: Code under `peakshift/verify/` must not import or depend on solver modules (`construct.py`, `improve.py`, `portfolio.py`).
- **Frontend**: Dashboard components use standard HTML5, CSS variables (`tokens.css`), and ES modules without runtime build steps.
