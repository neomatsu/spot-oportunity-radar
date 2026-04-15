from __future__ import annotations

import numpy as np
import pandas as pd


class TechnicalService:
    @staticmethod
    def compute_indicators(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame.copy()

        result = frame.copy()
        close = result["close"]
        high = result["high"]
        low = result["low"]

        delta = close.diff()
        gains = delta.clip(lower=0.0)
        losses = -delta.clip(upper=0.0)
        avg_gain = gains.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        avg_loss = losses.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.where(avg_loss != 0, 100.0)
        rsi = rsi.where(avg_gain != 0, 0.0)
        rsi = rsi.where((avg_gain != 0) | (avg_loss != 0), 50.0)
        result["rsi14"] = rsi

        result["sma50"] = close.rolling(window=50, min_periods=50).mean()
        result["sma200"] = close.rolling(window=200, min_periods=200).mean()
        result["ema20"] = close.ewm(span=20, adjust=False).mean()

        prev_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        result["atr14"] = true_range.rolling(window=14, min_periods=14).mean()
        result["atr14_avg"] = result["atr14"].rolling(window=63, min_periods=20).mean()

        rolling_high = close.rolling(window=252, min_periods=20).max()
        rolling_low = close.rolling(window=252, min_periods=20).min()
        result["week_52_high"] = rolling_high
        result["week_52_low"] = rolling_low
        result["distance_52w_high_pct"] = ((close / rolling_high) - 1) * 100
        result["distance_52w_low_pct"] = ((close / rolling_low) - 1) * 100

        result["roc10"] = close.pct_change(periods=10) * 100
        result["roc20"] = close.pct_change(periods=20) * 100

        volume = result["volume"]
        vol_sma20 = volume.rolling(window=20, min_periods=10).mean()
        result["vol_sma20"] = vol_sma20
        result["vol_ratio"] = volume / vol_sma20.replace(0, np.nan)
        vol_sma5 = volume.rolling(window=5, min_periods=3).mean()
        result["vol_trend"] = vol_sma5 / vol_sma20.replace(0, np.nan)

        return result

    @staticmethod
    def trend_structure_score(latest: pd.Series) -> tuple[float, dict[str, float | str]]:
        score = 0.0
        rationale: dict[str, float | str] = {}
        close = float(latest["close"])
        ema20 = latest.get("ema20")
        sma50 = latest.get("sma50")
        sma200 = latest.get("sma200")

        if pd.notna(ema20) and pd.notna(sma50) and pd.notna(sma200):
            if close > ema20 > sma50 > sma200:
                score = 100.0
                rationale["trend"] = "strong_uptrend"
            elif close > sma200 and sma50 > sma200:
                score = 70.0
                rationale["trend"] = "constructive"
            elif close > sma200:
                score = 55.0
                rationale["trend"] = "mixed_above_long_term"
            else:
                score = 25.0
                rationale["trend"] = "weak_below_long_term"
        return score, rationale
