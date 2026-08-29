from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from core.config import get_settings, load_assets_config
from core.enums import AssetType
from core.logger import configure_logging, get_logger


class Base(DeclarativeBase):
    pass


class AssetORM(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(20))
    sector: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_fundamentals: Mapped[bool] = mapped_column(Boolean, default=False)
    quote_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)

    prices: Mapped[list[PriceBarDailyORM]] = relationship(back_populates="asset")
    technical_snapshots: Mapped[list[TechnicalSnapshotORM]] = relationship(
        back_populates="asset"
    )
    fundamentals_snapshots: Mapped[list[FundamentalsSnapshotORM]] = relationship(
        back_populates="asset"
    )
    positions: Mapped[list[PortfolioPositionORM]] = relationship(back_populates="asset")
    portfolio_transactions: Mapped[list[PortfolioTransactionORM]] = relationship(
        back_populates="asset"
    )
    external_asset_mappings: Mapped[list[ExternalAssetMappingORM]] = relationship(
        back_populates="asset"
    )
    signals: Mapped[list[SignalORM]] = relationship(back_populates="asset")
    market_regime_history: Mapped[list[MarketRegimeHistoryORM]] = relationship(
        back_populates="asset"
    )
    data_status: Mapped[AssetDataStatusORM | None] = relationship(back_populates="asset")
    refresh_logs: Mapped[list[DataRefreshLogORM]] = relationship(back_populates="asset")
    backtest_trades: Mapped[list[BacktestTradeORM]] = relationship(back_populates="asset")
    backtest_portfolio_events: Mapped[list[BacktestPortfolioEventORM]] = relationship(
        back_populates="asset"
    )
    alerts: Mapped[list[AlertORM]] = relationship(back_populates="asset")
    market_events: Mapped[list[MarketEventORM]] = relationship(back_populates="asset")
    trade_intents: Mapped[list[TradeIntentORM]] = relationship(back_populates="asset")
    planned_entry_levels: Mapped[list[PlannedEntryLevelORM]] = relationship(
        back_populates="asset"
    )


class PriceBarDailyORM(Base):
    __tablename__ = "price_bars_daily"
    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_price_asset_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    quote_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_adjusted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    inserted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="prices")


class TechnicalSnapshotORM(Base):
    __tablename__ = "technical_snapshots"
    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_technical_asset_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    rsi14: Mapped[float | None] = mapped_column(Float, nullable=True)
    sma50: Mapped[float | None] = mapped_column(Float, nullable=True)
    sma200: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema20: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr14: Mapped[float | None] = mapped_column(Float, nullable=True)
    support_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    support_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_support_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="technical_snapshots")


class FundamentalsSnapshotORM(Base):
    __tablename__ = "fundamentals_snapshots"
    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_fundamentals_asset_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    fcf_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    ps: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_ebitda: Mapped[float | None] = mapped_column(Float, nullable=True)
    fundamental_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="fundamentals_snapshots")


class PortfolioPositionORM(Base):
    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), unique=True, index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)
    current_weight: Mapped[float] = mapped_column(Float, default=0.0)
    target_weight: Mapped[float] = mapped_column(Float, default=0.0)

    asset: Mapped[AssetORM] = relationship(back_populates="positions")


class PortfolioTransactionORM(Base):
    __tablename__ = "portfolio_transactions"
    __table_args__ = (
        UniqueConstraint(
            "external_source",
            "external_transaction_id",
            name="uq_portfolio_transaction_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(10), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    gross_amount: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    taxes: Mapped[float] = mapped_column(Float, default=0.0)
    transaction_currency: Mapped[str] = mapped_column(String(10), default="EUR")
    price_source: Mapped[str] = mapped_column(String(40), default="manual")
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    external_transaction_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )
    external_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    asset: Mapped[AssetORM] = relationship(back_populates="portfolio_transactions")


class ExternalAssetMappingORM(Base):
    __tablename__ = "external_asset_mappings"
    __table_args__ = (
        UniqueConstraint(
            "external_source",
            "external_asset_id",
            name="uq_external_asset_mapping_source_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_source: Mapped[str] = mapped_column(String(40), index=True)
    external_asset_id: Mapped[str] = mapped_column(String(200), index=True)
    external_symbol: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    asset: Mapped[AssetORM] = relationship(back_populates="external_asset_mappings")


class SignalORM(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_signal_asset_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    final_score: Mapped[float] = mapped_column(Float)
    recommendation: Mapped[str] = mapped_column(String(20))
    suggested_buy_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_buy_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_weight_add: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    rationale_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="signals")


class HistoricalScoreSnapshotORM(Base):
    __tablename__ = "historical_score_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "date",
            "source_version",
            "portfolio_context",
            name="uq_historical_score_asset_date_version_context",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    technical_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    portfolio_fit_score: Mapped[float] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float)
    recommendation: Mapped[str] = mapped_column(String(20))
    support_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    support_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_support_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    technical_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signal_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    source_version: Mapped[str] = mapped_column(String(80), index=True)
    portfolio_context: Mapped[str] = mapped_column(String(30), default="neutral")


class MarketRegimeHistoryORM(Base):
    __tablename__ = "market_regime_history"
    __table_args__ = (UniqueConstraint("asset_id", "date", name="uq_regime_asset_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    bull_probability: Mapped[float] = mapped_column(Float)
    bear_probability: Mapped[float] = mapped_column(Float)
    bubble_probability: Mapped[float] = mapped_column(Float)
    dominant_regime: Mapped[str] = mapped_column(String(20), index=True)
    breakdown_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    asset: Mapped[AssetORM] = relationship(back_populates="market_regime_history")


class BitcoinOpportunityHistoryORM(Base):
    __tablename__ = "bitcoin_opportunity_history"
    __table_args__ = (
        UniqueConstraint("date", "source_version", name="uq_bitcoin_opportunity_date_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification: Mapped[str] = mapped_column(String(30), index=True)
    available_components: Mapped[int] = mapped_column(default=0)
    bitcoin_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    components_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    source_version: Mapped[str] = mapped_column(String(80), index=True)


class SP500OpportunityHistoryORM(Base):
    __tablename__ = "sp500_opportunity_history"
    __table_args__ = (
        UniqueConstraint("date", "source_version", name="uq_sp500_opportunity_date_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification: Mapped[str] = mapped_column(String(40), index=True)
    available_components: Mapped[int] = mapped_column(default=0)
    sp500_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    components_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_quality_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    source_version: Mapped[str] = mapped_column(String(80), index=True)


class AppConfigORM(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class FxRateDailyORM(Base):
    __tablename__ = "fx_rates_daily"
    __table_args__ = (
        UniqueConstraint(
            "source_currency",
            "target_currency",
            "date",
            name="uq_fx_rate_currency_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source_currency: Mapped[str] = mapped_column(String(10), index=True)
    target_currency: Mapped[str] = mapped_column(String(10), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    rate: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(40), default="yfinance")
    inserted_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )


class AssetDataStatusORM(Base):
    __tablename__ = "asset_data_status"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), unique=True, index=True)
    last_available_bar_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_refresh_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_successful_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_refresh_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_refresh_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    primary_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    historical_provider_baseline: Mapped[str | None] = mapped_column(String(40), nullable=True)
    historical_coverage_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    historical_coverage_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    recent_provider_mix: Mapped[bool] = mapped_column(Boolean, default=False)
    data_mode: Mapped[str] = mapped_column(String(20), default="unknown")
    freshness_status: Mapped[str] = mapped_column(String(20), default="missing")
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="data_status")


class DataRefreshLogORM(Base):
    __tablename__ = "data_refresh_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(40))
    rows_inserted: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="refresh_logs")


class BacktestRunORM(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    name: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    train_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    train_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assets_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scenario_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="completed")

    parameter_sets: Mapped[list[BacktestParameterSetORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    metrics: Mapped[list[BacktestMetricORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    trades: Mapped[list[BacktestTradeORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class BacktestParameterSetORM(Base):
    __tablename__ = "backtest_parameter_sets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    parameters_json: Mapped[dict] = mapped_column(JSON)
    evaluation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_sample_metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    out_of_sample_metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[BacktestRunORM] = relationship(back_populates="parameter_sets")
    trades: Mapped[list[BacktestTradeORM]] = relationship(
        back_populates="parameter_set",
        cascade="all, delete-orphan",
    )


class BacktestMetricORM(Base):
    __tablename__ = "backtest_metrics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    scope: Mapped[str] = mapped_column(String(30), default="all")
    segment_type: Mapped[str] = mapped_column(String(40), default="summary")
    segment_value: Mapped[str] = mapped_column(String(120), default="all")
    metrics_json: Mapped[dict] = mapped_column(JSON)

    run: Mapped[BacktestRunORM] = relationship(back_populates="metrics")


class BacktestTradeORM(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    parameter_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_parameter_sets.id"),
        index=True,
        nullable=True,
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_type: Mapped[str] = mapped_column(String(20))
    sector: Mapped[str] = mapped_column(String(120))
    recommendation: Mapped[str] = mapped_column(String(20))
    score_band: Mapped[str] = mapped_column(String(30))
    risk_band: Mapped[str] = mapped_column(String(30))
    entry_signal_date: Mapped[date] = mapped_column(Date)
    entry_date: Mapped[date] = mapped_column(Date)
    exit_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    position_pct: Mapped[float] = mapped_column(Float, default=0.0)
    gross_return_pct: Mapped[float] = mapped_column(Float)
    net_return_pct: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    mae_pct: Mapped[float] = mapped_column(Float)
    mfe_pct: Mapped[float] = mapped_column(Float)
    holding_days: Mapped[int] = mapped_column()
    exit_reason: Mapped[str] = mapped_column(String(40))
    technical_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    portfolio_fit_score: Mapped[float] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float)
    invalidation_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parameters_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[BacktestRunORM] = relationship(back_populates="trades")
    parameter_set: Mapped[BacktestParameterSetORM | None] = relationship(back_populates="trades")
    asset: Mapped[AssetORM] = relationship(back_populates="backtest_trades")


class BacktestPortfolioEventORM(Base):
    __tablename__ = "backtest_portfolio_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(30))
    trigger_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    gross_value: Mapped[float] = mapped_column(Float, default=0.0)
    cash_before: Mapped[float] = mapped_column(Float, default=0.0)
    cash_after: Mapped[float] = mapped_column(Float, default=0.0)
    position_weight_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_weight_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    asset: Mapped[AssetORM | None] = relationship(back_populates="backtest_portfolio_events")


class AlertORM(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    alert_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(String(1000))
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivery_channels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), index=True)

    asset: Mapped[AssetORM | None] = relationship(back_populates="alerts")
    notification_logs: Mapped[list[NotificationLogORM]] = relationship(back_populates="alert")
    trade_intents: Mapped[list[TradeIntentORM]] = relationship(back_populates="source_alert")


class MarketEventORM(Base):
    __tablename__ = "market_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    event_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)

    asset: Mapped[AssetORM | None] = relationship(back_populates="market_events")


class PlannedEntryLevelORM(Base):
    __tablename__ = "planned_entry_levels"
    __table_args__ = (
        UniqueConstraint(
            "import_source",
            "external_reference",
            name="uq_planned_entry_import_reference",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    target_price: Mapped[float] = mapped_column(Float)
    price_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    suggested_weight_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_capital: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_pct: Mapped[float] = mapped_column(Float, default=1.0)
    rearm_distance_pct: Mapped[float] = mapped_column(Float, default=3.0)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    import_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    import_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_observed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_trigger_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )

    asset: Mapped[AssetORM] = relationship(back_populates="planned_entry_levels")


class TradeIntentORM(Base):
    __tablename__ = "trade_intents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    source_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alerts.id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="new")
    recommendation: Mapped[str] = mapped_column(String(20))
    final_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    suggested_buy_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_buy_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_weight_add: Mapped[float] = mapped_column(Float, default=0.0)
    suggested_capital: Mapped[float] = mapped_column(Float, default=0.0)
    invalidation: Mapped[str] = mapped_column(String(500))
    rationale_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    asset: Mapped[AssetORM] = relationship(back_populates="trade_intents")
    source_alert: Mapped[AlertORM | None] = relationship(back_populates="trade_intents")


class NotificationLogORM(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(40))
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    status: Mapped[str] = mapped_column(String(30))
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    alert: Mapped[AlertORM | None] = relationship(back_populates="notification_logs")


class ScheduledJobRunORM(Base):
    __tablename__ = "scheduled_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(120), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)


# --- Crypto Pump Radar -----------------------------------------------------------
# Modulo independiente: NO afecta scoring principal, alertas, cartera ni backtesting.

class CryptoPumpSnapshotORM(Base):
    __tablename__ = "crypto_pump_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True
    )
    chain: Mapped[str] = mapped_column(String(40), index=True)
    dex_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    pair_address: Mapped[str] = mapped_column(String(80), index=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    base_token_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    base_token_address: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    quote_token_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)

    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    fdv: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)

    volume_5m: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_6h: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)

    buys_5m: Mapped[int | None] = mapped_column(nullable=True)
    sells_5m: Mapped[int | None] = mapped_column(nullable=True)
    buys_1h: Mapped[int | None] = mapped_column(nullable=True)
    sells_1h: Mapped[int | None] = mapped_column(nullable=True)
    buys_6h: Mapped[int | None] = mapped_column(nullable=True)
    sells_6h: Mapped[int | None] = mapped_column(nullable=True)
    buys_24h: Mapped[int | None] = mapped_column(nullable=True)
    sells_24h: Mapped[int | None] = mapped_column(nullable=True)

    price_change_5m: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_6h: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_change_24h: Mapped[float | None] = mapped_column(Float, nullable=True)

    pair_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pair_age_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    pump_momentum_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    transaction_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    early_trend_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_pump_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)
    rug_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_speculative_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    scan_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("crypto_pump_scan_runs.id"), nullable=True, index=True
    )
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CryptoPumpScanRunORM(Base):
    __tablename__ = "crypto_pump_scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    query_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    chains_scanned: Mapped[list | None] = mapped_column(JSON, nullable=True)
    candidates_found: Mapped[int] = mapped_column(default=0)
    top_candidates_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)


settings = get_settings()
db_path = settings.db_url.replace("sqlite:///", "")
if settings.db_url.startswith("sqlite:///") and not Path(db_path).is_absolute():
    engine = create_engine(f"sqlite:///{settings.root_dir / db_path}", future=True)
else:
    engine = create_engine(settings.db_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

logger = get_logger(__name__)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_schema_migrations()


def ensure_schema_migrations() -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())

        if "price_bars_daily" in table_names:
            price_columns = {
                column["name"] for column in inspector.get_columns("price_bars_daily")
            }
            if "provider" not in price_columns:
                connection.execute(
                    text("ALTER TABLE price_bars_daily ADD COLUMN provider VARCHAR(40)")
                )
            if "is_adjusted" not in price_columns:
                connection.execute(
                    text("ALTER TABLE price_bars_daily ADD COLUMN is_adjusted BOOLEAN")
                )
            if "inserted_at" not in price_columns:
                connection.execute(
                    text("ALTER TABLE price_bars_daily ADD COLUMN inserted_at DATETIME")
                )
            if "quote_currency" not in price_columns:
                connection.execute(
                    text("ALTER TABLE price_bars_daily ADD COLUMN quote_currency VARCHAR(10)")
                )

        if "assets" in table_names:
            asset_columns = {column["name"] for column in inspector.get_columns("assets")}
            if "quote_currency" not in asset_columns:
                connection.execute(
                    text("ALTER TABLE assets ADD COLUMN quote_currency VARCHAR(10)")
                )

        if "asset_data_status" in table_names:
            status_columns = {
                column["name"] for column in inspector.get_columns("asset_data_status")
            }
            if "primary_provider" not in status_columns:
                connection.execute(
                    text("ALTER TABLE asset_data_status ADD COLUMN primary_provider VARCHAR(40)")
                )
            if "historical_provider_baseline" not in status_columns:
                connection.execute(
                    text(
                        "ALTER TABLE asset_data_status "
                        "ADD COLUMN historical_provider_baseline VARCHAR(40)"
                    )
                )
            if "historical_coverage_start" not in status_columns:
                connection.execute(
                    text("ALTER TABLE asset_data_status ADD COLUMN historical_coverage_start DATE")
                )
            if "historical_coverage_end" not in status_columns:
                connection.execute(
                    text("ALTER TABLE asset_data_status ADD COLUMN historical_coverage_end DATE")
                )
            if "recent_provider_mix" not in status_columns:
                connection.execute(
                    text(
                        "ALTER TABLE asset_data_status "
                        "ADD COLUMN recent_provider_mix BOOLEAN DEFAULT 0"
                    )
                )

        if "portfolio_transactions" in table_names:
            transaction_columns = {
                column["name"] for column in inspector.get_columns("portfolio_transactions")
            }
            if "price_source" not in transaction_columns:
                connection.execute(
                    text(
                        "ALTER TABLE portfolio_transactions "
                        "ADD COLUMN price_source VARCHAR(40) DEFAULT 'manual'"
                    )
                )
            if "notes" not in transaction_columns:
                connection.execute(
                    text("ALTER TABLE portfolio_transactions ADD COLUMN notes VARCHAR(500)")
                )
            if "created_at" not in transaction_columns:
                connection.execute(
                    text("ALTER TABLE portfolio_transactions ADD COLUMN created_at DATETIME")
                )
            if "taxes" not in transaction_columns:
                connection.execute(
                    text("ALTER TABLE portfolio_transactions ADD COLUMN taxes FLOAT DEFAULT 0")
                )
            if "transaction_currency" not in transaction_columns:
                connection.execute(
                    text(
                        "ALTER TABLE portfolio_transactions "
                        "ADD COLUMN transaction_currency VARCHAR(10) DEFAULT 'EUR'"
                    )
                )
            if "external_source" not in transaction_columns:
                connection.execute(
                    text(
                        "ALTER TABLE portfolio_transactions "
                        "ADD COLUMN external_source VARCHAR(40)"
                    )
                )
            if "external_transaction_id" not in transaction_columns:
                connection.execute(
                    text(
                        "ALTER TABLE portfolio_transactions "
                        "ADD COLUMN external_transaction_id VARCHAR(200)"
                    )
                )
            if "external_payload_json" not in transaction_columns:
                connection.execute(
                    text(
                        "ALTER TABLE portfolio_transactions "
                        "ADD COLUMN external_payload_json JSON"
                    )
                )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_portfolio_transaction_external_id "
                    "ON portfolio_transactions(external_source, external_transaction_id)"
                )
            )

        if "planned_entry_levels" in table_names:
            planned_entry_columns = {
                column["name"] for column in inspector.get_columns("planned_entry_levels")
            }
            if "import_source" not in planned_entry_columns:
                connection.execute(
                    text("ALTER TABLE planned_entry_levels ADD COLUMN import_source VARCHAR(40)")
                )
            if "external_reference" not in planned_entry_columns:
                connection.execute(
                    text(
                        "ALTER TABLE planned_entry_levels "
                        "ADD COLUMN external_reference VARCHAR(200)"
                    )
                )
            if "import_batch_id" not in planned_entry_columns:
                connection.execute(
                    text("ALTER TABLE planned_entry_levels ADD COLUMN import_batch_id VARCHAR(64)")
                )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_planned_entry_import_reference "
                    "ON planned_entry_levels(import_source, external_reference)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_planned_entry_import_batch "
                    "ON planned_entry_levels(import_batch_id)"
                )
            )


def seed_assets() -> None:
    from core.currency import infer_quote_currency
    from data.repositories.assets_repo import AssetsRepository

    assets_config = load_assets_config()
    yaml_symbols = {asset.symbol for asset in assets_config.assets}
    with session_scope() as session:
        repo = AssetsRepository(session)
        for asset in assets_config.assets:
            repo.upsert_asset(
                symbol=asset.symbol,
                name=asset.name,
                asset_type=AssetType(asset.asset_type).value,
                sector=asset.sector,
                region=asset.region,
                enabled=asset.enabled,
                supports_fundamentals=asset.supports_fundamentals,
                quote_currency=infer_quote_currency(asset.symbol, asset.quote_currency),
            )
        # Deshabilitar activos en DB que ya no están en el YAML
        for db_asset in repo.list_all():
            if not db_asset.quote_currency:
                db_asset.quote_currency = infer_quote_currency(db_asset.symbol)
            if db_asset.symbol not in yaml_symbols and db_asset.enabled:
                db_asset.enabled = False
        session.flush()
        session.execute(
            text(
                "UPDATE price_bars_daily "
                "SET quote_currency = ("
                "SELECT assets.quote_currency FROM assets "
                "WHERE assets.id = price_bars_daily.asset_id"
                ") WHERE quote_currency IS NULL"
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize Spot Opportunity Radar database")
    parser.add_argument("--init", action="store_true", help="Create tables and seed assets")
    args = parser.parse_args()

    configure_logging(settings.log_level)
    if args.init:
        init_db()
        seed_assets()
        logger.info("Database initialized and assets seeded.")


if __name__ == "__main__":
    main()


Base.metadata.create_all(bind=engine)
ensure_schema_migrations()
