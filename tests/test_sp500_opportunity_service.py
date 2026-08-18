from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from services.sp500_opportunity_service import SP500OpportunityService


def _config() -> dict:
    return {
        "index_symbol": "^GSPC",
        "minimum_available_components": 3,
        "weights": {
            "valuation": 0.15,
            "sentiment": 0.20,
            "momentum": 0.15,
            "drawdown": 0.25,
            "breadth": 0.15,
            "macro": 0.10,
        },
        "classification": [
            {"min_score": 80, "label": "EXCEPTIONAL"},
            {"min_score": 60, "label": "OPPORTUNITY"},
            {"min_score": 0, "label": "LOW"},
        ],
        "history": {
            "default_start_date": "2020-01-01",
            "warmup_years": 1,
            "source_version": "sp500_test_v1",
            "minimum_percentile_observations": 20,
        },
        "score_calibration": {
            "enabled": True,
            "target_mean": 50.0,
            "trailing_years": 1,
            "minimum_history_years": 1,
            "dispersion_scale": 0.8,
        },
        "market_data": {"cache_dir": "cache/test_sp500_opportunity", "retries": 1},
        "valuation": {
            "enabled": True,
            "publication_lag_days": 1,
            "minimum_percentile_observations": 10,
            "point_in_time_safe": False,
            "rolling_window_years": 1,
            "rolling_minimum_observations": 20,
            "subweights": {
                "full_history": 0.50,
                "rolling_20y": 0.25,
                "excess_cape_yield": 0.25,
            },
            "neutral_shrinkage": {
                "enabled": True,
                "weight": 0.25,
                "anchor_score": 5.0,
            },
            "real_yield": {
                "enabled": True,
                "publication_lag_days": 1,
                "minimum_percentile_observations": 20,
                "maximum_staleness_days": 10,
            },
        },
        "sentiment": {"enabled": True, "minimum_percentile_observations": 20},
        "momentum": {
            "rsi_daily_period": 14,
            "rsi_weekly_period": 14,
            "sma_period": 20,
            "subweights": {
                "rsi_daily": 0.25,
                "rsi_weekly": 0.25,
                "distance_sma200": 0.50,
            },
        },
        "drawdown": {"method": "percentile", "thresholds": []},
        "breadth": {
            "enabled": True,
            "minimum_percentile_observations": 20,
            "point_in_time_safe": False,
        },
        "macro": {
            "enabled": True,
            "publication_lag_days": 1,
            "level_weight": 0.6,
            "change_weight": 0.4,
            "change_lookback_sessions": 10,
            "minimum_percentile_observations": 20,
        },
    }


def _sources() -> tuple:
    dates = pd.date_range("2019-01-01", "2024-12-31", freq="B")
    wave = np.sin(np.arange(len(dates)) / 35)

    def price(start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame({"date": dates, "close": 2500 + np.arange(len(dates)) + wave * 200})

    def vix(start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame({"date": dates, "vix": 20 - wave * 8})

    def valuation(start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame({"date": dates, "cape": 25 + wave * 5})

    def breadth(start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame({"date": dates, "pct_above_sma200": 55 + wave * 35})

    def macro(start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame({"date": dates, "fed_funds_rate": 3 + wave})

    def real_yield(start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame({"date": dates, "real_yield_10y": 1.5 + wave})

    return price, vix, valuation, breadth, macro, real_yield


def test_builds_and_persists_six_component_history(db_session) -> None:
    price, vix, valuation, breadth, macro, real_yield = _sources()
    service = SP500OpportunityService(
        db_session,
        config=_config(),
        price_loader=price,
        vix_loader=vix,
        valuation_loader=valuation,
        real_yield_loader=real_yield,
        breadth_loader=breadth,
        macro_loader=macro,
    )
    history = service.update_history(date(2020, 1, 1), date(2022, 12, 30))

    assert not history.empty
    latest = history.iloc[-1]
    assert latest["available_components"] == 6
    assert 0 <= latest["overall_score"] <= 100
    assert set(latest["components_json"]) == set(_config()["weights"])
    assert latest["data_quality_json"]["point_in_time_unsafe_keys"] == [
        "valuation",
        "breadth",
        "macro",
    ]
    valuation_metadata = latest["components_json"]["valuation"]["metadata"]
    assert set(valuation_metadata["subscores"]) == {
        "full_history",
        "rolling_20y",
        "relative_real_yield",
    }
    assert all(
        value is not None for value in valuation_metadata["subscores"].values()
    )
    assert valuation_metadata["neutral_shrinkage_weight"] == 0.25
    assert valuation_metadata["neutral_anchor_score"] == 5.0
    assert latest["components_json"]["valuation"]["score"] == (
        valuation_metadata["hybrid_raw_score"] * 0.75 + 5.0 * 0.25
    )


def test_missing_sources_redistribute_weights_without_neutral_substitution(db_session) -> None:
    price, _, _, _, _, _ = _sources()

    def missing(start: date, end: date) -> pd.DataFrame:
        raise RuntimeError("offline")

    config = _config() | {"minimum_available_components": 2}
    service = SP500OpportunityService(
        db_session,
        config=config,
        price_loader=price,
        vix_loader=missing,
        valuation_loader=missing,
        breadth_loader=missing,
        macro_loader=missing,
    )
    history = service.update_history(date(2020, 1, 1), date(2022, 12, 30))
    latest = history.iloc[-1]

    assert latest["available_components"] == 2
    assert set(latest["data_quality_json"]["available_keys"]) == {"momentum", "drawdown"}
    assert set(latest["data_quality_json"]["normalized_weights"]) == {
        "momentum",
        "drawdown",
    }


def test_expanding_percentiles_do_not_change_when_future_is_added(db_session) -> None:
    price, vix, valuation, breadth, macro, real_yield = _sources()
    service = SP500OpportunityService(
        db_session,
        config=_config(),
        price_loader=price,
        vix_loader=vix,
        valuation_loader=valuation,
        real_yield_loader=real_yield,
        breadth_loader=breadth,
        macro_loader=macro,
    )
    first = service.update_history(date(2020, 1, 1), date(2022, 12, 30), force=True)
    reference = first.loc[pd.to_datetime(first["date"]) == "2021-06-30", "overall_score"].iloc[0]
    second = service.update_history(date(2020, 1, 1), date(2024, 12, 30), force=True)
    after = second.loc[pd.to_datetime(second["date"]) == "2021-06-30", "overall_score"].iloc[0]

    assert after == reference


def test_stale_component_is_not_forward_filled_indefinitely() -> None:
    base = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-10"])})
    source = pd.DataFrame(
        {"date": pd.to_datetime(["2024-01-01"]), "cape": [30.0]}
    )

    merged = SP500OpportunityService._merge_asof(
        base, source, "cape", maximum_staleness_days=3
    )

    assert merged.loc[0, "cape"] == 30.0
    assert pd.isna(merged.loc[1, "cape"])


def test_rolling_percentile_uses_only_trailing_observations() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 0.0])

    percentiles = SP500OpportunityService._rolling_percentile(
        values, window=3, min_periods=3
    )

    assert percentiles.iloc[2] == 1.0
    assert percentiles.iloc[3] == 1.0
    assert percentiles.iloc[4] == 1 / 3


def test_valuation_subscore_redistributes_missing_real_yield_weight() -> None:
    frame = pd.DataFrame(
        {
            "full": [2.0],
            "rolling": [6.0],
            "relative": [np.nan],
        }
    )

    result = SP500OpportunityService._weighted_available_columns(
        frame, {"full": 0.50, "rolling": 0.25, "relative": 0.25}
    )

    assert result.iloc[0] == (2.0 * 0.50 + 6.0 * 0.25) / 0.75


def test_score_calibration_uses_only_prior_trailing_values(db_session) -> None:
    config = _config()
    service = SP500OpportunityService(db_session, config=config)
    raw = pd.Series([40.0] * 252 + [60.0, 20.0])

    calibrated, baseline = service._calibrate_overall_scores(raw)

    assert baseline.iloc[252] == 40.0
    assert calibrated.iloc[252] == 66.0
    assert baseline.iloc[253] == (40.0 * 251 + 60.0) / 252
    assert calibrated.iloc[253] < 35.0


def test_parses_multpl_monthly_cape_table() -> None:
    document = """
    <table id="datatable">
      <tr><th>Date</th><th>Value</th></tr>
      <tr class="odd"><td>Aug 17, 2026</td><td>&#x2002;42.35</td></tr>
      <tr class="even"><td>Jul 1, 2026</td><td>&#x2002;40.73</td></tr>
    </table>
    """

    parsed = SP500OpportunityService._parse_multpl_table(document)

    assert parsed["date"].dt.date.tolist() == [date(2026, 8, 17), date(2026, 7, 1)]
    assert parsed["cape"].tolist() == [42.35, 40.73]


def test_splices_multpl_only_after_yale_when_overlap_is_compatible() -> None:
    primary = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-08-01", "2023-09-01"]),
            "cape": [30.47, 30.81],
        }
    )
    extension = pd.DataFrame(
        {
            "date": pd.to_datetime(["2023-08-01", "2023-09-01", "2023-10-01"]),
            "cape": [30.09, 29.80, 28.70],
        }
    )
    config = {
        "extension": {
            "overlap_months": 12,
            "maximum_median_difference_pct": 5.0,
        }
    }

    merged = SP500OpportunityService._splice_valuation_series(
        primary, extension, config
    )

    assert merged["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2023-08-01",
        "2023-09-01",
        "2023-10-01",
    ]
    assert merged["cape"].tolist() == [30.47, 30.81, 28.70]
