from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import BitcoinOpportunityHistoryORM


class BitcoinOpportunityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def history(
        self,
        *,
        start_date: date,
        end_date: date,
        source_version: str,
    ) -> pd.DataFrame:
        statement = (
            select(BitcoinOpportunityHistoryORM)
            .where(
                BitcoinOpportunityHistoryORM.date >= start_date,
                BitcoinOpportunityHistoryORM.date <= end_date,
                BitcoinOpportunityHistoryORM.source_version == source_version,
            )
            .order_by(BitcoinOpportunityHistoryORM.date)
        )
        rows = list(self.session.scalars(statement))
        return pd.DataFrame(
            [
                {
                    "date": row.date,
                    "overall_score": row.overall_score,
                    "classification": row.classification,
                    "available_components": row.available_components,
                    "bitcoin_price": row.bitcoin_price,
                    "components_json": row.components_json or {},
                    "computed_at": row.computed_at,
                    "source_version": row.source_version,
                }
                for row in rows
            ]
        )

    def upsert_many(self, rows: list[dict[str, Any]], *, source_version: str) -> int:
        if not rows:
            return 0
        dates = [pd.Timestamp(row["date"]).date() for row in rows]
        existing = {
            row.date: row
            for row in self.session.scalars(
                select(BitcoinOpportunityHistoryORM).where(
                    BitcoinOpportunityHistoryORM.date.in_(dates),
                    BitcoinOpportunityHistoryORM.source_version == source_version,
                )
            )
        }
        inserted = 0
        computed_at = datetime.now(UTC).replace(tzinfo=None)
        for payload in rows:
            history_date = pd.Timestamp(payload["date"]).date()
            values = {
                "date": history_date,
                "overall_score": payload.get("overall_score"),
                "classification": str(payload["classification"]),
                "available_components": int(payload["available_components"]),
                "bitcoin_price": payload.get("bitcoin_price"),
                "components_json": payload.get("components_json") or {},
                "computed_at": computed_at,
                "source_version": source_version,
            }
            entity = existing.get(history_date)
            if entity is None:
                self.session.add(BitcoinOpportunityHistoryORM(**values))
                inserted += 1
            else:
                for key, value in values.items():
                    setattr(entity, key, value)
        self.session.flush()
        return inserted
