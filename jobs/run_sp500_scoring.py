from __future__ import annotations

import argparse
import logging
from pathlib import Path

from core.logger import configure_logging
from services.sp500_scoring_service import SP500ScoringService

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score the current S&P 500 universe without modifying tracked assets."
    )
    parser.add_argument("--force", action="store_true", help="Ignore cached price histories.")
    parser.add_argument(
        "--refresh-constituents",
        action="store_true",
        help="Refresh the cached S&P 500 constituent snapshot.",
    )
    parser.add_argument("--limit", type=int, help="Process only the first N symbols (smoke test).")
    parser.add_argument("--period", help="yfinance history period, for example 2y or 5y.")
    parser.add_argument("--batch-size", type=int, help="Number of Yahoo symbols per request.")
    parser.add_argument("--output", type=Path, help="Custom .xlsx output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    service = SP500ScoringService()
    try:
        result = service.run(
            force=args.force,
            refresh_constituents=args.refresh_constituents,
            limit=args.limit,
            period=args.period,
            batch_size=args.batch_size,
        )
        if result.ranking.empty:
            logger.error("S&P 500 scoring produced no valid rows. Failures: %s", len(result.errors))
            return 1
        output_path = service.export_excel(result, output_path=args.output)
    except Exception:
        logger.exception("S&P 500 scoring job failed")
        return 1

    logger.info(
        "S&P 500 scoring completed: scored=%s failed=%s cache_hits=%s output=%s",
        len(result.ranking),
        len(result.errors),
        result.metadata.get("price_cache_hits", 0),
        output_path,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
