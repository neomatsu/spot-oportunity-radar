from __future__ import annotations

import logging

from core.logger import configure_logging
from services.sp500_scoring_service import SP500ScoringService

logger = logging.getLogger(__name__)


def main() -> int:
    configure_logging()
    try:
        result = SP500ScoringService().refresh_price_cache_incremental()
    except Exception:
        logger.exception("S&P 500 breadth incremental refresh failed")
        return 1

    logger.info(
        "S&P 500 breadth refresh completed: target=%s total=%s current=%s "
        "refreshed=%s failed=%s",
        result.target_session,
        result.symbols_total,
        result.symbols_current,
        result.symbols_refreshed,
        result.symbols_failed,
    )
    print(
        f"target={result.target_session} total={result.symbols_total} "
        f"current={result.symbols_current} refreshed={result.symbols_refreshed} "
        f"failed={result.symbols_failed}"
    )
    return 1 if result.symbols_failed == result.symbols_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
