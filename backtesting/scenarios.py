from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScenarioConfig:
    buy_threshold: float = 70.0
    holding_days: int = 20
