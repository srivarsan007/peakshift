"""Timegrid calculations and slot conversions."""

from typing import Tuple


def slot_hours(slot_minutes: int) -> float:
    """Returns duration of one slot in hours."""
    return slot_minutes / 60.0


def slot_to_hour_of_day(slot: int, slot_minutes: int) -> float:
    """Returns the hour of day [0, 24) for the start of the given slot.
    
    Formula from Section 4: (t * slot_minutes / 60) mod 24.
    """
    return (slot * slot_minutes / 60.0) % 24.0


def slot_to_day_and_hour(slot: int, slot_minutes: int) -> Tuple[int, float]:
    """Returns (day_index, hour_of_day) for a given slot."""
    total_hours = slot * slot_minutes / 60.0
    day = int(total_hours // 24)
    hour = total_hours % 24.0
    return day, hour


def time_to_slot(day: int, hour: float, slot_minutes: int) -> int:
    """Converts day and hour of day to absolute slot index."""
    total_hours = day * 24.0 + hour
    return int(round(total_hours * 60.0 / slot_minutes))
