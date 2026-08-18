from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.config import get_settings, load_yaml_config
from core.logger import get_logger
from data.repositories.job_runs_repo import JobRunsRepository
from services.alert_service import AlertService
from services.bitcoin_opportunity_service import BitcoinOpportunityService
from services.recommendation_facade import RecommendationFacade
from services.sp500_opportunity_service import SP500OpportunityService

logger = get_logger(__name__)


@dataclass(slots=True)
class DailyMarketRunOptions:
    dry_run: bool = False
    no_telegram: bool = False
    only_refresh: bool = False
    only_alerts: bool = False
    force: bool = False


@dataclass(slots=True)
class DailyMarketRunSummary:
    job_name: str = "daily_market_run"
    status: str = "running"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    total_assets: int = 0
    refreshed_assets: int = 0
    cached_assets: int = 0
    preserved_assets: int = 0
    demo_fallback_assets: int = 0
    refresh_error_assets: int = 0
    generated_signals: int = 0
    events_detected: int = 0
    alerts_created: int = 0
    alerts_deduplicated: int = 0
    trade_intents_created: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0
    alerts_skipped: int = 0
    telegram_enabled: bool = False
    telegram_attempted: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        for key in ("started_at", "finished_at"):
            if payload[key] is not None:
                payload[key] = payload[key].isoformat()
        return payload


class DailyMarketRunService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()
        self.config = load_yaml_config("scheduler.yaml")
        self.job_runs_repo = JobRunsRepository(session)
        self.recommendation_facade = RecommendationFacade(session)
        self.alert_service = AlertService(session)

    def run(self, options: DailyMarketRunOptions) -> DailyMarketRunSummary:
        summary = DailyMarketRunSummary(started_at=datetime.now(UTC))
        run = self.job_runs_repo.create_run(job_name=summary.job_name)

        try:
            self._validate_configuration(options, summary)
            if options.only_refresh and options.only_alerts:
                raise ValueError("No puedes combinar --only-refresh y --only-alerts")

            if not options.only_alerts:
                logger.info("Iniciando refresh y recalculo de senales")
                refresh_force = options.force or bool(
                    self.config.get("runner", {}).get("default_force_refresh", False)
                )
                recommendation_summary = self.recommendation_facade.refresh_and_generate_all(
                    force=refresh_force
                )
                summary.total_assets = recommendation_summary.total_assets
                summary.refreshed_assets = recommendation_summary.refreshed_assets
                summary.cached_assets = recommendation_summary.cached_assets
                summary.preserved_assets = recommendation_summary.preserved_assets
                summary.demo_fallback_assets = recommendation_summary.demo_fallback_assets
                summary.generated_signals = recommendation_summary.generated_signals
                summary.refresh_error_assets = len(
                    recommendation_summary.provider_error_assets
                ) + len(recommendation_summary.provider_unavailable_assets)
                if recommendation_summary.provider_error_assets:
                    summary.warnings.append(
                        "Errores de provider en: "
                        + ", ".join(recommendation_summary.provider_error_assets)
                    )
                if recommendation_summary.provider_unavailable_assets:
                    summary.warnings.append(
                        "Providers no disponibles para: "
                        + ", ".join(recommendation_summary.provider_unavailable_assets)
                    )

            if not options.only_refresh:
                if self.config.get("runner", {}).get(
                    "update_bitcoin_opportunity_history", False
                ):
                    try:
                        logger.info("Actualizando indicador Bitcoin Opportunity")
                        BitcoinOpportunityService(self.session).update_latest_history()
                    except Exception as exc:
                        logger.warning(
                            "No se pudo actualizar Bitcoin Opportunity: %s", exc
                        )
                        summary.warnings.append(
                            f"Bitcoin Opportunity no actualizado: {exc}"
                        )
                if self.config.get("runner", {}).get(
                    "update_sp500_opportunity_history", False
                ):
                    try:
                        logger.info("Actualizando indicador S&P 500 Opportunity")
                        SP500OpportunityService(self.session).update_latest_history()
                    except Exception as exc:
                        logger.warning(
                            "No se pudo actualizar S&P 500 Opportunity: %s", exc
                        )
                        summary.warnings.append(
                            f"S&P 500 Opportunity no actualizado: {exc}"
                        )
                logger.info("Iniciando deteccion de eventos y alertas")
                alert_summary = self.alert_service.scan_market_events()
                summary.events_detected = alert_summary.events_detected
                summary.alerts_created = alert_summary.alerts_created
                summary.alerts_deduplicated = alert_summary.alerts_deduplicated
                summary.trade_intents_created = alert_summary.trade_intents_created
                if alert_summary.errors:
                    summary.warnings.extend(alert_summary.errors)

                send_notifications = not options.dry_run and not options.no_telegram
                summary.telegram_enabled = bool(
                    self.settings.telegram_enabled
                    and self.settings.telegram_bot_token
                    and self.settings.telegram_chat_id
                )
                summary.telegram_attempted = send_notifications
                if send_notifications:
                    logger.info("Enviando alertas pendientes")
                    send_summary = self.alert_service.send_pending_alerts()
                    summary.alerts_sent = send_summary.alerts_sent
                    summary.alerts_failed = send_summary.alerts_failed
                    summary.alerts_skipped = send_summary.alerts_skipped
                else:
                    logger.info("Envio de Telegram omitido por configuracion o dry-run")

            summary.status = self._resolve_status(summary)
            return summary
        except Exception as exc:
            logger.exception("Fallo critico en daily_market_run")
            summary.status = "failed"
            summary.errors.append(str(exc))
            return summary
        finally:
            summary.finished_at = datetime.now(UTC)
            summary.duration_seconds = round(
                (summary.finished_at - summary.started_at).total_seconds(),
                2,
            )
            self.job_runs_repo.finalize_run(
                run.id,
                status=summary.status,
                summary_json=summary.to_dict(),
                error_message="; ".join(summary.errors) if summary.errors else None,
            )

    def _validate_configuration(
        self,
        options: DailyMarketRunOptions,
        summary: DailyMarketRunSummary,
    ) -> None:
        runner_cfg = self.config.get("runner", {})
        if not runner_cfg.get("allow_demo_mode", True) and self.settings.demo_mode:
            raise ValueError(
                "El runner esta configurado para abortar cuando APP_DEMO_MODE=true"
            )
        if options.no_telegram:
            summary.warnings.append("Telegram desactivado por flag --no-telegram")
        elif options.dry_run:
            summary.warnings.append("Dry run activo: no se enviaran alertas por Telegram")
        elif not (
            self.settings.telegram_enabled
            and self.settings.telegram_bot_token
            and self.settings.telegram_chat_id
        ):
            summary.warnings.append("Telegram no configurado; el runner continuara sin enviar")

    @staticmethod
    def _resolve_status(summary: DailyMarketRunSummary) -> str:
        if summary.errors:
            return "failed"
        if summary.refresh_error_assets > 0 or summary.alerts_failed > 0:
            return "partial_success"
        return "success"
