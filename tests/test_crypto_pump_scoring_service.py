from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data.providers.dexscreener_provider import DexPair
from services.crypto_pump_scoring_service import CryptoPumpScoringService


def _base_pair(**overrides) -> DexPair:
    now = datetime.now(UTC)
    base = dict(
        chain="ethereum",
        dex_id="uniswap",
        pair_address="0xpair",
        symbol="PUMP/WETH",
        base_token_name="Pump Token",
        base_token_address="0xtoken",
        base_token_symbol="PUMP",
        quote_token_symbol="WETH",
        price_usd=0.01,
        liquidity_usd=500_000.0,
        fdv=2_000_000.0,
        market_cap=1_800_000.0,
        volume_5m=20_000.0,
        volume_1h=150_000.0,
        volume_6h=400_000.0,
        volume_24h=1_200_000.0,
        buys_5m=80,
        sells_5m=40,
        buys_1h=400,
        sells_1h=200,
        buys_6h=900,
        sells_6h=600,
        buys_24h=2_500,
        sells_24h=2_000,
        price_change_5m=4.0,
        price_change_1h=12.0,
        price_change_6h=25.0,
        price_change_24h=40.0,
        pair_created_at=now - timedelta(days=3),
        pair_age_hours=72.0,
        url="https://dexscreener.com/ethereum/0xpair",
    )
    base.update(overrides)
    return DexPair(**base)


def test_pump_momentum_responds_to_positive_movement():
    service = CryptoPumpScoringService()
    weak = _base_pair(price_change_5m=0, price_change_1h=0, price_change_6h=0)
    strong = _base_pair(price_change_5m=10, price_change_1h=30, price_change_6h=80)

    weak_score = service.score_pair(weak).pump_momentum_score
    strong_score = service.score_pair(strong).pump_momentum_score

    assert strong_score > weak_score
    assert weak_score >= 0
    assert strong_score <= 100


def test_rug_risk_high_for_very_new_pairs_with_low_liquidity():
    service = CryptoPumpScoringService()
    safe = _base_pair()
    rug_candidate = _base_pair(
        liquidity_usd=5_000.0,
        pair_age_hours=0.2,
        fdv=10_000_000.0,
    )

    safe_rug = service.score_pair(safe).rug_risk_score
    rug = service.score_pair(rug_candidate).rug_risk_score

    assert rug > safe_rug
    assert rug >= 50


def test_prior_pump_penalty_blocks_already_pumped_tokens():
    service = CryptoPumpScoringService()
    early = _base_pair(price_change_24h=40, price_change_6h=12)
    late = _base_pair(price_change_24h=400, price_change_6h=200)

    early_pen = service.score_pair(early).prior_pump_penalty
    late_pen = service.score_pair(late).prior_pump_penalty

    assert late_pen > early_pen
    assert late_pen > 30


def test_prior_high_score_in_history_adds_penalty():
    service = CryptoPumpScoringService()
    pair = _base_pair()
    without_history = service.score_pair(pair, prior_high_score_detected=False)
    with_history = service.score_pair(pair, prior_high_score_detected=True)

    assert with_history.prior_pump_penalty > without_history.prior_pump_penalty


def test_classification_bands_cover_full_range():
    service = CryptoPumpScoringService()
    assert service._classify(0) == "IGNORE"
    assert service._classify(34.9) == "IGNORE"
    assert service._classify(35) == "WATCH"
    assert service._classify(54.9) == "WATCH"
    assert service._classify(55) == "EARLY_MOMENTUM"
    assert service._classify(69.9) == "EARLY_MOMENTUM"
    assert service._classify(70) == "HIGH_RISK_PUMP"
    assert service._classify(84.9) == "HIGH_RISK_PUMP"
    assert service._classify(85) == "EXTREME_SPECULATION"
    assert service._classify(100) == "EXTREME_SPECULATION"


def test_final_score_clamped_to_zero_when_only_penalties_apply():
    service = CryptoPumpScoringService()
    nothing_pair = _base_pair(
        liquidity_usd=1_000.0,
        volume_1h=0,
        volume_6h=0,
        volume_24h=0,
        buys_1h=0,
        sells_1h=0,
        buys_6h=0,
        sells_6h=0,
        price_change_5m=0,
        price_change_1h=0,
        price_change_6h=0,
        price_change_24h=0,
        pair_age_hours=0.1,
        fdv=1_000_000_000.0,
    )
    result = service.score_pair(nothing_pair)
    assert result.final_speculative_score == 0
    assert result.classification == "IGNORE"


def test_breakdown_contains_reasons_for_audit():
    service = CryptoPumpScoringService()
    result = service.score_pair(_base_pair())
    assert "pump_momentum" in result.breakdown
    assert "reasons" in result.breakdown["pump_momentum"]
    assert isinstance(result.breakdown["pump_momentum"]["reasons"], list)


def test_handles_missing_optional_fields_gracefully():
    service = CryptoPumpScoringService()
    incomplete = _base_pair(
        fdv=None,
        market_cap=None,
        volume_5m=None,
        volume_6h=None,
        buys_6h=None,
        sells_6h=None,
        price_change_5m=None,
        pair_age_hours=None,
        pair_created_at=None,
    )
    result = service.score_pair(incomplete)
    # Aun con datos incompletos debe entregar un score valido y no romper.
    assert 0 <= result.final_speculative_score <= 100
    assert result.rug_risk_score > 0  # missing market cap/fdv penalizes risk
