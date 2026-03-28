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
