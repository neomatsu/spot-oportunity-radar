"""Orchestrator for the Crypto Pump Radar.

Pulls candidate pairs from DexScreener, applies filters, deduplicates, scores
each candidate via :class:`CryptoPumpScoringService` and (optionally) persists
the snapshots through :class:`CryptoPumpRepository`.

Designed as a stand-alone module: it does not touch the main daily job,
recommendation pipeline, alerts or portfolio.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.config import load_yaml_config
from core.logger import get_logger
from data.providers.dexscreener_provider import (
    DexPair,
    DexScreenerError,  # noqa: F401  (re-exported convenience)
    DexScreenerProvider,
)
from data.repositories.crypto_pump_repo import CryptoPumpRepository
from services.crypto_pump_scoring_service import (
    CryptoPumpScoringService,
    PumpScoreResult,
)

logger = get_logger(__name__)


# Search seeds per chain. They are intentionally generic so that DexScreener's
# fuzzy-match returns a wide pool of trending/recent pairs. The scoring then
# filters out anything that does not look like a pump candidate.
DEFAULT_CHAIN_SEEDS: dict[str, list[str]] = {
    "solana": ["sol", "trending", "new", "pump", "meme", "moon"],
    "ethereum": ["eth", "trending", "new", "pepe", "meme", "moon"],
    "base": ["base", "trending", "new", "meme", "moon", "pepe"],
    "bsc": ["bnb", "trending", "new", "meme", "moon", "pepe"],
    "arbitrum": ["arb", "trending", "new", "meme", "moon", "pepe"],
    "polygon": ["matic", "trending", "new", "meme", "moon", "pepe"],
}


@dataclass(slots=True)
class CandidateRow:
    pair: DexPair
    score: PumpScoreResult
    snapshot_payload: dict[str, Any]

    def to_summary(self) -> dict[str, Any]:
        return {
            "rank": None,
            "symbol": self.pair.symbol,
            "chain": self.pair.chain,
            "dex": self.pair.dex_id,
            "pair_address": self.pair.pair_address,
            "price_usd": self.pair.price_usd,
            "liquidity_usd": self.pair.liquidity_usd,
            "volume_1h": self.pair.volume_1h,
            "volume_24h": self.pair.volume_24h,
            "buys_1h": self.pair.buys_1h,
            "sells_1h": self.pair.sells_1h,
            "price_change_5m": self.pair.price_change_5m,
            "price_change_1h": self.pair.price_change_1h,
            "price_change_6h": self.pair.price_change_6h,
            "price_change_24h": self.pair.price_change_24h,
            "pair_age_hours": self.pair.pair_age_hours,
            "pump_momentum_score": self.score.pump_momentum_score,
            "rug_risk_score": self.score.rug_risk_score,
            "final_speculative_score": self.score.final_speculative_score,
            "classification": self.score.classification,
            "url": self.pair.url,
        }


@dataclass(slots=True)
class ScanResult:
    candidates: list[CandidateRow] = field(default_factory=list)
    rejected_count: int = 0
    chains_scanned: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)

    @property
    def top_n(self) -> list[CandidateRow]:
        return sorted(
            self.candidates,
            key=lambda c: c.score.final_speculative_score,
            reverse=True,
        )


class CryptoPumpRadarService:
    """High-level scanner + scoring orchestration."""

    def __init__(
        self,
        repository: CryptoPumpRepository | None = None,
        *,
        provider: DexScreenerProvider | None = None,
        scoring_service: CryptoPumpScoringService | None = None,
        config: dict | None = None,
    ) -> None:
        self.config = config or load_yaml_config("crypto_pump_radar.yaml")
        self.scanner_cfg = self.config.get("scanner", {})
        self.repository = repository
        self.provider = provider or DexScreenerProvider(
            timeout=float(self.scanner_cfg.get("request_timeout_seconds", 20.0))
        )
        self.scoring_service = scoring_service or CryptoPumpScoringService(
            config=self.config
        )

    # -- Scanner API ---------------------------------------------------------

    def scan(
        self,
        *,
        chains: list[str] | None = None,
        persist: bool = True,
        dry_run: bool = False,
    ) -> ScanResult:
        """Run a full scan across the configured chains and return scored pairs."""

        target_chains = chains or list(self.scanner_cfg.get("chains", []))
        if not target_chains:
            logger.warning("Crypto Pump Radar: no chains configured.")
        target_chains_lc = [c.lower() for c in target_chains]

        scan_run_orm = None
        if persist and not dry_run and self.repository is not None:
            scan_run_orm = self.repository.start_scan_run(
                query_mode="scanner",
                chains=target_chains_lc,
            )

        result = ScanResult(chains_scanned=target_chains_lc)
        try:
            pairs = self._collect_candidates(target_chains_lc, result)
            filtered = self._filter_and_dedupe(pairs, target_chains_lc, result)
            scored = self._score_candidates(filtered)
            result.candidates = scored

            if persist and not dry_run and self.repository is not None:
                top_payloads = [
                    self._add_rank(row, idx + 1)
                    for idx, row in enumerate(result.top_n[: self._top_n()])
                ]
                self._persist_snapshots(
                    scored,
                    scan_run_id=(scan_run_orm.id if scan_run_orm is not None else None),
                )
                if scan_run_orm is not None:
                    self.repository.finish_scan_run(
                        scan_run_orm,
                        status="completed",
                        candidates_found=len(result.candidates),
                        top_candidates=top_payloads,
                    )
            return result
        except Exception as exc:  # defensive: never break the caller
            logger.exception("Crypto Pump Radar scan failed: %s", exc)
            result.error_messages.append(str(exc))
            if scan_run_orm is not None and self.repository is not None:
                try:
                    self.repository.finish_scan_run(
                        scan_run_orm,
                        status="failed",
                        candidates_found=len(result.candidates),
                        top_candidates=None,
                        error_message=str(exc),
                    )
                except Exception as inner_exc:  # pragma: no cover  - persistence error
                    logger.warning(
                        "Could not record failed scan run: %s", inner_exc
                    )
            return result

    def manual_lookup(self, query: str) -> list[DexPair]:
        """Resolve a free-text query (symbol, address, pair URL) to DexPairs."""

        cleaned = (query or "").strip()
        if not cleaned:
            return []

        # If the query looks like a DexScreener URL extract chain + pair_address
        if "dexscreener.com" in cleaned.lower():
            parts = [p for p in cleaned.split("/") if p]
            if len(parts) >= 2:
                pair_address = parts[-1].split("?")[0]
                chain = parts[-2]
                pair = self.provider.get_pair(chain=chain, pair_address=pair_address)
                if pair is not None:
                    return [pair]

        # Else, try a normal search (DexScreener accepts symbol, name, or address)
        return self.provider.search(cleaned)

    def manual_lookup_persisted(
        self,
        query: str,
        *,
        scan_run_id: int | None = None,
        prior_high_score_detected: bool = False,
    ) -> list[CandidateRow]:
        """Lookup + score + persist a snapshot for each match.

        Returns the scored rows but never raises. Persists only if a repository
        was provided to the service.
        """

        pairs = self.manual_lookup(query)
        if not pairs:
            return []

        deduped = self._dedupe(pairs)
        rows: list[CandidateRow] = []
        for pair in deduped:
            score = self.scoring_service.score_pair(
                pair, prior_high_score_detected=prior_high_score_detected
            )
            payload = self._build_snapshot_payload(pair, score, scan_run_id=scan_run_id)
            rows.append(CandidateRow(pair=pair, score=score, snapshot_payload=payload))

        if self.repository is not None:
            self.repository.add_snapshots_bulk([r.snapshot_payload for r in rows])
        return rows

    # -- Internal helpers ----------------------------------------------------

    def _top_n(self) -> int:
        return int(self.scanner_cfg.get("top_n", 10))

    def _collect_candidates(
        self, chains: list[str], result: ScanResult
    ) -> list[DexPair]:
        max_terms = int(self.scanner_cfg.get("max_search_terms_per_chain", 6))
        pause = float(self.scanner_cfg.get("request_pause_seconds", 0.2))
        max_total = int(self.scanner_cfg.get("max_candidates_per_scan", 300))

        collected: list[DexPair] = []
        seen_keys: set[str] = set()
        for chain in chains:
            seeds = DEFAULT_CHAIN_SEEDS.get(chain, ["trending", "new"])[:max_terms]
            for seed in seeds:
                if len(collected) >= max_total:
                    break
                query = f"{seed} {chain}".strip()
                try:
                    pairs = self.provider.search(query)
                except Exception as exc:
                    logger.warning(
                        "Crypto Pump Radar: provider search failed for '%s': %s",
                        query,
                        exc,
                    )
                    result.error_messages.append(f"search '{query}': {exc}")
                    continue
                for pair in pairs:
                    if pair.dedupe_key in seen_keys:
                        continue
                    seen_keys.add(pair.dedupe_key)
                    collected.append(pair)
                if pause > 0:
                    time.sleep(pause)
            if len(collected) >= max_total:
                break
        return collected

    def _filter_and_dedupe(
        self,
        pairs: list[DexPair],
        chains: list[str],
        result: ScanResult,
    ) -> list[DexPair]:
        min_liq = float(self.scanner_cfg.get("min_liquidity_usd", 0))
        max_liq = float(self.scanner_cfg.get("max_liquidity_usd", 1e15))
        min_vol_1h = float(self.scanner_cfg.get("min_volume_1h_usd", 0))
        min_vol_24h = float(self.scanner_cfg.get("min_volume_24h_usd", 0))
        min_txns_1h = int(self.scanner_cfg.get("min_txns_1h", 0))
        max_pair_age_days = float(self.scanner_cfg.get("max_pair_age_days", 1e9))
        exclude_under_min = float(
            self.scanner_cfg.get("exclude_pair_age_minutes_under", 0)
        )

        deduped = self._dedupe(pairs)
        chain_set = {c.lower() for c in chains}
        filtered: list[DexPair] = []
        for pair in deduped:
            if pair.chain.lower() not in chain_set:
                result.rejected_count += 1
                continue

            liquidity = pair.liquidity_usd or 0.0
            if liquidity < min_liq or liquidity > max_liq:
                result.rejected_count += 1
                continue

            vol_1h = pair.volume_1h or 0.0
            vol_24h = pair.volume_24h or 0.0
            if vol_1h < min_vol_1h or vol_24h < min_vol_24h:
                result.rejected_count += 1
                continue

            buys_1h = pair.buys_1h or 0
            sells_1h = pair.sells_1h or 0
            if (buys_1h + sells_1h) < min_txns_1h:
                result.rejected_count += 1
                continue

            age = pair.pair_age_hours
            if age is not None:
                if age * 60.0 < exclude_under_min:
                    result.rejected_count += 1
                    continue
                if age / 24.0 > max_pair_age_days:
                    result.rejected_count += 1
                    continue

            filtered.append(pair)
        return filtered

    def _dedupe(self, pairs: list[DexPair]) -> list[DexPair]:
        seen: set[str] = set()
        out: list[DexPair] = []
        for pair in pairs:
            key = pair.dedupe_key
            if key in seen:
                continue
            seen.add(key)
            out.append(pair)
        return out

    def _score_candidates(self, pairs: list[DexPair]) -> list[CandidateRow]:
        rows: list[CandidateRow] = []
        prior_lookback_hours = 48.0
        threshold = float(
            self.config.get("scoring", {})
            .get("prior_pump_penalty", {})
            .get("historical_score_threshold", 70)
        )
        for pair in pairs:
            prior_detected = False
            if self.repository is not None:
                try:
                    prior_detected = self.repository.has_recent_high_score(
                        chain=pair.chain,
                        pair_address=pair.pair_address,
                        within_hours=prior_lookback_hours,
                        score_threshold=threshold,
                    )
                except Exception as exc:  # defensive
                    logger.debug("prior history lookup failed: %s", exc)
            score = self.scoring_service.score_pair(
                pair, prior_high_score_detected=prior_detected
            )
            payload = self._build_snapshot_payload(pair, score)
            rows.append(
                CandidateRow(pair=pair, score=score, snapshot_payload=payload)
            )
        return rows

    def _persist_snapshots(
        self,
        rows: list[CandidateRow],
        *,
        scan_run_id: int | None,
    ) -> None:
        if not rows or self.repository is None:
            return
        payloads = []
        for row in rows:
            payload = dict(row.snapshot_payload)
            payload["scan_run_id"] = scan_run_id
            payloads.append(payload)
        try:
            self.repository.add_snapshots_bulk(payloads)
        except Exception as exc:  # defensive
            logger.warning("Crypto Pump Radar: failed to persist snapshots: %s", exc)

    def _add_rank(self, row: CandidateRow, rank: int) -> dict[str, Any]:
        summary = row.to_summary()
        summary["rank"] = rank
        return summary

    def _build_snapshot_payload(
        self,
        pair: DexPair,
        score: PumpScoreResult,
        *,
        scan_run_id: int | None = None,
    ) -> dict[str, Any]:
        return {
            "chain": pair.chain,
            "dex_id": pair.dex_id,
            "pair_address": pair.pair_address,
            "symbol": pair.symbol,
            "base_token_name": pair.base_token_name,
            "base_token_address": pair.base_token_address,
            "quote_token_symbol": pair.quote_token_symbol,
            "price_usd": pair.price_usd,
            "liquidity_usd": pair.liquidity_usd,
            "fdv": pair.fdv,
            "market_cap": pair.market_cap,
            "volume_5m": pair.volume_5m,
            "volume_1h": pair.volume_1h,
            "volume_6h": pair.volume_6h,
            "volume_24h": pair.volume_24h,
            "buys_5m": pair.buys_5m,
            "sells_5m": pair.sells_5m,
            "buys_1h": pair.buys_1h,
            "sells_1h": pair.sells_1h,
            "buys_6h": pair.buys_6h,
            "sells_6h": pair.sells_6h,
            "buys_24h": pair.buys_24h,
            "sells_24h": pair.sells_24h,
            "price_change_5m": pair.price_change_5m,
            "price_change_1h": pair.price_change_1h,
            "price_change_6h": pair.price_change_6h,
            "price_change_24h": pair.price_change_24h,
            "pair_created_at": (
                pair.pair_created_at.replace(tzinfo=None)
                if pair.pair_created_at is not None
                else None
            ),
            "pair_age_hours": pair.pair_age_hours,
            "pump_momentum_score": score.pump_momentum_score,
            "liquidity_quality_score": score.liquidity_quality_score,
            "transaction_quality_score": score.transaction_quality_score,
            "early_trend_score": score.early_trend_score,
            "prior_pump_penalty": score.prior_pump_penalty,
            "rug_risk_score": score.rug_risk_score,
            "final_speculative_score": score.final_speculative_score,
            "classification": score.classification,
            "scan_run_id": scan_run_id,
            "payload_json": {
                "url": pair.url,
                "base_token_symbol": pair.base_token_symbol,
                "score_breakdown": score.breakdown,
            },
        }
