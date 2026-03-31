from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from jobs import check_market_events, daily_signal_scan, send_alerts


@contextmanager
def _fake_session_scope():
    yield object()


def test_check_market_events_job_prints_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(check_market_events, "session_scope", _fake_session_scope)
    monkeypatch.setattr(check_market_events, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        check_market_events,
        "get_settings",
        lambda: SimpleNamespace(log_level="INFO"),
    )

    class FakeAlertService:
        def __init__(self, session) -> None:
            self.session = session

        def scan_market_events(self):
            return SimpleNamespace(
                scanned_assets=5,
                events_detected=4,
                alerts_created=2,
                alerts_deduplicated=1,
                trade_intents_created=1,
                expired_intents=0,
            )

    monkeypatch.setattr(check_market_events, "AlertService", FakeAlertService)
    check_market_events.main()
    output = capsys.readouterr().out

    assert "'alerts_created': 2" in output
    assert "'trade_intents_created': 1" in output


def test_send_alerts_job_processes_pending(monkeypatch, capsys) -> None:
    monkeypatch.setattr(send_alerts, "session_scope", _fake_session_scope)
    monkeypatch.setattr(send_alerts, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        send_alerts,
        "get_settings",
        lambda: SimpleNamespace(log_level="INFO"),
    )

    class FakeAlertService:
        def __init__(self, session) -> None:
            self.session = session

        def send_pending_alerts(self):
            return SimpleNamespace(alerts_sent=3)

    monkeypatch.setattr(send_alerts, "AlertService", FakeAlertService)
    send_alerts.main()
    output = capsys.readouterr().out

    assert "'alerts_sent': 3" in output


def test_daily_signal_scan_job_combines_refresh_and_alerts(monkeypatch, capsys) -> None:
    monkeypatch.setattr(daily_signal_scan, "session_scope", _fake_session_scope)
    monkeypatch.setattr(daily_signal_scan, "configure_logging", lambda level: None)
    monkeypatch.setattr(
        daily_signal_scan,
        "get_settings",
        lambda: SimpleNamespace(log_level="INFO"),
    )

    class FakeRecommendationFacade:
        def __init__(self, session) -> None:
            self.session = session

        def refresh_and_generate_all(self, force: bool):
            assert force is False
            return SimpleNamespace(generated_signals=11)

    class FakeAlertService:
        def __init__(self, session) -> None:
            self.session = session

        def scan_market_events(self):
            return SimpleNamespace(
                alerts_created=4,
                alerts_deduplicated=2,
                trade_intents_created=1,
            )

    monkeypatch.setattr(daily_signal_scan, "RecommendationFacade", FakeRecommendationFacade)
    monkeypatch.setattr(daily_signal_scan, "AlertService", FakeAlertService)
    daily_signal_scan.main()
    output = capsys.readouterr().out

    assert "'signals_generated': 11" in output
    assert "'alerts_created': 4" in output
