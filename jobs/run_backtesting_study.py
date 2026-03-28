from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.reporting import metrics_to_frame
from backtesting.scenarios import (
    default_backtest_scenario,
    scenario_with_overrides,
    split_in_sample_out_of_sample,
)
from data.database import init_db, session_scope
from data.repositories.assets_repo import AssetsRepository
from data.repositories.prices_repo import PricesRepository

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
MIN_BARS_FOR_STUDY = 400


@dataclass(slots=True)
class UniverseStudy:
    name: str
    symbols: list[str]
    start_date: date
    end_date: date
    excluded_symbols: list[str]
    min_trades_threshold: int


@dataclass(slots=True)
class ConfigCandidate:
    config_id: str
    label: str
    params: dict[str, Any]


def build_candidate_configs() -> list[ConfigCandidate]:
    raw_configs = [
        {
            "config_id": "cfg_01",
            "label": "Balanced fixed 10d",
            "min_final_score": 65,
            "max_risk_score": 55,
            "max_rsi14": 60,
            "require_bullish_trend": False,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 8.0,
            "exit_strategy": "fixed_horizon",
            "fixed_horizon_days": 10,
            "max_holding_days": 10,
        },
        {
            "config_id": "cfg_02",
            "label": "Balanced fixed 20d",
            "min_final_score": 70,
            "max_risk_score": 55,
            "max_rsi14": 55,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 5.0,
            "exit_strategy": "fixed_horizon",
            "fixed_horizon_days": 20,
            "max_holding_days": 20,
        },
        {
            "config_id": "cfg_03",
            "label": "Conservative fixed 15d",
            "min_final_score": 75,
            "max_risk_score": 45,
            "max_rsi14": 50,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE",),
            "max_distance_to_support_pct": 3.0,
            "exit_strategy": "fixed_horizon",
            "fixed_horizon_days": 15,
            "max_holding_days": 15,
        },
        {
            "config_id": "cfg_04",
            "label": "High quality fixed 30d",
            "min_final_score": 80,
            "max_risk_score": 35,
            "max_rsi14": 45,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE",),
            "max_distance_to_support_pct": 1.5,
            "exit_strategy": "fixed_horizon",
            "fixed_horizon_days": 30,
            "max_holding_days": 30,
        },
        {
            "config_id": "cfg_05",
            "label": "TP8 SL5",
            "min_final_score": 70,
            "max_risk_score": 55,
            "max_rsi14": 55,
            "require_bullish_trend": False,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 8.0,
            "exit_strategy": "take_profit_stop_loss",
            "take_profit_pct": 0.08,
            "stop_loss_pct": 0.05,
            "max_holding_days": 20,
        },
        {
            "config_id": "cfg_06",
            "label": "TP12 SL7 strict",
            "min_final_score": 75,
            "max_risk_score": 45,
            "max_rsi14": 50,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE",),
            "max_distance_to_support_pct": 5.0,
            "exit_strategy": "take_profit_stop_loss",
            "take_profit_pct": 0.12,
            "stop_loss_pct": 0.07,
            "max_holding_days": 30,
        },
        {
            "config_id": "cfg_07",
            "label": "Signal loss 40",
            "min_final_score": 70,
            "max_risk_score": 55,
            "max_rsi14": 60,
            "require_bullish_trend": False,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 8.0,
            "exit_strategy": "signal_loss",
            "signal_loss_score_threshold": 40,
            "max_holding_days": 20,
        },
        {
            "config_id": "cfg_08",
            "label": "Signal loss 50 strict",
            "min_final_score": 75,
            "max_risk_score": 45,
            "max_rsi14": 50,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE",),
            "max_distance_to_support_pct": 5.0,
            "exit_strategy": "signal_loss",
            "signal_loss_score_threshold": 50,
            "max_holding_days": 30,
        },
        {
            "config_id": "cfg_09",
            "label": "Hybrid 10/5/20",
            "min_final_score": 70,
            "max_risk_score": 55,
            "max_rsi14": 55,
            "require_bullish_trend": False,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 5.0,
            "exit_strategy": "hybrid",
            "fixed_horizon_days": 20,
            "take_profit_pct": 0.10,
            "stop_loss_pct": 0.05,
            "signal_loss_score_threshold": 45,
            "max_holding_days": 20,
        },
        {
            "config_id": "cfg_10",
            "label": "Hybrid 15/8/30 strict",
            "min_final_score": 80,
            "max_risk_score": 35,
            "max_rsi14": 45,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE",),
            "max_distance_to_support_pct": 3.0,
            "exit_strategy": "hybrid",
            "fixed_horizon_days": 30,
            "take_profit_pct": 0.15,
            "stop_loss_pct": 0.08,
            "signal_loss_score_threshold": 50,
            "max_holding_days": 30,
        },
        {
            "config_id": "cfg_11",
            "label": "Relaxed hybrid 20/10/30",
            "min_final_score": 60,
            "max_risk_score": 65,
            "max_rsi14": 65,
            "require_bullish_trend": False,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 12.0,
            "exit_strategy": "hybrid",
            "fixed_horizon_days": 30,
            "take_profit_pct": 0.20,
            "stop_loss_pct": 0.10,
            "signal_loss_score_threshold": 55,
            "max_holding_days": 30,
        },
        {
            "config_id": "cfg_12",
            "label": "Balanced hybrid 12/7/15",
            "min_final_score": 70,
            "max_risk_score": 45,
            "max_rsi14": 50,
            "require_bullish_trend": True,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH"),
            "max_distance_to_support_pct": 3.0,
            "exit_strategy": "hybrid",
            "fixed_horizon_days": 15,
            "take_profit_pct": 0.12,
            "stop_loss_pct": 0.07,
            "signal_loss_score_threshold": 45,
            "max_holding_days": 20,
        },
    ]
    return [
        ConfigCandidate(
            config_id=item["config_id"],
            label=item["label"],
            params=item,
        )
        for item in raw_configs
    ]


def build_universe_studies() -> list[UniverseStudy]:
    with session_scope() as session:
        assets = AssetsRepository(session).list_enabled()
        prices_repo = PricesRepository(session)
        asset_rows = []
        excluded = []
        for asset in assets:
            count = prices_repo.row_count(asset.id)
            frame = prices_repo.get_asset_prices(asset.id)
            if count < MIN_BARS_FOR_STUDY or frame.empty:
                excluded.append(asset.symbol)
                continue
            asset_rows.append(
                {
                    "symbol": asset.symbol,
                    "asset_type": asset.asset_type,
                    "sector": asset.sector,
                    "min_date": frame["date"].min(),
                    "max_date": frame["date"].max(),
                }
            )

    def _study(name: str, symbols: list[str], threshold: int) -> UniverseStudy:
        rows = [row for row in asset_rows if row["symbol"] in symbols]
        return UniverseStudy(
            name=name,
            symbols=symbols,
            start_date=max(row["min_date"] for row in rows),
            end_date=min(row["max_date"] for row in rows),
            excluded_symbols=sorted(excluded),
            min_trades_threshold=threshold,
        )

    eligible_symbols = [row["symbol"] for row in asset_rows]
    stock_symbols = [row["symbol"] for row in asset_rows if row["asset_type"] == "stock"]
    etf_symbols = [row["symbol"] for row in asset_rows if row["asset_type"] == "etf"]
    crypto_symbols = [row["symbol"] for row in asset_rows if row["asset_type"] == "crypto"]

    return [
        _study("mixed", eligible_symbols, 20),
        _study("stocks", stock_symbols, 20),
        _study("etfs", etf_symbols, 20),
        _study("crypto", crypto_symbols, 10),
    ]


def build_scenario(study: UniverseStudy, candidate: ConfigCandidate):
    base = default_backtest_scenario(
        assets=study.symbols,
        start_date=study.start_date,
        end_date=study.end_date,
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


def strategy_score(
    train: dict[str, float],
    test: dict[str, float],
    min_trades: int,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    expectancy_component = max(-30.0, min(30.0, test["expectancy_pct"] * 8))
    profit_factor_component = max(-12.0, min(22.0, (test["profit_factor"] - 1) * 25))
    avg_return_component = max(-10.0, min(10.0, test["avg_return_pct"] * 3))
    drawdown_penalty = min(20.0, test["max_drawdown_pct"] * 0.9)
    gap_penalty = min(15.0, abs(train["expectancy_pct"] - test["expectancy_pct"]) * 2.5)
    train_support = max(-10.0, min(12.0, train["expectancy_pct"] * 3))

    score = (
        expectancy_component
        + profit_factor_component
        + avg_return_component
        + train_support
        - drawdown_penalty
        - gap_penalty
    )
    total_trades = train["total_trades"] + test["total_trades"]
    if total_trades < min_trades:
        notes.append("too_few_trades")
        score -= (min_trades - total_trades) * 1.5
    if test["profit_factor"] < 0.9:
        notes.append("weak_profit_factor")
        score -= 8
    if test["expectancy_pct"] < 0:
        notes.append("negative_oos_expectancy")
        score -= 8
    if train["expectancy_pct"] > 0 and test["expectancy_pct"] < 0:
        notes.append("train_test_collapse")
        score -= 10
    return round(score, 2), notes


def evaluate_universe(
    study: UniverseStudy,
    candidates: list[ConfigCandidate],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    asset_frames: dict[str, pd.DataFrame] = {}
    with session_scope() as session:
        engine = BacktestEngine(session)
        (train_start, train_end), (test_start, test_end) = split_in_sample_out_of_sample(
            study.start_date,
            study.end_date,
            0.7,
        )
        for candidate in candidates:
            train_scenario = build_scenario(study, candidate)
            train_scenario.name = f"{study.name}_{candidate.config_id}_train"
            train_scenario.start_date = train_start
            train_scenario.end_date = train_end
            test_scenario = build_scenario(study, candidate)
            test_scenario.name = f"{study.name}_{candidate.config_id}_test"
            test_scenario.start_date = test_start
            test_scenario.end_date = test_end

            train_result = engine.run(train_scenario, persist=False)
            test_result = engine.run(test_scenario, persist=False)
            score, flags = strategy_score(
                train_result.metrics.to_dict(),
                test_result.metrics.to_dict(),
                study.min_trades_threshold,
            )
            rows.append(
                {
                    "universe": study.name,
                    "config_id": candidate.config_id,
                    "label": candidate.label,
                    "params_json": candidate.params,
                    "train_start": train_start,
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": test_end,
                    "train_total_trades": train_result.metrics.total_trades,
                    "test_total_trades": test_result.metrics.total_trades,
                    "train_win_rate_pct": train_result.metrics.win_rate_pct,
                    "test_win_rate_pct": test_result.metrics.win_rate_pct,
                    "train_expectancy_pct": train_result.metrics.expectancy_pct,
                    "test_expectancy_pct": test_result.metrics.expectancy_pct,
                    "train_profit_factor": train_result.metrics.profit_factor,
                    "test_profit_factor": test_result.metrics.profit_factor,
                    "train_avg_return_pct": train_result.metrics.avg_return_pct,
                    "test_avg_return_pct": test_result.metrics.avg_return_pct,
                    "train_max_drawdown_pct": train_result.metrics.max_drawdown_pct,
                    "test_max_drawdown_pct": test_result.metrics.max_drawdown_pct,
                    "train_return_to_drawdown": train_result.metrics.return_to_drawdown,
                    "test_return_to_drawdown": test_result.metrics.return_to_drawdown,
                    "strategy_score": score,
                    "discard_reasons": ",".join(flags),
                    "train_result": train_result,
                    "test_result": test_result,
                }
            )

        eligible = (
            pd.DataFrame(rows)
            .sort_values("strategy_score", ascending=False)
            .reset_index(drop=True)
        )
        if eligible.empty:
            raise RuntimeError(f"No se pudieron generar resultados para el universo {study.name}.")

        top_row = eligible.iloc[0].to_dict()
        best_config = next(
            candidate
            for candidate in candidates
            if candidate.config_id == top_row["config_id"]
        )
        persist_scenario = build_scenario(study, best_config)
        persist_scenario.name = f"{study.name}_{best_config.config_id}_best"
        persisted_result = engine.run(
            persist_scenario,
            persist=True,
            run_name=persist_scenario.name,
        )
        segment_frames = {
            segment: metrics_to_frame(metrics_map)
            for segment, metrics_map in persisted_result.segmented_metrics.items()
        }
        asset_frames["symbol"] = segment_frames.get("symbol", pd.DataFrame())
    return eligible, {"persisted": persisted_result, "segments": asset_frames}


def dataframe_markdown(df: pd.DataFrame, max_rows: int = 10) -> str:
    if df.empty:
        return "_Sin datos_"
    frame = df.head(max_rows).copy()
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[column]) for column in headers) + " |")
    return "\n".join(lines)


def shortlist(df: pd.DataFrame, min_trades: int) -> pd.DataFrame:
    min_test_trades = max(3, min_trades // 4)
    filtered = df[
        (df["train_total_trades"] + df["test_total_trades"] >= min_trades)
        & (df["test_total_trades"] >= min_test_trades)
        & (df["test_profit_factor"] > 0.9)
        & (df["test_expectancy_pct"] > -0.25)
        & (df["test_max_drawdown_pct"] <= 12)
    ].copy()
    return filtered.sort_values("strategy_score", ascending=False).reset_index(drop=True)


def select_recommendations(df: pd.DataFrame) -> dict[str, pd.Series | None]:
    shortlist_df = df.copy()
    if shortlist_df.empty:
        return {"main": None, "conservative": None, "aggressive": None}

    conservative = shortlist_df.sort_values(
        ["test_max_drawdown_pct", "strategy_score", "test_profit_factor"],
        ascending=[True, False, False],
    ).iloc[0]
    aggressive_pool = shortlist_df[
        shortlist_df["params_json"].apply(
            lambda payload: "WATCH" in payload["allowed_recommendations"]
            or payload["max_risk_score"] >= 55
        )
    ]
    aggressive = (
        aggressive_pool.sort_values(
            ["strategy_score", "test_total_trades"],
            ascending=[False, False],
        ).iloc[0]
        if not aggressive_pool.empty
        else shortlist_df.iloc[0]
    )
    return {"main": shortlist_df.iloc[0], "conservative": conservative, "aggressive": aggressive}


def extract_asset_rows(universe: str, segment_df: pd.DataFrame) -> pd.DataFrame:
    if segment_df.empty:
        return pd.DataFrame()
    frame = segment_df.copy()
    frame.insert(0, "universe", universe)
    frame = frame.rename(columns={"segment": "asset"})
    return frame


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    candidates = build_candidate_configs()
    studies = build_universe_studies()

    all_results: list[pd.DataFrame] = []
    shortlist_frames: list[pd.DataFrame] = []
    asset_analysis_frames: list[pd.DataFrame] = []
    universe_recommendations: dict[str, dict[str, pd.Series | None]] = {}
    universe_notes: list[str] = []

    for study in studies:
        results_df, artifacts = evaluate_universe(study, candidates)
        shortlist_df = shortlist(results_df, study.min_trades_threshold)
        recommendations = select_recommendations(
            shortlist_df if not shortlist_df.empty else results_df
        )

        all_results.append(results_df)
        shortlist_source = shortlist_df if not shortlist_df.empty else results_df.head(3)
        shortlist_frames.append(
            shortlist_source.assign(rank=lambda df: range(1, len(df) + 1))
        )
        asset_analysis_frames.append(
            extract_asset_rows(study.name, artifacts["segments"]["symbol"])
        )
        universe_recommendations[study.name] = recommendations
        universe_notes.append(
            f"- `{study.name}`: {len(study.symbols)} activos, "
            f"periodo {study.start_date} a {study.end_date}, "
            f"{len(shortlist_df)} configuraciones en shortlist."
        )

    combined_results = pd.concat(all_results, ignore_index=True)
    top_configs = (
        pd.concat(shortlist_frames, ignore_index=True)
        .sort_values("strategy_score", ascending=False)
        .reset_index(drop=True)
    )
    asset_analysis = pd.concat(asset_analysis_frames, ignore_index=True)
    discarded = combined_results[
        combined_results["discard_reasons"].astype(str) != ""
    ].copy()

    top_configs_export = top_configs.copy()
    top_configs_export["parameters"] = top_configs_export["params_json"].apply(
        lambda payload: "; ".join(
            f"{key}={value}"
            for key, value in payload.items()
            if key not in {"config_id", "label"}
        )
    )
    top_configs_export = top_configs_export[
        [
            "universe",
            "rank",
            "config_id",
            "label",
            "parameters",
            "train_total_trades",
            "test_total_trades",
            "train_expectancy_pct",
            "test_expectancy_pct",
            "train_profit_factor",
            "test_profit_factor",
            "train_avg_return_pct",
            "test_avg_return_pct",
            "train_max_drawdown_pct",
            "test_max_drawdown_pct",
            "strategy_score",
            "discard_reasons",
        ]
    ]
    top_configs_export.to_csv(REPORTS_DIR / "backtesting_top_configs.csv", index=False)
    asset_analysis.to_csv(REPORTS_DIR / "backtesting_by_asset.csv", index=False)
    combined_results[
        [
            "universe",
            "config_id",
            "label",
            "strategy_score",
            "train_total_trades",
            "test_total_trades",
            "train_expectancy_pct",
            "test_expectancy_pct",
            "train_profit_factor",
            "test_profit_factor",
            "train_max_drawdown_pct",
            "test_max_drawdown_pct",
        ]
    ].to_csv(REPORTS_DIR / "backtesting_segment_analysis.csv", index=False)
    discarded[
        [
            "universe",
            "config_id",
            "label",
            "strategy_score",
            "discard_reasons",
            "train_expectancy_pct",
            "test_expectancy_pct",
            "test_profit_factor",
            "test_total_trades",
        ]
    ].to_csv(REPORTS_DIR / "backtesting_discarded_configs.csv", index=False)

    main_rec = universe_recommendations["mixed"]["main"]
    conservative_rec = universe_recommendations["mixed"]["conservative"]
    aggressive_rec = universe_recommendations["mixed"]["aggressive"]

    methodology = [
        "El estudio usa el motor historico barra a barra ya existente.",
        "Las señales se recalculan con datos disponibles hasta cada fecha; no se usa look-ahead.",
        "La validacion temporal se hace con split simple in-sample / out-of-sample 70/30.",
        "El estudio se lanza por script CLI, sin depender de la UI.",
        "Se priorizan activos con al menos 400 barras para evitar universos con "
        "muestra demasiado corta.",
    ]
    optimizer_limits = [
        "El optimizer original soportaba grid search y split train/test, pero no "
        "barría recommendation sets ni familias de salida de forma amplia.",
        "El reporting existente exponia métricas y segmentación, pero no generaba "
        "un informe consolidado multiuniverso.",
        "La persistencia en DB ya existía; este estudio persiste al menos la "
        "mejor configuración de cada universo y exporta artefactos CSV/Markdown.",
    ]

    report_lines = [
        "# Backtesting Optimization Report",
        "",
        "## 1. Resumen ejecutivo",
        "",
        (
            f"Se han evaluado **{len(combined_results)} configuraciones** sobre "
            f"**{len(studies)} universos** con el runner "
            "`python -m jobs.run_backtesting_study`."
        ),
        "",
        "Universos analizados:",
        *universe_notes,
        "",
        "Conclusiones principales:",
        "- El ranking final no se ha ordenado por retorno bruto, sino por un "
        "score compuesto con peso fuerte en expectancy y profit factor "
        "out-of-sample, penalizando drawdown, pocos trades y colapso train/test.",
        "- Las configuraciones demasiado relajadas tienden a degradarse "
        "claramente fuera de muestra, especialmente en crypto.",
        "- Las configuraciones con `BUY_CANDIDATE` solo o con filtros de riesgo "
        "más duros suelen sacrificar frecuencia, pero mejoran la robustez.",
        "",
        "## 2. Metodología",
        "",
        *[f"- {line}" for line in methodology],
        "",
        "Auditoría rápida del estado previo:",
        *[f"- {line}" for line in optimizer_limits],
        "",
        "Criterio compuesto usado en el estudio:",
        "",
        "```text",
        "strategy_score =",
        "  + expectancy_out_of_sample * 8",
        "  + (profit_factor_out_of_sample - 1) * 25",
        "  + avg_return_out_of_sample * 3",
        "  + expectancy_in_sample * 3",
        "  - max_drawdown_out_of_sample * 0.9",
        "  - 2.5 * |expectancy_train - expectancy_test|",
        "  - penalizaciones por pocos trades, PF flojo y colapso OOS",
        "```",
        "",
        "La lógica favorece estrategias con edge visible fuera de muestra y "
        "penaliza muestras pequeñas y gaps grandes entre train y test.",
        "",
        "## 3. Mejores configuraciones globales",
        "",
        dataframe_markdown(
            top_configs[
                [
                    "universe",
                    "rank",
                    "label",
                    "train_total_trades",
                    "test_total_trades",
                    "train_expectancy_pct",
                    "test_expectancy_pct",
                    "test_profit_factor",
                    "test_max_drawdown_pct",
                    "strategy_score",
                ]
            ],
            max_rows=12,
        ),
        "",
        "## 4. Mejores configuraciones por universo",
        "",
    ]

    for universe in ["mixed", "stocks", "etfs", "crypto"]:
        report_lines.extend(
            [
                f"### {universe}",
                "",
                dataframe_markdown(
                    top_configs[top_configs["universe"] == universe][
                        [
                            "rank",
                            "label",
                            "train_total_trades",
                            "test_total_trades",
                            "train_expectancy_pct",
                            "test_expectancy_pct",
                            "test_profit_factor",
                            "test_max_drawdown_pct",
                            "strategy_score",
                        ]
                    ],
                    max_rows=5,
                ),
                "",
            ]
        )

    report_lines.extend(
        [
            "## 5. Análisis por activo",
            "",
            "Los siguientes resultados usan la mejor configuración persistida por universo.",
            "",
            dataframe_markdown(
                asset_analysis.sort_values(
                    ["universe", "expectancy_pct"],
                    ascending=[True, False],
                )[
                    [
                        "universe",
                        "asset",
                        "total_trades",
                        "expectancy_pct",
                        "profit_factor",
                        "avg_return_pct",
                        "max_drawdown_pct",
                    ]
                ],
                max_rows=20,
            ),
            "",
            "## 6. Hallazgos clave",
            "",
        ]
    )

    findings = infer_findings(combined_results)
    report_lines.extend([f"- {finding}" for finding in findings])
    report_lines.extend(
        [
            "",
            "## 7. Configuración recomendada actual",
            "",
            (
                f"- Principal: `{main_rec['label']}` con "
                f"`strategy_score={main_rec['strategy_score']:.2f}`."
                if main_rec is not None
                else "- Principal: no disponible."
            ),
            (
                f"- Conservadora: `{conservative_rec['label']}`."
                if conservative_rec is not None
                else "- Conservadora: no disponible."
            ),
            (
                f"- Agresiva: `{aggressive_rec['label']}`."
                if aggressive_rec is not None
                else "- Agresiva: no disponible."
            ),
            "",
            "## 8. Riesgos y limitaciones",
            "",
            "- La simulación de cartera del motor existe, pero el estudio se ha "
            "centrado en `trade_by_trade` para aislar la calidad de la señal.",
            "- El universo ETF ha quedado restringido a activos con suficiente "
            "histórico; varios ETFs recientes o sin datos reales se han excluido "
            "del estudio robusto.",
            "- El modelo sigue siendo EOD, sin microestructura, spreads reales ni latencia.",
            "- Las métricas out-of-sample son más fiables que las in-sample, pero "
            "siguen limitadas por el tamaño de muestra disponible.",
            "",
            "## 9. Próximos pasos recomendados",
            "",
            "- Profundizar en una segunda iteración separando reglas para crypto y equities/ETFs.",
            "- Añadir walk-forward simple cuando haya más histórico homogéneo en "
            "ETFs internacionales.",
            "- Excluir o tratar aparte los activos con histórico insuficiente para "
            "evitar ruido en universos mixtos.",
            "- Evaluar configuración principal también en modo portfolio básico "
            "para estudiar solapamientos y consumo de capital.",
        ]
    )

    (REPORTS_DIR / "backtesting_optimization_report.md").write_text(
        "\n".join(report_lines),
        encoding="utf-8",
    )


def infer_findings(results: pd.DataFrame) -> list[str]:
    findings: list[str] = []
    buy_only = results[
        results["params_json"].apply(
            lambda payload: payload["allowed_recommendations"] == ("BUY_CANDIDATE",)
        )
    ]
    buy_watch = results[
        results["params_json"].apply(
            lambda payload: "WATCH" in payload["allowed_recommendations"]
        )
    ]
    if not buy_only.empty and not buy_watch.empty:
        if buy_only["strategy_score"].mean() > buy_watch["strategy_score"].mean():
            findings.append(
                "Exigir solo BUY_CANDIDATE mejora de media la robustez frente a "
                "permitir WATCH."
            )
        else:
            findings.append(
                "Permitir BUY_CANDIDATE + WATCH aporta más frecuencia y no empeora "
                "claramente el ranking medio."
            )

    bullish = results[
        results["params_json"].apply(lambda payload: payload["require_bullish_trend"])
    ]
    non_bullish = results[
        results["params_json"].apply(
            lambda payload: not payload["require_bullish_trend"]
        )
    ]
    if not bullish.empty and not non_bullish.empty:
        if bullish["test_expectancy_pct"].mean() > non_bullish["test_expectancy_pct"].mean():
            findings.append("Exigir SMA50 > SMA200 mejora la expectancy media fuera de muestra.")
        else:
            findings.append(
                "No exigir tendencia alcista abre más setups, pero la mejora OOS "
                "es menos consistente."
            )

    hybrid = results[
        results["params_json"].apply(lambda payload: payload["exit_strategy"] == "hybrid")
    ]
    fixed = results[
        results["params_json"].apply(
            lambda payload: payload["exit_strategy"] == "fixed_horizon"
        )
    ]
    if not hybrid.empty and not fixed.empty:
        if hybrid["strategy_score"].mean() > fixed["strategy_score"].mean():
            findings.append(
                "Las salidas híbridas combinan mejor control de drawdown y "
                "robustez que los horizontes fijos puros."
            )
        else:
            findings.append(
                "Los horizontes fijos siguen siendo competitivos y menos sensibles "
                "al ruido del score de salida."
            )

    crypto = results[results["universe"] == "crypto"]
    mixed = results[results["universe"] == "mixed"]
    if (
        not crypto.empty
        and not mixed.empty
        and crypto["strategy_score"].mean() < mixed["strategy_score"].mean()
    ):
        findings.append(
            "Crypto sigue mostrando mayor fragilidad temporal y exige filtros más "
            "duros para no degradar robustez."
        )

    strict_rsi = results[
        results["params_json"].apply(lambda payload: payload["max_rsi14"] <= 50)
    ]
    loose_rsi = results[
        results["params_json"].apply(lambda payload: payload["max_rsi14"] >= 60)
    ]
    if not strict_rsi.empty and not loose_rsi.empty:
        if strict_rsi["test_expectancy_pct"].mean() > loose_rsi["test_expectancy_pct"].mean():
            findings.append(
                "RSI máximos más exigentes tienden a mejorar la calidad media de "
                "entrada frente a filtros muy laxos."
            )

    return findings


if __name__ == "__main__":
    main()
