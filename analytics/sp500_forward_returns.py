from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class ForwardReturnsResult:
    observations: pd.DataFrame
    summary: pd.DataFrame
    monotonicity: pd.DataFrame


class SP500ForwardReturnsAnalyzer:
    def analyze(
        self,
        history: pd.DataFrame,
        *,
        horizons: dict[str, int],
        score_bands: list[float],
    ) -> ForwardReturnsResult:
        frame = self._prepare(history)
        labels = [
            f"{score_bands[index]:g}-{score_bands[index + 1]:g}"
            for index in range(len(score_bands) - 1)
        ]
        frame["score_band"] = pd.cut(
            frame["overall_score"],
            bins=score_bands,
            labels=labels,
            right=False,
            include_lowest=True,
        )
        summary_rows: list[dict[str, float | int | str]] = []
        monotonicity_rows: list[dict[str, float | int | str]] = []
        for horizon_label, sessions in horizons.items():
            return_column = f"return_{horizon_label}_pct"
            mae_column = f"mae_{horizon_label}_pct"
            frame[return_column] = (
                frame["sp500_price"].shift(-sessions) / frame["sp500_price"] - 1.0
            ) * 100.0
            frame[mae_column] = self._forward_mae(frame["sp500_price"], sessions)

            valid = frame.dropna(subset=["overall_score", return_column])
            monotonicity_rows.append(
                {
                    "horizon": horizon_label,
                    "sessions": sessions,
                    "observations": len(valid),
                    "spearman_score_vs_return": valid["overall_score"].corr(
                        valid[return_column], method="spearman"
                    ),
                }
            )
            grouped = valid.groupby("score_band", observed=False)
            for band, group in grouped:
                values = group[return_column].dropna()
                mae_values = group[mae_column].dropna()
                summary_rows.append(
                    {
                        "score_band": str(band),
                        "horizon": horizon_label,
                        "sessions": sessions,
                        "observations": len(values),
                        "mean_return_pct": values.mean() if not values.empty else np.nan,
                        "median_return_pct": values.median() if not values.empty else np.nan,
                        "p25_return_pct": values.quantile(0.25) if not values.empty else np.nan,
                        "p75_return_pct": values.quantile(0.75) if not values.empty else np.nan,
                        "positive_probability_pct": (
                            (values > 0).mean() * 100.0 if not values.empty else np.nan
                        ),
                        "mean_mae_pct": (
                            mae_values.mean() if not mae_values.empty else np.nan
                        ),
                        "median_mae_pct": (
                            mae_values.median() if not mae_values.empty else np.nan
                        ),
                    }
                )
        return ForwardReturnsResult(
            observations=frame,
            summary=pd.DataFrame(summary_rows),
            monotonicity=pd.DataFrame(monotonicity_rows),
        )

    @staticmethod
    def _forward_mae(price: pd.Series, sessions: int) -> pd.Series:
        values = price.to_numpy(dtype=float)
        output = np.full(len(values), np.nan)
        for index in range(len(values) - sessions):
            future_min = np.nanmin(values[index + 1 : index + sessions + 1])
            output[index] = (future_min / values[index] - 1.0) * 100.0
        return pd.Series(output, index=price.index)

    @staticmethod
    def _prepare(history: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "overall_score", "sp500_price"}
        missing = required.difference(history.columns)
        if missing:
            raise ValueError(f"Missing historical columns: {sorted(missing)}")
        frame = history[list(required)].copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame["overall_score"] = pd.to_numeric(
            frame["overall_score"], errors="coerce"
        )
        frame["sp500_price"] = pd.to_numeric(frame["sp500_price"], errors="coerce")
        return (
            frame.dropna(subset=["sp500_price"])
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
