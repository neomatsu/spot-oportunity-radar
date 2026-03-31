from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.config import load_yaml_config
from data.database import AlertORM, AssetORM
from data.repositories.trade_intents_repo import TradeIntentsRepository
from services.execution_prep_service import ExecutionPrepService


class TradeIntentService:
    def __init__(self, session) -> None:
        self.session = session
        self.repo = TradeIntentsRepository(session)
        self.config = load_yaml_config("execution_rules.yaml")
        self.execution_prep = ExecutionPrepService()

    def maybe_create_intent(
        self,
        *,
        asset: AssetORM,
        alert: AlertORM,
        watchlist_row: dict[str, Any],
        exposure,
    ):
        defaults = self.config["defaults"]
        constraints = self.config["constraints"]
        allowed_universes = set(defaults["allowed_universes"])

        if asset.asset_type not in allowed_universes:
            return None
        if defaults["allow_watch_intents"]:
            allowed_recommendations = {"BUY_CANDIDATE", "WATCH"}
        else:
            allowed_recommendations = {"BUY_CANDIDATE"}
        if watchlist_row.get("recommendation") not in allowed_recommendations:
            return None
        if float(watchlist_row.get("final_opportunity_score") or 0.0) < float(
            defaults["min_final_score_for_intent"]
        ):
            return None
        if float(watchlist_row.get("risk_score") or 100.0) > float(
            defaults["max_risk_score_for_intent"]
        ):
            return None
        if float(watchlist_row.get("portfolio_fit_score") or 0.0) < float(
            defaults["min_portfolio_fit_score_for_intent"]
        ):
            return None
        if constraints["block_if_demo_or_stale"] and (
            watchlist_row.get("data_mode") != "real"
            or watchlist_row.get("freshness_status") != "fresh"
        ):
            return None

        existing = self.repo.latest_open_for_asset(asset.id)
        if existing is not None:
            return existing

        prep = self.execution_prep.prepare(
            asset=asset,
            watchlist_row=watchlist_row,
            exposure=exposure,
        )
        blockers = prep["blockers"]
        if (
            constraints["block_if_asset_over_limit"]
            and "Activo ya en maximo de cartera" in blockers
        ):
            return None
        if constraints["block_if_sector_over_limit"] and "Sector en limite maximo" in blockers:
            return None
        if (
            constraints["block_if_asset_type_over_limit"]
            and "Clase de activo en limite maximo" in blockers
        ):
            return None
        if prep["suggested_capital"] <= 0:
            return None

        rationale = {
            "source_alert_id": alert.id,
            "alert_type": alert.alert_type,
            "alert_severity": alert.severity,
            "blockers": blockers,
            "buy_zone": [
                watchlist_row.get("suggested_buy_low"),
                watchlist_row.get("suggested_buy_high"),
            ],
            "score_breakdown": watchlist_row.get("score_breakdown", {}),
            "reasons": watchlist_row.get("reasons", []),
            "reward_risk": prep["reward_risk"],
            "cash_weight": prep["cash_weight"],
            "existing_position": prep["has_existing_position"],
        }
        return self.repo.create_intent(
            {
                "asset_id": asset.id,
                "symbol": asset.symbol,
                "source_alert_id": alert.id,
                "status": "new",
                "recommendation": watchlist_row.get("recommendation"),
                "final_score": float(watchlist_row.get("final_opportunity_score") or 0.0),
                "risk_score": float(watchlist_row.get("risk_score") or 0.0),
                "suggested_buy_low": watchlist_row.get("suggested_buy_low"),
                "suggested_buy_high": watchlist_row.get("suggested_buy_high"),
                "suggested_weight_add": prep["suggested_weight_pct"],
                "suggested_capital": prep["suggested_capital"],
                "invalidation": watchlist_row.get("reasons", ["Sin invalidacion clara"])[0]
                if watchlist_row.get("reasons")
                else "Sin invalidacion clara",
                "rationale_json": rationale,
            }
        )

    def expire_old_intents(self) -> int:
        expiration_hours = int(self.config["defaults"]["intent_expiration_hours"])
        cutoff = datetime.now(UTC) - timedelta(hours=expiration_hours)
        updated = 0
        for intent in self.repo.list_recent(limit=500):
            created_at = intent.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if intent.status in {"new", "reviewed", "approved"} and created_at < cutoff:
                self.repo.update_status(intent.id, "expired")
                updated += 1
        return updated
