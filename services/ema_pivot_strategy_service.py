from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from services.estimated_volume_profile_service import EstimatedVolumeProfileService


@dataclass(frozen=True, slots=True)
class EmaPivotStrategyConfig:
    ema_period: int = 12
    atr_period: int = 14
    rsi_period: int = 14
    pivot_left_bars: int = 5
    pivot_right_bars: int = 5
    pivot_lookback_bars: int = 400
    stop_atr_buffer: float = 0.10
    min_pivot_distance_atr: float = 0.50
    tp1_r_multiple: float = 1.70
    tp1_position_fraction: float = 0.75
    pending_expiry_bars: int = 12
    pending_invalidation_progress: float = 0.80
    volume_window_bars: int = 7 * 24 * 12
    volume_profile_bins: int = 100
    volume_proximity_atr: float = 0.50
    divergence_lookback_bars: int = 72
    divergence_min_separation_bars: int = 5
    divergence_rsi_window_bars: int = 6
    divergence_min_rsi_delta: float = 1.5
    divergence_overbought_rsi: float = 70.0
    divergence_oversold_rsi: float = 30.0


@dataclass(frozen=True, slots=True)
class PivotPoint:
    kind: str
    pivot_index: int
    confirmed_index: int
    pivot_time: pd.Timestamp
    confirmed_time: pd.Timestamp
    price: float
    rsi: float | None
    divergence: bool
    divergence_from_index: int | None
    divergence_from_time: pd.Timestamp | None
    divergence_from_price: float | None
    divergence_from_rsi: float | None


@dataclass(frozen=True, slots=True)
class StrategySetup:
    direction: str
    signal_index: int
    signal_time: pd.Timestamp
    pivot: PivotPoint
    entry: float
    stop: float
    tp1: float
    atr: float
    pivot_distance_atr: float
    trend: str
    trend_aligned: bool
    divergence: bool
    volume_reference: str | None
    confirmation_score: int
    status: str
    fill_time: pd.Timestamp | None
    exit_time: pd.Timestamp | None
    result_r: float | None


@dataclass(frozen=True, slots=True)
class EmaPivotStrategyResult:
    bars: pd.DataFrame
    pivots: tuple[PivotPoint, ...]
    setups: tuple[StrategySetup, ...]


class EmaPivotStrategyService:
    """Causal 5-minute EMA/pivot research model with no external side effects."""

    REQUIRED_COLUMNS = {"open_time", "open", "high", "low", "close", "volume"}

    def __init__(self, config: EmaPivotStrategyConfig | None = None) -> None:
        self.config = config or EmaPivotStrategyConfig()

    def analyze(self, frame: pd.DataFrame) -> EmaPivotStrategyResult:
        bars = self._prepare_bars(frame)
        minimum = max(
            self.config.ema_period,
            self.config.atr_period,
            self.config.pivot_left_bars + self.config.pivot_right_bars + 1,
        )
        if len(bars) < minimum:
            raise ValueError(f"At least {minimum} valid 5-minute bars are required")

        bars = self._add_indicators(bars)
        pivots = self._detect_pivots(bars)
        setups = self._detect_setups(bars, pivots)
        return EmaPivotStrategyResult(
            bars=bars,
            pivots=tuple(pivots),
            setups=tuple(setups),
        )

    def _prepare_bars(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = self.REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing OHLCV columns: {', '.join(sorted(missing))}")
        data = frame.loc[:, sorted(self.REQUIRED_COLUMNS)].copy()
        data["open_time"] = pd.to_datetime(data["open_time"], errors="coerce", utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna().sort_values("open_time").drop_duplicates("open_time", keep="last")
        data = data[
            (data["high"] >= data["low"])
            & (data[["open", "high", "low", "close"]] > 0).all(axis=1)
            & (data["volume"] >= 0)
        ]
        return data.reset_index(drop=True)

    def _add_indicators(self, bars: pd.DataFrame) -> pd.DataFrame:
        data = bars.copy()
        data["ema"] = data["close"].ewm(span=self.config.ema_period, adjust=False).mean()
        previous_close = data["close"].shift(1)
        true_range = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - previous_close).abs(),
                (data["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        data["atr"] = true_range.ewm(
            alpha=1 / self.config.atr_period,
            adjust=False,
            min_periods=self.config.atr_period,
        ).mean()

        delta = data["close"].diff()
        gain = delta.clip(lower=0).ewm(
            alpha=1 / self.config.rsi_period,
            adjust=False,
            min_periods=self.config.rsi_period,
        ).mean()
        loss = (-delta.clip(upper=0)).ewm(
            alpha=1 / self.config.rsi_period,
            adjust=False,
            min_periods=self.config.rsi_period,
        ).mean()
        relative_strength = gain / loss.replace(0, np.nan)
        data["rsi"] = (100 - (100 / (1 + relative_strength))).fillna(50.0)
        data["cross_up"] = (data["close"].shift(1) <= data["ema"].shift(1)) & (
            data["close"] > data["ema"]
        )
        data["cross_down"] = (data["close"].shift(1) >= data["ema"].shift(1)) & (
            data["close"] < data["ema"]
        )
        return self._add_higher_timeframe_trend(data)

    @staticmethod
    def _add_higher_timeframe_trend(data: pd.DataFrame) -> pd.DataFrame:
        close_times = data["open_time"] + pd.Timedelta(minutes=5)
        hourly = (
            data.assign(close_time=close_times)
            .set_index("close_time")["close"]
            .resample("1h", label="right", closed="right")
            .last()
            .dropna()
            .to_frame()
        )
        hourly["htf_fast"] = hourly["close"].ewm(span=50, adjust=False).mean()
        hourly["htf_slow"] = hourly["close"].ewm(span=200, adjust=False).mean()
        mapped = pd.merge_asof(
            pd.DataFrame({"close_time": close_times}).sort_values("close_time"),
            hourly[["htf_fast", "htf_slow"]].reset_index().sort_values("close_time"),
            on="close_time",
            direction="backward",
        )
        result = data.copy()
        result[["htf_fast", "htf_slow"]] = mapped[["htf_fast", "htf_slow"]]
        result["htf_trend"] = np.where(
            result["htf_fast"] >= result["htf_slow"], "BULL", "BEAR"
        )
        return result

    def _detect_pivots(self, bars: pd.DataFrame) -> list[PivotPoint]:
        left = self.config.pivot_left_bars
        right = self.config.pivot_right_bars
        pivots: list[PivotPoint] = []
        prior_by_kind: dict[str, list[PivotPoint]] = {"HIGH": [], "LOW": []}
        highs = bars["high"].to_numpy(dtype=float)
        lows = bars["low"].to_numpy(dtype=float)
        for index in range(left, len(bars) - right):
            candidates: list[tuple[str, float]] = []
            if highs[index] > highs[index - left : index].max() and highs[index] >= highs[
                index + 1 : index + right + 1
            ].max():
                candidates.append(("HIGH", highs[index]))
            if lows[index] < lows[index - left : index].min() and lows[index] <= lows[
                index + 1 : index + right + 1
            ].min():
                candidates.append(("LOW", lows[index]))

            for kind, price in candidates:
                rsi = self._pivot_rsi(bars, index=index, kind=kind)
                previous = self._find_divergence_source(
                    prior_by_kind[kind],
                    kind=kind,
                    current_index=index,
                    current_price=price,
                    current_rsi=rsi,
                )
                pivot = PivotPoint(
                    kind=kind,
                    pivot_index=index,
                    confirmed_index=index + right,
                    pivot_time=pd.Timestamp(bars.iloc[index]["open_time"]),
                    confirmed_time=pd.Timestamp(bars.iloc[index + right]["open_time"]),
                    price=float(price),
                    rsi=rsi,
                    divergence=previous is not None,
                    divergence_from_index=(
                        previous.pivot_index if previous is not None else None
                    ),
                    divergence_from_time=(
                        previous.pivot_time if previous is not None else None
                    ),
                    divergence_from_price=(
                        previous.price if previous is not None else None
                    ),
                    divergence_from_rsi=(previous.rsi if previous is not None else None),
                )
                pivots.append(pivot)
                prior_by_kind[kind].append(pivot)
        return sorted(pivots, key=lambda pivot: (pivot.confirmed_index, pivot.pivot_index))

    def _pivot_rsi(self, bars: pd.DataFrame, *, index: int, kind: str) -> float:
        window = self.config.divergence_rsi_window_bars
        start = max(0, index - window)
        # Never inspect beyond the bars already known when the price pivot is confirmed.
        end = min(len(bars) - 1, index + min(window, self.config.pivot_right_bars))
        values = bars.iloc[start : end + 1]["rsi"]
        return float(values.max() if kind == "HIGH" else values.min())

    def _find_divergence_source(
        self,
        previous_pivots: list[PivotPoint],
        *,
        kind: str,
        current_index: int,
        current_price: float,
        current_rsi: float,
    ) -> PivotPoint | None:
        candidates = [
            pivot
            for pivot in previous_pivots
            if self.config.divergence_min_separation_bars
            <= current_index - pivot.pivot_index
            <= self.config.divergence_lookback_bars
            and pivot.rsi is not None
        ]
        if not candidates:
            return None
        # Divergence originates at the strongest oscillator extreme in the short
        # window. Minor price pivots during the recovery must not replace it.
        previous = (
            max(candidates, key=lambda pivot: pivot.rsi)
            if kind == "HIGH"
            else min(candidates, key=lambda pivot: pivot.rsi)
        )
        if kind == "HIGH":
            price_confirms = current_price > previous.price
            rsi_confirms = previous.rsi - current_rsi >= self.config.divergence_min_rsi_delta
            extreme = (
                max(previous.rsi, current_rsi) >= self.config.divergence_overbought_rsi
            )
        else:
            price_confirms = current_price < previous.price
            rsi_confirms = current_rsi - previous.rsi >= self.config.divergence_min_rsi_delta
            extreme = (
                min(previous.rsi, current_rsi) <= self.config.divergence_oversold_rsi
            )
        return previous if price_confirms and rsi_confirms and extreme else None

    def _detect_setups(
        self, bars: pd.DataFrame, pivots: list[PivotPoint]
    ) -> list[StrategySetup]:
        latest: dict[str, PivotPoint | None] = {"HIGH": None, "LOW": None}
        invalidated: set[tuple[str, int]] = set()
        used: set[tuple[str, int]] = set()
        by_confirmation: dict[int, list[PivotPoint]] = {}
        for pivot in pivots:
            by_confirmation.setdefault(pivot.confirmed_index, []).append(pivot)

        setups: list[StrategySetup] = []
        for index, row in bars.iterrows():
            for pivot in by_confirmation.get(index, []):
                latest[pivot.kind] = pivot

            high_pivot = latest["HIGH"]
            low_pivot = latest["LOW"]
            if high_pivot is not None and row["close"] > high_pivot.price:
                invalidated.add(("HIGH", high_pivot.pivot_index))
            if low_pivot is not None and row["close"] < low_pivot.price:
                invalidated.add(("LOW", low_pivot.pivot_index))

            direction = "LONG" if row["cross_up"] else "SHORT" if row["cross_down"] else None
            if direction is None or not np.isfinite(row["atr"]):
                continue
            kind = "LOW" if direction == "LONG" else "HIGH"
            pivot = latest[kind]
            if pivot is None:
                continue
            pivot_key = (kind, pivot.pivot_index)
            if (
                pivot_key in invalidated
                or pivot_key in used
                or index - pivot.pivot_index > self.config.pivot_lookback_bars
            ):
                continue

            entry = float(row["ema"])
            atr = float(row["atr"])
            distance_atr = abs(entry - pivot.price) / atr
            if distance_atr < self.config.min_pivot_distance_atr:
                continue
            stop = (
                pivot.price - self.config.stop_atr_buffer * atr
                if direction == "LONG"
                else pivot.price + self.config.stop_atr_buffer * atr
            )
            risk = entry - stop if direction == "LONG" else stop - entry
            if risk <= 0:
                continue
            tp1 = (
                entry + self.config.tp1_r_multiple * risk
                if direction == "LONG"
                else entry - self.config.tp1_r_multiple * risk
            )
            trend = str(row["htf_trend"])
            aligned = (direction == "LONG" and trend == "BULL") or (
                direction == "SHORT" and trend == "BEAR"
            )
            volume_reference = self._volume_reference(bars, index, entry, atr)
            score = (2 if aligned else 0) + (2 if pivot.divergence else 0) + (
                1 if volume_reference else 0
            )
            simulation = self._simulate_setup(
                bars,
                signal_index=index,
                direction=direction,
                entry=entry,
                stop=stop,
                tp1=tp1,
            )
            setups.append(
                StrategySetup(
                    direction=direction,
                    signal_index=index,
                    signal_time=pd.Timestamp(row["open_time"]),
                    pivot=pivot,
                    entry=entry,
                    stop=stop,
                    tp1=tp1,
                    atr=atr,
                    pivot_distance_atr=distance_atr,
                    trend=trend,
                    trend_aligned=aligned,
                    divergence=pivot.divergence,
                    volume_reference=volume_reference,
                    confirmation_score=score,
                    **simulation,
                )
            )
            used.add(pivot_key)
        return setups

    def _volume_reference(
        self, bars: pd.DataFrame, signal_index: int, entry: float, atr: float
    ) -> str | None:
        start = max(0, signal_index - self.config.volume_window_bars + 1)
        window = bars.iloc[start : signal_index + 1].rename(columns={"open_time": "date"})
        if len(window) < 20:
            return None
        try:
            profile = EstimatedVolumeProfileService().calculate(
                window,
                bins=self.config.volume_profile_bins,
                max_hvns=6,
            )
        except ValueError:
            return None
        tolerance = self.config.volume_proximity_atr * atr
        for node in (profile.poc, *profile.hvns):
            if node.low - tolerance <= entry <= node.high + tolerance:
                return node.node_type
        return None

    def _simulate_setup(
        self,
        bars: pd.DataFrame,
        *,
        signal_index: int,
        direction: str,
        entry: float,
        stop: float,
        tp1: float,
    ) -> dict[str, object]:
        final_pending_index = min(
            len(bars) - 1, signal_index + self.config.pending_expiry_bars
        )
        invalidation_price = entry + self.config.pending_invalidation_progress * (tp1 - entry)
        fill_index: int | None = None
        for index in range(signal_index + 1, final_pending_index + 1):
            row = bars.iloc[index]
            invalidated = (
                row["high"] >= invalidation_price
                if direction == "LONG"
                else row["low"] <= invalidation_price
            )
            if invalidated:
                return self._simulation_values("INVALIDATED", None, row["open_time"], None)
            if row["low"] <= entry <= row["high"]:
                fill_index = index
                break
        if fill_index is None:
            status = "PENDING" if final_pending_index == len(bars) - 1 else "EXPIRED"
            exit_time = None if status == "PENDING" else bars.iloc[final_pending_index]["open_time"]
            return self._simulation_values(status, None, exit_time, None)

        fill_time = bars.iloc[fill_index]["open_time"]
        tp1_hit = False
        base_result = self.config.tp1_position_fraction * self.config.tp1_r_multiple
        runner_fraction = 1.0 - self.config.tp1_position_fraction
        risk = abs(entry - stop)
        for index in range(fill_index, len(bars)):
            row = bars.iloc[index]
            stop_price = entry if tp1_hit else stop
            stop_hit = (
                row["low"] <= stop_price
                if direction == "LONG"
                else row["high"] >= stop_price
            )
            target_hit = row["high"] >= tp1 if direction == "LONG" else row["low"] <= tp1
            if not tp1_hit:
                if stop_hit:
                    return self._simulation_values("STOP", fill_time, row["open_time"], -1.0)
                if target_hit:
                    tp1_hit = True
                    continue
            elif stop_hit:
                return self._simulation_values("TP1_BE", fill_time, row["open_time"], base_result)

            opposite_cross = row["cross_down"] if direction == "LONG" else row["cross_up"]
            if tp1_hit and opposite_cross and index + 1 < len(bars):
                exit_index = index + 1
                exit_price = float(bars.iloc[exit_index]["open"])
                runner_r = (
                    (exit_price - entry) / risk
                    if direction == "LONG"
                    else (entry - exit_price) / risk
                )
                result_r = base_result + runner_fraction * runner_r
                return self._simulation_values(
                    "RUNNER_EXIT", fill_time, bars.iloc[exit_index]["open_time"], result_r
                )

        status = "TP1_RUNNER_OPEN" if tp1_hit else "OPEN"
        return self._simulation_values(status, fill_time, None, base_result if tp1_hit else None)

    @staticmethod
    def _simulation_values(
        status: str,
        fill_time: object | None,
        exit_time: object | None,
        result_r: float | None,
    ) -> dict[str, object]:
        return {
            "status": status,
            "fill_time": pd.Timestamp(fill_time) if fill_time is not None else None,
            "exit_time": pd.Timestamp(exit_time) if exit_time is not None else None,
            "result_r": float(result_r) if result_r is not None else None,
        }
