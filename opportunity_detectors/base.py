from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class OpportunityComponent:
    key: str
    label: str
    score: float | None
    raw_value: float | None
    description: str
    source: str
    as_of: date | None = None
    percentile: float | None = None
    available: bool = True
    point_in_time_safe: bool = True
    error: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "available", self.score is not None and self.available)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat() if self.as_of else None
        return payload


def weighted_available_score(
    components: list[OpportunityComponent],
    weights: dict[str, float],
    *,
    minimum_available: int,
) -> tuple[float | None, dict[str, float]]:
    available = [component for component in components if component.available]
    if len(available) < minimum_available:
        return None, {}
    total_weight = sum(max(0.0, float(weights.get(item.key, 0.0))) for item in available)
    if total_weight <= 0:
        return None, {}
    normalized = {
        item.key: max(0.0, float(weights.get(item.key, 0.0))) / total_weight
        for item in available
    }
    score = sum(float(item.score) * normalized[item.key] for item in available) * 10.0
    return max(0.0, min(100.0, score)), normalized


def classify_score(score: float | None, bands: list[dict[str, Any]]) -> str:
    if score is None:
        return "DATOS_INSUFICIENTES"
    ordered = sorted(bands, key=lambda row: float(row["min_score"]), reverse=True)
    for band in ordered:
        if score >= float(band["min_score"]):
            return str(band["label"])
    return str(ordered[-1]["label"]) if ordered else "SIN_CLASIFICAR"
