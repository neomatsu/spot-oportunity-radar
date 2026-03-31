from __future__ import annotations

import json
from types import SimpleNamespace

from jobs import daily_market_run


def test_daily_market_run_main_invocable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(daily_market_run, "configure_logging", lambda level: None)
    monkeypatch.setattr(daily_market_run, "init_db", lambda: None)
    monkeypatch.setattr(
        daily_market_run,
        "get_settings",
        lambda: SimpleNamespace(log_level="INFO"),
    )

    class FakeService:
        def __init__(self, session) -> None:
            self.session = session

        def run(self, options):
            return SimpleNamespace(
                status="success",
                to_dict=lambda: {
                    "status": "success",
                    "alerts_sent": 2,
                    "dry_run": options.dry_run,
                },
            )

    class FakeScope:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(daily_market_run, "session_scope", lambda: FakeScope())
    monkeypatch.setattr(daily_market_run, "DailyMarketRunService", FakeService)

    exit_code = daily_market_run.main(["--dry-run"])
    output = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert json.loads(output)["dry_run"] is True
