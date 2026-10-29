"""Data loaders for Plants and Tariffs from JSON, CSV, and Excel."""

import ast
import csv
import io
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from peakshift.core.models import Job, Machine, Plant, Tariff, TariffWindow

logger = logging.getLogger(__name__)


def _clean_pydantic_errors(
    exc: Any,
    job_rows: Optional[Dict[str, int]] = None,
    machine_rows: Optional[Dict[str, int]] = None,
) -> List[str]:
    """Converts validation errors into one plain readable sentence each.
    Keeps row numbers for CSV and Excel, removes library noise, URLs, and internal types.
    """
    clean_lines: List[str] = []

    if hasattr(exc, "errors") and callable(exc.errors):
        for err in exc.errors():
            raw_msg = str(err.get("msg", "")).strip()
            if raw_msg.startswith("Value error, "):
                msg = raw_msg[len("Value error, "):].strip()
            else:
                msg = raw_msg

            if " (release_slot >= deadline_slot)" in msg:
                msg = msg.replace(" (release_slot >= deadline_slot)", "")
            if " (unknown machine in allowed_machines)" in msg:
                msg = msg.replace(" (unknown machine in allowed_machines)", "")
            if " (start must be < end and >= 0)" in msg:
                msg = msg.replace(" (start must be < end and >= 0)", "")

            if "[type=" in msg:
                msg = msg.split("[type=")[0].strip()
            if "For further information" in msg:
                msg = msg.split("For further information")[0].strip()

            if err.get("type") == "missing":
                loc = err.get("loc", ())
                field = str(loc[-1]) if loc else "field"
                msg = f"missing required field '{field}'"

            # Inject row numbers if available and not yet present
            if job_rows:
                for jid, rnum in job_rows.items():
                    pattern = re.compile(rf"\bJob {re.escape(jid)}\b(?!\s*\(row)")
                    if pattern.search(msg):
                        msg = pattern.sub(f"Job {jid} (row {rnum})", msg)
                        break

            if machine_rows:
                for mid, rnum in machine_rows.items():
                    pattern = re.compile(rf"\bMachine {re.escape(mid)}\b(?!\s*\(row)")
                    if pattern.search(msg):
                        msg = pattern.sub(f"Machine {mid} (row {rnum})", msg)
                        break

            msg = " ".join(msg.split())
            if msg and msg not in clean_lines:
                clean_lines.append(msg)
    else:
        msg = str(exc).strip()
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):].strip()
        if " (release_slot >= deadline_slot)" in msg:
            msg = msg.replace(" (release_slot >= deadline_slot)", "")
        if " (unknown machine in allowed_machines)" in msg:
            msg = msg.replace(" (unknown machine in allowed_machines)", "")
        if "[type=" in msg:
            msg = msg.split("[type=")[0].strip()
        if "For further information" in msg:
            msg = msg.split("For further information")[0].strip()
        msg = " ".join(msg.split())
        clean_lines.append(msg)

    return clean_lines if clean_lines else ["Validation failed"]


def _detect_delimiter_and_read_csv(file_or_bytes: Union[str, bytes, io.IOBase, Path]) -> pd.DataFrame:
    """Reads CSV with UTF-8 BOM handling and auto-detects delimiter (comma or semicolon)."""
    if isinstance(file_or_bytes, Path):
        raw_bytes = file_or_bytes.read_bytes()
    elif isinstance(file_or_bytes, bytes):
        raw_bytes = file_or_bytes
    elif isinstance(file_or_bytes, str):
        is_path = False
        if len(file_or_bytes) <= 255 and "\n" not in file_or_bytes and "\r" not in file_or_bytes and "{" not in file_or_bytes:
            try:
                p = Path(file_or_bytes)
                if p.exists() and p.is_file():
                    is_path = True
            except OSError:
                is_path = False
        if is_path:
            raw_bytes = Path(file_or_bytes).read_bytes()
        else:
            raw_bytes = file_or_bytes.encode("utf-8")
    else:
        raw_bytes = file_or_bytes.read()
        if isinstance(raw_bytes, str):
            raw_bytes = raw_bytes.encode("utf-8")

    text = raw_bytes.decode("utf-8-sig")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";" if ";" in sample and "," not in sample else ","

    return pd.read_csv(io.StringIO(text), sep=delimiter)


def _parse_downtime(val: Any) -> List[Tuple[int, int]]:
    """Parses downtime representations into a list of (start, end) tuples."""
    if pd.isna(val) or val is None or str(val).strip() == "":
        return []
    s = str(val).strip()
    if s.startswith("[") or s.startswith("("):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list):
                result = []
                for item in parsed:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        result.append((int(item[0]), int(item[1])))
                return result
            elif isinstance(parsed, tuple) and len(parsed) == 2:
                return [(int(parsed[0]), int(parsed[1]))]
        except Exception:
            pass
    # Support "10-15; 20-25" or "10:15"
    intervals = []
    for part in s.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            sp = part.split("-")
            intervals.append((int(sp[0]), int(sp[1])))
        elif ":" in part:
            sp = part.split(":")
            intervals.append((int(sp[0]), int(sp[1])))
    return intervals


def _parse_allowed_machines(val: Any) -> Optional[List[str]]:
    """Parses allowed_machines field."""
    if pd.isna(val) or val is None or str(val).strip() == "":
        return None
    s = str(val).strip()
    if s.startswith("["):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    # Comma or semicolon or space separated
    delims = [",", ";", "|"]
    for d in delims:
        if d in s:
            return [x.strip() for x in s.split(d) if x.strip()]
    return [s] if s else None


def load_plant_from_dataframes(
    machines_df: pd.DataFrame,
    jobs_df: pd.DataFrame,
    plant_name: str = "Imported Plant",
    horizon_slots: int = 48,
    slot_minutes: int = 30,
    contract_kva: Optional[float] = None,
    power_factor: float = 0.95,
) -> Tuple[Optional[Plant], List[str]]:
    """Loads a Plant from machines and jobs DataFrames, reporting row-specific errors."""
    errors: List[str] = []
    machines: List[Machine] = []
    jobs: List[Job] = []
    machine_rows: Dict[str, int] = {}
    job_rows: Dict[str, int] = {}

    # Clean column names (strip whitespace and lowercase)
    machines_df.columns = [str(c).strip().lower() for c in machines_df.columns]
    jobs_df.columns = [str(c).strip().lower() for c in jobs_df.columns]

    # Validate required columns upfront
    req_m_cols = ["id", "run_kw", "idle_kw", "restart_kwh"]
    for col in req_m_cols:
        if col not in machines_df.columns:
            return None, [f"machines file: missing column '{col}'"]

    req_j_cols = ["id", "duration_slots"]
    for col in req_j_cols:
        if col not in jobs_df.columns:
            return None, [f"jobs file: missing column '{col}'"]

    # Validate Machines
    for idx, row in machines_df.iterrows():
        row_num = idx + 2  # 1-based, assuming header is row 1
        m_id = str(row.get("id", "")).strip()
        if not m_id or pd.isna(row.get("id")):
            errors.append(f"machines file (row {row_num}): missing machine 'id'")
            continue
        machine_rows[m_id] = row_num

        try:
            run_kw = float(row["run_kw"])
            idle_kw = float(row["idle_kw"])
            restart_kwh = float(row["restart_kwh"])
        except KeyError as e:
            errors.append(f"Machines row {row_num} (id: {m_id}): missing required column {e}")
            continue
        except (ValueError, TypeError):
            errors.append(f"Machines row {row_num} (id: {m_id}): power fields must be numeric")
            continue

        warmup_slots = 0
        if "warmup_slots" in row and not pd.isna(row["warmup_slots"]):
            try:
                warmup_slots = int(row["warmup_slots"])
            except ValueError:
                errors.append(f"Machine '{m_id}' (row {row_num}): warmup_slots must be integer")

        min_off_slots = 0
        if "min_off_slots" in row and not pd.isna(row["min_off_slots"]):
            try:
                min_off_slots = int(row["min_off_slots"])
            except ValueError:
                errors.append(f"Machine '{m_id}' (row {row_num}): min_off_slots must be integer")

        max_switch_cycles = None
        if "max_switch_cycles" in row and not pd.isna(row["max_switch_cycles"]):
            try:
                max_switch_cycles = int(row["max_switch_cycles"])
            except ValueError:
                errors.append(f"Machine '{m_id}' (row {row_num}): max_switch_cycles must be integer")

        initial_state = "idle"
        if "initial_state" in row and not pd.isna(row["initial_state"]):
            st = str(row["initial_state"]).strip().lower()
            if st in ("idle", "off"):
                initial_state = st
            else:
                errors.append(f"Machine '{m_id}' (row {row_num}): initial_state must be 'idle' or 'off'")

        downtime = []
        if "downtime" in row:
            try:
                downtime = _parse_downtime(row["downtime"])
            except Exception as e:
                errors.append(f"Machine '{m_id}' (row {row_num}): invalid downtime format: {e}")

        try:
            m = Machine(
                id=m_id,
                run_kw=run_kw,
                idle_kw=idle_kw,
                restart_kwh=restart_kwh,
                warmup_slots=warmup_slots,
                min_off_slots=min_off_slots,
                max_switch_cycles=max_switch_cycles,
                initial_state=initial_state,
                downtime=downtime,
            )
            machines.append(m)
        except Exception as e:
            errs = _clean_pydantic_errors(e, machine_rows={m_id: row_num})
            errors.extend(errs)

    # Validate Jobs
    for idx, row in jobs_df.iterrows():
        row_num = idx + 2
        j_id = str(row.get("id", "")).strip()
        if not j_id or pd.isna(row.get("id")):
            errors.append(f"jobs file (row {row_num}): missing job 'id'")
            continue
        job_rows[j_id] = row_num

        try:
            duration_slots = int(row["duration_slots"])
        except KeyError:
            errors.append(f"Job '{j_id}' (row {row_num}): missing 'duration_slots'")
            continue
        except (ValueError, TypeError):
            errors.append(f"Job '{j_id}' (row {row_num}): duration_slots must be integer")
            continue

        release_slot = 0
        if "release_slot" in row and not pd.isna(row["release_slot"]):
            try:
                release_slot = int(row["release_slot"])
            except ValueError:
                errors.append(f"Job '{j_id}' (row {row_num}): release_slot must be integer")

        deadline_slot = None
        if "deadline_slot" in row and not pd.isna(row["deadline_slot"]):
            try:
                deadline_slot = int(row["deadline_slot"])
            except ValueError:
                errors.append(f"Job '{j_id}' (row {row_num}): deadline_slot must be integer")

        allowed_machines = None
        if "allowed_machines" in row:
            allowed_machines = _parse_allowed_machines(row["allowed_machines"])

        priority = 0
        if "priority" in row and not pd.isna(row["priority"]):
            try:
                priority = int(row["priority"])
            except ValueError:
                errors.append(f"Job '{j_id}' (row {row_num}): priority must be integer")

        units = 1
        if "units" in row and not pd.isna(row["units"]):
            try:
                units = int(row["units"])
            except ValueError:
                errors.append(f"Job '{j_id}' (row {row_num}): units must be integer")

        mandatory = True
        if "mandatory" in row and not pd.isna(row["mandatory"]):
            m_val = str(row["mandatory"]).strip().lower()
            mandatory = m_val in ("true", "1", "yes", "t")

        try:
            j = Job(
                id=j_id,
                duration_slots=duration_slots,
                release_slot=release_slot,
                deadline_slot=deadline_slot,
                allowed_machines=allowed_machines,
                priority=priority,
                units=units,
                mandatory=mandatory,
            )
            jobs.append(j)
        except Exception as e:
            errs = _clean_pydantic_errors(e, job_rows={j_id: row_num})
            formatted_errs = []
            for er in errs:
                if f"Jobs row {row_num}" not in er:
                    detail = er.split(":", 1)[-1].strip() if ":" in er else er
                    formatted_errs.append(f"Jobs row {row_num} (Job {j_id}): {detail}")
                else:
                    formatted_errs.append(er)
            errors.extend(formatted_errs)

    if errors:
        return None, errors

    try:
        plant = Plant(
            name=plant_name,
            horizon_slots=horizon_slots,
            slot_minutes=slot_minutes,
            contract_kva=contract_kva,
            power_factor=power_factor,
            machines=machines,
            jobs=jobs,
        )
        return plant, []
    except Exception as e:
        clean_errs = _clean_pydantic_errors(e, job_rows=job_rows, machine_rows=machine_rows)
        return None, clean_errs


def load_plant_from_excel(
    file_or_bytes: Union[str, bytes, io.IOBase, Path],
    **plant_kwargs: Any,
) -> Tuple[Optional[Plant], List[str]]:
    """Loads plant from Excel with 'machines' and 'jobs' sheets."""
    try:
        excel_file = pd.ExcelFile(file_or_bytes)
    except Exception as e:
        return None, [f"Failed to open Excel file: {e}"]

    sheet_names = {str(s).strip().lower(): s for s in excel_file.sheet_names}
    if "machines" not in sheet_names or "jobs" not in sheet_names:
        return None, [
            f"Excel file must contain sheets 'machines' and 'jobs'. Found: {list(excel_file.sheet_names)}"
        ]

    machines_df = pd.read_excel(excel_file, sheet_name=sheet_names["machines"])
    jobs_df = pd.read_excel(excel_file, sheet_name=sheet_names["jobs"])
    return load_plant_from_dataframes(machines_df, jobs_df, **plant_kwargs)


def load_plant_from_csv_files(
    machines_csv: Union[str, bytes, io.IOBase, Path],
    jobs_csv: Union[str, bytes, io.IOBase, Path],
    **plant_kwargs: Any,
) -> Tuple[Optional[Plant], List[str]]:
    """Loads plant from separate machines and jobs CSV sources."""
    try:
        m_df = _detect_delimiter_and_read_csv(machines_csv)
    except Exception as e:
        return None, [f"Failed to read machines CSV: {e}"]

    try:
        j_df = _detect_delimiter_and_read_csv(jobs_csv)
    except Exception as e:
        return None, [f"Failed to read jobs CSV: {e}"]

    return load_plant_from_dataframes(m_df, j_df, **plant_kwargs)


def load_plant_from_json(json_str_or_path: Union[str, Path, Dict[str, Any]]) -> Tuple[Optional[Plant], List[str]]:
    """Loads and strictly validates a Plant from JSON."""
    try:
        if isinstance(json_str_or_path, dict):
            data = json_str_or_path
        elif isinstance(json_str_or_path, Path):
            with open(json_str_or_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        elif isinstance(json_str_or_path, str):
            is_path = False
            if len(json_str_or_path) <= 255 and "\n" not in json_str_or_path and "\r" not in json_str_or_path and "{" not in json_str_or_path:
                try:
                    p = Path(json_str_or_path)
                    if p.exists() and p.is_file():
                        is_path = True
                except OSError:
                    is_path = False
            if is_path:
                with open(Path(json_str_or_path), "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            else:
                data = json.loads(json_str_or_path)
        else:
            data = json.loads(str(json_str_or_path))
    except json.JSONDecodeError as e:
        return None, [f"Invalid JSON format: {e.msg} at line {e.lineno}"]
    except Exception as e:
        return None, [f"Failed to read JSON: {e}"]

    try:
        plant = Plant.model_validate(data)
        return plant, []
    except Exception as e:
        return None, _clean_pydantic_errors(e)


def load_tariff_from_hourly_csv(
    file_or_content: Union[str, bytes, io.IOBase],
    base_rate_per_kwh: float = 7.50,
    name: str = "CSV Tariff",
    source: str = "Uploaded CSV",
) -> Tuple[Optional[Tariff], List[str]]:
    """Loads a Tariff from a CSV of 24 hourly multipliers or (hour, multiplier)."""
    try:
        df = _detect_delimiter_and_read_csv(file_or_content)
    except Exception as e:
        return None, [f"Failed to read hourly tariff CSV: {e}"]

    df.columns = [str(c).strip().lower() for c in df.columns]

    multipliers: List[float] = []
    if "multiplier" in df.columns and "hour" in df.columns:
        df = df.sort_values(by="hour")
        if len(df) != 24:
            return None, [f"Expected 24 hourly values, got {len(df)} rows"]
        multipliers = [float(x) for x in df["multiplier"]]
    elif "multiplier" in df.columns:
        if len(df) != 24:
            return None, [f"Expected 24 hourly multipliers, got {len(df)} rows"]
        multipliers = [float(x) for x in df["multiplier"]]
    elif len(df.columns) == 1:
        col = df.columns[0]
        multipliers = [float(x) for x in df[col]]
        if len(multipliers) != 24:
            return None, [f"Expected 24 hourly rows, got {len(multipliers)}"]
    else:
        return None, ["CSV must have 'hour' and 'multiplier' columns or a single column of 24 multipliers"]

    # Merge contiguous hours with identical multipliers into TariffWindows
    windows: List[TariffWindow] = []
    curr_start = 0.0
    curr_mult = multipliers[0]

    for h in range(1, 24):
        if abs(multipliers[h] - curr_mult) > 1e-6:
            # Window ended
            if abs(curr_mult - 1.0) > 1e-6:
                windows.append(TariffWindow(start_hour=curr_start, end_hour=float(h), multiplier=curr_mult))
            curr_start = float(h)
            curr_mult = multipliers[h]

    # Final window
    if abs(curr_mult - 1.0) > 1e-6:
        windows.append(TariffWindow(start_hour=curr_start, end_hour=24.0, multiplier=curr_mult))

    try:
        tariff = Tariff(
            name=name,
            source=source,
            base_rate_per_kwh=base_rate_per_kwh,
            windows=windows,
        )
        return tariff, []
    except Exception as e:
        return None, [str(e)]
