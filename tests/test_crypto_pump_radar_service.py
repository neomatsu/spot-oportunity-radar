from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from data.providers.dexscreener_provider import DexPair
from data.repositories.crypto_pump_repo import CryptoPumpRepository
from services.crypto_pump_radar_service import CryptoPumpRadarService


class StubProvider:
    def __init__(self, pairs: list[DexPair]) -> None:
        self._pairs = pairs
        self.search_calls: list[str] = []

    def search(self, query: str) -> list[DexPair]:
        self.search_calls.append(query)
        return list(self._pairs)

    def get_pair(self, chain: str, pair_address: str) -> DexPair | None:
        for pair in self._pairs:
            if pair.chain == chain and pair.pair_address == pair_address:
                return pair
        return None


def _pair(
    chain: str,
    pair_address: str,
    *,
    liquidity: float = 500_000,
    volume_1h: float = 100_000,
    volume_24h: float = 500_000,
    buys_1h: int = 200,
    sells_1h: int = 100,
    age_hours: float = 24.0,
    price_change_1h: float = 10.0,
    price_change_24h: float = 40.0,
) -> DexPair:
    now = datetime.now(UTC)
    return DexPair(
        chain=chain,
        dex_id="uniswap",
        pair_address=pair_address,
        symbol=f"{pair_address[-4:].upper()}/WETH",
        base_token_name="Test Token",
        base_token_address=f"0x{pair_address[-6:].zfill(6)}",
        base_token_symbol="TEST",
        quote_token_symbol="WETH",
        price_usd=0.1,
        liquidity_usd=liquidity,
        fdv=2_000_000,
        market_cap=1_500_000,
        volume_5m=10_000,
        volume_1h=volume_1h,
        volume_6h=volume_1h * 4,
        volume_24h=volume_24h,
        buys_5m=20,
        sells_5m=10,
        buys_1h=buys_1h,
        sells_1h=sells_1h,
        buys_6h=buys_1h * 4,
        sells_6h=sells_1h * 4,
        buys_24h=buys_1h * 12,
        sells_24h=sells_1h * 12,
        price_change_5m=2.0,
        price_change_1h=price_change_1h,
        price_change_6h=20.0,
        price_change_24h=price_change_24h,
        pair_created_at=now - timedelta(hours=age_hours),
        pair_age_hours=age_hours,
        url=f"https://dexscreener.com/{chain}/{pair_address}",
    )


@pytest.fixture
def base_config() -> dict:
    return {
        "scanner": {
            "chains": ["ethereum"],
            "max_candidates_per_scan": 100,
            "max_search_terms_per_chain": 1,
            "request_pause_seconds": 0,
            "request_timeout_seconds": 1.0,
            "top_n": 5,
            "min_liquidity_usd": 50_000,
            "max_liquidity_usd": 5_000_000,
            "min_volume_1h_usd": 10_000,
            "min_volume_24h_usd": 100_000,
            "min_txns_1h": 50,
            "max_pair_age_days": 180,
            "exclude_pair_age_minutes_under": 15,
        },
        "scoring": {
            "weights": {
                "pump_momentum": 0.30,
                "liquidity_quality": 0.20,
                "transaction_quality": 0.20,
                "early_trend": 0.20,
                "rug_risk": 0.25,
                "prior_pump_penalty": 0.15,
            },
            "classification_bands": {
                "ignore": 35,
                "watch": 55,
                "early_momentum": 70,
                "high_risk_pump": 85,
            },
            "pump_momentum": {
                "strong_5m_pct": 8,
                "strong_1h_pct": 25,
                "strong_6h_pct": 60,
                "accelerating_volume_ratio": 2.0,
                "bullish_buys_ratio": 1.4,
            },
            "liquidity_quality": {
                "healthy_liquidity_usd": 250_000,
                "excellent_liquidity_usd": 1_500_000,
                "max_volume_to_liquidity_ratio": 50,
            },
            "transaction_quality": {
                "healthy_txns_1h": 200,
                "excellent_txns_1h": 800,
                "min_buys_ratio": 0.45,
            },
            "early_trend": {
                "max_24h_for_early_pct": 80,
                "min_1h_for_early_pct": 3,
                "healthy_volume_growth_ratio": 1.5,
            },
            "prior_pump_penalty": {
                "excessive_24h_pct": 150,
                "excessive_6h_pct": 80,
                "historical_score_threshold": 70,
            },
            "rug_risk": {
                "very_low_liquidity_usd": 30_000,
                "max_fdv_to_liquidity_ratio": 200,
                "new_pair_age_hours": 2,
                "very_new_pair_age_hours": 0.5,
                "high_risk_chains": [],
            },
        },
    }


def test_filters_remove_pairs_below_min_liquidity(db_session, base_config):
    repo = CryptoPumpRepository(db_session)
    provider = StubProvider(
        [
            _pair("ethereum", "0xgood"),
            _pair("ethereum", "0xtinyliq", liquidity=1_000),
        ]
    )
    service = CryptoPumpRadarService(
        repository=repo, provider=provider, config=base_config
    )
    result = service.scan(persist=False, dry_run=True)
    addresses = [c.pair.pair_address for c in result.candidates]
    assert "0xgood" in addresses
    assert "0xtinyliq" not in addresses
    assert result.rejected_count >= 1


def test_filters_remove_pairs_too_young(db_session, base_config):
    repo = CryptoPumpRepository(db_session)
    provider = StubProvider(
        [
            _pair("ethereum", "0xfresh", age_hours=0.1),
            _pair("ethereum", "0xold", age_hours=24),
        ]
    )
    service = CryptoPumpRadarService(
        repository=repo, provider=provider, config=base_config
    )
    result = service.scan(persist=False, dry_run=True)
    addresses = [c.pair.pair_address for c in result.candidates]
    assert "0xfresh" not in addresses
    assert "0xold" in addresses


def test_scanner_deduplicates_repeated_pairs(db_session, base_config):
    repo = CryptoPumpRepository(db_session)
    repeated_pair = _pair("ethereum", "0xdupes")
    provider = StubProvider([repeated_pair, repeated_pair, repeated_pair])
    service = CryptoPumpRadarService(
        repository=repo, provider=provider, config=base_config
    )
    result = service.scan(persist=False, dry_run=True)
    addresses = [c.pair.pair_address for c in result.candidates]
    assert addresses.count("0xdupes") == 1


def test_scan_persists_snapshots_and_run(db_session, base_config):
    repo = CryptoPumpRepository(db_session)
    pair = _pair("ethereum", "0xpersisted")
    provider = StubProvider([pair])
    service = CryptoPumpRadarService(
        repository=repo, provider=provider, config=base_config
    )
    result = service.scan(persist=True, dry_run=False)

    snapshots = repo.list_for_pair(chain="ethereum", pair_address="0xpersisted")
    assert len(snapshots) == 1
    saved = snapshots[0]
    assert saved.symbol == pair.symbol
    assert saved.classification in {
        "IGNORE",
        "WATCH",
        "EARLY_MOMENTUM",
        "HIGH_RISK_PUMP",
        "EXTREME_SPECULATION",
    }
    assert saved.scan_run_id is not None

    runs = repo.list_recent_runs()
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].candidates_found == len(result.candidates)


def test_manual_lookup_resolves_dexscreener_url(db_session, base_config):
    repo = CryptoPumpRepository(db_session)
    pair = _pair("ethereum", "0xabc1234567890")
    provider = StubProvider([pair])
    service = CryptoPumpRadarService(
        repository=repo, provider=provider, config=base_config
    )

    found = service.manual_lookup(
        "https://dexscreener.com/ethereum/0xabc1234567890"
    )
    assert len(found) == 1
    assert found[0].pair_address == "0xabc1234567890"


def test_scan_does_not_propagate_provider_failures(db_session, base_config):
    class BoomProvider:
        def search(self, query: str) -> list[DexPair]:
            raise RuntimeError("rate limited")

        def get_pair(self, chain: str, pair_address: str) -> DexPair | None:
            return None

    repo = CryptoPumpRepository(db_session)
    service = CryptoPumpRadarService(
        repository=repo, provider=BoomProvider(), config=base_config
    )
    result = service.scan(persist=False, dry_run=True)
    assert result.candidates == []
    assert any("rate limited" in err for err in result.error_messages)
