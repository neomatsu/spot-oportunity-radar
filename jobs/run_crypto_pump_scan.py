"""CLI entry point for the Crypto Pump Radar scanner.

Usage examples::

    python -m jobs.run_crypto_pump_scan
    python -m jobs.run_crypto_pump_scan --dry-run
    python -m jobs.run_crypto_pump_scan --chains solana ethereum --top 5
    python -m jobs.run_crypto_pump_scan --no-persist

This job is intentionally decoupled from ``jobs/daily_market_run.py`` and does
NOT trigger Telegram, alerts or portfolio side-effects.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from core.config import get_settings, load_yaml_config
from core.logger import configure_logging, get_logger
from data.database import init_db, session_scope
from data.repositories.crypto_pump_repo import CryptoPumpRepository
from services.crypto_pump_radar_service import CryptoPumpRadarService

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crypto Pump Radar scanner (modulo especulativo independiente). "
            "Detecta candidatos a posible pump temprano en DexScreener. "
            "No afecta al pipeline principal."
        )
    )
    parser.add_argument(
        "--chains",
        nargs="*",
        help="Lista de chains a escanear. Por defecto las definidas en YAML.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Top N candidatos a mostrar (por defecto el del YAML).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No persiste snapshots ni el scan_run.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Alias mas explicito de --dry-run.",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Imprime resultado en JSON en lugar de tabla legible.",
    )
    return parser


def _format_row(row: dict) -> str:
    return (
        f"#{row.get('rank'):>2} "
        f"{(row.get('symbol') or '?'):<22} "
        f"{(row.get('chain') or '?'):<10} "
        f"score={row.get('final_speculative_score'):>5.1f} "
        f"momentum={row.get('pump_momentum_score'):>5.1f} "
        f"rug_risk={row.get('rug_risk_score'):>5.1f} "
        f"liq=${(row.get('liquidity_usd') or 0):>11,.0f} "
        f"vol1h=${(row.get('volume_1h') or 0):>10,.0f} "
        f"1h={(row.get('price_change_1h') or 0):>+6.1f}% "
        f"24h={(row.get('price_change_24h') or 0):>+6.1f}% "
        f"cls={row.get('classification')}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()

    config = load_yaml_config("crypto_pump_radar.yaml")
    top_n = int(args.top or config.get("scanner", {}).get("top_n", 10))
    dry_run = bool(args.dry_run or args.no_persist)

    with session_scope() as session:
        repo = CryptoPumpRepository(session)
        service = CryptoPumpRadarService(repository=repo, config=config)
        result = service.scan(
            chains=args.chains,
            persist=not dry_run,
            dry_run=dry_run,
        )

        top_rows = []
        for idx, row in enumerate(result.top_n[:top_n], start=1):
            payload = row.to_summary()
            payload["rank"] = idx
            top_rows.append(payload)

    summary = {
        "candidates_total": len(result.candidates),
        "rejected_filtered": result.rejected_count,
        "chains_scanned": result.chains_scanned,
        "dry_run": dry_run,
        "top": top_rows,
        "errors": result.error_messages,
    }

    if args.json_output:
        print(json.dumps(summary, ensure_ascii=False, default=str))
    else:
        header = (
            f"Crypto Pump Radar — {len(result.candidates)} candidatos "
            f"({result.rejected_count} filtrados). Chains: {result.chains_scanned}"
        )
        logger.info(header)
        print(header)
        print("ALTA ESPECULACION — los scores no predicen pumps con certeza.")
        if not top_rows:
            print("Sin candidatos con los filtros actuales.")
        for row in top_rows:
            print(_format_row(row))
        if result.error_messages:
            print("Errores:")
            for err in result.error_messages:
                print(f"  - {err}")

    return 0 if not result.error_messages else 0


if __name__ == "__main__":
    raise SystemExit(main())
