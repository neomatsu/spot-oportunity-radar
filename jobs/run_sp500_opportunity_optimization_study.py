from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import combinations, combinations_with_replacement, product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.sp500_opportunity import (
    SP500OpportunityBacktestConfig,
    SP500OpportunityBacktester,
)
from core.config import get_settings, load_yaml_config
from data.database import session_scope
from data.repositories.sp500_opportunity_repo import SP500OpportunityRepository

_WORKER_HISTORY: pd.DataFrame | None = None
_WORKER_PERIODS: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}


def _worker_init(
    history: pd.DataFrame,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> None:
    global _WORKER_HISTORY, _WORKER_PERIODS
    _WORKER_HISTORY = history
    _WORKER_PERIODS = periods


def _config_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _task(stage: str, config: SP500OpportunityBacktestConfig) -> dict[str, Any]:
    payload = asdict(config)
    return {"stage": stage, "config_id": _config_id(payload), "config": payload}


def _evaluate_task(task: dict[str, Any]) -> dict[str, Any]:
    if _WORKER_HISTORY is None:
        raise RuntimeError("Study worker was not initialized")
    config = SP500OpportunityBacktestConfig(**task["config"])
    engine = SP500OpportunityBacktester()
    row: dict[str, Any] = {
        "stage": task["stage"],
        "config_id": task["config_id"],
        **_flatten_config(config),
    }
    for period_name in ("train", "validation", "test"):
        if period_name not in _WORKER_PERIODS:
            continue
        start, end = _WORKER_PERIODS[period_name]
        dates = pd.to_datetime(_WORKER_HISTORY["date"])
        selected = _WORKER_HISTORY[(dates >= start) & (dates <= end)]
        if len(selected) < 3:
            continue
        result = engine.run(selected, config)
        row.update(_result_metrics(period_name, result))
    return row


def _flatten_config(config: SP500OpportunityBacktestConfig) -> dict[str, Any]:
    row: dict[str, Any] = {
        "initial_capital": config.initial_capital,
        "buy_sizing_basis": config.buy_sizing_basis,
        "buy_reset_threshold": config.buy_reset_threshold,
        "sell_reset_threshold": config.sell_reset_threshold,
        "commission_bps": config.commission_bps,
        "slippage_bps": config.slippage_bps,
    }
    for index, value in enumerate(config.buy_thresholds, 1):
        row[f"buy_threshold_{index}"] = value
    for index, value in enumerate(config.sell_thresholds, 1):
        row[f"sell_threshold_{index}"] = value
    for index, value in enumerate(config.buy_capital_pcts, 1):
        row[f"buy_pct_{index}"] = value
    for index, value in enumerate(config.sell_position_pcts, 1):
        row[f"sell_pct_{index}"] = value
    return row


def _result_metrics(prefix: str, result: Any) -> dict[str, Any]:
    return {
        f"{prefix}_total_return_pct": result.total_return_pct,
        f"{prefix}_benchmark_return_pct": result.benchmark_return_pct,
        f"{prefix}_excess_return_pct": result.excess_return_pct,
        f"{prefix}_cagr_pct": result.cagr_pct,
        f"{prefix}_max_drawdown_pct": result.max_drawdown_pct,
        f"{prefix}_sharpe": result.sharpe,
        f"{prefix}_sortino": result.sortino,
        f"{prefix}_calmar": result.calmar,
        f"{prefix}_buy_count": result.buy_count,
        f"{prefix}_sell_count": result.sell_count,
        f"{prefix}_average_exposure_pct": result.average_exposure_pct,
        f"{prefix}_turnover_pct": result.turnover_pct,
        f"{prefix}_final_equity": result.final_equity,
    }


def _base_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    execution = config["execution"]
    return {
        "initial_capital": float(execution["initial_capital"]),
        "buy_sizing_basis": str(execution["buy_sizing_basis"]),
        "buy_reset_threshold": float(execution["buy_reset_threshold"]),
        "sell_reset_threshold": float(execution["sell_reset_threshold"]),
        "commission_bps": float(execution["commission_bps"]),
        "slippage_bps": float(execution["slippage_bps"]),
        "minimum_trade_value": float(execution["minimum_trade_value"]),
    }


def stage_1_tasks(config: dict[str, Any]) -> list[dict[str, Any]]:
    stage = config["stage_1_thresholds"]
    common = _base_kwargs(config)
    tasks = []
    for buy_thresholds in combinations(map(float, stage["buy_candidates"]), 3):
        for sell_thresholds in combinations(map(float, stage["sell_candidates"]), 3):
            candidate = SP500OpportunityBacktestConfig(
                **common,
                buy_thresholds=buy_thresholds,
                sell_thresholds=sell_thresholds,
                buy_capital_pcts=tuple(map(float, stage["fixed_buy_pcts"])),
                sell_position_pcts=tuple(map(float, stage["fixed_sell_pcts"])),
            )
            tasks.append(_task("stage_1_thresholds", candidate))
    return tasks


def _sizing_profiles(values: Iterable[float], *, reverse: bool) -> list[tuple[float, ...]]:
    profiles = list(combinations_with_replacement(sorted(set(map(float, values))), 3))
    profiles = [profile for profile in profiles if any(value > 0 for value in profile)]
    return [tuple(reversed(profile)) for profile in profiles] if reverse else profiles


def stage_2_tasks(
    config: dict[str, Any], stage_1_ranked: pd.DataFrame
) -> list[dict[str, Any]]:
    keep = int(config["stage_1_thresholds"]["keep_top"])
    seeds = stage_1_ranked.head(keep)
    values = config["stage_2_sizing"]["percentage_candidates"]
    buys = _sizing_profiles(values, reverse=False)
    sells = _sizing_profiles(values, reverse=True)
    common = _base_kwargs(config)
    tasks: dict[str, dict[str, Any]] = {}
    for _, seed in seeds.iterrows():
        for buy_pcts, sell_pcts in product(buys, sells):
            candidate = SP500OpportunityBacktestConfig(
                **common,
                buy_thresholds=_tuple_from_row(seed, "buy_threshold"),
                sell_thresholds=_tuple_from_row(seed, "sell_threshold"),
                buy_capital_pcts=buy_pcts,
                sell_position_pcts=sell_pcts,
            )
            item = _task("stage_2_sizing", candidate)
            tasks[item["config_id"]] = item
    return list(tasks.values())


def stage_3_tasks(
    config: dict[str, Any], stage_2_ranked: pd.DataFrame
) -> list[dict[str, Any]]:
    local = config["stage_3_local"]
    seeds = stage_2_ranked.head(int(local["seed_count"]))
    pct_step = float(local["percentage_step"])
    threshold_step = float(local["threshold_step"])
    common = _base_kwargs(config)
    tasks: dict[str, dict[str, Any]] = {}
    for _, seed in seeds.iterrows():
        base_buy_pct = _tuple_from_row(seed, "buy_pct")
        base_sell_pct = _tuple_from_row(seed, "sell_pct")
        base_buy_threshold = _tuple_from_row(seed, "buy_threshold")
        base_sell_threshold = _tuple_from_row(seed, "sell_threshold")
        pct_neighbors = [
            sorted({max(0.0, min(1.0, value + delta)) for delta in (-pct_step, 0, pct_step)})
            for value in (*base_buy_pct, *base_sell_pct)
        ]
        for values in product(*pct_neighbors):
            buy_pcts = tuple(values[:3])
            sell_pcts = tuple(values[3:])
            if buy_pcts != tuple(sorted(buy_pcts)):
                continue
            if sell_pcts != tuple(sorted(sell_pcts, reverse=True)):
                continue
            if not any(buy_pcts) or not any(sell_pcts):
                continue
            candidate = SP500OpportunityBacktestConfig(
                **common,
                buy_thresholds=base_buy_threshold,
                sell_thresholds=base_sell_threshold,
                buy_capital_pcts=buy_pcts,
                sell_position_pcts=sell_pcts,
            )
            item = _task("stage_3_local", candidate)
            tasks[item["config_id"]] = item
        threshold_neighbors = [
            [value - threshold_step, value, value + threshold_step]
            for value in (*base_buy_threshold, *base_sell_threshold)
        ]
        for values in product(*threshold_neighbors):
            buy_thresholds = tuple(values[:3])
            sell_thresholds = tuple(values[3:])
            if buy_thresholds != tuple(sorted(buy_thresholds)):
                continue
            if sell_thresholds != tuple(sorted(sell_thresholds)):
                continue
            if len(set(buy_thresholds)) < 3 or len(set(sell_thresholds)) < 3:
                continue
            if buy_thresholds[0] <= common["buy_reset_threshold"]:
                continue
            if sell_thresholds[-1] >= common["sell_reset_threshold"]:
                continue
            candidate = SP500OpportunityBacktestConfig(
                **common,
                buy_thresholds=buy_thresholds,
                sell_thresholds=sell_thresholds,
                buy_capital_pcts=base_buy_pct,
                sell_position_pcts=base_sell_pct,
            )
            item = _task("stage_3_local", candidate)
            tasks[item["config_id"]] = item
    return list(tasks.values())


def _tuple_from_row(row: pd.Series, prefix: str) -> tuple[float, float, float]:
    return tuple(float(row[f"{prefix}_{index}"]) for index in range(1, 4))  # type: ignore[return-value]


def rank_results(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    if frame.empty:
        return frame
    ranked = frame.copy()
    rules = config["ranking"]
    eligible = (
        (ranked["train_buy_count"] >= int(rules["minimum_train_buys"]))
        & (ranked["validation_buy_count"] >= int(rules["minimum_validation_buys"]))
        & (
            ranked["train_sell_count"] + ranked["validation_sell_count"]
            >= int(rules["minimum_total_sells"])
        )
        & (
            ranked["validation_average_exposure_pct"]
            >= float(rules["minimum_average_exposure_pct"])
        )
        & (
            ranked["validation_average_exposure_pct"]
            <= float(rules["maximum_average_exposure_pct"])
        )
    )
    ranked["eligible"] = eligible
    metric_weights = rules["metric_weights"]
    for period in ("train", "validation"):
        quality = pd.Series(0.0, index=ranked.index)
        for metric, weight in metric_weights.items():
            if metric == "trades":
                values = ranked[f"{period}_buy_count"] + ranked[f"{period}_sell_count"]
            else:
                values = pd.to_numeric(ranked[f"{period}_{metric}"], errors="coerce")
            percentiles = values.replace([np.inf, -np.inf], np.nan).rank(
                pct=True
            ).fillna(0)
            quality += percentiles * float(weight)
        ranked[f"{period}_quality"] = quality
    train_weight = float(rules["train_weight"])
    validation_weight = float(rules["validation_weight"])
    gap = (ranked["train_quality"] - ranked["validation_quality"]).abs()
    ranked["robustness_score"] = (
        train_weight * ranked["train_quality"]
        + validation_weight * ranked["validation_quality"]
        - float(rules["gap_penalty"]) * gap
    )
    ranked.loc[~eligible, "robustness_score"] = -1.0
    return ranked.sort_values(
        ["robustness_score", "validation_cagr_pct"], ascending=False
    ).reset_index(drop=True)


class StudyStore:
    def __init__(self, output_dir: Path, config: dict[str, Any]) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = output_dir / "state.json"
        self.config_hash = _config_id(config)

    def initialize(self, *, restart: bool) -> dict[str, Any]:
        if restart:
            for path in self.output_dir.glob("stage_*.csv"):
                path.unlink(missing_ok=True)
            self.state_path.unlink(missing_ok=True)
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("config_hash") != self.config_hash:
                raise RuntimeError(
                    "Study configuration changed; use --restart or restore the original YAML"
                )
            return state
        state = {
            "config_hash": self.config_hash,
            "started_at": datetime.now(UTC).isoformat(),
            "completed_stages": [],
            "counts": {},
        }
        self.save_state(state)
        return state

    def stage_path(self, stage: str) -> Path:
        return self.output_dir / f"{stage}.csv"

    def load_stage(self, stage: str) -> pd.DataFrame:
        path = self.stage_path(stage)
        return pd.read_csv(path) if path.exists() else pd.DataFrame()

    def save_stage(self, stage: str, frame: pd.DataFrame) -> None:
        path = self.stage_path(stage)
        temporary = path.with_suffix(".tmp")
        frame.to_csv(temporary, index=False)
        os.replace(temporary, path)

    def save_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)


def run_stage(
    stage: str,
    tasks: list[dict[str, Any]],
    store: StudyStore,
    history: pd.DataFrame,
    periods: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    *,
    workers: int,
    checkpoint_every: int,
    max_runs: int | None,
) -> tuple[pd.DataFrame, int]:
    existing = store.load_stage(stage)
    completed = set(existing.get("config_id", pd.Series(dtype=str)).astype(str))
    pending = [task for task in tasks if task["config_id"] not in completed]
    if max_runs is not None:
        pending = pending[:max_runs]
    if not pending:
        return existing.drop_duplicates("config_id", keep="last"), 0
    rows = existing.to_dict("records")
    started = time.perf_counter()
    if workers <= 1:
        _worker_init(history, periods)
        iterator = map(_evaluate_task, pending)
        executor = None
    else:
        executor = ProcessPoolExecutor(
            max_workers=workers, initializer=_worker_init, initargs=(history, periods)
        )
        iterator = executor.map(_evaluate_task, pending, chunksize=10)
    try:
        for index, row in enumerate(iterator, 1):
            rows.append(row)
            if index % checkpoint_every == 0:
                store.save_stage(stage, pd.DataFrame(rows))
                elapsed = time.perf_counter() - started
                print(f"{stage}: {index}/{len(pending)} new runs in {elapsed:.1f}s")
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
    result = pd.DataFrame(rows).drop_duplicates("config_id", keep="last")
    store.save_stage(stage, result)
    return result, len(pending)


def _load_history(config: dict[str, Any]) -> pd.DataFrame:
    periods = config["periods"]
    start = min(pd.Timestamp(value[0]).date() for value in periods.values())
    end = max(pd.Timestamp(value[1]).date() for value in periods.values())
    with session_scope() as session:
        return SP500OpportunityRepository(session).history(
            start_date=start,
            end_date=end,
            source_version=str(config["source_version"]),
        )


def _periods(
    config: dict[str, Any], *, include_test: bool
) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    names = ["train", "validation"] + (["test"] if include_test else [])
    return {
        name: (pd.Timestamp(config["periods"][name][0]), pd.Timestamp(config["periods"][name][1]))
        for name in names
    }


def _mark_stage(state: dict[str, Any], store: StudyStore, stage: str, count: int) -> None:
    if stage not in state["completed_stages"]:
        state["completed_stages"].append(stage)
    state["counts"][stage] = count
    state["updated_at"] = datetime.now(UTC).isoformat()
    store.save_state(state)


def _write_report(output_dir: Path, final: pd.DataFrame, config: dict[str, Any]) -> None:
    top = final.head(25)
    columns = [
        "config_id", "robustness_score", "test_cagr_pct", "test_max_drawdown_pct",
        "test_sharpe", "test_calmar", "test_excess_return_pct", "test_buy_count",
        "test_sell_count", "buy_threshold_1", "buy_threshold_2", "buy_threshold_3",
        "buy_pct_1", "buy_pct_2", "buy_pct_3", "sell_threshold_1",
        "sell_threshold_2", "sell_threshold_3", "sell_pct_1", "sell_pct_2",
        "sell_pct_3",
    ]
    top[columns].to_csv(output_dir / "top_configs.csv", index=False)
    report_table = top[columns].fillna("").astype(str)
    header = "| " + " | ".join(report_table.columns) + " |"
    separator = "| " + " | ".join("---" for _ in report_table.columns) + " |"
    rows = [
        "| "
        + " | ".join(value.replace("|", "\\|") for value in row)
        + " |"
        for row in report_table.itertuples(index=False, name=None)
    ]
    lines = [
        "# S&P 500 Opportunity staged optimization",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Source: `{config['source_version']}`",
        "Sizing: `available_cash`; execution: D+1.",
        "",
        "The final test period was not used for candidate ranking.",
        "",
        "\n".join([header, separator, *rows]),
    ]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_study(
    config: dict[str, Any], *, workers: int, max_runs: int | None, restart: bool
) -> dict[str, Any]:
    root = get_settings().root_dir
    output_dir = root / str(config["output_dir"])
    store = StudyStore(output_dir, config)
    state = store.initialize(restart=restart)
    history = _load_history(config)
    if history.empty:
        raise RuntimeError("No cached S&P 500 Opportunity history for the configured version")
    checkpoint_every = int(config["runtime"]["checkpoint_every"])

    stage_1 = stage_1_tasks(config)
    frame_1, ran = run_stage(
        "stage_1_thresholds", stage_1, store, history, _periods(config, include_test=False),
        workers=workers, checkpoint_every=checkpoint_every, max_runs=max_runs,
    )
    if len(frame_1) < len(stage_1):
        return {"status": "partial", "stage": "stage_1_thresholds", "new_runs": ran}
    ranked_1 = rank_results(frame_1, config)
    store.save_stage("stage_1_thresholds_ranked", ranked_1)
    _mark_stage(state, store, "stage_1_thresholds", len(frame_1))

    stage_2 = stage_2_tasks(config, ranked_1)
    frame_2, ran = run_stage(
        "stage_2_sizing", stage_2, store, history, _periods(config, include_test=False),
        workers=workers, checkpoint_every=checkpoint_every, max_runs=max_runs,
    )
    if len(frame_2) < len(stage_2):
        return {"status": "partial", "stage": "stage_2_sizing", "new_runs": ran}
    ranked_2 = rank_results(frame_2, config)
    store.save_stage("stage_2_sizing_ranked", ranked_2)
    _mark_stage(state, store, "stage_2_sizing", len(frame_2))

    stage_3 = stage_3_tasks(config, ranked_2)
    frame_3, ran = run_stage(
        "stage_3_local", stage_3, store, history, _periods(config, include_test=False),
        workers=workers, checkpoint_every=checkpoint_every, max_runs=max_runs,
    )
    if len(frame_3) < len(stage_3):
        return {"status": "partial", "stage": "stage_3_local", "new_runs": ran}
    ranked_3 = rank_results(pd.concat([frame_2, frame_3], ignore_index=True), config)
    store.save_stage("stage_3_local_ranked", ranked_3)
    _mark_stage(state, store, "stage_3_local", len(frame_3))

    final_count = int(config["stage_3_local"]["final_candidates"])
    final_tasks = []
    for _, row in ranked_3.head(final_count).iterrows():
        candidate = SP500OpportunityBacktestConfig(
            **_base_kwargs(config),
            buy_thresholds=_tuple_from_row(row, "buy_threshold"),
            sell_thresholds=_tuple_from_row(row, "sell_threshold"),
            buy_capital_pcts=_tuple_from_row(row, "buy_pct"),
            sell_position_pcts=_tuple_from_row(row, "sell_pct"),
        )
        final_tasks.append(_task("stage_4_final_validation", candidate))
    final_frame, ran = run_stage(
        "stage_4_final_validation", final_tasks, store, history,
        _periods(config, include_test=True), workers=workers,
        checkpoint_every=checkpoint_every, max_runs=max_runs,
    )
    if len(final_frame) < len(final_tasks):
        return {"status": "partial", "stage": "stage_4_final_validation", "new_runs": ran}
    final_ranked = rank_results(final_frame, config)
    store.save_stage("stage_4_final_validation_ranked", final_ranked)
    _write_report(output_dir, final_ranked, config)
    _mark_stage(state, store, "stage_4_final_validation", len(final_frame))
    state["finished_at"] = datetime.now(UTC).isoformat()
    store.save_state(state)
    return {"status": "complete", "output_dir": str(output_dir), "runs": state["counts"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run resumable staged S&P 500 optimization")
    parser.add_argument("--config", default="sp500_opportunity_study.yaml")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)
    config = load_yaml_config(args.config)
    workers = args.workers or int(config["runtime"]["default_workers"])
    result = run_study(
        config, workers=max(1, workers), max_runs=args.max_runs, restart=args.restart
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
