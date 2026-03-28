from __future__ import annotations

from typing import Any

import pandas as pd

from core.config import load_yaml_config


class ScoringService:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_yaml_config("scoring.yaml")

    def compute_technical_score(
        self,
        latest_row: pd.Series,
        *,
        distance_to_support_pct: float | None,
        trend_score: float,
    ) -> tuple[float, dict[str, Any]]:
        technical_cfg = self.config["technical"]

        rsi_score, rsi_reason = self._score_rsi(latest_row.get("rsi14"))
        support_score, support_reason = self._score_support(distance_to_support_pct)
        trend_component = self._score_trend(latest_row, trend_score)
        range_component = self._score_52w_range(latest_row)

        raw_score = (
            rsi_score
            + support_score
            + trend_component["score"]
            + range_component["score"]
        )
        final_score = self._clamp(raw_score, technical_cfg["min_score"], technical_cfg["max_score"])

        breakdown = {
            "rsi": round(rsi_score, 2),
            "support_distance": round(support_score, 2),
            "trend_structure": round(trend_component["score"], 2),
            "range_52w": round(range_component["score"], 2),
        }
        reasons = [reason for reason in [rsi_reason, support_reason] if reason]
        reasons.extend(trend_component["reasons"])
        reasons.extend(range_component["reasons"])

        rationale = {
            "breakdown": breakdown,
            "reasons": reasons,
            "technical_score": round(final_score, 2),
            "trend_score_input": round(trend_score, 2),
        }
        return final_score, rationale

    def compute_final_score(
        self,
        *,
        technical_score: float,
        risk_score: float,
        portfolio_fit_score: float,
    ) -> float:
        return self.compute_final_score_details(
            technical_score=technical_score,
            risk_score=risk_score,
            portfolio_fit_score=portfolio_fit_score,
        )["final"]

    def compute_final_score_details(
        self,
        *,
        technical_score: float,
        risk_score: float,
        portfolio_fit_score: float,
    ) -> dict[str, float]:
        weights = self.config["weights"]
        weighted_technical = weights["technical_score"] * technical_score
        weighted_risk = weights["inverted_risk_score"] * (100 - risk_score)
        weighted_portfolio = weights["portfolio_fit_score"] * portfolio_fit_score
        final = round(weighted_technical + weighted_risk + weighted_portfolio, 2)
        return {
            "technical": round(technical_score, 2),
            "risk": round(risk_score, 2),
            "portfolio_fit": round(portfolio_fit_score, 2),
            "weighted_technical": round(weighted_technical, 2),
            "weighted_inverted_risk": round(weighted_risk, 2),
            "weighted_portfolio_fit": round(weighted_portfolio, 2),
            "final": final,
        }

    def _score_rsi(self, rsi: object) -> tuple[float, str | None]:
        cfg = self.config["technical"]["rsi"]
        if rsi is None or pd.isna(rsi):
            return 0.0, "RSI no disponible"

        rsi_value = float(rsi)
        if rsi_value < cfg["very_positive_max"]:
            return float(cfg["scores"]["very_positive"]), "RSI en zona de sobreventa"
        if rsi_value < cfg["positive_max"]:
            return float(cfg["scores"]["positive"]), "RSI en zona favorable"
        if rsi_value <= cfg["neutral_max"]:
            return float(cfg["scores"]["neutral"]), "RSI en zona neutra"
        if rsi_value <= cfg["negative_max"]:
            return float(cfg["scores"]["negative"]), "RSI empieza a exigir prudencia"
        return float(cfg["scores"]["very_negative"]), "RSI en zona de sobrecompra"

    def _score_support(self, distance_to_support_pct: float | None) -> tuple[float, str | None]:
        cfg = self.config["technical"]["support_distance_pct"]
        if distance_to_support_pct is None:
            return 0.0, "Soporte no disponible"

        distance = abs(distance_to_support_pct)
        if distance < cfg["very_positive_max"]:
            return float(cfg["scores"]["very_positive"]), "Precio muy cerca de soporte"
        if distance < cfg["positive_max"]:
            return float(cfg["scores"]["positive"]), "Precio relativamente cerca de soporte"
        if distance <= cfg["neutral_max"]:
            return float(cfg["scores"]["neutral"]), "Precio a distancia razonable del soporte"
        return float(cfg["scores"]["negative"]), "Precio demasiado lejos del soporte"

    def _score_trend(self, latest_row: pd.Series, trend_score: float) -> dict[str, Any]:
        cfg = self.config["technical"]["trend_structure"]
        score = 0.0
        reasons: list[str] = []

        close = latest_row.get("close")
        sma50 = latest_row.get("sma50")
        sma200 = latest_row.get("sma200")

        if pd.notna(sma50) and pd.notna(sma200):
            if float(sma50) > float(sma200):
                score += float(cfg["bullish_alignment_score"])
                reasons.append("SMA50 por encima de SMA200")
            else:
                score += float(cfg["bearish_penalty"])
                reasons.append("SMA50 por debajo de SMA200")

        if pd.notna(close) and pd.notna(sma200) and float(close) < float(sma200):
            score += float(cfg["below_sma200_penalty"])
            reasons.append("Precio por debajo de SMA200")
        elif pd.notna(close) and pd.notna(sma200):
            score += float(cfg["constructive_score"]) * 0.35
            reasons.append("Precio por encima de SMA200")

        # Reuse the precomputed trend score as a softer adjustment instead of a separate hard rule.
        score += ((trend_score - 50) / 50) * (float(cfg["constructive_score"]) * 0.35)

        return {"score": score, "reasons": reasons}

    def _score_52w_range(self, latest_row: pd.Series) -> dict[str, Any]:
        cfg = self.config["technical"]["range_52w"]
        score = 0.0
        reasons: list[str] = []

        low_pct = latest_row.get("distance_52w_low_pct")
        high_pct = latest_row.get("distance_52w_high_pct")

        if low_pct is not None and pd.notna(low_pct) and float(low_pct) <= cfg["near_low_max_pct"]:
            score += float(cfg["near_low_bonus"])
            reasons.append("Precio relativamente cerca de minimos de 52 semanas")
        elif low_pct is not None and pd.notna(low_pct):
            score += float(cfg["mid_range_score"])

        if high_pct is not None and pd.notna(high_pct):
            percentile_to_high = 100 + float(high_pct)
            if percentile_to_high >= cfg["near_high_min_pct"]:
                score += float(cfg["near_high_penalty"])
                reasons.append("Precio cerca de maximos de 52 semanas")

        return {"score": score, "reasons": reasons}

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))
