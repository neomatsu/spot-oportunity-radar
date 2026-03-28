from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
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

    prices: Mapped[list[PriceBarDailyORM]] = relationship(back_populates="asset")
    technical_snapshots: Mapped[list[TechnicalSnapshotORM]] = relationship(
        back_populates="asset"
    )
    fundamentals_snapshots: Mapped[list[FundamentalsSnapshotORM]] = relationship(
        back_populates="asset"
    )
    positions: Mapped[list[PortfolioPositionORM]] = relationship(back_populates="asset")
    signals: Mapped[list[SignalORM]] = relationship(back_populates="asset")
    data_status: Mapped[AssetDataStatusORM | None] = relationship(back_populates="asset")
    refresh_logs: Mapped[list[DataRefreshLogORM]] = relationship(back_populates="asset")


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


class AppConfigORM(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AssetDataStatusORM(Base):
    __tablename__ = "asset_data_status"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), unique=True, index=True)
    last_available_bar_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_refresh_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_successful_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_refresh_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_refresh_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
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


def seed_assets() -> None:
    from data.repositories.assets_repo import AssetsRepository

    assets_config = load_assets_config()
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
