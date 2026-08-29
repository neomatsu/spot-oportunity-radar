from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from core.config import load_yaml_config
from data.repositories.alerts_repo import AlertsRepository
from data.repositories.bitcoin_opportunity_repo import BitcoinOpportunityRepository
from data.repositories.job_runs_repo import JobRunsRepository
from data.repositories.portfolio_repo import PortfolioRepository
from data.repositories.sp500_opportunity_repo import SP500OpportunityRepository
from services.watchlist_service import WatchlistService


@dataclass(frozen=True, slots=True)
class DashboardDetectorSnapshot:
    key: str
    label: str
    score: float | None
    classification: str
    price: float | None
    price_label: str
    price_date: date | None
    available_components: int
    score_change: float | None
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DashboardPortfolioSnapshot:
    total_capital: float
    invested_cost: float
    market_value: float
    estimated_cash: float
    pnl: float
    pnl_pct: float | None
    exposure_pct: float
    positions_count: int
    largest_position_pct: float
    allocation: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DashboardJobSnapshot:
    status: str
    finished_at: datetime | None
    errors: int


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    bitcoin: DashboardDetectorSnapshot
    sp500: DashboardDetectorSnapshot
    portfolio: DashboardPortfolioSnapshot
    opportunities: tuple[dict[str, Any], ...]
    attention_items: tuple[dict[str, Any], ...]
    watched_assets: int
    ready_assets: int
    buy_candidates: int
    stale_assets: int
    last_job: DashboardJobSnapshot | None


class DashboardService:
    """Builds the dashboard exclusively from persisted application state."""

    _ATTENTION_GROUPS = {"position_management", "planned_entry", "risk"}

    def __init__(self, session: Session, *, now: datetime | None = None) -> None:
        self.session = session
        self.now = now or datetime.now(UTC)
        self.watchlist_service = WatchlistService(session)
        self.portfolio_repo = PortfolioRepository(session)
        self.alerts_repo = AlertsRepository(session)
        self.job_runs_repo = JobRunsRepository(session)

    def build(self) -> DashboardSnapshot:
        rows = self.watchlist_service.get_watchlist_rows()
        ready = [row for row in rows if row.get("final_opportunity_score") is not None]
        opportunities = [
            row
            for row in ready
            if row.get("recommendation") in {"BUY_CANDIDATE", "WATCH"}
        ]
        opportunities.sort(
            key=lambda row: float(row.get("final_opportunity_score") or 0),
            reverse=True,
        )

        return DashboardSnapshot(
            bitcoin=self._bitcoin_snapshot(),
            sp500=self._sp500_snapshot(),
            portfolio=self._portfolio_snapshot(),
            opportunities=tuple(opportunities[:10]),
            attention_items=self._attention_items(),
            watched_assets=len(rows),
            ready_assets=len(ready),
            buy_candidates=sum(
                row.get("recommendation") == "BUY_CANDIDATE" for row in ready
            ),
            stale_assets=sum(
                row.get("freshness_status") != "fresh" or row.get("data_mode") != "real"
                for row in rows
            ),
            last_job=self._last_job(),
        )

    def _bitcoin_snapshot(self) -> DashboardDetectorSnapshot:
        config = load_yaml_config("bitcoin_opportunity.yaml")
        version = str(config.get("history", {}).get("source_version", "bitcoin_opportunity_v1"))
        history = BitcoinOpportunityRepository(self.session).history(
            start_date=self.now.date() - timedelta(days=120),
            end_date=self.now.date(),
            source_version=version,
        )
        return self._detector_snapshot(
            history=history,
            key="bitcoin",
            label="Bitcoin Opportunity",
            price_column="bitcoin_price",
            price_label="BTC",
        )

    def _sp500_snapshot(self) -> DashboardDetectorSnapshot:
        config = load_yaml_config("sp500_opportunity.yaml")
        version = str(config.get("history", {}).get("source_version", "sp500_opportunity_v1"))
        history = SP500OpportunityRepository(self.session).history(
            start_date=self.now.date() - timedelta(days=120),
            end_date=self.now.date(),
            source_version=version,
        )
        return self._detector_snapshot(
            history=history,
            key="sp500",
            label="S&P 500 Opportunity",
            price_column="sp500_price",
            price_label="S&P 500",
        )

    @staticmethod
    def _detector_snapshot(
        *, history, key: str, label: str, price_column: str, price_label: str
    ) -> DashboardDetectorSnapshot:
        if history.empty:
            return DashboardDetectorSnapshot(
                key=key,
                label=label,
                score=None,
                classification="DATOS_INSUFICIENTES",
                price=None,
                price_label=price_label,
                price_date=None,
                available_components=0,
                score_change=None,
            )

        ordered = history.sort_values("date").copy()
        latest = ordered.iloc[-1]
        previous = ordered.iloc[max(0, len(ordered) - 6)]
        latest_score = DashboardService._optional_float(latest.get("overall_score"))
        previous_score = DashboardService._optional_float(previous.get("overall_score"))
        score_change = (
            latest_score - previous_score
            if latest_score is not None and previous_score is not None and len(ordered) > 1
            else None
        )
        chart_rows = tuple(
            {
                "date": row.date,
                "score": DashboardService._optional_float(row.overall_score),
                "price": DashboardService._optional_float(getattr(row, price_column)),
            }
            for row in ordered.tail(90).itertuples(index=False)
        )
        return DashboardDetectorSnapshot(
            key=key,
            label=label,
            score=latest_score,
            classification=str(latest.get("classification") or "DATOS_INSUFICIENTES"),
            price=DashboardService._optional_float(latest.get(price_column)),
            price_label=price_label,
            price_date=latest["date"],
            available_components=int(latest.get("available_components") or 0),
            score_change=score_change,
            history=chart_rows,
        )

    def _portfolio_snapshot(self) -> DashboardPortfolioSnapshot:
        positions = self.watchlist_service.get_positions_rows()
        total_capital = self.portfolio_repo.get_total_capital(default=0.0)
        invested_cost = sum(
            float(row.get("quantity") or 0) * float(row.get("avg_cost") or 0)
            for row in positions
        )
        exposure = sum(float(row.get("current_weight") or 0) for row in positions)
        market_value = total_capital * exposure if total_capital > 0 else invested_cost
        pnl = market_value - invested_cost
        allocation = tuple(
            {
                "symbol": row["symbol"],
                "weight": float(row.get("current_weight") or 0),
            }
            for row in sorted(
                positions,
                key=lambda item: float(item.get("current_weight") or 0),
                reverse=True,
            )
            if float(row.get("current_weight") or 0) > 0
        )
        return DashboardPortfolioSnapshot(
            total_capital=total_capital,
            invested_cost=invested_cost,
            market_value=market_value,
            estimated_cash=max(0.0, total_capital - invested_cost),
            pnl=pnl,
            pnl_pct=(pnl / invested_cost * 100) if invested_cost > 0 else None,
            exposure_pct=exposure * 100,
            positions_count=len(positions),
            largest_position_pct=max(
                (float(row.get("current_weight") or 0) * 100 for row in positions),
                default=0.0,
            ),
            allocation=allocation,
        )

    def _attention_items(self) -> tuple[dict[str, Any], ...]:
        boundary = self.now - timedelta(days=7)
        latest_by_key: dict[str, dict[str, Any]] = {}
        for alert in self.alerts_repo.list_recent(limit=200):
            created_at = alert.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            payload = alert.payload_json or {}
            if created_at < boundary or payload.get("alert_group") not in self._ATTENTION_GROUPS:
                continue
            if alert.dedupe_key in latest_by_key:
                continue
            latest_by_key[alert.dedupe_key] = {
                "symbol": alert.symbol,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "title": alert.title,
                "action": payload.get("action_suggestion") or alert.message,
                "created_at": alert.created_at,
            }
            if len(latest_by_key) >= 8:
                break
        return tuple(latest_by_key.values())

    def _last_job(self) -> DashboardJobSnapshot | None:
        runs = self.job_runs_repo.list_recent(job_name="daily_market_run", limit=1)
        if not runs:
            return None
        run = runs[0]
        summary = run.summary_json or {}
        return DashboardJobSnapshot(
            status=run.status,
            finished_at=run.finished_at,
            errors=len(summary.get("errors", [])),
        )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        return float(value)
