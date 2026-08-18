from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from core.config import load_yaml_config
from services.bitcoin_opportunity_alerts_service import (
    BitcoinOpportunityAlertsService,
)


@dataclass(frozen=True, slots=True)
class SP500OpportunityThresholdSignal:
    event_type: str
    signal_date: date
    score: float
    thresholds: tuple[float, ...]
    recommended_pct: float
    action: str


class SP500OpportunityAlertsService:
    """Detect configured S&P 500 score crossings without coupling them to an ETF."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_yaml_config("sp500_opportunity.yaml").get(
            "alerts", {}
        )

    def detect_latest_signal(
        self, history: pd.DataFrame
    ) -> SP500OpportunityThresholdSignal | None:
        signal = BitcoinOpportunityAlertsService(self.config).detect_latest_signal(
            history
        )
        if signal is None:
            return None
        return SP500OpportunityThresholdSignal(
            event_type=(
                "sp500_opportunity_buy"
                if signal.action == "BUY"
                else "sp500_opportunity_sell"
            ),
            signal_date=signal.signal_date,
            score=signal.score,
            thresholds=signal.thresholds,
            recommended_pct=signal.recommended_pct,
            action=signal.action,
        )
