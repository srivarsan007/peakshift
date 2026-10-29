"""Tariff lookup and calculation helpers."""

import json
from pathlib import Path
from typing import List, Union

from peakshift.core.models import Tariff
from peakshift.core.timegrid import slot_to_hour_of_day


def get_multiplier(tariff: Tariff, hour: float) -> float:
    """Find the tariff multiplier for a specific hour of the day [0, 24).
    
    If hour falls in [window.start_hour, window.end_hour), returns window.multiplier.
    Otherwise returns default 1.0.
    """
    for window in tariff.windows:
        if window.start_hour <= hour < window.end_hour:
            return window.multiplier
    return 1.0


def get_rate_at_slot(tariff: Tariff, slot: int, slot_minutes: int) -> float:
    """Calculates the rate per kWh for a given slot.
    
    rate[t] = base_rate * multiplier(t)
    where hour of slot t = (t * slot_minutes / 60) mod 24.
    """
    hour = slot_to_hour_of_day(slot, slot_minutes)
    mult = get_multiplier(tariff, hour)
    return tariff.base_rate_per_kwh * mult


def get_rate_profile(tariff: Tariff, horizon_slots: int, slot_minutes: int) -> List[float]:
    """Generates the array of rate[t] for all slots t in 0..horizon_slots-1."""
    return [get_rate_at_slot(tariff, t, slot_minutes) for t in range(horizon_slots)]


def load_tariff_from_json(path_or_str: Union[str, Path]) -> Tariff:
    """Loads a Tariff from a JSON file path or JSON string."""
    is_path = False
    if isinstance(path_or_str, Path):
        is_path = True
    elif isinstance(path_or_str, str) and len(path_or_str) <= 255 and "\n" not in path_or_str and "\r" not in path_or_str and "{" not in path_or_str:
        try:
            p = Path(path_or_str)
            if p.exists() and p.is_file():
                is_path = True
        except OSError:
            is_path = False

    if is_path:
        with open(Path(path_or_str), "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(str(path_or_str))
    return Tariff.model_validate(data)


def load_default_tariff() -> Tariff:
    """Loads the default TN HT-I tariff from data/tariffs/tn_ht1.json."""
    base_dir = Path(__file__).resolve().parent.parent.parent
    path = base_dir / "data" / "tariffs" / "tn_ht1.json"
    return load_tariff_from_json(path)
