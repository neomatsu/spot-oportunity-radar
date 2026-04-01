from __future__ import annotations

from enum import StrEnum


class AssetType(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"


class RecommendationStatus(StrEnum):
    BUY_CANDIDATE = "BUY_CANDIDATE"
    WATCH = "WATCH"
    AVOID = "AVOID"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DataMode(StrEnum):
    REAL = "real"
    DEMO = "demo"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    NEW = "new"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class AlertType(StrEnum):
    ENTRY_SIGNAL = "entry_signal"
    WATCH_SIGNAL = "watch_signal"
    RISK_DETERIORATION = "risk_deterioration"
    DATA_QUALITY = "data_quality"
    PORTFOLIO_CONSTRAINT = "portfolio_constraint"
    OVERBOUGHT_WARNING = "overbought_warning"
    TAKE_PROFIT = "take_profit"
    TRIM_POSITION = "trim_position"
    REDUCE_RISK = "reduce_risk"
    EXIT_CANDIDATE = "exit_candidate"
    STOP_LOSS_WARNING = "stop_loss_warning"
    REBALANCE_SELL = "rebalance_sell"


class TradeIntentStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED_MANUALLY = "executed_manually"
