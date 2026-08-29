from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from core.config import load_yaml_config
from data.database import AssetORM, PlannedEntryLevelORM
from data.repositories.planned_entries_repo import PlannedEntriesRepository

VALID_STATUSES = {
    "active",
    "triggered",
    "paused",
    "executed_manually",
    "expired",
}


@dataclass(slots=True)
class PlannedEntryTrigger:
    event_type: str
    severity: str
    levels: list[PlannedEntryLevelORM]
    current_price: float
    observed_date: date


class PlannedEntryService:
    def __init__(self, session) -> None:
        self.session = session
        self.repo = PlannedEntriesRepository(session)
        config = load_yaml_config("planned_entries.yaml")
        self.config = config.get("planned_entries", {})

    def create_level(
        self,
        *,
        asset: AssetORM,
        target_price: float,
        suggested_weight_pct: float | None = None,
        suggested_capital: float | None = None,
        tolerance_pct: float | None = None,
        rearm_distance_pct: float | None = None,
        notes: str | None = None,
        expires_at: date | None = None,
        import_source: str | None = None,
        external_reference: str | None = None,
        import_batch_id: str | None = None,
    ) -> PlannedEntryLevelORM:
        target_price = float(target_price)
        if target_price <= 0:
            raise ValueError("El precio objetivo debe ser mayor que cero.")
        weight = self._optional_positive(suggested_weight_pct, "El porcentaje sugerido")
        capital = self._optional_positive(suggested_capital, "El capital sugerido")
        tolerance = float(
            tolerance_pct
            if tolerance_pct is not None
            else self.config.get("default_tolerance_pct", 1.0)
        )
        rearm = float(
            rearm_distance_pct
            if rearm_distance_pct is not None
            else self.config.get("default_rearm_distance_pct", 3.0)
        )
        if tolerance < 0:
            raise ValueError("La tolerancia no puede ser negativa.")
        if rearm <= tolerance:
            raise ValueError("El rearme debe ser mayor que la tolerancia de aviso.")
        if expires_at is not None and expires_at < date.today():
            raise ValueError("La fecha de expiracion no puede estar en el pasado.")
        return self.repo.create(
            asset_id=asset.id,
            target_price=target_price,
            price_currency=asset.quote_currency,
            suggested_weight_pct=weight,
            suggested_capital=capital,
            tolerance_pct=tolerance,
            rearm_distance_pct=rearm,
            notes=(notes or "").strip() or None,
            expires_at=expires_at,
            import_source=import_source,
            external_reference=external_reference,
            import_batch_id=import_batch_id,
        )

    def set_status(self, level_id: int, status: str) -> PlannedEntryLevelORM | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Estado de plan no valido: {status}")
        entity = self.repo.update_status(level_id, status)
        if entity is not None and status == "active":
            entity.triggered_at = None
            entity.last_trigger_type = None
        return entity

    def evaluate_asset(
        self,
        *,
        asset: AssetORM,
        current_price: float,
        observed_date: date,
    ) -> list[PlannedEntryTrigger]:
        if not self.config.get("enabled", True) or current_price <= 0:
            return []

        now = datetime.now(UTC).replace(tzinfo=None)
        triggered: dict[str, list[PlannedEntryLevelORM]] = {
            "manual_buy_level_crossed": [],
            "manual_buy_level_near": [],
        }
        for level in self.repo.list_for_asset(asset.id, include_inactive=False):
            if level.expires_at is not None and observed_date > level.expires_at:
                level.status = "expired"
                level.updated_at = now
                continue

            rearm_price = level.target_price * (1.0 + level.rearm_distance_pct / 100.0)
            if level.status == "triggered" and current_price >= rearm_price:
                level.status = "active"
                level.triggered_at = None
                level.last_trigger_type = None

            if level.status == "active":
                distance_pct = self.distance_pct(current_price, level.target_price)
                if current_price <= level.target_price:
                    event_type = "manual_buy_level_crossed"
                elif distance_pct <= level.tolerance_pct:
                    event_type = "manual_buy_level_near"
                else:
                    event_type = None

                if event_type is not None:
                    level.status = "triggered"
                    level.triggered_at = now
                    level.last_alerted_at = now
                    level.last_trigger_type = event_type
                    triggered[event_type].append(level)

            level.last_observed_price = current_price
            level.last_observed_at = now
            level.updated_at = now

        self.session.flush()
        if triggered["manual_buy_level_crossed"] and triggered["manual_buy_level_near"]:
            # One actionable alert per asset and scan; the more extreme condition wins.
            near_levels = triggered["manual_buy_level_near"]
            for level in near_levels:
                level.last_trigger_type = "manual_buy_level_crossed"
            triggered["manual_buy_level_crossed"].extend(near_levels)
            triggered["manual_buy_level_near"] = []
        result: list[PlannedEntryTrigger] = []
        for event_type, levels in triggered.items():
            if not levels:
                continue
            severity_key = "crossed_severity" if event_type.endswith("crossed") else "near_severity"
            result.append(
                PlannedEntryTrigger(
                    event_type=event_type,
                    severity=str(self.config.get(severity_key, "warning")),
                    levels=levels,
                    current_price=current_price,
                    observed_date=observed_date,
                )
            )
        return result

    @staticmethod
    def distance_pct(current_price: float, target_price: float) -> float:
        return (float(current_price) / float(target_price) - 1.0) * 100.0

    @staticmethod
    def _optional_positive(value: float | None, label: str) -> float | None:
        if value is None or float(value) == 0:
            return None
        value = float(value)
        if value < 0:
            raise ValueError(f"{label} no puede ser negativo.")
        return value
