from __future__ import annotations

from pathlib import Path

import pandas as pd

from jobs import run_scoring_tuning_study


def test_deep_merge_merges_nested_dicts_without_mutating_base() -> None:
    base = {
        "technical": {
            "trend": {"death_cross_penalty": -10, "ema20_above_sma50": 10},
            "rsi_contextual": {"oversold_shock": 24},
        }
    }
    overrides = {"technical": {"trend": {"death_cross_penalty": -6}}}

    merged = run_scoring_tuning_study.deep_merge(base, overrides)

    assert merged["technical"]["trend"]["death_cross_penalty"] == -6
    assert merged["technical"]["trend"]["ema20_above_sma50"] == 10
    assert base["technical"]["trend"]["death_cross_penalty"] == -10


def test_build_reference_universes_loads_expected_entries() -> None:
    config = run_scoring_tuning_study.load_tuning_config()

    universes = run_scoring_tuning_study.build_reference_universes(config)

    assert any(universe.name == "mixed_reference" for universe in universes)
    assert any(universe.candidate_config_id == "cfg_12" for universe in universes)


def test_build_markdown_report_renders_iteration_and_details() -> None:
    summary = pd.DataFrame(
        [
            {
                "iteration_id": "trend",
                "iteration_label": "Trend slow adjustments",
                "variant_id": "trend_balanced",
                "variant_label": "Balanced trend",
                "aggregate_score": 12.4,
                "scoring_overrides": {},
            }
        ]
    )
    details = pd.DataFrame(
        [
            {
                "iteration_id": "trend",
                "variant_label": "Balanced trend",
                "universe": "stocks_reference",
                "candidate_label": "Hybrid 10/5/20",
                "train_expectancy_pct": 0.8,
                "test_expectancy_pct": 0.5,
                "train_profit_factor": 1.2,
                "test_profit_factor": 1.1,
                "test_max_drawdown_pct": 1.3,
                "train_total_trades": 40,
                "test_total_trades": 15,
                "weighted_universe_score": 3.1,
                "universe_score": 10.2,
            }
        ]
    )

    report = run_scoring_tuning_study.build_markdown_report(summary, details)

    assert "Trend slow adjustments" in report
    assert "Balanced trend" in report
    assert "stocks_reference" in report


def test_main_writes_output_files(monkeypatch, tmp_path: Path, capsys) -> None:
    summary = pd.DataFrame(
        [
            {
                "iteration_id": "trend",
                "iteration_label": "Trend slow adjustments",
                "variant_id": "trend_control",
                "variant_label": "Control",
                "aggregate_score": 8.0,
                "scoring_overrides": {},
            }
        ]
    )
    details = pd.DataFrame(
        [
            {
                "iteration_id": "trend",
                "variant_label": "Control",
                "universe": "mixed_reference",
                "candidate_label": "Balanced hybrid 12/7/15",
                "train_expectancy_pct": 0.4,
                "test_expectancy_pct": 0.3,
                "train_profit_factor": 1.1,
                "test_profit_factor": 1.05,
                "test_max_drawdown_pct": 2.0,
                "train_total_trades": 20,
                "test_total_trades": 8,
                "weighted_universe_score": 2.8,
                "universe_score": 8.0,
            }
        ]
    )

    monkeypatch.setattr(run_scoring_tuning_study, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(run_scoring_tuning_study, "init_db", lambda: None)
    monkeypatch.setattr(
        run_scoring_tuning_study,
        "run_tuning_study",
        lambda config_name="scoring_tuning.yaml": (summary, details),
    )

    run_scoring_tuning_study.main([])
    output = capsys.readouterr().out

    assert "scoring_tuning_iteration_summary.csv" in output
    assert (tmp_path / "scoring_tuning_iteration_summary.csv").exists()
    assert (tmp_path / "scoring_tuning_iteration_details.csv").exists()
    assert (tmp_path / "scoring_tuning_study.md").exists()
