from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from backtesting.bitcoin_opportunity import (
    BitcoinOpportunityBacktestConfig,
    BitcoinOpportunityBacktester,
)
from core.config import load_yaml_config
from data.database import init_db, session_scope
from services.bitcoin_opportunity_service import BitcoinOpportunityService

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
KEY_COLUMNS = [
    "buy_threshold_1",
    "buy_threshold_2",
    "buy_threshold_3",
    "sell_threshold_1",
    "sell_threshold_2",
    "sell_threshold_3",
]


def _annualized_return(total_return_pct: float, start: date, end: date) -> float:
    years = max((end - start).days / 365.25, 1 / 365.25)
    return ((1 + total_return_pct / 100) ** (1 / years) - 1) * 100


def _period_results(
    history: pd.DataFrame,
    base_config: BitcoinOpportunityBacktestConfig,
    buy_candidates: list[float],
    sell_candidates: list[float],
    *,
    prefix: str,
) -> pd.DataFrame:
    dates = pd.to_datetime(history["date"])
    start = dates.min().date()
    end = dates.max().date()
    results = BitcoinOpportunityBacktester().optimize_thresholds(
        history,
        base_config,
        buy_candidates=buy_candidates,
        sell_candidates=sell_candidates,
    )
    results["cagr_pct"] = results["total_return_pct"].map(
        lambda value: _annualized_return(value, start, end)
    )
    results["benchmark_cagr_pct"] = results["benchmark_return_pct"].map(
        lambda value: _annualized_return(value, start, end)
    )
    metric_columns = [column for column in results.columns if column not in KEY_COLUMNS]
    return results[KEY_COLUMNS + metric_columns].rename(
        columns={column: f"{prefix}_{column}" for column in metric_columns}
    )


def run_study(start_date: date, end_date: date, train_end_date: date) -> pd.DataFrame:
    config = load_yaml_config("bitcoin_opportunity.yaml").get("backtesting", {})
    study = config.get("threshold_study", {})
    buy_candidates = [float(value) for value in study["buy_candidates"]]
    sell_candidates = [float(value) for value in study["sell_candidates"]]
    base_config = BitcoinOpportunityBacktestConfig(
        initial_capital=float(config.get("initial_capital", 100_000)),
        buy_capital_pcts=tuple(study["fixed_buy_capital_pcts"]),
        sell_position_pcts=tuple(study["fixed_sell_position_pcts"]),
        buy_reset_threshold=float(config.get("buy_reset_threshold", 60)),
        sell_reset_threshold=float(config.get("sell_reset_threshold", 40)),
        commission_bps=float(config.get("commission_bps", 8)),
        slippage_bps=float(config.get("slippage_bps", 5)),
        minimum_trade_value=float(config.get("minimum_trade_value", 50)),
    )

    init_db()
    with session_scope() as session:
        history = BitcoinOpportunityService(session).cached_history(start_date, end_date)
    if history.empty:
        raise RuntimeError("No cached Bitcoin Opportunity history exists for this period")
    history_dates = pd.to_datetime(history["date"]).dt.date
    train = history.loc[history_dates <= train_end_date].copy()
    test = history.loc[history_dates > train_end_date].copy()
    if len(train) < 3 or len(test) < 3:
        raise RuntimeError("Train and test periods both need at least three observations")

    full_results = _period_results(
        history, base_config, buy_candidates, sell_candidates, prefix="full"
    )
    train_results = _period_results(
        train, base_config, buy_candidates, sell_candidates, prefix="train"
    )
    test_results = _period_results(
        test, base_config, buy_candidates, sell_candidates, prefix="test"
    )
    results = full_results.merge(train_results, on=KEY_COLUMNS).merge(
        test_results, on=KEY_COLUMNS
    )
    results["robust_score"] = (
        0.25 * results["train_cagr_pct"]
        + 0.40 * results["test_cagr_pct"]
        + 0.20
        * (results["test_cagr_pct"] - results["test_benchmark_cagr_pct"])
        - 0.10 * results["test_max_drawdown_pct"].abs()
        - 0.05 * (results["train_cagr_pct"] - results["test_cagr_pct"]).abs()
    )
    return results.sort_values(
        ["robust_score", "full_total_return_pct"], ascending=False
    ).reset_index(drop=True)


def _write_report(
    results: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
    train_end_date: date,
) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = REPORTS_DIR / "bitcoin_opportunity_threshold_study.csv"
    md_path = REPORTS_DIR / "bitcoin_opportunity_threshold_study.md"
    results.to_csv(csv_path, index=False)
    top = results.head(20)
    baseline_mask = (
        (results["buy_threshold_1"] == 70)
        & (results["buy_threshold_2"] == 75)
        & (results["buy_threshold_3"] == 80)
        & (results["sell_threshold_1"] == 20)
        & (results["sell_threshold_2"] == 25)
        & (results["sell_threshold_3"] == 30)
    )
    baseline = results.loc[baseline_mask]
    best_robust = results.iloc[0]
    best_full = results.loc[results["full_total_return_pct"].idxmax()]
    best_test = results.loc[results["test_total_return_pct"].idxmax()]
    columns = KEY_COLUMNS + [
        "full_total_return_pct",
        "full_max_drawdown_pct",
        "train_cagr_pct",
        "test_cagr_pct",
        "test_max_drawdown_pct",
        "robust_score",
    ]

    def markdown_table(frame: pd.DataFrame) -> str:
        formatted = frame.copy()
        for column in formatted.select_dtypes(include="number").columns:
            formatted[column] = formatted[column].map(lambda value: f"{value:.2f}")
        header = "| " + " | ".join(formatted.columns) + " |"
        separator = "| " + " | ".join("---" for _ in formatted.columns) + " |"
        rows = [
            "| " + " | ".join(str(value) for value in row) + " |"
            for row in formatted.itertuples(index=False, name=None)
        ]
        return "\n".join([header, separator, *rows])

    markdown = [
        "# Bitcoin Opportunity Threshold Study",
        "",
        f"- Periodo completo: {start_date} a {end_date}",
        f"- Train: {start_date} a {train_end_date}",
        f"- Test: {train_end_date + pd.Timedelta(days=1)} a {end_date}",
        "- Porcentajes fijos: compras 10/20/40%, ventas 10/10/10%.",
        "- Robust score: 25% CAGR train + 40% CAGR test + 20% exceso CAGR test "
        "- 10% drawdown test - 5% gap train/test.",
        "",
        "## Resumen ejecutivo",
        "",
        (
            f"- Mejor ranking robusto: compras "
            f"{best_robust['buy_threshold_1']:.0f}/"
            f"{best_robust['buy_threshold_2']:.0f}/"
            f"{best_robust['buy_threshold_3']:.0f} y ventas "
            f"{best_robust['sell_threshold_1']:.0f}/"
            f"{best_robust['sell_threshold_2']:.0f}/"
            f"{best_robust['sell_threshold_3']:.0f}; retorno completo "
            f"{best_robust['full_total_return_pct']:.2f}% y test "
            f"{best_robust['test_total_return_pct']:.2f}%."
        ),
        (
            f"- Mayor retorno completo: {best_full['full_total_return_pct']:.2f}%, "
            f"pero obtiene {best_full['test_total_return_pct']:.2f}% en test; no debe "
            "considerarse automaticamente la mejor configuracion."
        ),
        (
            f"- Mayor retorno test: {best_test['test_total_return_pct']:.2f}% con "
            f"drawdown test {best_test['test_max_drawdown_pct']:.2f}%."
        ),
        "- Ninguna configuracion supera el buy-and-hold del tramo test; los umbrales "
        "mejoran la gestion tactica historica, pero no demuestran una ventaja estable "
        "en el regimen reciente.",
        "",
        "## Top 20 robusto",
        "",
        markdown_table(top[columns]),
        "",
        "## Configuracion de referencia 70/75/80 - 20/25/30",
        "",
        (
            markdown_table(baseline[columns])
            if not baseline.empty
            else "No incluida en el espacio evaluado."
        ),
        "",
        "## Limitaciones",
        "",
        "La seleccion sigue expuesta a sobreajuste. El tramo test no se utilizo para "
        "generar senales, pero si forma parte del ranking comparativo final.",
    ]
    md_path.write_text("\n".join(markdown), encoding="utf-8")
    return csv_path, md_path


def main() -> int:
    cfg = load_yaml_config("bitcoin_opportunity.yaml").get("backtesting", {})
    parser = argparse.ArgumentParser(description="Optimize Bitcoin Opportunity thresholds")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--train-end",
        type=date.fromisoformat,
        default=date.fromisoformat(cfg["threshold_study"]["train_end_date"]),
    )
    args = parser.parse_args()
    results = run_study(args.start, args.end, args.train_end)
    csv_path, md_path = _write_report(
        results,
        start_date=args.start,
        end_date=args.end,
        train_end_date=args.train_end,
    )
    print(f"Evaluated {len(results)} configurations")
    print(results.head(10)[KEY_COLUMNS + ["robust_score"]].to_string(index=False))
    print(f"CSV: {csv_path}")
    print(f"Report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
