from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _pass_rate(validations: Any) -> float:
    if validations is None:
        return 0.0
    if isinstance(validations, (int, float)):
        return min(1.0, max(0.0, float(validations)))
    values = list(validations.values()) if isinstance(validations, Mapping) else list(validations)
    applicable = [
        item
        for item in values
        if getattr(item, "status", item.get("status") if isinstance(item, Mapping) else None)
        in {"passed", "failed"}
    ]
    return (
        sum(
            getattr(item, "status", item.get("status") if isinstance(item, Mapping) else None)
            == "passed"
            for item in applicable
        )
        / len(applicable)
        if applicable
        else 0.0
    )


def score(agreement: float, validations: Any, self_confidences: Iterable[float]) -> float:
    confidences = list(self_confidences)
    mean_self_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return 0.4 * agreement + 0.4 * _pass_rate(validations) + 0.2 * mean_self_confidence


def weighted_score(scores: Iterable[tuple[float, int]]) -> float:
    values = [(value, count) for value, count in scores if count > 0]
    total = sum(count for _, count in values)
    return sum(value * count for value, count in values) / total if total else 0.0


def overall_score(scores: Iterable[float], object_counts: Iterable[int]) -> float:
    return weighted_score(zip(scores, object_counts, strict=False))


__all__ = ["overall_score", "score", "weighted_score"]
