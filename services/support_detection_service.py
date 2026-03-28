from __future__ import annotations

import pandas as pd

from core.models import SupportZoneModel


class SupportDetectionService:
    @staticmethod
    def detect_support_zone(frame: pd.DataFrame, lookback: int = 90) -> SupportZoneModel:
        if frame.empty or len(frame) < 20:
            return SupportZoneModel()

        window = frame.tail(lookback).copy()
        window["pivot_low"] = (
            (window["low"] < window["low"].shift(1))
            & (window["low"] < window["low"].shift(-1))
            & (window["low"] <= window["low"].rolling(5, center=True).min())
        )
        pivot_lows = window.loc[window["pivot_low"], "low"]

        if pivot_lows.empty:
            pivot_lows = window.nsmallest(5, "low")["low"]

        zone_low = float(pivot_lows.quantile(0.25))
        zone_high = float(pivot_lows.quantile(0.75))
        current_close = float(frame.iloc[-1]["close"])
        distance = ((current_close / zone_high) - 1) * 100 if zone_high else None

        return SupportZoneModel(
            support_zone_low=zone_low,
            support_zone_high=zone_high,
            distance_to_support_pct=distance,
        )
