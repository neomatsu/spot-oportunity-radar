from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import find_peaks


@dataclass(frozen=True, slots=True)
class VolumeProfileNode:
    node_type: str
    center: float
    low: float
    high: float
    estimated_volume: float
    relative_intensity: float
    distance_to_current_price_pct: float
    start_bin: int
    end_bin: int


@dataclass(frozen=True, slots=True)
class EstimatedVolumeProfile:
    profile: pd.DataFrame
    poc: VolumeProfileNode
    hvns: tuple[VolumeProfileNode, ...]
    current_price: float
    price_min: float
    price_max: float
    bars_used: int


class EstimatedVolumeProfileService:
    """Build an approximate volume-at-price profile from daily OHLCV bars."""

    REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}

    def __init__(
        self,
        *,
        min_hvn_intensity: float = 0.40,
        min_hvn_prominence: float = 0.07,
        min_hvn_separation_ratio: float = 0.035,
        zone_peak_ratio: float = 0.65,
    ) -> None:
        if not 0.0 <= min_hvn_intensity <= 1.0:
            raise ValueError("min_hvn_intensity must be between 0 and 1")
        if not 0.0 <= min_hvn_prominence <= 1.0:
            raise ValueError("min_hvn_prominence must be between 0 and 1")
        if min_hvn_separation_ratio <= 0:
            raise ValueError("min_hvn_separation_ratio must be positive")
        if not 0.0 < zone_peak_ratio <= 1.0:
            raise ValueError("zone_peak_ratio must be between 0 and 1")
        self.min_hvn_intensity = min_hvn_intensity
        self.min_hvn_prominence = min_hvn_prominence
        self.min_hvn_separation_ratio = min_hvn_separation_ratio
        self.zone_peak_ratio = zone_peak_ratio

    def calculate(
        self,
        frame: pd.DataFrame,
        *,
        bins: int = 120,
        max_hvns: int = 6,
    ) -> EstimatedVolumeProfile:
        if bins < 10:
            raise ValueError("At least 10 price bins are required")
        if max_hvns < 1:
            raise ValueError("max_hvns must be positive")

        data = self._prepare_frame(frame)
        price_min = float(data["low"].min())
        price_max = float(data["high"].max())
        if not np.isfinite(price_min) or not np.isfinite(price_max) or price_max <= price_min:
            raise ValueError("The selected bars do not contain a valid price range")

        edges = np.linspace(price_min, price_max, bins + 1, dtype=float)
        estimated_volume = self._distribute_volume(data, edges)
        poc_volume = float(estimated_volume.max())
        if poc_volume <= 0:
            raise ValueError("The selected bars do not contain positive volume")

        centers = (edges[:-1] + edges[1:]) / 2.0
        intensity = estimated_volume / poc_volume
        profile = pd.DataFrame(
            {
                "bin_index": np.arange(bins, dtype=int),
                "price_low": edges[:-1],
                "price_high": edges[1:],
                "price_center": centers,
                "estimated_volume": estimated_volume,
                "normalized_intensity": intensity,
            }
        )

        current_price = float(data.iloc[-1]["close"])
        poc_index = int(np.argmax(estimated_volume))
        poc = self._node_from_range(
            node_type="POC",
            start_bin=poc_index,
            end_bin=poc_index,
            peak_bin=poc_index,
            profile=profile,
            current_price=current_price,
        )
        hvns = self._detect_hvns(
            profile,
            poc_index=poc_index,
            current_price=current_price,
            max_hvns=max_hvns,
        )
        return EstimatedVolumeProfile(
            profile=profile,
            poc=poc,
            hvns=tuple(hvns),
            current_price=current_price,
            price_min=price_min,
            price_max=price_max,
            bars_used=len(data),
        )

    def table_rows(self, result: EstimatedVolumeProfile) -> list[dict[str, float | str]]:
        nodes = (result.poc, *result.hvns)
        return [
            {
                "Type": node.node_type,
                "Price Center": node.center,
                "Zone Low": node.low,
                "Zone High": node.high,
                "Estimated Volume": node.estimated_volume,
                "Relative Intensity": node.relative_intensity,
                "Distance from Current Price %": node.distance_to_current_price_pct,
            }
            for node in nodes
        ]

    def _prepare_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = self.REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing OHLCV columns: {', '.join(sorted(missing))}")

        data = frame.loc[:, sorted(self.REQUIRED_COLUMNS)].copy()
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        numeric_columns = ["open", "high", "low", "close", "volume"]
        for column in numeric_columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        data = data.dropna(subset=["date", *numeric_columns])
        data = data[
            (data["high"] >= data["low"])
            & (data["high"] > 0)
            & (data["low"] > 0)
            & (data["close"] > 0)
            & (data["volume"] >= 0)
        ]
        data = data.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        if data.empty:
            raise ValueError("No valid OHLCV bars are available for the selected period")
        return data.reset_index(drop=True)

    @staticmethod
    def _distribute_volume(data: pd.DataFrame, edges: np.ndarray) -> np.ndarray:
        bin_count = len(edges) - 1
        totals = np.zeros(bin_count, dtype=float)
        lows = data["low"].to_numpy(dtype=float)
        highs = data["high"].to_numpy(dtype=float)
        volumes = data["volume"].to_numpy(dtype=float)

        for low, high, volume in zip(lows, highs, volumes, strict=True):
            start_bin = int(np.searchsorted(edges, low, side="right") - 1)
            end_bin = int(np.searchsorted(edges, high, side="right") - 1)
            start_bin = int(np.clip(start_bin, 0, bin_count - 1))
            end_bin = int(np.clip(end_bin, start_bin, bin_count - 1))
            touched_bins = end_bin - start_bin + 1
            totals[start_bin : end_bin + 1] += volume / touched_bins
        return totals

    def _detect_hvns(
        self,
        profile: pd.DataFrame,
        *,
        poc_index: int,
        current_price: float,
        max_hvns: int,
    ) -> list[VolumeProfileNode]:
        intensity = profile["normalized_intensity"].to_numpy(dtype=float)
        minimum_separation = max(2, round(len(profile) * self.min_hvn_separation_ratio))
        peaks, _ = find_peaks(
            intensity,
            height=self.min_hvn_intensity,
            prominence=self.min_hvn_prominence,
            distance=minimum_separation,
        )
        ordered_peaks = sorted(
            (int(index) for index in peaks),
            key=lambda index: float(intensity[index]),
            reverse=True,
        )

        nodes: list[VolumeProfileNode] = []
        occupied_ranges: list[tuple[int, int]] = []
        for peak_index in ordered_peaks:
            start_bin, end_bin = self._expand_peak(intensity, peak_index)
            if start_bin <= poc_index <= end_bin:
                continue
            if any(
                start_bin <= used_end and end_bin >= used_start
                for used_start, used_end in occupied_ranges
            ):
                continue
            nodes.append(
                self._node_from_range(
                    node_type="HVN",
                    start_bin=start_bin,
                    end_bin=end_bin,
                    peak_bin=peak_index,
                    profile=profile,
                    current_price=current_price,
                )
            )
            occupied_ranges.append((start_bin, end_bin))
            if len(nodes) >= max_hvns:
                break
        return nodes

    def _expand_peak(self, intensity: np.ndarray, peak_index: int) -> tuple[int, int]:
        floor = max(
            self.min_hvn_intensity * self.zone_peak_ratio,
            float(intensity[peak_index]) * self.zone_peak_ratio,
        )
        start_bin = peak_index
        while start_bin > 0 and intensity[start_bin - 1] >= floor:
            start_bin -= 1
        end_bin = peak_index
        while end_bin < len(intensity) - 1 and intensity[end_bin + 1] >= floor:
            end_bin += 1
        return start_bin, end_bin

    @staticmethod
    def _node_from_range(
        *,
        node_type: str,
        start_bin: int,
        end_bin: int,
        peak_bin: int,
        profile: pd.DataFrame,
        current_price: float,
    ) -> VolumeProfileNode:
        selected = profile.iloc[start_bin : end_bin + 1]
        weights = selected["estimated_volume"].to_numpy(dtype=float)
        centers = selected["price_center"].to_numpy(dtype=float)
        center = (
            float(np.average(centers, weights=weights))
            if weights.sum()
            else float(centers.mean())
        )
        distance = ((center - current_price) / current_price) * 100.0
        return VolumeProfileNode(
            node_type=node_type,
            center=center,
            low=float(selected.iloc[0]["price_low"]),
            high=float(selected.iloc[-1]["price_high"]),
            estimated_volume=float(weights.sum()),
            relative_intensity=float(profile.iloc[peak_bin]["normalized_intensity"]),
            distance_to_current_price_pct=float(distance),
            start_bin=start_bin,
            end_bin=end_bin,
        )
