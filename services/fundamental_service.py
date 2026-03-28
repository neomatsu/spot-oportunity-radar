from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core.models import FundamentalSnapshotModel
from data.database import AssetORM
from data.providers.fmp_provider import FinancialModelingPrepProvider
from data.repositories.fundamentals_repo import FundamentalsRepository


class FundamentalService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = FundamentalsRepository(session)
        self.provider = FinancialModelingPrepProvider()

    def refresh_for_asset(self, asset: AssetORM) -> FundamentalSnapshotModel | None:
        if not asset.supports_fundamentals or not self.provider.supports(asset):
            return None
        payload = self.provider.fetch_fundamentals(asset)
        if not payload:
            return None

        fundamental_score = self._compute_fundamental_score(payload)
        valuation_score = self._compute_valuation_score(payload)

        snapshot_payload = {
            "asset_id": asset.id,
            "date": date.today(),
            **payload,
            "fundamental_score": fundamental_score,
            "valuation_score": valuation_score,
        }
        self.repo.upsert_snapshot(snapshot_payload)
        return FundamentalSnapshotModel.model_validate(snapshot_payload)

    @staticmethod
    def _compute_fundamental_score(payload: dict) -> float:
        score = 50.0
        if (payload.get("revenue_growth") or 0) > 0.1:
            score += 15
        if (payload.get("eps_growth") or 0) > 0.1:
            score += 15
        if (payload.get("fcf_margin") or 0) > 0.1:
            score += 10
        if payload.get("debt_to_ebitda") is not None and payload["debt_to_ebitda"] < 3:
            score += 10
        return max(0.0, min(100.0, score))

    @staticmethod
    def _compute_valuation_score(payload: dict) -> float:
        score = 50.0
        pe = payload.get("pe")
        ps = payload.get("ps")
        ev_ebitda = payload.get("ev_ebitda")
        if pe and pe < 25:
            score += 15
        if ps and ps < 8:
            score += 15
        if ev_ebitda and ev_ebitda < 18:
            score += 20
        return max(0.0, min(100.0, score))
