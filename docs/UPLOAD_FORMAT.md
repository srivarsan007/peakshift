# Uploading your own data

On the **Load & config** page choose a file (or select both files for CSV) and press **Run optimization**.

| Format | What to select | Notes |
|---|---|---|
| JSON | one `.json` file | whole plant in one file; the only format that can change the horizon or slot length |
| Excel | one `.xlsx` file with two sheets named `machines` and `jobs` | |
| CSV | two files together: a machines file and a jobs file | comma or semicolon separated, UTF-8 with or without BOM |

**Time.** One slot is 30 minutes and the horizon is 48 slots (24 hours) for CSV and Excel. Slot 0 is 00:00, so a job running slots 4 to 8 runs from 02:00 to 04:00. `deadline_slot` is exclusive. In JSON you can set `horizon_slots` and `slot_minutes` (60 must divide evenly by `slot_minutes`; the horizon can be at most 3 days).

**Machines**

| Column | Required | Meaning | Default |
|---|---|---|---|
| `id` | yes | unique name, for example `M1` | |
| `run_kw` | yes | power while running (kW) | |
| `idle_kw` | yes | power while on but idle (kW) | |
| `restart_kwh` | yes | energy to start again after switching off (kWh) | |
| `warmup_slots` | no | slots needed to warm up before running after a restart | 0 |
| `min_off_slots` | no | shortest gap worth switching off for | 0 |
| `max_switch_cycles` | no | most switch-offs allowed in the horizon (empty means no limit) | none |
| `initial_state` | no | `idle` or `off` at slot 0 | `idle` |
| `downtime` | no | unavailable slots as `start-end`, several separated by `;`, for example `10-14;30-34` | none |

**Jobs**

| Column | Required | Meaning | Default |
|---|---|---|---|
| `id` | yes | unique name, for example `J1` | |
| `duration_slots` | yes | length in slots (4 slots = 2 hours) | |
| `release_slot` | no | earliest start slot | 0 |
| `deadline_slot` | no | the job must finish by this slot | end of horizon |
| `allowed_machines` | no | machines that may run it, separated by `;` (empty means any) | any |
| `priority` | no | higher number is scheduled first | 0 |
| `units` | no | units produced (used for the production target) | 1 |
| `mandatory` | no | `true` or `false` | true |

**Smallest working examples** are in `templates/`: `minimal_machines.csv` with `minimal_jobs.csv`, `minimal_plant.json`, and `minimal_plant.xlsx`; the `full_*.csv` pair shows every column.

**Rules the file must follow:** ids are unique; every machine in `allowed_machines` exists in the machines list; `release_slot` is smaller than `deadline_slot`; the duration fits inside that window; the deadline is within the horizon; there is at least one machine and one job.

**What you get.** A schedule with a green "target verified" line when every job fits. If the jobs need more machine time than exists, the app shows "Target at risk" and lists the jobs it could not place instead of pretending it succeeded.

**Common messages**

| Message | Cause | Fix |
|---|---|---|
| `machines file: missing column 'restart_kwh'` | a required column is missing or misspelled | add the column; names are not case sensitive |
| `jobs file: missing column 'duration_slots'` | same, for jobs | add the column |
| `CSV uploads require both machines and jobs files` | only one CSV was selected | select both files together |
| `Job J5: machine 'M99' does not exist` | `allowed_machines` names an unknown machine | correct the name or add the machine |
| `Job J3: release slot 30 is after deadline slot 20` | the window is backwards | swap or correct the slots |
| `Job J7: duration (10) larger than feasible window (6)` | the job cannot fit in its window | widen the window or shorten the job |
| `Duplicate machine id` or `Duplicate job id` | two rows share an id | make ids unique |
| `slot_minutes must divide 60 evenly` | for example 25 minutes | use 5, 10, 15, 20, 30 or 60 |
| `Horizon length exceeds maximum 3 days` | too many slots | reduce `horizon_slots` |
| `Cannot connect to backend server` | the server is not running | start it with the command in Quick start |
