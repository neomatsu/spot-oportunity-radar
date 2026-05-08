from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from sklearn.cluster import DBSCAN

from core.config import load_yaml_config
from core.models import SupportZoneModel


@dataclass
class _ClusterSeed:
    price: float
    index: int
    touch_type: str


class SupportDetectionService:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        loaded = config if config is not None else load_yaml_config("support_detection.yaml")
        self.config = loaded.get("support_detection", loaded)

    def detect_support_zone(
        self,
        frame: pd.DataFrame,
        lookback: int | None = None,
    ) -> SupportZoneModel:
        if frame.empty or len(frame) < 20:
            return SupportZoneModel(method=str(self.config.get("method", "simple")))

        structural_frame = self._structural_window(frame, lookback)
        method = str(self.config.get("method", "combined")).lower()
        detectors = {
            "simple": self._detect_simple,
            "clustering": self._detect_clustering,
            "price_time": self._detect_price_time,
            "combined": self._detect_combined,
        }
        detector = detectors.get(method, self._detect_simple)
        result = detector(structural_frame)
        result.method = method if method in detectors else "simple"

        if result.nearest_support_zone:
            result.support_zone_low = float(result.nearest_support_zone["low"])
            result.support_zone_high = float(result.nearest_support_zone["high"])
            current_close = float(structural_frame.iloc[-1]["close"])
            result.distance_to_support_pct = (
                ((current_close / float(result.nearest_support_zone["high"])) - 1) * 100
            )
        elif result.support_zone_high is not None:
            current_close = float(structural_frame.iloc[-1]["close"])
            result.distance_to_support_pct = ((current_close / result.support_zone_high) - 1) * 100
        return result

    def build_zone_table_rows(
        self,
        support: SupportZoneModel,
        *,
        current_price: float | None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        ordered = [
            support.nearest_support_zone,
            support.major_support_zone,
            support.structural_support_zone,
            support.nearest_resistance_zone,
            support.major_resistance_zone,
            support.structural_resistance_zone,
        ]
        seen: set[tuple[str, float, float]] = set()
        for zone in ordered:
            if not zone:
                continue
            zone_key = (str(zone.get("role")), float(zone["low"]), float(zone["high"]))
            if zone_key in seen:
                continue
            seen.add(zone_key)
            distance = None
            if current_price is not None:
                distance = ((float(zone["center"]) / current_price) - 1) * 100
            rows.append(
                {
                    "type": str(zone.get("role", "zone")).replace("_", " ").title(),
                    "zone": f"{float(zone['low']):,.2f} - {float(zone['high']):,.2f}",
                    "distance_pct": round(distance, 2) if distance is not None else None,
                    "score": round(float(zone.get("combined_score", 0.0)), 2),
                    "touches": int(zone.get("touch_count", 0)),
                    "method": zone.get("method"),
                }
            )
        return rows

    def build_zone_bands(self, support: SupportZoneModel) -> list[dict[str, Any]]:
        bands: list[dict[str, Any]] = []
        palette = {
            "nearest_support": "rgba(39, 174, 96, 0.18)",
            "major_support": "rgba(39, 174, 96, 0.28)",
            "structural_support": "rgba(127, 140, 141, 0.18)",
            "nearest_resistance": "rgba(231, 76, 60, 0.16)",
            "major_resistance": "rgba(192, 57, 43, 0.22)",
            "structural_resistance": "rgba(149, 165, 166, 0.16)",
        }
        for zone in support.support_zones + support.resistance_zones:
            role = str(zone.get("role", "zone"))
            if role not in palette:
                continue
            bands.append(
                {
                    "y0": float(zone["low"]),
                    "y1": float(zone["high"]),
                    "fillcolor": palette[role],
                    "line_width": 0,
                    "annotation_text": role.replace("_", " ").title(),
                    "annotation_position": "top left",
                }
            )
        return bands

    def _detect_simple(self, frame: pd.DataFrame) -> SupportZoneModel:
        lookback = int(self.config.get("simple_lookback", 90))
        window = frame.tail(lookback).copy()
        window["pivot_low"] = (
            (window["low"] < window["low"].shift(1))
            & (window["low"] < window["low"].shift(-1))
            & (window["low"] <= window["low"].rolling(5, center=True).min())
        )
        pivot_lows = window.loc[window["pivot_low"], "low"]

        if pivot_lows.empty:
            pivot_lows = window.nsmallest(5, "low")["low"]

        zone_low = float(pivot_lows.quantile(0.25))
        zone_high = float(pivot_lows.quantile(0.75))
        current_close = float(frame.iloc[-1]["close"])
        center = (zone_low + zone_high) / 2
        nearest_support = {
            "center": center,
            "low": zone_low,
            "high": zone_high,
            "touch_count": int(len(pivot_lows)),
            "pivot_score": float(len(pivot_lows)),
            "time_score": 0.0,
            "combined_score": float(len(pivot_lows)),
            "role": "nearest_support",
            "kind": "support",
            "method": "simple",
        }
        return SupportZoneModel(
            support_zone_low=zone_low,
            support_zone_high=zone_high,
            distance_to_support_pct=((current_close / zone_high) - 1) * 100 if zone_high else None,
            support_zones=[nearest_support],
            resistance_zones=[],
            nearest_support_zone=nearest_support,
            method="simple",
        )

    def _detect_clustering(self, frame: pd.DataFrame) -> SupportZoneModel:
        support_seeds, resistance_seeds = self._pivot_seeds(frame)
        support_zones = self._cluster_seeds(frame, support_seeds, kind="support")
        resistance_zones = self._cluster_seeds(frame, resistance_seeds, kind="resistance")
        return self._finalize_zone_roles(
            frame,
            support_zones,
            resistance_zones,
            method="clustering",
        )

    def _detect_price_time(self, frame: pd.DataFrame) -> SupportZoneModel:
        support_zones, resistance_zones = self._price_time_zones(frame)
        return self._finalize_zone_roles(
            frame,
            support_zones,
            resistance_zones,
            method="price_time",
        )

    def _detect_combined(self, frame: pd.DataFrame) -> SupportZoneModel:
        support_seeds, resistance_seeds = self._pivot_seeds(frame)
        clustered_support = self._cluster_seeds(frame, support_seeds, kind="support")
        clustered_resistance = self._cluster_seeds(frame, resistance_seeds, kind="resistance")
        hvn_support, hvn_resistance = self._price_time_zones(frame)

        support_zones = self._merge_zones(clustered_support, hvn_support, kind="support")
        resistance_zones = self._merge_zones(
            clustered_resistance,
            hvn_resistance,
            kind="resistance",
        )
        return self._finalize_zone_roles(frame, support_zones, resistance_zones, method="combined")

    def _pivot_seeds(self, frame: pd.DataFrame) -> tuple[list[_ClusterSeed], list[_ClusterSeed]]:
        order = int(self.config.get("pivot_order", 5))
        use_pivot_highs = bool(self.config.get("use_pivot_highs", True))
        lows = frame["low"].to_numpy(dtype=float)
        highs = frame["high"].to_numpy(dtype=float)
        low_idx = argrelextrema(lows, np.less_equal, order=order)[0].tolist()
        high_idx = argrelextrema(highs, np.greater_equal, order=order)[0].tolist()

        support_seeds = [
            _ClusterSeed(price=float(lows[idx]), index=int(idx), touch_type="low")
            for idx in low_idx
        ]
        resistance_seeds: list[_ClusterSeed] = []
        if use_pivot_highs:
            resistance_seeds = [
                _ClusterSeed(price=float(highs[idx]), index=int(idx), touch_type="high")
                for idx in high_idx
            ]
        return support_seeds, resistance_seeds

    def _cluster_seeds(
        self,
        frame: pd.DataFrame,
        seeds: list[_ClusterSeed],
        *,
        kind: str,
    ) -> list[dict[str, Any]]:
        if not seeds:
            return []

        current_price = float(frame.iloc[-1]["close"])
        eps = max(current_price * float(self.config.get("cluster_eps_pct", 0.006)), 0.01)
        min_samples = int(self.config.get("min_cluster_samples", 2))
        prices = np.array([[seed.price] for seed in seeds], dtype=float)
        labels = DBSCAN(eps=eps, min_samples=min_samples).fit(prices).labels_

        zones: list[dict[str, Any]] = []
        use_recency_weighting = bool(self.config.get("use_recency_weighting", True))
        for label in sorted(set(labels)):
            if label == -1:
                continue
            cluster = [
                seed
                for seed, seed_label in zip(seeds, labels, strict=False)
                if seed_label == label
            ]
            if len(cluster) < min_samples:
                continue
            cluster_prices = np.array([seed.price for seed in cluster], dtype=float)
            cluster_indices = np.array([seed.index for seed in cluster], dtype=float)
            touch_count = len(cluster)
            center = float(np.median(cluster_prices))
            zone_low = float(np.percentile(cluster_prices, 15))
            zone_high = float(np.percentile(cluster_prices, 85))
            if zone_high <= zone_low:
                padding = center * 0.003
                zone_low = center - padding
                zone_high = center + padding
            recency_component = 0.0
            if use_recency_weighting and len(frame) > 1:
                recency_component = float(np.mean(cluster_indices / (len(frame) - 1))) * 2.0
            pivot_score = round((touch_count * 1.5) + recency_component, 2)
            zones.append(
                {
                    "center": center,
                    "low": zone_low,
                    "high": zone_high,
                    "touch_count": touch_count,
                    "pivot_score": pivot_score,
                    "time_score": 0.0,
                    "combined_score": pivot_score,
                    "role": kind,
                    "kind": kind,
                    "method": "clustering",
                }
            )
        return zones

    def _price_time_zones(
        self,
        frame: pd.DataFrame,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if frame.empty:
            return [], []

        bins = int(self.config.get("price_time_bins", 120))
        hvn_threshold_pct = float(self.config.get("hvn_threshold_pct", 0.75))
        current_price = float(frame.iloc[-1]["close"])
        price_min = float(frame["low"].min())
        price_max = float(frame["high"].max())
        if price_max <= price_min:
            return [], []

        edges = np.linspace(price_min, price_max, bins + 1)
        histogram = np.zeros(bins, dtype=float)
        typical = ((frame["high"] + frame["low"] + frame["close"]) / 3).to_numpy(dtype=float)
        closes = frame["close"].to_numpy(dtype=float)
        for price in typical:
            index = int(np.clip(np.digitize(price, edges) - 1, 0, bins - 1))
            histogram[index] += 0.65
        for price in closes:
            index = int(np.clip(np.digitize(price, edges) - 1, 0, bins - 1))
            histogram[index] += 0.35

        threshold = float(np.quantile(histogram, hvn_threshold_pct))
        hvn_indices = np.where(histogram >= threshold)[0]
        if len(hvn_indices) == 0:
            return [], []

        groups: list[list[int]] = [[int(hvn_indices[0])]]
        for index in hvn_indices[1:]:
            if int(index) == groups[-1][-1] + 1:
                groups[-1].append(int(index))
            else:
                groups.append([int(index)])

        supports: list[dict[str, Any]] = []
        resistances: list[dict[str, Any]] = []
        for group in groups:
            low = float(edges[group[0]])
            high = float(edges[group[-1] + 1])
            center = (low + high) / 2
            time_score = round(float(np.mean(histogram[group])) * 2.5, 2)
            zone = {
                "center": center,
                "low": low,
                "high": high,
                "touch_count": int(len(group)),
                "pivot_score": 0.0,
                "time_score": time_score,
                "combined_score": time_score,
                "role": "support" if center <= current_price else "resistance",
                "kind": "support" if center <= current_price else "resistance",
                "method": "price_time",
            }
            if center <= current_price:
                supports.append(zone)
            else:
                resistances.append(zone)
        return supports, resistances

    def _merge_zones(
        self,
        pivot_zones: list[dict[str, Any]],
        time_zones: list[dict[str, Any]],
        *,
        kind: str,
    ) -> list[dict[str, Any]]:
        proximity_pct = float(self.config.get("combine_proximity_pct", 0.008))
        merged: list[dict[str, Any]] = []
        for pivot_zone in pivot_zones:
            zone = dict(pivot_zone)
            nearby_time = None
            for time_zone in time_zones:
                proximity = abs(float(time_zone["center"]) - float(zone["center"])) / float(
                    zone["center"]
                )
                if proximity <= proximity_pct:
                    if nearby_time is None or float(time_zone["time_score"]) > float(
                        nearby_time["time_score"]
                    ):
                        nearby_time = time_zone
            if nearby_time is not None:
                zone["low"] = min(float(zone["low"]), float(nearby_time["low"]))
                zone["high"] = max(float(zone["high"]), float(nearby_time["high"]))
                zone["time_score"] = max(
                    float(zone["time_score"]),
                    float(nearby_time["time_score"]),
                )
                zone["combined_score"] = round(
                    (float(zone["pivot_score"]) * 0.65)
                    + (float(zone["time_score"]) * 0.35)
                    + 1.0,
                    2,
                )
            else:
                zone["combined_score"] = round(
                    (float(zone["pivot_score"]) * 0.75) + (float(zone["time_score"]) * 0.25),
                    2,
                )
            zone["kind"] = kind
            zone["method"] = "combined"
            merged.append(zone)

        existing_centers = [float(zone["center"]) for zone in merged]
        for time_zone in time_zones:
            if any(
                abs(float(time_zone["center"]) - center) / center <= proximity_pct
                for center in existing_centers
            ):
                continue
            zone = dict(time_zone)
            zone["combined_score"] = round(float(zone["time_score"]) * 0.7, 2)
            zone["kind"] = kind
            zone["method"] = "combined"
            merged.append(zone)

        return merged

    def _finalize_zone_roles(
        self,
        frame: pd.DataFrame,
        support_zones: list[dict[str, Any]],
        resistance_zones: list[dict[str, Any]],
        *,
        method: str,
    ) -> SupportZoneModel:
        current_price = float(frame.iloc[-1]["close"])
        max_returned = int(self.config.get("max_returned_zones", 6))

        supports_below = [
            zone for zone in support_zones if float(zone["center"]) <= current_price
        ]
        resistances_above = [
            zone for zone in resistance_zones if float(zone["center"]) >= current_price
        ]

        supports_sorted = sorted(
            supports_below,
            key=lambda zone: (
                abs(current_price - float(zone["center"])),
                -float(zone["combined_score"]),
            ),
        )
        supports_by_strength = sorted(
            supports_below,
            key=lambda zone: (-float(zone["combined_score"]), -float(zone["touch_count"])),
        )
        resistances_sorted = sorted(
            resistances_above,
            key=lambda zone: (
                abs(float(zone["center"]) - current_price),
                -float(zone["combined_score"]),
            ),
        )
        resistances_by_strength = sorted(
            resistances_above,
            key=lambda zone: (-float(zone["combined_score"]), -float(zone["touch_count"])),
        )

        nearest_support = (
            self._tag_role(supports_sorted[0], "nearest_support")
            if supports_sorted
            else None
        )
        major_support = self._pick_secondary_zone(supports_sorted, "major_support")
        structural_support = self._pick_structural_zone(supports_by_strength, "structural_support")

        nearest_resistance = (
            self._tag_role(resistances_sorted[0], "nearest_resistance")
            if resistances_sorted
            else None
        )
        major_resistance = self._pick_secondary_zone(resistances_sorted, "major_resistance")
        structural_resistance = self._pick_structural_zone(
            resistances_by_strength,
            "structural_resistance",
        )

        labeled_supports = self._dedupe_and_order(
            [nearest_support, major_support, structural_support],
            supports_by_strength,
            max_returned=max_returned,
        )
        labeled_resistances = self._dedupe_and_order(
            [nearest_resistance, major_resistance, structural_resistance],
            resistances_by_strength,
            max_returned=max_returned,
        )

        return SupportZoneModel(
            support_zones=labeled_supports,
            resistance_zones=labeled_resistances,
            nearest_support_zone=nearest_support,
            major_support_zone=major_support,
            structural_support_zone=structural_support,
            nearest_resistance_zone=nearest_resistance,
            major_resistance_zone=major_resistance,
            structural_resistance_zone=structural_resistance,
            method=method,
        )

    def _structural_window(self, frame: pd.DataFrame, lookback: int | None) -> pd.DataFrame:
        years = int(self.config.get("historical_window_years", 5))
        max_rows = years * 252
        if lookback is not None:
            return frame.tail(lookback).copy()
        return frame.tail(max_rows).copy()

    @staticmethod
    def _tag_role(zone: dict[str, Any], role: str) -> dict[str, Any]:
        tagged = dict(zone)
        tagged["role"] = role
        return tagged

    def _pick_secondary_zone(
        self,
        zones_sorted: list[dict[str, Any]],
        role: str,
    ) -> dict[str, Any] | None:
        if len(zones_sorted) < 2:
            return None
        return self._tag_role(zones_sorted[1], role)

    def _pick_structural_zone(
        self,
        zones_by_strength: list[dict[str, Any]],
        role: str,
    ) -> dict[str, Any] | None:
        if not zones_by_strength:
            return None
        candidate = sorted(zones_by_strength, key=lambda zone: float(zone["center"]))[0]
        if "resistance" in role:
            candidate = sorted(
                zones_by_strength,
                key=lambda zone: float(zone["center"]),
                reverse=True,
            )[0]
        return self._tag_role(candidate, role)

    def _dedupe_and_order(
        self,
        priority_zones: list[dict[str, Any] | None],
        zones_by_strength: list[dict[str, Any]],
        *,
        max_returned: int,
    ) -> list[dict[str, Any]]:
        ordered: list[dict[str, Any]] = []
        seen: set[tuple[float, float]] = set()
        for zone in priority_zones + zones_by_strength:
            if zone is None:
                continue
            key = (round(float(zone["low"]), 6), round(float(zone["high"]), 6))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(zone)
            if len(ordered) >= max_returned:
                break
        return ordered
