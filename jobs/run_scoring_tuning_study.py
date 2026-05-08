from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.scenarios import (
    default_backtest_scenario,
    scenario_with_overrides,
    split_in_sample_out_of_sample,
)
from core.config import load_yaml_config
from data.database import init_db, session_scope
from jobs.run_backtesting_study import build_candidate_configs

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass(slots=True)
class ReferenceUniverse:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    candidate_config_id: str
    weight: float
    min_test_trades: int


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_tuning_config(config_name: str = "scoring_tuning.yaml") -> dict[str, Any]:
    return load_yaml_config(config_name)


def build_reference_universes(config: dict[str, Any]) -> list[ReferenceUniverse]:
    universes: list[ReferenceUniverse] = []
    for name, payload in config["reference_universes"].items():
        universes.append(
            ReferenceUniverse(
                name=name,
                symbols=list(payload["symbols"]),
                start_date=date.fromisoformat(str(payload["start_date"])),
                end_date=date.fromisoformat(str(payload["end_date"])),
                candidate_config_id=str(payload["candidate_config_id"]),
                weight=float(payload["weight"]),
                min_test_trades=int(payload["min_test_trades"]),
            )
        )
    return universes


def build_reference_scenario(universe: ReferenceUniverse, candidate) -> Any:
    base = default_backtest_scenario(
        assets=universe.symbols,
        start_date=universe.start_date,
        end_date=universe.end_date,
    )
    params = candidate.params
    return scenario_with_overrides(
        base,
        entry_overrides={
            "min_final_score": params["min_final_score"],
            "max_risk_score": params["max_risk_score"],
            "max_distance_to_support_pct": params["max_distance_to_support_pct"],
            "max_rsi14": params["max_rsi14"],
            "require_bullish_trend": params["require_bullish_trend"],
            "allowed_recommendations": params["allowed_recommendations"],
        },
        exit_overrides={
            "strategy": params["exit_strategy"],
            "fixed_horizon_days": params.get("fixed_horizon_days", 20),
            "take_profit_pct": params.get("take_profit_pct"),
            "stop_loss_pct": params.get("stop_loss_pct"),
            "signal_loss_score_threshold": params.get("signal_loss_score_threshold"),
            "max_holding_days": params.get("max_holding_days", 20),
        },
    )


def case_score(
    *,
    train_expectancy: float,
    test_expectancy: float,
    train_profit_factor: float,
    test_profit_factor: float,
    test_drawdown: float,
    test_trades: int,
    min_test_trades: int,
) -> float:
    score = (
        (test_expectancy * 10)
        + ((test_profit_factor - 1.0) * 12)
        + (train_expectancy * 3)
        + ((train_profit_factor - 1.0) * 2)
        - (test_drawdown * 1.2)
        - (abs(train_expectancy - test_expectancy) * 0.8)
    )
    if test_trades < min_test_trades:
        score -= (min_test_trades - test_trades) * 4
    return round(score, 2)


def evaluate_variant(
    *,
    scoring_config: dict[str, Any],
    universes: list[ReferenceUniverse],
) -> tuple[pd.DataFrame, float]:
    import core.config as core_config_module
    import services.recommendation_service as recommendation_service_module
    import services.scoring_service as scoring_service_module

    original_loader = core_config_module.load_yaml_config
    candidates = {candidate.config_id: candidate for candidate in build_candidate_configs()}
    rows: list[dict[str, Any]] = []

    def _loader(name: str) -> dict[str, Any]:
        if name == "scoring.yaml":
            return deepcopy(scoring_config)
        return original_loader(name)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(scoring_service_module, "load_yaml_config", side_effect=_loader)
        )
        stack.enter_context(
            patch.object(recommendation_service_module, "load_yaml_config", side_effect=_loader)
        )
        with session_scope() as session:
            engine = BacktestEngine(session)
            for universe in universes:
                candidate = candidates[universe.candidate_config_id]
                scenario = build_reference_scenario(universe, candidate)
                (train_start, train_end), (test_start, test_end) = split_in_sample_out_of_sample(
                    universe.start_date,
                    universe.end_date,
                    0.7,
                )
                train_scenario = deepcopy(scenario)
                train_scenario.start_date = train_start
                train_scenario.end_date = train_end
                test_scenario = deepcopy(scenario)
                test_scenario.start_date = test_start
                test_scenario.end_date = test_end

                train_result = engine.run(train_scenario, persist=False)
                test_result = engine.run(test_scenario, persist=False)
                universe_score = case_score(
                    train_expectancy=train_result.metrics.expectancy_pct,
                    test_expectancy=test_result.metrics.expectancy_pct,
                    train_profit_factor=train_result.metrics.profit_factor,
                    test_profit_factor=test_result.metrics.profit_factor,
                    test_drawdown=test_result.metrics.max_drawdown_pct,
                    test_trades=test_result.metrics.total_trades,
                    min_test_trades=universe.min_test_trades,
                )
                rows.append(
                    {
                        "universe": universe.name,
                        "candidate_config_id": universe.candidate_config_id,
                        "candidate_label": candidate.label,
                        "train_total_trades": train_result.metrics.total_trades,
                        "test_total_trades": test_result.metrics.total_trades,
                        "train_expectancy_pct": train_result.metrics.expectancy_pct,
                        "test_expectancy_pct": test_result.metrics.expectancy_pct,
                        "train_profit_factor": train_result.metrics.profit_factor,
                        "test_profit_factor": test_result.metrics.profit_factor,
                        "train_max_drawdown_pct": train_result.metrics.max_drawdown_pct,
                        "test_max_drawdown_pct": test_result.metrics.max_drawdown_pct,
                        "weighted_universe_score": round(universe_score * universe.weight, 2),
                        "universe_score": universe_score,
                        "universe_weight": universe.weight,
                    }
                )

    frame = pd.DataFrame(rows)
    aggregate_score = round(float(frame["weighted_universe_score"].sum()), 2)
    return frame, aggregate_score


def run_tuning_study(config_name: str = "scoring_tuning.yaml") -> tuple[pd.DataFrame, pd.DataFrame]:
    config = load_tuning_config(config_name)
    universes = build_reference_universes(config)
    baseline_scoring = load_yaml_config("scoring.yaml")
    champion_config = deepcopy(baseline_scoring)
    iteration_summary_rows: list[dict[str, Any]] = []
    detail_frames: list[pd.DataFrame] = []

    for iteration in config["iterations"]:
        variant_rows: list[dict[str, Any]] = []
        for variant in iteration["variants"]:
            effective_config = deep_merge(champion_config, variant.get("scoring_overrides", {}))
            case_frame, aggregate_score = evaluate_variant(
                scoring_config=effective_config,
                universes=universes,
            )
            case_frame["iteration_id"] = iteration["id"]
            case_frame["iteration_label"] = iteration["label"]
            case_frame["variant_id"] = variant["id"]
            case_frame["variant_label"] = variant["label"]
            detail_frames.append(case_frame)
            variant_rows.append(
                {
                    "iteration_id": iteration["id"],
                    "iteration_label": iteration["label"],
                    "variant_id": variant["id"],
                    "variant_label": variant["label"],
                    "aggregate_score": aggregate_score,
                    "scoring_overrides": variant.get("scoring_overrides", {}),
                }
            )

        iteration_frame = pd.DataFrame(variant_rows).sort_values(
            "aggregate_score",
            ascending=False,
        )
        best_row = iteration_frame.iloc[0].to_dict()
        iteration_summary_rows.extend(iteration_frame.to_dict("records"))
        champion_config = deep_merge(champion_config, best_row["scoring_overrides"])

    summary = pd.DataFrame(iteration_summary_rows)
    details = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()
    return summary, details


def build_markdown_report(
    summary: pd.DataFrame,
    details: pd.DataFrame,
    *,
    report_title: str = "Scoring Tuning Study",
) -> str:
    lines = [
        f"# {report_title}",
        "",
        "Runner para iteraciones de ajuste del scoring con comparativa "
        "sobre universos de referencia.",
        "",
        "## Resumen por iteracion",
        "",
    ]
    for _iteration_id, frame in summary.groupby("iteration_id", sort=False):
        ordered = frame.sort_values("aggregate_score", ascending=False)
        lines.append(f"### {ordered.iloc[0]['iteration_label']}")
        lines.append("")
        lines.append("| Variante | Aggregate score |")
        lines.append("| --- | --- |")
        for _, row in ordered.iterrows():
            lines.append(f"| {row['variant_label']} | {row['aggregate_score']} |")
        lines.append("")
        winner = ordered.iloc[0]
        lines.append(
            f"Ganadora de la iteracion: `{winner['variant_id']}` con "
            f"`aggregate_score={winner['aggregate_score']}`."
        )
        lines.append("")

    lines.extend(
        [
            "## Detalle por universo",
            "",
            "| Iteracion | Variante | Universo | Config | Train exp | Test exp | "
            "Train PF | Test PF | Test DD | Train trades | Test trades | Universe score |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for _, row in details.sort_values(
        ["iteration_id", "weighted_universe_score"],
        ascending=[True, False],
    ).iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["iteration_id"]),
                    str(row["variant_label"]),
                    str(row["universe"]),
                    str(row["candidate_label"]),
                    str(row["train_expectancy_pct"]),
                    str(row["test_expectancy_pct"]),
                    str(row["train_profit_factor"]),
                    str(row["test_profit_factor"]),
                    str(row["test_max_drawdown_pct"]),
                    str(row["train_total_trades"]),
                    str(row["test_total_trades"]),
                    str(row["universe_score"]),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append(
        "Interpretacion: cada iteracion parte del campeon anterior y compara variantes "
        "sobre universos de referencia fijos para aislar el efecto del cambio."
    )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run iterative scoring tuning studies.")
    parser.add_argument(
        "--config",
        default="scoring_tuning.yaml",
        help="YAML config file name from config/ to use for the study.",
    )
    parser.add_argument(
        "--report-prefix",
        default="scoring_tuning",
        help="Prefix for generated files inside reports/.",
    )
    parser.add_argument(
        "--report-title",
        default="Scoring Tuning Study",
        help="Markdown title for the generated report.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    summary, details = run_tuning_study(args.config)
    summary_path = REPORTS_DIR / f"{args.report_prefix}_iteration_summary.csv"
    detail_path = REPORTS_DIR / f"{args.report_prefix}_iteration_details.csv"
    report_path = REPORTS_DIR / f"{args.report_prefix}_study.md"
    summary.to_csv(summary_path, index=False)
    details.to_csv(detail_path, index=False)
    report_path.write_text(
        build_markdown_report(summary, details, report_title=args.report_title),
        encoding="utf-8",
    )
    print(
        {
            "summary_csv": str(summary_path),
            "detail_csv": str(detail_path),
            "report_md": str(report_path),
        }
    )


if __name__ == "__main__":
    main()
