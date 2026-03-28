from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from data.database import (
    AssetDataStatusORM,
    AssetORM,
    PortfolioPositionORM,
    PriceBarDailyORM,
    SignalORM,
    TechnicalSnapshotORM,
)


class WatchlistService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_watchlist_rows(self) -> list[dict[str, Any]]:
        latest_technical = (
            select(
                TechnicalSnapshotORM.asset_id.label("asset_id"),
                func.max(TechnicalSnapshotORM.date).label("latest_date"),
            )
            .group_by(TechnicalSnapshotORM.asset_id)
            .subquery()
        )
        latest_signal = (
            select(
                SignalORM.asset_id.label("asset_id"),
                func.max(SignalORM.date).label("latest_date"),
            )
            .group_by(SignalORM.asset_id)
            .subquery()
        )
        latest_price = (
            select(
                PriceBarDailyORM.asset_id.label("asset_id"),
                func.max(PriceBarDailyORM.date).label("latest_date"),
            )
            .group_by(PriceBarDailyORM.asset_id)
            .subquery()
        )

        statement: Select[
            tuple[
                AssetORM,
                TechnicalSnapshotORM | None,
                SignalORM | None,
                PriceBarDailyORM | None,
                AssetDataStatusORM | None,
            ]
        ] = (
            select(AssetORM, TechnicalSnapshotORM, SignalORM, PriceBarDailyORM, AssetDataStatusORM)
            .outerjoin(latest_technical, latest_technical.c.asset_id == AssetORM.id)
            .outerjoin(
                TechnicalSnapshotORM,
                (TechnicalSnapshotORM.asset_id == latest_technical.c.asset_id)
                & (TechnicalSnapshotORM.date == latest_technical.c.latest_date),
            )
            .outerjoin(latest_signal, latest_signal.c.asset_id == AssetORM.id)
            .outerjoin(
                SignalORM,
                (SignalORM.asset_id == latest_signal.c.asset_id)
                & (SignalORM.date == latest_signal.c.latest_date),
            )
            .outerjoin(latest_price, latest_price.c.asset_id == AssetORM.id)
            .outerjoin(
                PriceBarDailyORM,
                (PriceBarDailyORM.asset_id == latest_price.c.asset_id)
                & (PriceBarDailyORM.date == latest_price.c.latest_date),
            )
            .outerjoin(AssetDataStatusORM, AssetDataStatusORM.asset_id == AssetORM.id)
            .where(AssetORM.enabled.is_(True))
            .order_by(AssetORM.symbol)
        )

        rows = self.session.execute(statement).all()
        return [
            self._serialize_watchlist_row(asset, technical, signal, price, data_status)
            for asset, technical, signal, price, data_status in rows
        ]

    def get_positions_rows(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(PortfolioPositionORM, AssetORM)
            .join(AssetORM, AssetORM.id == PortfolioPositionORM.asset_id)
            .order_by(AssetORM.symbol)
        ).all()
        return [
            {
                "symbol": asset.symbol,
                "asset_type": asset.asset_type,
                "sector": asset.sector,
                "quantity": position.quantity,
                "avg_cost": position.avg_cost,
                "current_weight": position.current_weight,
                "target_weight": position.target_weight,
            }
            for position, asset in rows
        ]

    @staticmethod
    def _serialize_watchlist_row(
        asset: AssetORM,
        technical: TechnicalSnapshotORM | None,
        signal: SignalORM | None,
        price: PriceBarDailyORM | None,
        data_status: AssetDataStatusORM | None,
    ) -> dict[str, Any]:
        signal_rationale = signal.rationale_json if signal and signal.rationale_json else {}
        technical_rationale = (
            technical.rationale_json if technical and technical.rationale_json else {}
        )
        score_breakdown = signal_rationale.get("score_breakdown", {})

        return {
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.asset_type,
            "sector": asset.sector,
            "last_price": price.close if price else None,
            "last_price_date": price.date if price else None,
            "data_mode": data_status.data_mode if data_status else "unknown",
            "freshness_status": data_status.freshness_status if data_status else "missing",
            "last_refresh_source": data_status.last_refresh_source if data_status else None,
            "last_error_message": data_status.last_error_message if data_status else None,
            "last_available_bar_date": (
                data_status.last_available_bar_date
                if data_status
                else (price.date if price else None)
            ),
            "rsi14": technical.rsi14 if technical else None,
            "sma50": technical.sma50 if technical else None,
            "sma200": technical.sma200 if technical else None,
            "ema20": technical.ema20 if technical else None,
            "atr14": technical.atr14 if technical else None,
            "support_low": technical.support_low if technical else None,
            "support_high": technical.support_high if technical else None,
            "distance_to_support_pct": technical.distance_to_support_pct if technical else None,
            "technical_score": technical.technical_score if technical else None,
            "risk_score": signal.risk_score if signal else None,
            "risk_level": signal_rationale.get("risk_level"),
            "portfolio_fit_score": signal_rationale.get("portfolio_fit_score"),
            "final_opportunity_score": signal.final_score if signal else None,
            "recommendation": signal.recommendation if signal else None,
            "suggested_buy_low": signal.suggested_buy_low if signal else None,
            "suggested_buy_high": signal.suggested_buy_high if signal else None,
            "suggested_weight_add": signal.suggested_weight_add if signal else None,
            "score_breakdown": score_breakdown,
            "reasons": signal_rationale.get("reasons", []),
            "technical_breakdown": technical_rationale.get("breakdown", {}),
        }

    @staticmethod
    def top_opportunities(rows: Sequence[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        filtered = [row for row in rows if row["final_opportunity_score"] is not None]
        return sorted(
            filtered,
            key=lambda row: float(row["final_opportunity_score"]),
            reverse=True,
        )[:limit]
