"""Repository for Crypto Pump Radar persistence.

Independent from the main investment pipeline. Stores snapshots of DEX pairs
and the metadata of each scan run so we can reconstruct evolution over time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import CryptoPumpScanRunORM, CryptoPumpSnapshotORM


class CryptoPumpRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- Scan runs -----------------------------------------------------------

    def start_scan_run(
        self,
        *,
        query_mode: str,
        chains: list[str],
    ) -> CryptoPumpScanRunORM:
        run = CryptoPumpScanRunORM(
            query_mode=query_mode,
            chains_scanned=list(chains),
            status="running",
        )
        self.session.add(run)
        self.session.flush()
        return run

    def finish_scan_run(
        self,
        run: CryptoPumpScanRunORM,
        *,
        status: str,
        candidates_found: int,
        top_candidates: list[dict[str, Any]] | None,
        error_message: str | None = None,
    ) -> None:
        run.finished_at = datetime.now(UTC).replace(tzinfo=None)
        run.status = status
        run.candidates_found = candidates_found
        run.top_candidates_json = top_candidates
        run.error_message = error_message
        self.session.flush()

    def list_recent_runs(self, limit: int = 50) -> list[CryptoPumpScanRunORM]:
        statement = (
            select(CryptoPumpScanRunORM)
            .order_by(CryptoPumpScanRunORM.started_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    # -- Snapshots -----------------------------------------------------------

    def add_snapshot(self, payload: dict[str, Any]) -> CryptoPumpSnapshotORM:
        entity = CryptoPumpSnapshotORM(**payload)
        self.session.add(entity)
        self.session.flush()
        return entity

    def add_snapshots_bulk(
        self, snapshots: list[dict[str, Any]]
    ) -> list[CryptoPumpSnapshotORM]:
        entities = [CryptoPumpSnapshotORM(**payload) for payload in snapshots]
        if not entities:
            return []
        self.session.add_all(entities)
        self.session.flush()
        return entities

    def list_for_pair(
        self,
        *,
        chain: str,
        pair_address: str,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> list[CryptoPumpSnapshotORM]:
        statement = (
            select(CryptoPumpSnapshotORM)
            .where(
                CryptoPumpSnapshotORM.chain == chain,
                CryptoPumpSnapshotORM.pair_address == pair_address,
            )
            .order_by(CryptoPumpSnapshotORM.detected_at.asc())
        )
        if since is not None:
            if since.tzinfo is not None:
                since = since.astimezone(UTC).replace(tzinfo=None)
            statement = statement.where(CryptoPumpSnapshotORM.detected_at >= since)
        rows = list(self.session.scalars(statement))
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def list_for_base_token(
        self,
        base_token_address: str,
        *,
        chain: str | None = None,
        limit: int | None = None,
    ) -> list[CryptoPumpSnapshotORM]:
        statement = select(CryptoPumpSnapshotORM).where(
            CryptoPumpSnapshotORM.base_token_address == base_token_address
        )
        if chain is not None:
            statement = statement.where(CryptoPumpSnapshotORM.chain == chain)
        statement = statement.order_by(CryptoPumpSnapshotORM.detected_at.asc())
        rows = list(self.session.scalars(statement))
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def list_for_symbol(
        self,
        symbol: str,
        *,
        limit: int | None = None,
    ) -> list[CryptoPumpSnapshotORM]:
        statement = (
            select(CryptoPumpSnapshotORM)
            .where(CryptoPumpSnapshotORM.symbol == symbol)
            .order_by(CryptoPumpSnapshotORM.detected_at.asc())
        )
        rows = list(self.session.scalars(statement))
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def has_recent_high_score(
        self,
        *,
        chain: str,
        pair_address: str,
        within_hours: float,
        score_threshold: float,
    ) -> bool:
        """Has this pair shown a high final score recently? Used by prior_pump_penalty."""

        if within_hours <= 0:
            return False
        boundary = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=within_hours)
        statement = (
            select(CryptoPumpSnapshotORM)
            .where(
                CryptoPumpSnapshotORM.chain == chain,
                CryptoPumpSnapshotORM.pair_address == pair_address,
                CryptoPumpSnapshotORM.detected_at >= boundary,
                CryptoPumpSnapshotORM.final_speculative_score >= score_threshold,
            )
            .limit(1)
        )
        return self.session.scalar(statement) is not None

    def latest_for_pair(
        self,
        *,
        chain: str,
        pair_address: str,
    ) -> CryptoPumpSnapshotORM | None:
        statement = (
            select(CryptoPumpSnapshotORM)
            .where(
                CryptoPumpSnapshotORM.chain == chain,
                CryptoPumpSnapshotORM.pair_address == pair_address,
            )
            .order_by(CryptoPumpSnapshotORM.detected_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def list_top_classifications(
        self,
        classifications: list[str],
        *,
        limit: int = 100,
    ) -> list[CryptoPumpSnapshotORM]:
        statement = (
            select(CryptoPumpSnapshotORM)
            .where(CryptoPumpSnapshotORM.classification.in_(classifications))
            .order_by(CryptoPumpSnapshotORM.detected_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))
