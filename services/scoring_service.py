from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core.config import deep_merge_configs, load_yaml_config


class ScoringService:
    def __init__(self, config: dict | None = None, overrides: dict | None = None) -> None:
        base = config or load_yaml_config("scoring.yaml")
        self.config = deep_merge_configs(base, overrides) if overrides else base

    def compute_technical_score(
        self,
        latest_row: pd.Series,
        *,
        distance_to_support_pct: float | None,
        trend_score: float,
    ) -> tuple[float, dict[str, Any]]:
        technical_cfg = self.config["technical"]
        market_regime = self._detect_market_regime(latest_row)
        rsi_score, rsi_reason = self._rsi_contextual_score(
            latest_row.get("rsi14"),
            latest_row,
            market_regime=market_regime,
        )
        support_score, support_reason = self._support_distance_score(
            distance_to_support_pct,
            latest_row=latest_row,
        )
        trend_component = self._score_trend(latest_row)
        momentum_component = self._momentum_score(latest_row)
        volume_score, volume_reason = self._volume_confirmation_score(latest_row)

        raw_score = (
            rsi_score
            + support_score
            + trend_component["score"]
            + momentum_component["score"]
            + volume_score
        )
        final_score = self._clamp(raw_score, technical_cfg["min_score"], technical_cfg["max_score"])

        breakdown = {
            "rsi": round(rsi_score, 2),
            "support_distance": round(support_score, 2),
            "trend_structure": round(trend_component["score"], 2),
            "range_52w": round(momentum_component["score"], 2),
            "rsi_contextual": round(rsi_score, 2),
            "support_distance_continuous": round(support_score, 2),
            "trend": round(trend_component["score"], 2),
            "momentum_52w": round(momentum_component["score"], 2),
            "volume_confirmation": round(volume_score, 2),
            "ema_momentum_component": round(trend_component["components"]["ema20_vs_sma50"], 2),
            "market_regime": market_regime,
            "shock_detected": market_regime == "shock",
        }
        reasons = [reason for reason in [rsi_reason, support_reason, volume_reason] if reason]
        reasons.extend(trend_component["reasons"])
        reasons.extend(momentum_component["reasons"])

        rationale = {
            "breakdown": breakdown,
            "reasons": reasons,
            "technical_score": round(final_score, 2),
            "trend_score_input": round(trend_score, 2),
            "week_52_position": momentum_component["week_52_position"],
            "rsi_context": market_regime,
            "market_regime": market_regime,
            "shock_detected": market_regime == "shock",
            "trend_components": trend_component["components"],
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
    ) -> dict[str, float | dict[str, float] | bool]:
        technical_weight, risk_weight, portfolio_weight = self._adaptive_weights(risk_score)
        weighted_technical = technical_weight * technical_score
        weighted_risk = risk_weight * (100 - risk_score)
        weighted_portfolio = portfolio_weight * portfolio_fit_score
        final_raw = weighted_technical + weighted_risk + weighted_portfolio
        final = round(self._clamp(final_raw, 0.0, 100.0), 2)
        return {
            "technical": round(technical_score, 2),
            "risk": round(risk_score, 2),
            "portfolio_fit": round(portfolio_fit_score, 2),
            "weighted_technical": round(weighted_technical, 2),
            "weighted_inverted_risk": round(weighted_risk, 2),
            "weighted_portfolio_fit": round(weighted_portfolio, 2),
            "final": final,
            "adaptive_weights_enabled": bool(
                self.config.get("adaptive_weights", {}).get("enabled", False)
            ),
            "weights_used": {
                "technical": round(technical_weight, 4),
                "risk": round(risk_weight, 4),
                "portfolio_fit": round(portfolio_weight, 4),
            },
        }

    def _rsi_contextual_score(
        self,
        rsi: object,
        latest_row: pd.Series,
        *,
        market_regime: str | None = None,
    ) -> tuple[float, str | None]:
        cfg = self._merged_rsi_contextual_config()
        if rsi is None or pd.isna(rsi):
            return 0.0, "RSI no disponible"

        rsi_value = float(rsi)
        regime = market_regime or self._detect_market_regime(latest_row)
        if rsi_value < cfg["oversold_threshold"]:
            if regime == "uptrend":
                return float(cfg["oversold_uptrend"]), "RSI bajo dentro de una tendencia alcista"
            if regime == "shock":
                return float(cfg["oversold_shock"]), "RSI bajo dentro de un shock de mercado"
            if regime == "downtrend":
                return float(cfg["oversold_downtrend"]), "RSI bajo pero en tendencia bajista"
            return float(cfg["oversold_neutral"]), "RSI bajo en estructura neutral"

        if rsi_value <= cfg["neutral_threshold"]:
            if regime == "uptrend":
                return float(cfg["neutral_uptrend"]), "RSI neutro con tendencia alcista"
            if regime == "shock":
                return (
                    float(cfg["neutral_shock"]),
                    "RSI neutro tras un shock con recuperacion inicial",
                )
            if regime == "downtrend":
                return float(cfg["neutral_downtrend"]), "RSI neutro en tendencia bajista"
            return float(cfg["neutral_neutral"]), "RSI en zona neutra"

        return float(cfg["overbought_any"]), "RSI en zona de sobrecompra"

    def _support_distance_score(
        self,
        distance_pct: float | None,
        *,
        latest_row: pd.Series | None = None,
    ) -> tuple[float, str | None]:
        cfg = self._merged_support_distance_config()
        max_points = float(cfg["max_points"])
        atr_pct = self._atr_pct(latest_row)
        if distance_pct is None:
            return (
                max_points * float(cfg["missing_distance_score_ratio"]),
                "Soporte no disponible",
            )

        if distance_pct < 0:
            overshoot_threshold = atr_pct
            if overshoot_threshold is not None and abs(distance_pct) < overshoot_threshold:
                return (
                    max_points * float(cfg["overshoot_near_support_score_ratio"]),
                    "Precio bajo soporte pero dentro de una tolerancia normal por volatilidad",
                )
            return float(cfg["negative_distance_score"]), "Precio por debajo del soporte"

        score = max_points * math.exp(-float(cfg["decay"]) * float(distance_pct))
        if atr_pct is not None and distance_pct < atr_pct:
            score = min(max_points, score * float(cfg["atr_proximity_bonus_multiplier"]))
        if distance_pct < 2:
            reason = "Precio muy cerca de soporte"
        elif distance_pct < 5:
            reason = "Precio relativamente cerca de soporte"
        elif distance_pct <= 10:
            reason = "Precio a distancia razonable del soporte"
        else:
            reason = "Precio demasiado lejos del soporte"
        return score, reason

    def _score_trend(self, latest_row: pd.Series) -> dict[str, Any]:
        cfg = self._merged_trend_config()
        score = 0.0
        reasons: list[str] = []
        components = {
            "sma50_vs_sma200": 0.0,
            "price_vs_sma200": 0.0,
            "ema20_vs_sma50": 0.0,
        }

        close = latest_row.get("close")
        ema20 = latest_row.get("ema20")
        sma50 = latest_row.get("sma50")
        sma200 = latest_row.get("sma200")

        if pd.notna(sma50) and pd.notna(sma200):
            if float(sma50) > float(sma200):
                component = float(cfg["golden_cross_bonus"])
                score += component
                components["sma50_vs_sma200"] = component
                reasons.append("SMA50 por encima de SMA200")
            elif float(sma50) < float(sma200):
                component = float(cfg["death_cross_penalty"])
                score += component
                components["sma50_vs_sma200"] = component
                reasons.append("SMA50 por debajo de SMA200")

        if pd.notna(close) and pd.notna(sma200):
            if float(close) > float(sma200):
                component = float(cfg["price_above_sma200"])
                score += component
                components["price_vs_sma200"] = component
                reasons.append("Precio por encima de SMA200")
            else:
                component = float(cfg["price_below_sma200"])
                score += component
                components["price_vs_sma200"] = component
                reasons.append("Precio por debajo de SMA200")

        if pd.notna(ema20) and pd.notna(sma50):
            if float(ema20) > float(sma50):
                component = float(cfg["ema20_above_sma50"])
                score += component
                components["ema20_vs_sma50"] = component
                reasons.append("EMA20 por encima de SMA50")
            elif float(ema20) < float(sma50):
                component = float(cfg["ema20_below_sma50"])
                score += component
                components["ema20_vs_sma50"] = component
                reasons.append("EMA20 por debajo de SMA50")

        return {
            "score": score,
            "reasons": reasons,
            "trend_context": self._trend_context(latest_row),
            "components": components,
        }

    def _momentum_score(self, latest_row: pd.Series) -> dict[str, Any]:
        cfg = self._merged_momentum_config()
        max_points = float(cfg["max_points"])
        position_weight = float(cfg["position_weight"])
        roc_weight = float(cfg["roc_weight"])

        close = latest_row.get("close")
        week_52_low = latest_row.get("week_52_low")
        week_52_high = latest_row.get("week_52_high")

        if (
            close is None
            or week_52_low is None
            or week_52_high is None
            or pd.isna(close)
            or pd.isna(week_52_low)
            or pd.isna(week_52_high)
        ):
            return {
                "score": max_points * 0.5,
                "reasons": ["Rango 52 semanas incompleto"],
                "week_52_position": None,
            }

        range_size = float(week_52_high) - float(week_52_low)
        if range_size <= 0:
            return {
                "score": max_points * 0.5,
                "reasons": ["Rango 52 semanas degenerado"],
                "week_52_position": None,
            }

        position = (float(close) - float(week_52_low)) / range_size

        # --- Componente 1: posicion en rango 52W (logica existente) ---
        if cfg["optimal_min_position"] <= position <= cfg["optimal_max_position"]:
            position_score = max_points
            position_reason = "Momento relativo favorable dentro del rango anual"
        elif cfg["low_confirmation_position"] <= position < cfg["optimal_min_position"]:
            position_score = max_points * 0.7
            position_reason = "Precio saliendo de zona baja con confirmacion inicial"
        elif cfg["optimal_max_position"] < position <= cfg["mid_position"]:
            position_score = max_points * 0.5
            position_reason = "Precio en zona media del rango anual"
        elif cfg["mid_position"] < position <= cfg["overextended_position"]:
            position_score = max_points * 0.25
            position_reason = "Precio algo extendido dentro del rango anual"
        elif position > cfg["overextended_position"]:
            position_score = 0.0
            position_reason = "Precio demasiado cerca de maximos anuales"
        else:
            position_score = max_points * 0.3
            position_reason = "Precio demasiado pegado a minimos anuales"

        # --- Componente 2: velocidad ROC (nuevo) ---
        roc_score, roc_reason = self._roc_velocity_score(latest_row, cfg, max_points)

        # --- Combinacion ponderada ---
        score = self._clamp(
            position_weight * position_score + roc_weight * roc_score,
            0.0,
            max_points,
        )

        reasons = [position_reason]
        if roc_reason:
            reasons.append(roc_reason)

        return {
            "score": score,
            "reasons": reasons,
            "week_52_position": round(position, 4),
            "roc_velocity": roc_reason,
        }

    def _roc_velocity_score(
        self,
        latest_row: pd.Series,
        cfg: dict[str, Any],
        max_points: float,
    ) -> tuple[float, str | None]:
        roc10 = latest_row.get("roc10")
        roc20 = latest_row.get("roc20")
        threshold = float(cfg["roc_threshold_pct"])

        if roc10 is None or roc20 is None or pd.isna(roc10) or pd.isna(roc20):
            return max_points * 0.5, None  # neutral cuando no disponible

        r10, r20 = float(roc10), float(roc20)

        if r10 > threshold and r20 > 0:
            return max_points, "Momentum alcista: ROC10 y ROC20 positivos con aceleracion"
        if r10 > 0 and r20 > 0:
            return max_points * 0.70, "Momentum moderadamente positivo"
        if r10 < -threshold and r20 < 0:
            return 0.0, "Momentum bajista: ROC10 y ROC20 negativos con aceleracion"
        if r10 < 0 and r20 < 0:
            return max_points * 0.30, "Momentum moderadamente negativo"
        return max_points * 0.5, None  # señales mixtas, neutral

    def _volume_confirmation_score(
        self, latest_row: pd.Series
    ) -> tuple[float, str | None]:
        cfg = self._merged_volume_config()
        max_points = float(cfg["max_points"])
        vol_ratio = latest_row.get("vol_ratio")

        if vol_ratio is None or pd.isna(vol_ratio):
            return max_points * float(cfg["missing_ratio"]), None

        r = float(vol_ratio)
        if r >= float(cfg["high_threshold"]):
            return max_points, "Volumen elevado confirma la señal"
        if r >= float(cfg["normal_high_threshold"]):
            return max_points * float(cfg["normal_high_ratio"]), "Volumen por encima de la media"
        if r >= float(cfg["low_threshold"]):
            return max_points * float(cfg["normal_ratio"]), None
        return max_points * float(cfg["low_ratio"]), "Volumen bajo: señal sin confirmacion de participacion"

    def _adaptive_weights(self, risk_score: float) -> tuple[float, float, float]:
        adaptive_cfg = self._merged_adaptive_weights_config()
        if not adaptive_cfg["enabled"]:
            weights = self.config["weights"]
            return (
                float(weights["technical_score"]),
                float(weights["inverted_risk_score"]),
                float(weights["portfolio_fit_score"]),
            )

        if risk_score >= float(adaptive_cfg["high_risk_threshold"]):
            selected = adaptive_cfg["high_risk"]
        elif risk_score >= float(adaptive_cfg["low_risk_threshold"]):
            selected = adaptive_cfg["medium_risk"]
        else:
            selected = adaptive_cfg["low_risk"]

        return (
            float(selected["technical"]),
            float(selected["risk"]),
            float(selected["portfolio_fit"]),
        )

    @staticmethod
    def _trend_context(latest_row: pd.Series) -> str:
        sma50 = latest_row.get("sma50")
        sma200 = latest_row.get("sma200")
        if pd.notna(sma50) and pd.notna(sma200):
            if float(sma50) > float(sma200):
                return "uptrend"
            if float(sma50) < float(sma200):
                return "downtrend"
        return "neutral"

    def _detect_market_regime(self, latest_row: pd.Series) -> str:
        cfg = self._merged_market_regime_config()
        rsi = latest_row.get("rsi14")
        ema20 = latest_row.get("ema20")
        sma50 = latest_row.get("sma50")
        sma200 = latest_row.get("sma200")
        close = latest_row.get("close")
        atr14 = latest_row.get("atr14")
        atr14_avg = latest_row.get("atr14_avg")

        if (
            pd.notna(rsi)
            and pd.notna(ema20)
            and pd.notna(sma50)
            and pd.notna(atr14)
            and pd.notna(atr14_avg)
            and float(sma50) != 0
            and float(atr14_avg) > 0
        ):
            rsi_crushed = float(rsi) < float(cfg["shock_rsi_threshold"])
            ema_dislocated = float(ema20) < float(sma50) * (
                1 - float(cfg["shock_ema_below_sma50_pct"])
            )
            atr_expanded = float(atr14) > float(atr14_avg) * float(cfg["shock_atr_multiplier"])
            if rsi_crushed and ema_dislocated and atr_expanded:
                return "shock"

        if pd.notna(sma50) and pd.notna(sma200) and pd.notna(close):
            if float(sma50) < float(sma200) and float(close) < float(sma200):
                return "downtrend"
            if float(sma50) > float(sma200) and float(close) > float(sma200):
                return "uptrend"

        return "neutral"

    def _merged_rsi_contextual_config(self) -> dict[str, float]:
        defaults = {
            "oversold_threshold": 35,
            "neutral_threshold": 60,
            "oversold_uptrend": 28,
            "oversold_shock": 24,
            "oversold_neutral": 18,
            "oversold_downtrend": 8,
            "neutral_uptrend": 14,
            "neutral_shock": 14,
            "neutral_neutral": 10,
            "neutral_downtrend": 4,
            "overbought_any": 0,
        }
        configured = self.config.get("technical", {}).get("rsi_contextual", {})
        return {**defaults, **configured}

    def _merged_support_distance_config(self) -> dict[str, float]:
        defaults = {
            "max_points": 28,
            "decay": 0.35,
            "negative_distance_score": 0,
            "missing_distance_score_ratio": 0.25,
            "overshoot_near_support_score_ratio": 0.4,
            "atr_proximity_bonus_multiplier": 1.15,
        }
        configured = self.config.get("technical", {}).get("support_distance", {})
        return {**defaults, **configured}

    def _merged_trend_config(self) -> dict[str, float]:
        defaults = {
            "golden_cross_bonus": 14,
            "death_cross_penalty": -10,
            "price_above_sma200": 8,
            "price_below_sma200": -6,
            "ema20_above_sma50": 10,
            "ema20_below_sma50": -4,
        }
        configured = self.config.get("technical", {}).get("trend", {})
        if configured:
            return {**defaults, **configured}

        legacy = self.config.get("technical", {}).get("trend_structure", {})
        return {
            "golden_cross_bonus": float(legacy.get("bullish_alignment_score", 24)),
            "death_cross_penalty": float(legacy.get("bearish_penalty", -14)),
            "price_above_sma200": float(legacy.get("constructive_score", 18)) * 0.35,
            "price_below_sma200": float(legacy.get("below_sma200_penalty", -8)),
            "ema20_above_sma50": 10.0,
            "ema20_below_sma50": -4.0,
        }

    def _merged_momentum_config(self) -> dict[str, float]:
        defaults = {
            "max_points": 16,
            "optimal_min_position": 0.15,
            "optimal_max_position": 0.40,
            "low_confirmation_position": 0.10,
            "mid_position": 0.60,
            "overextended_position": 0.75,
            "position_weight": 1.00,
            "roc_weight": 0.00,
            "roc_threshold_pct": 5.0,
        }
        configured = self.config.get("technical", {}).get("momentum_52w", {})
        return {**defaults, **configured}

    def _merged_volume_config(self) -> dict[str, float]:
        defaults = {
            "max_points": 0,  # desactivado por defecto hasta implementar volumen direccional
            "high_threshold": 1.5,
            "normal_high_threshold": 1.0,
            "low_threshold": 0.7,
            "normal_high_ratio": 0.60,
            "normal_ratio": 0.40,
            "low_ratio": 0.10,
            "missing_ratio": 0.30,
        }
        configured = self.config.get("technical", {}).get("volume_confirmation", {})
        return {**defaults, **configured}

    def _merged_adaptive_weights_config(self) -> dict[str, Any]:
        defaults = {
            "enabled": False,
            "low_risk_threshold": 45,
            "high_risk_threshold": 70,
            "low_risk": {
                "technical": 0.55,
                "risk": 0.20,
                "portfolio_fit": 0.25,
            },
            "medium_risk": {
                "technical": 0.50,
                "risk": 0.30,
                "portfolio_fit": 0.20,
            },
            "high_risk": {
                "technical": 0.40,
                "risk": 0.40,
                "portfolio_fit": 0.20,
            },
        }
        configured = self.config.get("adaptive_weights", {})
        merged = {**defaults, **configured}
        for bucket in ("low_risk", "medium_risk", "high_risk"):
            merged[bucket] = {**defaults[bucket], **configured.get(bucket, {})}
        return merged

    def _merged_market_regime_config(self) -> dict[str, float]:
        defaults = {
            "shock_rsi_threshold": 35,
            "shock_ema_below_sma50_pct": 0.03,
            "shock_atr_multiplier": 1.5,
        }
        configured = self.config.get("technical", {}).get("market_regime", {})
        return {**defaults, **configured}

    @staticmethod
    def _atr_pct(latest_row: pd.Series | None) -> float | None:
        if latest_row is None:
            return None
        atr14 = latest_row.get("atr14")
        close = latest_row.get("close")
        if (
            atr14 is None
            or close is None
            or pd.isna(atr14)
            or pd.isna(close)
            or float(close) == 0
        ):
            return None
        return (float(atr14) / float(close)) * 100

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))
