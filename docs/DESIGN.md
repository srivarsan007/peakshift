# PeakShift Technical Design Document

PeakShift is an industrial factory production scheduler that minimizes electricity costs under time-varying industrial electricity tariffs. An independent verifier checks 100% production target fulfillment and hard physical constraint satisfaction on every schedule.

---

## 1. Core Principles

1. **Production Target is Hard**: A schedule that misses deadlines or omits mandatory units is never marked as valid.
2. **Zero Cloud / Offline Operation**: All optimization algorithms, verifiers, and user interface assets operate locally without external API or cloud dependencies.
3. **Reproducibility**: Identical plant configuration and random seed yield identical mathematical solutions.
4. **Independent Verification**: A separate verifier with no algorithmic dependencies independently re-evaluates all schedule constraints and physics before reporting savings.

---

## 2. Architecture and Repository Layout

The system is implemented in Python with an ASGI FastAPI service and a static HTML5/JavaScript dashboard:

```
peakshift/
  core/
    models.py        # Pydantic data schemas (Plant, Machine, Job, Tariff)
    tariff.py        # Tariff lookup, time-of-day multipliers, rate profiles
    timegrid.py      # Time slot discretization, hour conversions, indexing
    cost.py          # Schedule evaluation, metric calculations, cost engines
    machine_cost.py  # Per-machine exact interval evaluation and idle-vs-restart decisions
    baseline.py      # Dual physical baselines: B0 (Always-On) and B1 (Smart-Rule)
    construct.py     # Heuristic builders (H1 Early-Dispatch, H2 Regret, H3 Dense Packing)
    improve.py       # Relational local search (job shifts, pairwise swaps, relocations)
    portfolio.py     # Multi-heuristic portfolio search and winner selection
    polish.py        # Exact CP-SAT constraint programming model (OR-Tools)
    replan.py        # Dynamic mid-day re-scheduling under machine failure or tariff shocks
    reasons.py       # Plain-language explanation engine for scheduling choices
    generator.py     # Deterministic factory scenario generator from industrial presets
    loaders.py       # Robust ingestion for JSON, multi-sheet Excel, and dual CSV files
    sweep.py         # Multi-utilization and peak-cap sweep simulator
  verify/
    verifier.py      # Independent 9-point physical constraint and metric validator
  api/
    main.py          # FastAPI endpoints, multipart upload handling, error handling
    schemas.py       # API request and response data schemas
app/static/          # UI dashboard (HTML, CSS, ES modules, vendor assets)
data/
  tariffs/           # Default statutory tariff specifications (e.g. Tamil Nadu HT-I)
  presets/           # Industrial scenario presets (machining, textile, food lines)
tests/               # Unit, contract, negative, and property test suites
```

---

## 3. Data Model

All models use strict schema validation via Pydantic:

### 3.1 Plant
- `name`: Human-readable identifier.
- `horizon_slots`: Total scheduling horizon in discrete slots (default 48 = 24 hours at 30 min/slot). Maximum allowed duration: 3 days (4320 minutes).
- `slot_minutes`: Granularity in minutes (must divide 60 evenly; default 30).
- `contract_kva`: Contracted maximum demand capacity in kVA (optional).
- `power_factor`: Facility power factor (default 0.95, range (0.0, 1.0]).
- `machines`: List of `Machine` specifications.
- `jobs`: List of `Job` specifications.

### 3.2 Machine
- `id`: Unique string identifier.
- `run_kw`: Active operating power draw in kilowatts (>= 0).
- `idle_kw`: Standby / idle power draw in kilowatts (>= 0).
- `restart_kwh`: Energy consumed during cold restart in kilowatt-hours (>= 0).
- `warmup_slots`: Slots of warm-up required before running after being powered off (>= 0).
- `min_off_slots`: Minimum duration a machine must remain off if shut down (>= 0).
- `max_switch_cycles`: Maximum allowed off-to-on switching cycles per day (null = unlimited).
- `initial_state`: Machine state at slot 0 (`"idle"` or `"off"`).
- `downtime`: List of half-open intervals `[start_slot, end_slot)` during which the machine is unavailable.

### 3.3 Job
- `id`: Unique string identifier.
- `duration_slots`: Processing time in slots (>= 1).
- `release_slot`: Earliest slot at which processing may begin (>= 0).
- `deadline_slot`: Latest slot by which processing must finish (<= `horizon_slots`).
- `allowed_machines`: Optional subset of machine IDs capable of running the job (null = all).
- `priority`: Scheduling priority integer (>= 0, higher = more urgent).
- `units`: Production batch count (>= 1).
- `mandatory`: Boolean indicating whether job must be completed.

### 3.4 Tariff
- `name`: Statutory tariff designation.
- `source`: Regulatory order citation.
- `base_rate_per_kwh`: Base electricity energy charge (₹/kWh > 0).
- `windows`: List of time intervals `{start_hour, end_hour, multiplier}` (0.0 to 24.0, non-overlapping).
- `tax_rate`: Statutory electricity tax (default 0.05 = 5%).
- `demand_rate_per_kva`: Monthly maximum demand charge (₹/kVA/month).
- `demand_floor_fraction`: Minimum billing demand fraction (default 0.90 = 90% of contract kVA).
- `contract_exceed_penalty_per_kva`: Surcharge per kVA exceeding contract demand.

### 3.5 Schedule Representation
- `segments`: Machine-indexed list of contiguous, non-overlapping intervals `{state: "run"|"idle"|"off"|"warmup", start, end, job_id?}` partitioning `[0, horizon_slots)`.
- `placements`: List of assignments `{job_id, machine_id, start, end}`.

---

## 4. Cost and Physical Energy Model

### 4.1 Time Discretization & Rate Profile
- Slot duration in hours: `h = slot_minutes / 60.0`.
- Slot hour index: `hour(t) = ((t * slot_minutes) / 60.0) mod 24`.
- Effective rate at slot `t`:
  $$\text{rate}[t] = \text{base\_rate} \times \text{multiplier}(\text{hour}(t))$$

### 4.2 Segment Energy & Costs
- **Run segment**: Power = $\text{run\_kw}$, Energy = $\text{run\_kw} \times h \text{ kWh/slot}$, Cost = $\sum_{t} \text{run\_kw} \times h \times \text{rate}[t]$.
- **Idle segment**: Power = $\text{idle\_kw}$, Energy = $\text{idle\_kw} \times h \text{ kWh/slot}$, Cost = $\sum_{t} \text{idle\_kw} \times h \times \text{rate}[t]$.
- **Off segment**: Power = $0\text{ kW}$, Energy = $0\text{ kWh}$, Cost = $0$.
- **Warm-up segment**: Machine draws warm-up power for $w = \text{warmup\_slots}$ slots preceding job start $s$. Restart energy $\text{restart\_kwh}$ is evaluated at the rate of the initial warm-up slot $\text{rate}[s - w]$.

### 4.3 Exact Gap Decision Rule
For every non-processing interval between jobs on a machine:
$$\text{Cost}_{\text{idle}} = \sum_{t \in \text{gap}} \text{idle\_kw} \times h \times \text{rate}[t]$$
$$\text{Cost}_{\text{off}} = \text{restart\_kwh} \times \text{rate}[s_{\text{next}} - w]$$
If $\text{length}(\text{gap}) \ge w + \text{min\_off\_slots}$ and $\text{Cost}_{\text{off}} < \text{Cost}_{\text{idle}}$:
The machine powers down during the gap (off + warm-up), provided the total cycles do not exceed `max_switch_cycles`. Otherwise, the machine remains in idle standby.

### 4.4 Demand and Total Financials
- Total Energy Cost: Sum of run, idle, and restart costs across all machines.
- Tax: $\text{tax\_rate} \times \text{Energy Cost}$.
- Peak kW: $\max_{t} \sum_{m} \text{power}_m[t]$.
- Billing Demand: $\text{billed\_kva} = \max\left(\frac{\text{peak\_kw}}{\text{power\_factor}}, \text{demand\_floor\_fraction} \times \text{contract\_kva}\right)$.
- CO2 Emissions: $\text{Total kWh} \times 0.73\text{ kg CO}_2/\text{kWh}$.

---

## 5. Algorithms

### 5.1 Dual Baseline Benchmarks
1. **Baseline B0 (Always-On Dispatch)**:
   - Jobs sorted by earliest release, dispatched to the earliest available machine.
   - All machines remain powered continuously (`run` or `idle`; zero shut-offs).
2. **Baseline B1 (Smart-Rule Dispatch)**:
   - Identical job placements as B0.
   - Applies break-even gap shutdown logic whenever idle energy over the gap exceeds restart energy.
   - Mathematical guarantee: $\text{Cost}(B_1) \le \text{Cost}(B_0)$.

### 5.2 Heuristic Scheduling Portfolio
The optimizer concurrently runs three distinct constructive strategies:
- **H1 (Tariff-Aware Early Dispatch)**: Jobs sequenced by earliest deadline with exact cost-optimal gap evaluations.
- **H2 (Regret-Based Insertion)**: Jobs sorted by priority and duration. For each job, evaluates all feasible machine placements and start slots, computing exact marginal energy cost via `machine_cost`.
- **H3 (Dense Machine Packing)**: Concentrates jobs onto fewer machines to maximize contiguous idle blocks on unused machines, allowing full-day shutdowns.

### 5.3 Relational Local Search (Improvement Phase)
Post-construction iterative local search executes up to 150,000 deterministic iterations:
- **Relocate moves**: Shifts an operation to a different slot or eligible machine.
- **Swap moves**: Exchanges slot assignments between two operations.
- Updates are evaluated incrementally via per-machine load profiles and accepted strictly on objective cost decrease.

### 5.4 Peak Demand Limiting
When a peak cap ($\text{cap\_kw}$) is specified:
- Construction and moves strictly reject solutions exceeding the cap.
- If no fully feasible schedule exists, the solver returns the least-violating schedule with `cap_satisfied = false` and flags excess kW.

### 5.5 Mid-Day Resilient Re-Planner
When handling production disruptions at slot $t_{\text{now}}$:
- Freezes elapsed production slots $0 \dots t_{\text{now}} - 1$.
- Any job active on a failed machine is marked `REROUTED_MACHINE_DOWN` and rescheduled.
- Re-optimizes remaining jobs with a stability penalty ($\lambda = ₹25$ per moved job) to prevent unnecessary schedule disruption.

---

## 6. Independent Verifier Gate

The verifier in `peakshift/verify/` imports only data models and tariff definitions, ensuring zero shared state with the solver. It executes 9 physical tests:
1. **Horizon Coverage**: Segments partition $[0, \text{horizon\_slots})$ without gaps or overlaps.
2. **Window Feasibility**: Every job starts $\ge \text{release\_slot}$ and finishes $\le \text{deadline\_slot}$.
3. **Machine Affinity**: Jobs execute only on specified `allowed_machines`.
4. **Collision Absence**: No two operations overlap on the same machine.
5. **Downtime Adherence**: Zero machine run/warmup states during scheduled downtime.
6. **Warmup Integrity**: Every off-to-run transition has prerequisite warm-up slots and obeys $\text{min\_off\_slots}$.
7. **Switch Cycle Cap**: Number of restarts $\le \text{max\_switch\_cycles}$.
8. **Peak Demand Compliance**: Max load $\le \text{peak\_cap\_kw}$ (if specified).
9. **Metric & Financial Consistency**: Independent slot-by-slot re-integration matches reported cost, kWh, and peak kW within 0.01 INR.

---

## 7. REST API Endpoints

- `GET /api/health`: Health status and service identifier.
- `GET /api/tariffs`: Available tariffs and statutory TN HT-I rates.
- `POST /api/plants/generate`: Synthesizes plant models from preset parameters.
- `POST /api/plants/upload`: Multipart upload supporting `.json`, `.xlsx`, or dual `.csv` files.
- `POST /api/optimize`: Solves portfolio optimization, generates decisions and verifier output.
- `POST /api/verify`: Runs independent verification gate on an arbitrary schedule.
- `POST /api/replan`: Dynamic rescheduling under simulated disruption events.
- `POST /api/refine`: Exact CP-SAT branch-and-bound optimization.
- `POST /api/sweep`: Multi-seed parametric utilization sweep.
- `POST /api/pareto`: Computes cost vs. peak demand trade-off frontiers.
