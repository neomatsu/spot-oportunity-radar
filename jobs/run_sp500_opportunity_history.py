from __future__ import annotations

import argparse
from datetime import date

from analytics.sp500_forward_returns import SP500ForwardReturnsAnalyzer
from core.config import get_settings, load_yaml_config
from core.logger import configure_logging, get_logger
from data.database import init_db, session_scope
from services.sp500_opportunity_service import SP500OpportunityService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild the independent S&P 500 Opportunity history."
    )
    parser.add_argument("--start", default="1990-01-02")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--forward-returns", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    init_db()
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    try:
        with session_scope() as session:
            history = SP500OpportunityService(session).update_history(
                start_date, end_date, force=args.force
            )
        logger.info(
            "S&P 500 Opportunity history ready: rows=%s start=%s end=%s",
            len(history),
            history["date"].min() if not history.empty else None,
            history["date"].max() if not history.empty else None,
        )
        if args.forward_returns and not history.empty:
            cfg = load_yaml_config("sp500_opportunity.yaml")["forward_returns"]
            result = SP500ForwardReturnsAnalyzer().analyze(
                history,
                horizons={key: int(value) for key, value in cfg["horizons_sessions"].items()},
                score_bands=[float(value) for value in cfg["score_bands"]],
            )
            output_dir = get_settings().root_dir / "reports"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "sp500_opportunity_forward_returns.csv"
            result.summary.to_csv(output_path, index=False)
            logger.info("Forward returns exported to %s", output_path)
        return 0
    except Exception:
        logger.exception("S&P 500 Opportunity history failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
