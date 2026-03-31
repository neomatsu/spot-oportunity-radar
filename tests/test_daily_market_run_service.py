from __future__ import annotations

from types import SimpleNamespace

from services.daily_market_run_service import (
    DailyMarketRunOptions,
    DailyMarketRunService,
)


class FakeJobRunsRepository:
    def __init__(self) -> None:
        self.created = []
        self.finalized = []

    def create_run(self, *, job_name: str):
        run = SimpleNamespace(id=1, job_name=job_name)
        self.created.append(run)
        return run

    def finalize_run(self, run_id: int, **kwargs):
        self.finalized.append((run_id, kwargs))
        return SimpleNamespace(id=run_id, **kwargs)


def _build_service():
    service = DailyMarketRunService.__new__(DailyMarketRunService)
    service.session = object()
    service.settings = SimpleNamespace(
        demo_mode=False,
        telegram_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="chat-id",
    )
    service.config = {"runner": {"allow_demo_mode": True, "default_force_refresh": False}}
    service.job_runs_repo = FakeJobRunsRepository()
    service.recommendation_facade = SimpleNamespace()
    service.alert_service = SimpleNamespace()
    return service


def test_runner_calls_phases_in_order() -> None:
    service = _build_service()
    calls: list[str] = []
    service.recommendation_facade.refresh_and_generate_all = lambda force: (
        calls.append(f"refresh:{force}") or SimpleNamespace(
            total_assets=3,
            refreshed_assets=1,
            cached_assets=2,
            preserved_assets=0,
            demo_fallback_assets=0,
            generated_signals=3,
            provider_error_assets=[],
            provider_unavailable_assets=[],
        )
    )
    service.alert_service.scan_market_events = lambda: (
        calls.append("scan") or SimpleNamespace(
            events_detected=4,
            alerts_created=2,
            alerts_deduplicated=1,
            trade_intents_created=1,
            errors=[],
        )
    )
    service.alert_service.send_pending_alerts = lambda: (
        calls.append("send") or SimpleNamespace(
            alerts_sent=2,
            alerts_failed=0,
            alerts_skipped=0,
        )
    )

    summary = service.run(DailyMarketRunOptions())

    assert calls == ["refresh:False", "scan", "send"]
    assert summary.status == "success"
    assert summary.alerts_sent == 2


def test_partial_failure_keeps_runner_alive() -> None:
    service = _build_service()
    service.recommendation_facade.refresh_and_generate_all = lambda force: SimpleNamespace(
        total_assets=3,
        refreshed_assets=1,
        cached_assets=1,
        preserved_assets=0,
        demo_fallback_assets=0,
        generated_signals=3,
        provider_error_assets=["ASML"],
        provider_unavailable_assets=[],
    )
    service.alert_service.scan_market_events = lambda: SimpleNamespace(
        events_detected=1,
        alerts_created=1,
        alerts_deduplicated=0,
        trade_intents_created=0,
        errors=[],
    )
    service.alert_service.send_pending_alerts = lambda: SimpleNamespace(
        alerts_sent=0,
        alerts_failed=1,
        alerts_skipped=0,
    )

    summary = service.run(DailyMarketRunOptions())

    assert summary.status == "partial_success"
    assert summary.refresh_error_assets == 1
    assert summary.alerts_failed == 1


def test_no_telegram_mode_does_not_fail() -> None:
    service = _build_service()
    service.recommendation_facade.refresh_and_generate_all = lambda force: SimpleNamespace(
        total_assets=1,
        refreshed_assets=1,
        cached_assets=0,
        preserved_assets=0,
        demo_fallback_assets=0,
        generated_signals=1,
        provider_error_assets=[],
        provider_unavailable_assets=[],
    )
    service.alert_service.scan_market_events = lambda: SimpleNamespace(
        events_detected=1,
        alerts_created=1,
        alerts_deduplicated=0,
        trade_intents_created=0,
        errors=[],
    )
    service.alert_service.send_pending_alerts = lambda: (_ for _ in ()).throw(
        AssertionError("No deberia enviar Telegram")
    )

    summary = service.run(DailyMarketRunOptions(no_telegram=True))

    assert summary.status == "success"
    assert summary.telegram_attempted is False
    assert summary.alerts_sent == 0


def test_only_alerts_skips_refresh_phase() -> None:
    service = _build_service()
    service.recommendation_facade.refresh_and_generate_all = lambda force: (_ for _ in ()).throw(
        AssertionError("No deberia refrescar")
    )
    service.alert_service.scan_market_events = lambda: SimpleNamespace(
        events_detected=2,
        alerts_created=1,
        alerts_deduplicated=1,
        trade_intents_created=0,
        errors=[],
    )
    service.alert_service.send_pending_alerts = lambda: SimpleNamespace(
        alerts_sent=1,
        alerts_failed=0,
        alerts_skipped=0,
    )

    summary = service.run(DailyMarketRunOptions(only_alerts=True))

    assert summary.events_detected == 2
    assert summary.generated_signals == 0
