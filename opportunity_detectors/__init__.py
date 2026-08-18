"""Shared primitives for independent opportunity detectors."""

from opportunity_detectors.base import (
    OpportunityComponent,
    classify_score,
    weighted_available_score,
)

__all__ = ["OpportunityComponent", "classify_score", "weighted_available_score"]
