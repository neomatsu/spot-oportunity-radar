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
        "technical": {
            "min_score": 0,
            "max_score": 100,
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
            "sma50": 106.0,
            "sma200": 100.0,
            "distance_52w_low_pct": 8.0,
            "distance_52w_high_pct": -15.0,
        }
    )
    score, rationale = service.compute_technical_score(
        row,
        distance_to_support_pct=1.5,
        trend_score=85.0,
    )
    assert score > 70
    assert "RSI en zona de sobreventa" in rationale["reasons"]


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
            "sma50": 110.0,
            "sma200": 100.0,
            "distance_52w_low_pct": 10.0,
            "distance_52w_high_pct": -12.0,
        }
    )
    bearish_row = pd.Series(
        {
            "close": 85.0,
            "rsi14": 66.0,
            "sma50": 90.0,
            "sma200": 100.0,
            "distance_52w_low_pct": 35.0,
            "distance_52w_high_pct": -40.0,
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
