"""Historical / replay analysis for the Crypto Pump Radar.

This service reads previously persisted snapshots for a pair and:

* exposes them as a pandas DataFrame for the Streamlit UI
* finds the timestamps at which the radar would have triggered each
  classification (WATCH / EARLY_MOMENTUM / HIGH_RISK_PUMP / EXTREME_SPECULATION)
* computes a light replay: drawdown and max move after the first significant
  signal, plus a heuristic on whether the signal was "early" or "late".

Replay quality depends entirely on snapshot frequency: callers must explain
this to the user in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from data.database import CryptoPumpSnapshotORM
from data.repositories.crypto_pump_repo import CryptoPumpRepository

SIGNAL_LABELS = (
    "WATCH",
    "EARLY_MOMENTUM",
    "HIGH_RISK_PUMP",
    "EXTREME_SPECULATION",
)


@dataclass(slots=True)
class SignalEvent:
    timestamp: datetime
    classification: str
    final_speculative_score: float
    price_usd: float | None
    rug_risk_score: float | None
    prior_pump_penalty: float | None


@dataclass(slots=True)
class ReplayMetrics:
    snapshots_total: int
    snapshots_post_signal: int
    first_signal: SignalEvent | None
    max_price_after_signal: float | None
    max_up_pct: float | None
    drawdown_after_max_pct: float | None
    timing_label: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HistoryReport:
    chain: str
    pair_address: str
    symbol: str | None
    snapshots: pd.DataFrame
    events: list[SignalEvent]
    replay: ReplayMetrics

    def to_summary(self) -> dict[str, Any]:
        first = self.replay.first_signal
        return {
            "chain": self.chain,
            "pair_address": self.pair_address,
            "symbol": self.symbol,
            "snapshots": int(self.replay.snapshots_total),
            "first_signal_at": (first.timestamp.isoformat() if first else None),
            "first_signal_class": (first.classification if first else None),
            "max_up_pct": self.replay.max_up_pct,
            "drawdown_after_max_pct": self.replay.drawdown_after_max_pct,
            "timing_label": self.replay.timing_label,
        }


class CryptoPumpHistoryService:
    def __init__(self, repository: CryptoPumpRepository) -> None:
        self.repository = repository

    # -- Public API ----------------------------------------------------------

    def build_report(
        self,
        *,
        chain: str,
        pair_address: str,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> HistoryReport:
        snapshots = self.repository.list_for_pair(
            chain=chain,
            pair_address=pair_address,
            limit=limit,
            since=since,
        )
        frame = self.snapshots_to_frame(snapshots)
        events = self.detect_events(snapshots)
        replay = self.compute_replay(snapshots, events)
        symbol = snapshots[0].symbol if snapshots else None
        return HistoryReport(
            chain=chain,
            pair_address=pair_address,
            symbol=symbol,
            snapshots=frame,
            events=events,
            replay=replay,
        )

    @staticmethod
    def snapshots_to_frame(
        snapshots: list[CryptoPumpSnapshotORM],
    ) -> pd.DataFrame:
        if not snapshots:
            return pd.DataFrame()
        rows = [
            {
                "detected_at": snap.detected_at,
                "symbol": snap.symbol,
                "chain": snap.chain,
                "price_usd": snap.price_usd,
                "liquidity_usd": snap.liquidity_usd,
                "volume_1h": snap.volume_1h,
                "volume_24h": snap.volume_24h,
                "buys_1h": snap.buys_1h,
                "sells_1h": snap.sells_1h,
                "price_change_1h": snap.price_change_1h,
                "price_change_6h": snap.price_change_6h,
                "price_change_24h": snap.price_change_24h,
                "pump_momentum_score": snap.pump_momentum_score,
                "liquidity_quality_score": snap.liquidity_quality_score,
                "transaction_quality_score": snap.transaction_quality_score,
                "early_trend_score": snap.early_trend_score,
                "prior_pump_penalty": snap.prior_pump_penalty,
                "rug_risk_score": snap.rug_risk_score,
                "final_speculative_score": snap.final_speculative_score,
                "classification": snap.classification,
            }
            for snap in snapshots
        ]
        frame = pd.DataFrame(rows)
        frame["detected_at"] = pd.to_datetime(frame["detected_at"])
        return frame.sort_values("detected_at").reset_index(drop=True)

    @staticmethod
    def detect_events(
        snapshots: list[CryptoPumpSnapshotORM],
    ) -> list[SignalEvent]:
        """Return the first time each classification fired, in chronological order."""

        triggered: dict[str, SignalEvent] = {}
        for snap in snapshots:
            if snap.classification not in SIGNAL_LABELS:
                continue
            if snap.classification in triggered:
                continue
            event = SignalEvent(
                timestamp=snap.detected_at,
                classification=snap.classification,
                final_speculative_score=snap.final_speculative_score or 0.0,
                price_usd=snap.price_usd,
                rug_risk_score=snap.rug_risk_score,
                prior_pump_penalty=snap.prior_pump_penalty,
            )
            triggered[snap.classification] = event
        return sorted(triggered.values(), key=lambda e: e.timestamp)

    @staticmethod
    def compute_replay(
        snapshots: list[CryptoPumpSnapshotORM],
        events: list[SignalEvent],
    ) -> ReplayMetrics:
        total = len(snapshots)
        if total == 0:
            return ReplayMetrics(
                snapshots_total=0,
                snapshots_post_signal=0,
                first_signal=None,
                max_price_after_signal=None,
                max_up_pct=None,
                drawdown_after_max_pct=None,
                timing_label="NO_DATA",
                notes=["No snapshots available."],
            )

        # First "actionable" signal: WATCH or higher. We treat WATCH as marginally
        # actionable since the user asked us to flag WATCH/EARLY_MOMENTUM/etc.
        first_signal = events[0] if events else None
        if first_signal is None:
            return ReplayMetrics(
                snapshots_total=total,
                snapshots_post_signal=0,
                first_signal=None,
                max_price_after_signal=None,
                max_up_pct=None,
                drawdown_after_max_pct=None,
                timing_label="NEVER_SIGNALED",
                notes=["No classification crossed WATCH or higher."],
            )

        # Locate index of first_signal in the snapshot timeline.
        signal_idx = None
        for idx, snap in enumerate(snapshots):
            if (
                snap.detected_at == first_signal.timestamp
                and snap.classification == first_signal.classification
            ):
                signal_idx = idx
                break
        if signal_idx is None:
            signal_idx = 0

        forward = snapshots[signal_idx:]
        prices = [s.price_usd for s in forward if s.price_usd is not None]
        notes: list[str] = []
        if len(prices) < 2:
            notes.append(
                "Insufficient post-signal snapshots to assess movement. "
                "Increase scan frequency to improve replay quality."
            )
            return ReplayMetrics(
                snapshots_total=total,
                snapshots_post_signal=len(forward),
                first_signal=first_signal,
                max_price_after_signal=prices[0] if prices else None,
                max_up_pct=None,
                drawdown_after_max_pct=None,
                timing_label="UNKNOWN",
                notes=notes,
            )

        anchor_price = prices[0]
        max_price = max(prices)
        max_up_pct = (
            (max_price - anchor_price) / anchor_price * 100.0
            if anchor_price > 0
            else None
        )

        # Drawdown from max to subsequent min.
        max_idx = prices.index(max_price)
        post_max = prices[max_idx:]
        if len(post_max) >= 2 and max_price > 0:
            post_min = min(post_max)
            drawdown_after_max_pct = (post_min - max_price) / max_price * 100.0
        else:
            drawdown_after_max_pct = None

        # Timing heuristic. "EARLY" = max_up_pct still big after signal,
        # "LATE" = max already happened around the signal (negative drawdown).
        if max_up_pct is None:
            timing = "UNKNOWN"
        elif max_up_pct >= 30:
            timing = "EARLY"
        elif max_up_pct >= 10:
            timing = "MID"
        else:
            timing = "LATE"
        if (
            drawdown_after_max_pct is not None
            and drawdown_after_max_pct <= -40
            and max_up_pct is not None
            and max_up_pct < 50
        ):
            notes.append("Strong drawdown after signal: likely too late.")

        return ReplayMetrics(
            snapshots_total=total,
            snapshots_post_signal=len(forward),
            first_signal=first_signal,
            max_price_after_signal=max_price,
            max_up_pct=max_up_pct,
            drawdown_after_max_pct=drawdown_after_max_pct,
            timing_label=timing,
            notes=notes,
        )
