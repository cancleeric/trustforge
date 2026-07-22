"""Pure calibration metrics and direction outcome semantics."""
from __future__ import annotations

import math
from typing import Iterable


def judge_direction_hit(direction: str, change_fraction: float) -> bool:
    """Judge a prediction; unknown directions are neutral and neutral means <2%."""
    if not math.isfinite(change_fraction):
        raise ValueError("change_fraction must be finite")
    if direction == "偏多":
        return change_fraction > 0
    if direction == "偏空":
        return change_fraction < 0
    return abs(change_fraction) < 0.02


def weighted_ece(confidences: Iterable[float], hits: Iterable[bool], *, bins: int = 10) -> float:
    """Return equal-width-bin ECE: sum(n/N * abs(mean confidence - hit rate))."""
    confidence_values = list(confidences)
    hit_values = list(hits)
    if not confidence_values or len(confidence_values) != len(hit_values):
        raise ValueError("confidence and hit inputs must be non-empty and aligned")
    if type(bins) is not int or bins <= 0:
        raise ValueError("bins must be a positive integer")
    for value in confidence_values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("confidence must be finite")
        if not 0 <= value <= 1:
            raise ValueError("confidence must be within [0, 1]")
    if not all(type(hit) is bool for hit in hit_values):
        raise ValueError("hits must be boolean")
    total = len(confidence_values)
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            position
            for position, confidence in enumerate(confidence_values)
            if lower <= confidence < upper or (index == bins - 1 and confidence == 1)
        ]
        if not members:
            continue
        mean_confidence = sum(confidence_values[position] for position in members) / len(members)
        hit_rate = sum(bool(hit_values[position]) for position in members) / len(members)
        error += len(members) / total * abs(mean_confidence - hit_rate)
    return error
