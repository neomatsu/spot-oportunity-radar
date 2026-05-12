from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select

from backtesting.engine import BacktestEngine
from backtesting.models import BacktestMode, BacktestRunResult
from backtesting.scenarios import default_backtest_scenario, scenario_with_overrides
from data.database import PriceBarDailyORM, session_scope
from data.repositories.assets_repo import AssetsRepository

REPORTS_DIR = Path("reports")


@dataclass(frozen=True)
class RegimeFilterCandidate:
    name: str
    enabled: bool
    min_bull_probability: float | None
    max_bear_probability: float | None
    reduce_size_if_bubble_probability_gt: float | None
    bubble_position_size_multiplier: float


def build_candidates() -> list[RegimeFilterCandidate]:
    return [
        RegimeFilterCandidate("baseline_no_regime_filter", False, None, None, None, 0.5),
        RegimeFilterCandidate("bull45_bear65_bubble70_size50", True, 45, 65, 70, 0.5),
        RegimeFilterCandidate("bull50_bear65_bubble70_size50", True, 50, 65, 70, 0.5),
        RegimeFilterCandidate("bull50_bear60_bubble70_size50", True, 50, 60, 70, 0.5),
        RegimeFilterCandidate("bull55_bear60_bubble65_size50", True, 55, 60, 65, 0.5),
        RegimeFilterCandidate("bull60_bear55_bubble65_size50", True, 60, 55, 65, 0.5),
        RegimeFilterCandidate("bull45_bear55_bubble60_size70", True, 45, 55, 60, 0.7),
        RegimeFilterCandidate("bull55_bear65_bubble75_size50", True, 55, 65, 75, 0.5),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare optional market-regime filters in the backtesting engine."
    )
    parser.add_argument("--start-date", default="2021-02-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--mode",
        default="trade_by_trade",
        choices=[mode.value for mode in BacktestMode],
    )
    parser.add_argument("--persist", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N candidates. Useful for timing/ETA checks.",
    )
    args = parser.parse_args()

    REPORTS_DIR.mkdir(exist_ok=True)
    start_date = date.fromisoformat(args.start_date)

    with session_scope() as session:
        assets = AssetsRepository(session).list_enabled()
        symbols = [asset.symbol for asset in assets]
        if not symbols:
            raise RuntimeError("No enabled assets available for regime filter study.")

        max_price_date = session.scalar(select(func.max(PriceBarDailyORM.date)))
        end_date = date.fromisoformat(args.end_date) if args.end_date else max_price_date
        if end_date is None:
            raise RuntimeError("No price data available for regime filter study.")

        base = default_backtest_scenario(
            assets=symbols,
            start_date=start_date,
            end_date=end_date,
        )
        engine = BacktestEngine(session)
        rows: list[dict[str, Any]] = []
        results: list[tuple[RegimeFilterCandidate, BacktestRunResult]] = []
        candidates = build_candidates()
        if args.limit is not None:
            candidates = candidates[: max(1, args.limit)]

        study_started = time.perf_counter()
        partial_csv_path = REPORTS_DIR / "regime_filter_backtesting_comparison.partial.csv"
        for candidate_index, candidate in enumerate(candidates, start=1):
            candidate_started = time.perf_counter()
            print(
                f"[{candidate_index}/{len(candidates)}] Running {candidate.name}...",
                flush=True,
            )
            scenario = scenario_with_overrides(
                base,
                name=f"regime_filter_{candidate.name}",
                mode=BacktestMode(args.mode),
                regime_filter_overrides={
                    "enabled": candidate.enabled,
                    "min_bull_probability": candidate.min_bull_probability,
                    "max_bear_probability": candidate.max_bear_probability,
                    "reduce_size_if_bubble_probability_gt": (
                        candidate.reduce_size_if_bubble_probability_gt
                    ),
                    "bubble_position_size_multiplier": candidate.bubble_position_size_multiplier,
                },
            )
            result = engine.run(scenario, persist=args.persist)
            results.append((candidate, result))
            metrics = result.metrics
            rows.append(
                {
                    "candidate": candidate.name,
                    "enabled": candidate.enabled,
                    "min_bull_probability": candidate.min_bull_probability,
                    "max_bear_probability": candidate.max_bear_probability,
                    "reduce_size_if_bubble_probability_gt": (
                        candidate.reduce_size_if_bubble_probability_gt
                    ),
                    "bubble_position_size_multiplier": candidate.bubble_position_size_multiplier,
                    "total_trades": metrics.total_trades,
                    "win_rate_pct": metrics.win_rate_pct,
                    "expectancy_pct": metrics.expectancy_pct,
                    "profit_factor": metrics.profit_factor,
                    "max_drawdown_pct": metrics.max_drawdown_pct,
                    "total_net_return_pct": metrics.total_net_return_pct,
                    "return_to_drawdown": metrics.return_to_drawdown,
                    "sharpe_like": metrics.sharpe_like,
                    "evaluation_score": _evaluation_score(metrics.to_dict()),
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "mode": args.mode,
                }
            )
            elapsed_candidate = time.perf_counter() - candidate_started
            elapsed_total = time.perf_counter() - study_started
            avg_per_candidate = elapsed_total / candidate_index
            remaining = avg_per_candidate * (len(candidates) - candidate_index)
            pd.DataFrame(rows).sort_values("evaluation_score", ascending=False).to_csv(
                partial_csv_path,
                index=False,
            )
            print(
                f"[{candidate_index}/{len(candidates)}] Done {candidate.name} in "
                f"{elapsed_candidate / 60:.1f} min. ETA ~ {remaining / 60:.1f} min.",
                flush=True,
            )

    comparison = pd.DataFrame(rows).sort_values("evaluation_score", ascending=False)
    csv_path = REPORTS_DIR / "regime_filter_backtesting_comparison.csv"
    md_path = REPORTS_DIR / "regime_filter_backtesting_comparison.md"
    comparison.to_csv(csv_path, index=False)
    md_path.write_text(_build_report(comparison), encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")
    print(comparison.head(8).to_string(index=False))
    return 0


def _evaluation_score(metrics: dict[str, float]) -> float:
    trade_penalty = max(0.0, (50 - metrics["total_trades"]) / 50) * 10
    return round(
        metrics["expectancy_pct"] * 30
        + max(0.0, metrics["profit_factor"] - 1.0) * 20
        + metrics["total_net_return_pct"] * 0.8
        + metrics["return_to_drawdown"] * 5
        - metrics["max_drawdown_pct"] * 0.6
        - trade_penalty,
        4,
    )


def _build_report(comparison: pd.DataFrame) -> str:
    baseline = comparison[comparison["candidate"] == "baseline_no_regime_filter"].iloc[0]
    winner = comparison.iloc[0]
    return "\n".join(
        [
            "# Regime Filter Backtesting Comparison",
            "",
            "Comparativa de filtros opcionales de Market Regime en backtesting. "
            "No modifica scoring ni recommendations; solo filtra entradas y reduce sizing "
            "si la probabilidad de bubble supera el umbral configurado.",
            "",
            f"- Periodo: {baseline['start_date']} a {baseline['end_date']}",
            f"- Modo: `{baseline['mode']}`",
            f"- Baseline trades: {int(baseline['total_trades'])}",
            f"- Baseline expectancy: {baseline['expectancy_pct']:.2f}%",
            f"- Baseline profit factor: {baseline['profit_factor']:.2f}",
            f"- Baseline max DD: {baseline['max_drawdown_pct']:.2f}%",
            f"- Mejor candidato: `{winner['candidate']}`",
            f"- Mejor expectancy: {winner['expectancy_pct']:.2f}%",
            f"- Mejor profit factor: {winner['profit_factor']:.2f}",
            f"- Mejor max DD: {winner['max_drawdown_pct']:.2f}%",
            "",
            "## Ranking",
            "",
            comparison.to_markdown(index=False),
            "",
            "## Lectura",
            "",
            "El `evaluation_score` pondera expectancy, profit factor, retorno total ponderado, "
            "return/drawdown y penaliza drawdown y muestras demasiado pequenas. Debe usarse "
            "como ranking comparativo, no como verdad absoluta.",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
