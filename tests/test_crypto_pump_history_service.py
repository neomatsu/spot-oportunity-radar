from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data.database import CryptoPumpSnapshotORM
from data.repositories.crypto_pump_repo import CryptoPumpRepository
from services.crypto_pump_history_service import CryptoPumpHistoryService


def _snapshot(
    *,
    chain: str = "ethereum",
    pair_address: str = "0xpair",
    detected_at: datetime,
    price_usd: float,
    classification: str = "IGNORE",
    final_score: float = 20.0,
    symbol: str = "TEST/WETH",
) -> dict:
    return {
        "chain": chain,
        "pair_address": pair_address,
        "symbol": symbol,
        "detected_at": detected_at.replace(tzinfo=None),
        "price_usd": price_usd,
        "liquidity_usd": 500_000.0,
        "volume_1h": 100_000.0,
        "volume_24h": 500_000.0,
        "buys_1h": 200,
        "sells_1h": 100,
        "price_change_1h": 5.0,
        "price_change_6h": 12.0,
        "price_change_24h": 25.0,
        "pump_momentum_score": 50.0,
        "liquidity_quality_score": 60.0,
        "transaction_quality_score": 55.0,
        "early_trend_score": 40.0,
        "prior_pump_penalty": 10.0,
        "rug_risk_score": 25.0,
        "final_speculative_score": final_score,
        "classification": classification,
    }


def test_detect_events_returns_first_occurrence_of_each_class(db_session):
    repo = CryptoPumpRepository(db_session)
    base = datetime.now(UTC) - timedelta(hours=12)

    repo.add_snapshots_bulk(
        [
            _snapshot(detected_at=base, price_usd=1.0, classification="IGNORE", final_score=20),
            _snapshot(
                detected_at=base + timedelta(hours=1),
                price_usd=1.05,
                classification="WATCH",
                final_score=40,
            ),
            _snapshot(
                detected_at=base + timedelta(hours=2),
                price_usd=1.20,
                classification="EARLY_MOMENTUM",
                final_score=60,
            ),
            _snapshot(
                detected_at=base + timedelta(hours=3),
                price_usd=1.50,
                classification="EARLY_MOMENTUM",
                final_score=68,
            ),
            _snapshot(
                detected_at=base + timedelta(hours=4),
                price_usd=2.00,
                classification="HIGH_RISK_PUMP",
                final_score=80,
            ),
        ]
    )

    service = CryptoPumpHistoryService(repo)
    report = service.build_report(chain="ethereum", pair_address="0xpair")
    events = report.events

    classes = [e.classification for e in events]
    assert classes == ["WATCH", "EARLY_MOMENTUM", "HIGH_RISK_PUMP"]
    # First WATCH happened at hour 1, not hour 2 or later.
    watch = next(e for e in events if e.classification == "WATCH")
    assert watch.timestamp == (base + timedelta(hours=1)).replace(tzinfo=None)


def test_replay_metrics_compute_max_up_and_drawdown(db_session):
    repo = CryptoPumpRepository(db_session)
    base = datetime.now(UTC) - timedelta(hours=12)
    repo.add_snapshots_bulk(
        [
            _snapshot(detected_at=base, price_usd=1.0, classification="WATCH", final_score=40),
            _snapshot(
                detected_at=base + timedelta(hours=1),
                price_usd=1.5,
                classification="EARLY_MOMENTUM",
                final_score=60,
            ),
            _snapshot(
                detected_at=base + timedelta(hours=2),
                price_usd=2.0,
                classification="HIGH_RISK_PUMP",
                final_score=80,
            ),
            _snapshot(
                detected_at=base + timedelta(hours=3),
                price_usd=1.2,
                classification="WATCH",
                final_score=45,
            ),
        ]
    )

    service = CryptoPumpHistoryService(repo)
    report = service.build_report(chain="ethereum", pair_address="0xpair")
    replay = report.replay

    assert replay.first_signal is not None
    assert replay.first_signal.classification == "WATCH"
    assert replay.max_price_after_signal == 2.0
    assert replay.max_up_pct is not None and replay.max_up_pct == 100.0
    assert replay.drawdown_after_max_pct is not None
    assert replay.drawdown_after_max_pct == -40.0


def test_replay_handles_no_snapshots(db_session):
    repo = CryptoPumpRepository(db_session)
    service = CryptoPumpHistoryService(repo)
    report = service.build_report(chain="solana", pair_address="0xempty")
    assert report.snapshots.empty
    assert report.events == []
    assert report.replay.timing_label == "NO_DATA"


def test_replay_marks_never_signaled_when_only_ignore(db_session):
    repo = CryptoPumpRepository(db_session)
    base = datetime.now(UTC) - timedelta(hours=12)
    repo.add_snapshots_bulk(
        [
            _snapshot(detected_at=base, price_usd=1.0, classification="IGNORE"),
            _snapshot(
                detected_at=base + timedelta(hours=1),
                price_usd=1.1,
                classification="IGNORE",
            ),
        ]
    )
    service = CryptoPumpHistoryService(repo)
    report = service.build_report(chain="ethereum", pair_address="0xpair")
    assert report.events == []
    assert report.replay.timing_label == "NEVER_SIGNALED"


def test_snapshots_persisted_via_repo_are_retrievable(db_session):
    repo = CryptoPumpRepository(db_session)
    base = datetime.now(UTC)
    repo.add_snapshot(_snapshot(detected_at=base, price_usd=1.0))
    repo.add_snapshot(
        _snapshot(detected_at=base + timedelta(hours=1), price_usd=1.2)
    )
    rows = repo.list_for_pair(chain="ethereum", pair_address="0xpair")
    assert len(rows) == 2
    assert isinstance(rows[0], CryptoPumpSnapshotORM)
    assert rows[0].price_usd == 1.0
    assert rows[1].price_usd == 1.2
