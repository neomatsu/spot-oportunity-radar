from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from core.config import get_settings
from core.logger import configure_logging, get_logger
from data.database import init_db, session_scope
from services.daily_market_run_service import DailyMarketRunOptions, DailyMarketRunService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Runner diario de Spot Opportunity Radar para refresh, senales y alertas."
    )
    parser.add_argument("--dry-run", action="store_true", help="No envia Telegram real.")
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Omite el envio de alertas aunque existan pendientes.",
    )
    parser.add_argument(
        "--only-refresh",
        action="store_true",
        help="Solo refresh de datos y recalculo de senales.",
    )
    parser.add_argument(
        "--only-alerts",
        action="store_true",
        help="Solo escaneo y envio de alertas sin refresh previo.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fuerza refresh aunque existan datos recientes en cache.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()

    options = DailyMarketRunOptions(
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
        only_refresh=args.only_refresh,
        only_alerts=args.only_alerts,
        force=args.force,
    )

    with session_scope() as session:
        summary = DailyMarketRunService(session).run(options)

    logger.info("Resumen daily_market_run: %s", summary.to_dict())
    print(json.dumps(summary.to_dict(), ensure_ascii=False))
    return 1 if summary.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
