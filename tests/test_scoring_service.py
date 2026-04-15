from __future__ import annotations

import pandas as pd

from services.scoring_service import ScoringService


def _config() -> dict:
    return {
        "weights": {
            "technical_score": 0.5,
            "inverted_risk_score": 0.25,
            "portfolio_fit_score": 0.25,
        },
        "adaptive_weights": {
            "enabled": False,
            "low_risk_threshold": 45,
            "high_risk_threshold": 70,
            "low_risk": {"technical": 0.55, "risk": 0.20, "portfolio_fit": 0.25},
            "medium_risk": {"technical": 0.50, "risk": 0.30, "portfolio_fit": 0.20},
            "high_risk": {"technical": 0.40, "risk": 0.40, "portfolio_fit": 0.20},
        },
        "technical": {
            "min_score": 0,
            "max_score": 100,
            "rsi_contextual": {
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
            },
            "market_regime": {
                "shock_rsi_threshold": 35,
                "shock_ema_below_sma50_pct": 0.03,
                "shock_atr_multiplier": 1.5,
            },
            "support_distance": {
                "max_points": 28,
                "decay": 0.35,
                "negative_distance_score": 0,
                "missing_distance_score_ratio": 0.25,
                "overshoot_near_support_score_ratio": 0.4,
                "atr_proximity_bonus_multiplier": 1.15,
            },
            "trend": {
                "golden_cross_bonus": 14,
                "death_cross_penalty": -10,
                "price_above_sma200": 8,
                "price_below_sma200": -6,
                "ema20_above_sma50": 10,
                "ema20_below_sma50": -4,
            },
            "momentum_52w": {
                "max_points": 16,
                "optimal_min_position": 0.15,
                "optimal_max_position": 0.40,
                "low_confirmation_position": 0.10,
                "mid_position": 0.60,
                "overextended_position": 0.75,
            },
            "rsi": {
                "very_positive_max": 30,
                "positive_max": 40,
                "neutral_max": 60,
                "negative_max": 70,
                "scores": {
                    "very_positive": 28,
                    "positive": 22,
                    "neutral": 14,
                    "negative": 6,
                    "very_negative": 0,
                },
            },
            "support_distance_pct": {
                "very_positive_max": 2,
                "positive_max": 5,
                "neutral_max": 10,
                "scores": {
                    "very_positive": 28,
                    "positive": 20,
                    "neutral": 10,
                    "negative": 2,
                },
            },
            "trend_structure": {
                "bullish_alignment_score": 24,
                "constructive_score": 18,
                "bearish_penalty": -14,
                "below_sma200_penalty": -8,
            },
            "range_52w": {
                "near_low_max_pct": 12,
                "near_high_min_pct": 88,
                "near_low_bonus": 12,
                "mid_range_score": 7,
                "near_high_penalty": -6,
            },
        },
    }


def test_compute_final_score_uses_configured_formula() -> None:
    service = ScoringService(config=_config())
    final_score = service.compute_final_score(
        technical_score=80,
        risk_score=40,
        portfolio_fit_score=70,
    )
    assert final_score == 72.5


def test_compute_technical_score_rewards_constructive_setup() -> None:
    service = ScoringService(config=_config())
    row = pd.Series(
        {
            "close": 110.0,
            "rsi14": 28.0,
            "ema20": 108.0,
            "sma50": 106.0,
            "sma200": 100.0,
            "atr14": 2.0,
            "atr14_avg": 1.0,
            "distance_52w_low_pct": 8.0,
            "distance_52w_high_pct": -15.0,
            "week_52_low": 90.0,
            "week_52_high": 130.0,
        }
    )
    score, rationale = service.compute_technical_score(
        row,
        distance_to_support_pct=1.5,
        trend_score=85.0,
    )
    assert score > 70
    assert any("RSI" in reason for reason in rationale["reasons"])


def test_compute_final_score_changes_with_weights() -> None:
    config = _config()
    config["weights"] = {
        "technical_score": 0.7,
        "inverted_risk_score": 0.2,
        "portfolio_fit_score": 0.1,
    }
    service = ScoringService(config=config)
    final_score = service.compute_final_score(
        technical_score=80,
        risk_score=40,
        portfolio_fit_score=70,
    )
    assert final_score == 75.0


def test_bearish_setup_scores_worse_than_bullish_setup() -> None:
    service = ScoringService(config=_config())
    bullish_row = pd.Series(
        {
            "close": 120.0,
            "rsi14": 35.0,
            "ema20": 112.0,
            "sma50": 110.0,
            "sma200": 100.0,
            "distance_52w_low_pct": 10.0,
            "distance_52w_high_pct": -12.0,
            "week_52_low": 100.0,
            "week_52_high": 150.0,
        }
    )
    bearish_row = pd.Series(
        {
            "close": 85.0,
            "rsi14": 66.0,
            "ema20": 88.0,
            "sma50": 90.0,
            "sma200": 100.0,
            "distance_52w_low_pct": 35.0,
            "distance_52w_high_pct": -40.0,
            "week_52_low": 80.0,
            "week_52_high": 160.0,
        }
    )

    bullish_score, _ = service.compute_technical_score(
        bullish_row,
        distance_to_support_pct=2.0,
        trend_score=85.0,
    )
    bearish_score, _ = service.compute_technical_score(
        bearish_row,
        distance_to_support_pct=12.0,
        trend_score=25.0,
    )

    assert bullish_score > bearish_score


def test_rsi_low_in_uptrend_scores_higher_than_low_in_downtrend() -> None:
    service = ScoringService(config=_config())
    uptrend_row = pd.Series(
        {"rsi14": 30.0, "close": 108.0, "sma50": 105.0, "sma200": 100.0}
    )
    downtrend_row = pd.Series(
        {"rsi14": 30.0, "close": 92.0, "sma50": 95.0, "sma200": 100.0}
    )

    uptrend_score, _ = service._rsi_contextual_score(uptrend_row["rsi14"], uptrend_row)
    downtrend_score, _ = service._rsi_contextual_score(downtrend_row["rsi14"], downtrend_row)

    assert uptrend_score > downtrend_score


def test_rsi_shock_scores_higher_than_downtrend() -> None:
    service = ScoringService(config=_config())
    shock_row = pd.Series(
        {
            "rsi14": 30.0,
            "close": 95.0,
            "ema20": 90.0,
            "sma50": 100.0,
            "sma200": 102.0,
            "atr14": 6.0,
            "atr14_avg": 3.0,
        }
    )
    downtrend_row = pd.Series(
        {
            "rsi14": 30.0,
            "close": 95.0,
            "ema20": 99.0,
            "sma50": 100.0,
            "sma200": 102.0,
            "atr14": 3.0,
            "atr14_avg": 3.0,
        }
    )

    shock_score, _ = service._rsi_contextual_score(shock_row["rsi14"], shock_row)
    downtrend_score, _ = service._rsi_contextual_score(downtrend_row["rsi14"], downtrend_row)

    assert shock_score > downtrend_score


def test_support_distance_score_is_continuous_and_zero_below_support() -> None:
    service = ScoringService(config=_config())

    reference_row = pd.Series({"close": 100.0, "atr14": 2.0})
    near_score, _ = service._support_distance_score(1.0, latest_row=reference_row)
    far_score, _ = service._support_distance_score(8.0, latest_row=reference_row)
    below_score, _ = service._support_distance_score(-3.0, latest_row=reference_row)
    overshoot_score, _ = service._support_distance_score(-1.0, latest_row=reference_row)
    missing_score, _ = service._support_distance_score(None)

    assert near_score > far_score > 0
    assert below_score == 0
    assert overshoot_score > 0
    assert missing_score > 0


def test_trend_score_penalizes_death_cross_symmetrically() -> None:
    service = ScoringService(config=_config())
    bullish = pd.Series({"close": 110.0, "ema20": 107.0, "sma50": 105.0, "sma200": 100.0})
    bearish = pd.Series({"close": 90.0, "ema20": 93.0, "sma50": 95.0, "sma200": 100.0})

    bullish_component = service._score_trend(bullish)
    bearish_component = service._score_trend(bearish)

    assert bullish_component["score"] == 32
    assert bearish_component["score"] == -20


def test_momentum_score_detects_optimal_zone() -> None:
    service = ScoringService(config=_config())
    favorable_row = pd.Series({"close": 115.0, "week_52_low": 100.0, "week_52_high": 150.0})
    extended_row = pd.Series({"close": 148.0, "week_52_low": 100.0, "week_52_high": 150.0})

    favorable = service._momentum_score(favorable_row)
    extended = service._momentum_score(extended_row)

    assert favorable["score"] > extended["score"]
    assert favorable["week_52_position"] is not None


def test_adaptive_weights_change_with_risk() -> None:
    config = _config()
    config["adaptive_weights"]["enabled"] = True
    service = ScoringService(config=config)

    low = service.compute_final_score_details(
        technical_score=80,
        risk_score=30,
        portfolio_fit_score=70,
    )
    high = service.compute_final_score_details(
        technical_score=80,
        risk_score=80,
        portfolio_fit_score=70,
    )

    assert low["weights_used"]["technical"] > high["weights_used"]["technical"]
    assert low["weights_used"]["risk"] < high["weights_used"]["risk"]


def test_final_score_is_clamped_to_range() -> None:
    config = _config()
    config["adaptive_weights"]["enabled"] = True
    service = ScoringService(config=config)

    result = service.compute_final_score_details(
        technical_score=100,
        risk_score=0,
        portfolio_fit_score=100,
    )

    assert 0 <= result["final"] <= 100


def _volume_enabled_config() -> dict:
    """Config con volume_confirmation habilitado para tests del componente."""
    config = _config()
    config["technical"]["volume_confirmation"] = {
        "max_points": 10,
        "high_threshold": 1.5,
        "normal_high_threshold": 1.0,
        "low_threshold": 0.7,
        "normal_high_ratio": 0.60,
        "normal_ratio": 0.40,
        "low_ratio": 0.10,
        "missing_ratio": 0.30,
    }
    return config


def test_volume_confirmation_high_volume_scores_max() -> None:
    service = ScoringService(config=_volume_enabled_config())
    cfg = service._merged_volume_config()
    max_points = float(cfg["max_points"])

    row = pd.Series({"vol_ratio": 2.0})  # well above high_threshold
    score, reason = service._volume_confirmation_score(row)

    assert score == max_points
    assert reason is not None


def test_volume_confirmation_low_volume_scores_min() -> None:
    service = ScoringService(config=_volume_enabled_config())
    cfg = service._merged_volume_config()
    max_points = float(cfg["max_points"])

    row = pd.Series({"vol_ratio": 0.4})  # below low_threshold
    score, reason = service._volume_confirmation_score(row)

    assert score == max_points * float(cfg["low_ratio"])
    assert reason is not None


def test_volume_confirmation_missing_returns_neutral() -> None:
    service = ScoringService(config=_volume_enabled_config())
    cfg = service._merged_volume_config()
    max_points = float(cfg["max_points"])

    row = pd.Series({"vol_ratio": float("nan")})
    score, reason = service._volume_confirmation_score(row)

    assert score == max_points * float(cfg["missing_ratio"])
    assert reason is None


def test_volume_confirmation_high_scores_more_than_low() -> None:
    service = ScoringService(config=_volume_enabled_config())
    row_high = pd.Series({"vol_ratio": 2.0})
    row_low = pd.Series({"vol_ratio": 0.4})

    score_high, _ = service._volume_confirmation_score(row_high)
    score_low, _ = service._volume_confirmation_score(row_low)

    assert score_high > score_low


def test_roc_velocity_bullish_scores_higher_than_bearish() -> None:
    # Explicitly enable ROC component to test its directional effect
    config = _config()
    config["technical"]["momentum_52w"]["position_weight"] = 0.70
    config["technical"]["momentum_52w"]["roc_weight"] = 0.30
    config["technical"]["momentum_52w"]["roc_threshold_pct"] = 3.0
    service = ScoringService(config=config)
    # Same 52W position (0.25, within optimal zone) — only ROC differs
    base = {
        "close": 112.5,
        "week_52_low": 100.0,
        "week_52_high": 150.0,
    }
    bullish_row = pd.Series({**base, "roc10": 5.0, "roc20": 2.0})
    bearish_row = pd.Series({**base, "roc10": -5.0, "roc20": -2.0})

    bullish = service._momentum_score(bullish_row)
    bearish = service._momentum_score(bearish_row)

    assert bullish["score"] > bearish["score"]


def test_roc_velocity_neutral_when_unavailable() -> None:
    service = ScoringService(config=_config())
    cfg = service._merged_momentum_config()
    max_points = float(cfg["max_points"])

    row_no_roc = pd.Series({"close": 100.0})
    score, reason = service._roc_velocity_score(row_no_roc, cfg, max_points)

    assert score == max_points * 0.5
    assert reason is None


def test_roc_velocity_bullish_returns_max_points() -> None:
    service = ScoringService(config=_config())
    cfg = service._merged_momentum_config()
    max_points = float(cfg["max_points"])
    threshold = float(cfg["roc_threshold_pct"])

    row = pd.Series({"roc10": threshold + 1.0, "roc20": 1.0})
    score, reason = service._roc_velocity_score(row, cfg, max_points)

    assert score == max_points
    assert reason is not None


def test_roc_velocity_bearish_returns_zero() -> None:
    service = ScoringService(config=_config())
    cfg = service._merged_momentum_config()
    max_points = float(cfg["max_points"])
    threshold = float(cfg["roc_threshold_pct"])

    row = pd.Series({"roc10": -(threshold + 1.0), "roc20": -1.0})
    score, reason = service._roc_velocity_score(row, cfg, max_points)

    assert score == 0.0
    assert reason is not None
