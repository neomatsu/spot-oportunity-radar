from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

RegimeLabel = Literal["BULL", "BEAR", "TRANSITION"]


@dataclass(slots=True)
class MarketRegime:
    bull_probability: float
    bear_probability: float
    bubble_probability: float
    dominant_regime: RegimeLabel
    as_of_date: date | None = None
    breakdown: dict[str, Any] = field(default_factory=dict)

