"""Speculative scoring for the Crypto Pump Radar.

This service is independent from the main investment scoring. Its outputs are
NEVER used to drive BUY_CANDIDATE recommendations, Telegram alerts or
portfolio sizing. They surface only inside the radar UI / CLI job.

All scores are clamped to [0, 100]. The final_speculative_score combines
positive sub-scores (momentum, liquidity quality, transactions, early trend)
with two penalties (rug_risk, prior_pump_penalty). It is then mapped to a
human-readable classification band.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import load_yaml_config
from data.providers.dexscreener_provider import DexPair


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _safe(value: float | None, default: float = 0.0) -> float:
    if value is None:
        return default
    if value != value:  # NaN
        return default
    return float(value)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    n = _safe(numerator, default=0.0)
    d = _safe(denominator, default=0.0)
    if d <= 0:
        return None
    return n / d


@dataclass(slots=True)
class PumpScoreResult:
    pump_momentum_score: float
    liquidity_quality_score: float
    transaction_quality_score: float
    early_trend_score: float
    prior_pump_penalty: float
    rug_risk_score: float
    final_speculative_score: float
    classification: str
    breakdown: dict[str, Any] = field(default_factory=dict)


class CryptoPumpScoringService:
    """Computes the speculative score for a DexScreener pair.

    The configuration comes from ``config/crypto_pump_radar.yaml`` but can be
    overridden via constructor for tests.
    """

    CLASSIFICATIONS = (
        "IGNORE",
        "WATCH",
        "EARLY_MOMENTUM",
        "HIGH_RISK_PUMP",
        "EXTREME_SPECULATION",
    )

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_yaml_config("crypto_pump_radar.yaml")
        scoring_cfg = self.config.get("scoring", {})
        self.weights = scoring_cfg.get("weights", {})
        self.bands = scoring_cfg.get("classification_bands", {})
        self.pump_cfg = scoring_cfg.get("pump_momentum", {})
        self.liquidity_cfg = scoring_cfg.get("liquidity_quality", {})
        self.tx_cfg = scoring_cfg.get("transaction_quality", {})
        self.early_cfg = scoring_cfg.get("early_trend", {})
        self.prior_cfg = scoring_cfg.get("prior_pump_penalty", {})
        self.rug_cfg = scoring_cfg.get("rug_risk", {})

    # -- Public API ----------------------------------------------------------

    def score_pair(
        self,
        pair: DexPair,
        *,
        prior_high_score_detected: bool = False,
    ) -> PumpScoreResult:
        pump_momentum, pump_details = self._pump_momentum(pair)
        liquidity, liq_details = self._liquidity_quality(pair)
        tx_quality, tx_details = self._transaction_quality(pair)
        early_trend, early_details = self._early_trend(pair)
        prior_penalty, prior_details = self._prior_pump_penalty(
            pair, prior_high_score_detected=prior_high_score_detected
        )
        rug_risk, rug_details = self._rug_risk(pair)

        weights = self.weights
        weighted = (
            weights.get("pump_momentum", 0.30) * pump_momentum
            + weights.get("liquidity_quality", 0.20) * liquidity
            + weights.get("transaction_quality", 0.20) * tx_quality
            + weights.get("early_trend", 0.20) * early_trend
            - weights.get("rug_risk", 0.25) * rug_risk
            - weights.get("prior_pump_penalty", 0.15) * prior_penalty
        )
        positive_weight_sum = (
            weights.get("pump_momentum", 0.30)
            + weights.get("liquidity_quality", 0.20)
            + weights.get("transaction_quality", 0.20)
            + weights.get("early_trend", 0.20)
        ) or 1.0
        # Re-escalado a 0-100 dividiendo por la suma positiva: las penalizaciones
        # se quedan como resta y el clamp final corta valores fuera de rango.
        final_raw = weighted / positive_weight_sum
        final = _clamp(final_raw)
        classification = self._classify(final)

        breakdown: dict[str, Any] = {
            "pump_momentum": pump_details,
            "liquidity_quality": liq_details,
            "transaction_quality": tx_details,
            "early_trend": early_details,
            "prior_pump_penalty": prior_details,
            "rug_risk": rug_details,
            "weights": weights,
            "final_raw": round(final_raw, 2),
            "final": round(final, 2),
            "classification": classification,
        }
        return PumpScoreResult(
            pump_momentum_score=round(pump_momentum, 2),
            liquidity_quality_score=round(liquidity, 2),
            transaction_quality_score=round(tx_quality, 2),
            early_trend_score=round(early_trend, 2),
            prior_pump_penalty=round(prior_penalty, 2),
            rug_risk_score=round(rug_risk, 2),
            final_speculative_score=round(final, 2),
            classification=classification,
            breakdown=breakdown,
        )

    # -- Sub-scores ----------------------------------------------------------

    def _pump_momentum(self, pair: DexPair) -> tuple[float, dict[str, Any]]:
        cfg = self.pump_cfg
        score = 0.0
        reasons: list[str] = []

        change_5m = _safe(pair.price_change_5m)
        change_1h = _safe(pair.price_change_1h)
        change_6h = _safe(pair.price_change_6h)

        strong_5m = float(cfg.get("strong_5m_pct", 8))
        strong_1h = float(cfg.get("strong_1h_pct", 25))
        strong_6h = float(cfg.get("strong_6h_pct", 60))

        # 5m: peso 25
        if change_5m > 0:
            score += 25.0 * _clamp(change_5m / strong_5m, 0.0, 1.0)
            reasons.append(f"5m {change_5m:+.1f}%")
        # 1h: peso 35
        if change_1h > 0:
            score += 35.0 * _clamp(change_1h / strong_1h, 0.0, 1.0)
            reasons.append(f"1h {change_1h:+.1f}%")
        # 6h: peso 15
        if change_6h > 0:
            score += 15.0 * _clamp(change_6h / strong_6h, 0.0, 1.0)
            reasons.append(f"6h {change_6h:+.1f}%")

        # Volumen acelerando: peso 15
        vol_1h = _safe(pair.volume_1h)
        vol_24h = _safe(pair.volume_24h)
        accel_ratio_target = float(cfg.get("accelerating_volume_ratio", 2.0))
        if vol_24h > 0 and vol_1h > 0:
            hourly_avg = vol_24h / 24.0
            if hourly_avg > 0:
                accel_ratio = vol_1h / hourly_avg
                score += 15.0 * _clamp(accel_ratio / accel_ratio_target, 0.0, 1.0)
                reasons.append(f"vol_1h/avg {accel_ratio:.2f}x")

        # Buys vs sells 1h: peso 10
        buys = _safe(pair.buys_1h)
        sells = _safe(pair.sells_1h)
        bullish_ratio = float(cfg.get("bullish_buys_ratio", 1.4))
        if buys + sells > 0:
            ratio = buys / max(sells, 1)
            score += 10.0 * _clamp(ratio / bullish_ratio, 0.0, 1.0)
            reasons.append(f"buys/sells 1h {ratio:.2f}")

        return _clamp(score), {"reasons": reasons, "score": round(score, 2)}

    def _liquidity_quality(self, pair: DexPair) -> tuple[float, dict[str, Any]]:
        cfg = self.liquidity_cfg
        score = 0.0
        reasons: list[str] = []

        liquidity = _safe(pair.liquidity_usd)
        healthy = float(cfg.get("healthy_liquidity_usd", 250000))
        excellent = float(cfg.get("excellent_liquidity_usd", 1500000))
        max_ratio = float(cfg.get("max_volume_to_liquidity_ratio", 50))

        if liquidity <= 0:
            return 0.0, {"reasons": ["no liquidity data"], "score": 0.0}

        if liquidity >= excellent:
            score += 70.0
            reasons.append(f"excellent liquidity ${liquidity:,.0f}")
        elif liquidity >= healthy:
            ratio = (liquidity - healthy) / max(excellent - healthy, 1.0)
            score += 40.0 + 30.0 * _clamp(ratio, 0.0, 1.0)
            reasons.append(f"healthy liquidity ${liquidity:,.0f}")
        else:
            ratio = liquidity / max(healthy, 1.0)
            score += 30.0 * _clamp(ratio, 0.0, 1.0)
            reasons.append(f"low liquidity ${liquidity:,.0f}")

        vol_24h = _safe(pair.volume_24h)
        if liquidity > 0:
            v_to_l = vol_24h / liquidity
            if v_to_l > max_ratio:
                penalty = min(30.0, 30.0 * (v_to_l / max_ratio - 1.0))
                score -= penalty
                reasons.append(
                    f"volume/liquidity {v_to_l:.1f}x exceeds {max_ratio:.0f}x"
                )
            else:
                # Bonus moderado por volumen saludable relativo a liquidez (max 30)
                score += 30.0 * _clamp(v_to_l / max_ratio, 0.0, 1.0)

        return _clamp(score), {"reasons": reasons, "score": round(_clamp(score), 2)}

    def _transaction_quality(self, pair: DexPair) -> tuple[float, dict[str, Any]]:
        cfg = self.tx_cfg
        score = 0.0
        reasons: list[str] = []

        buys = _safe(pair.buys_1h)
        sells = _safe(pair.sells_1h)
        total = buys + sells

        healthy = float(cfg.get("healthy_txns_1h", 200))
        excellent = float(cfg.get("excellent_txns_1h", 800))
        min_buys_ratio = float(cfg.get("min_buys_ratio", 0.45))

        if total <= 0:
            return 0.0, {"reasons": ["no txns in 1h"], "score": 0.0}

        if total >= excellent:
            score += 60.0
            reasons.append(f"{int(total)} txns 1h")
        elif total >= healthy:
            ratio = (total - healthy) / max(excellent - healthy, 1.0)
            score += 35.0 + 25.0 * _clamp(ratio, 0.0, 1.0)
            reasons.append(f"{int(total)} txns 1h")
        else:
            score += 30.0 * _clamp(total / healthy, 0.0, 1.0)
            reasons.append(f"{int(total)} txns 1h (low)")

        buys_ratio = buys / total if total > 0 else 0.0
        if buys_ratio < min_buys_ratio:
            penalty = 25.0 * (1.0 - buys_ratio / max(min_buys_ratio, 0.01))
            score -= penalty
            reasons.append(f"sells dominant ({buys_ratio:.0%} buys)")
        else:
            score += 40.0 * _clamp(
                (buys_ratio - min_buys_ratio) / max(1.0 - min_buys_ratio, 0.01),
                0.0,
                1.0,
            )
            reasons.append(f"{buys_ratio:.0%} buys")

        # Comparacion 1h vs 6h: actividad creciente
        buys_6h = _safe(pair.buys_6h)
        sells_6h = _safe(pair.sells_6h)
        total_6h = buys_6h + sells_6h
        if total_6h > 0:
            avg_hourly_6h = total_6h / 6.0
            if avg_hourly_6h > 0 and total > avg_hourly_6h:
                growth = (total - avg_hourly_6h) / avg_hourly_6h
                score += 10.0 * _clamp(growth, 0.0, 1.0)
                reasons.append(f"txn growth 1h vs 6h-avg {growth:+.1f}")

        return _clamp(score), {"reasons": reasons, "score": round(_clamp(score), 2)}

    def _early_trend(self, pair: DexPair) -> tuple[float, dict[str, Any]]:
        cfg = self.early_cfg
        score = 0.0
        reasons: list[str] = []

        change_1h = _safe(pair.price_change_1h)
        change_6h = _safe(pair.price_change_6h)
        change_24h = _safe(pair.price_change_24h)

        max_24h_early = float(cfg.get("max_24h_for_early_pct", 80))
        min_1h_early = float(cfg.get("min_1h_for_early_pct", 3))
        healthy_vol_growth = float(cfg.get("healthy_volume_growth_ratio", 1.5))

        # 24h moderado y 1h despertando
        if change_1h >= min_1h_early:
            score += 30.0 * _clamp(change_1h / (max_24h_early / 4.0), 0.0, 1.0)
            reasons.append(f"1h {change_1h:+.1f}% (waking up)")
        if 0 < change_24h <= max_24h_early:
            distance_from_extreme = (max_24h_early - change_24h) / max_24h_early
            score += 25.0 * _clamp(distance_from_extreme, 0.0, 1.0)
            reasons.append(f"24h {change_24h:+.1f}% (not parabolic yet)")
        elif change_24h > max_24h_early:
            reasons.append(f"24h {change_24h:+.1f}% already parabolic")

        # Volumen creciendo antes que el precio explote
        vol_1h = _safe(pair.volume_1h)
        vol_6h = _safe(pair.volume_6h)
        if vol_6h > 0:
            avg_hourly_6h = vol_6h / 6.0
            if avg_hourly_6h > 0:
                ratio = vol_1h / avg_hourly_6h
                if ratio >= healthy_vol_growth:
                    score += 25.0 * _clamp(ratio / (healthy_vol_growth * 2), 0.0, 1.0)
                    reasons.append(f"vol acceleration {ratio:.2f}x")

        # 1h > 6h (estructura escalonada hacia arriba)
        if change_1h > change_6h > 0:
            score += 10.0
            reasons.append("1h > 6h (stepping up)")

        # Edad razonable: ni recien nacido (<2h) ni muy maduro
        age_hours = _safe(pair.pair_age_hours, default=-1)
        if 6 <= age_hours <= 24 * 30:
            score += 10.0
            reasons.append(f"age {age_hours:.1f}h reasonable")
        elif age_hours < 2:
            reasons.append(f"age {age_hours:.1f}h too new")

        return _clamp(score), {"reasons": reasons, "score": round(_clamp(score), 2)}

    def _prior_pump_penalty(
        self,
        pair: DexPair,
        *,
        prior_high_score_detected: bool,
    ) -> tuple[float, dict[str, Any]]:
        cfg = self.prior_cfg
        score = 0.0
        reasons: list[str] = []

        change_24h = _safe(pair.price_change_24h)
        change_6h = _safe(pair.price_change_6h)

        excessive_24h = float(cfg.get("excessive_24h_pct", 150))
        excessive_6h = float(cfg.get("excessive_6h_pct", 80))

        if change_24h >= excessive_24h:
            score += 60.0 * _clamp(change_24h / (excessive_24h * 2), 0.0, 1.0) + 20.0
            reasons.append(f"24h {change_24h:+.1f}% already extreme")
        elif change_24h >= excessive_24h * 0.6:
            score += 30.0 * _clamp(
                (change_24h - excessive_24h * 0.6)
                / max(excessive_24h * 0.4, 1.0),
                0.0,
                1.0,
            )
            reasons.append(f"24h {change_24h:+.1f}% getting hot")

        if change_6h >= excessive_6h:
            score += 30.0 * _clamp(change_6h / (excessive_6h * 2), 0.0, 1.0) + 10.0
            reasons.append(f"6h {change_6h:+.1f}% extreme")

        if prior_high_score_detected:
            score += 25.0
            reasons.append("prior high speculative score detected in history")

        return _clamp(score), {"reasons": reasons, "score": round(_clamp(score), 2)}

    def _rug_risk(self, pair: DexPair) -> tuple[float, dict[str, Any]]:
        cfg = self.rug_cfg
        score = 0.0
        reasons: list[str] = []

        liquidity = _safe(pair.liquidity_usd)
        very_low_liq = float(cfg.get("very_low_liquidity_usd", 30000))
        max_fdv_to_liq = float(cfg.get("max_fdv_to_liquidity_ratio", 200))
        new_age_hours = float(cfg.get("new_pair_age_hours", 2))
        very_new_age_hours = float(cfg.get("very_new_pair_age_hours", 0.5))
        high_risk_chains = {c.lower() for c in cfg.get("high_risk_chains", [])}

        # Liquidez muy baja => muy peligroso
        if liquidity <= 0:
            score += 50.0
            reasons.append("no liquidity reported")
        elif liquidity < very_low_liq:
            ratio = liquidity / max(very_low_liq, 1.0)
            score += 50.0 * (1.0 - _clamp(ratio, 0.0, 1.0))
            reasons.append(f"liquidity ${liquidity:,.0f} below floor")

        # FDV/Liquidity desproporcionado
        fdv = _safe(pair.fdv)
        if fdv > 0 and liquidity > 0:
            ratio = fdv / liquidity
            if ratio > max_fdv_to_liq:
                score += min(30.0, 30.0 * (ratio / max_fdv_to_liq - 1.0))
                reasons.append(f"FDV/Liq {ratio:.0f}x extreme")

        # Edad muy nueva
        age_hours = _safe(pair.pair_age_hours, default=-1)
        if 0 <= age_hours < very_new_age_hours:
            score += 35.0
            reasons.append(f"pair very new ({age_hours:.2f}h)")
        elif 0 <= age_hours < new_age_hours:
            penalty = 25.0 * (1.0 - age_hours / max(new_age_hours, 0.01))
            score += penalty
            reasons.append(f"pair young ({age_hours:.2f}h)")

        # Market cap desconocido
        if pair.market_cap is None and pair.fdv is None:
            score += 10.0
            reasons.append("unknown market cap and FDV")

        # Datos insuficientes
        if pair.volume_24h is None or pair.price_usd is None:
            score += 15.0
            reasons.append("incomplete data fields")

        # Chain en lista de riesgo
        if pair.chain and pair.chain.lower() in high_risk_chains:
            score += 10.0
            reasons.append(f"chain '{pair.chain}' flagged as high-risk")

        # Volume / liquidity extremo
        if liquidity > 0:
            vol_24h = _safe(pair.volume_24h)
            ratio = vol_24h / liquidity
            extreme = float(self.liquidity_cfg.get("max_volume_to_liquidity_ratio", 50))
            if ratio > extreme * 1.5:
                score += 15.0
                reasons.append(f"vol/liq {ratio:.0f}x suspicious")

        return _clamp(score), {"reasons": reasons, "score": round(_clamp(score), 2)}

    # -- Classification ------------------------------------------------------

    def _classify(self, score: float) -> str:
        bands = self.bands
        ignore = float(bands.get("ignore", 35))
        watch = float(bands.get("watch", 55))
        early = float(bands.get("early_momentum", 70))
        high_risk = float(bands.get("high_risk_pump", 85))

        if score < ignore:
            return "IGNORE"
        if score < watch:
            return "WATCH"
        if score < early:
            return "EARLY_MOMENTUM"
        if score < high_risk:
            return "HIGH_RISK_PUMP"
        return "EXTREME_SPECULATION"


def classify_score(score: float, config: dict | None = None) -> str:
    """Module-level helper used by replay code that does not want to build a service."""

    return CryptoPumpScoringService(config=config)._classify(score)
