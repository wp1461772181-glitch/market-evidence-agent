"""Persisted V2 SEC monitoring that only queues research-only revisions.

One call scans the five supported stocks independently.  It records a durable
run even when a provider fails, retains per-symbol watermarks, and only queues
an automatic revision when an already-fetched official filing arrived after an
observed, open V2 root.  Queueing does not run a worker or change probabilities.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from collections.abc import Callable
from typing import Iterable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .forecast_evaluation_v2 import run_evaluation_batch
from .forecast_jobs import SUPPORTED_STOCKS, enqueue_job
from .forecast_v2_models import ForecastJobV2, ForecastVersionV2, OfficialMonitorRunV2
from .evidence_context import MAX_NEW_DOCUMENTS
from .market_time import xnys_session_close_at
from .models import SecFilingInventory
from .database import SessionLocal, engine
from .sec_filings import SEC_SOURCE, DiscoveredSecFiling, SecEdgarProvider, fetch_inventory_content


AUTOMATIC_REVISION_WINDOW = timedelta(hours=72)
MONITOR_INTERVAL = timedelta(hours=1)
INITIAL_LOOKBACK = timedelta(days=3)
BACKFILL_OVERLAP = timedelta(days=1)
MAX_BLOCKED_JOB_RECOVERY_ATTEMPTS = 2
MONITOR_LOCK_KEY = 6_214_237_903


@dataclass(frozen=True)
class SymbolMonitorV2Result:
    symbol: str
    status: str
    start_date: str
    discovered_count: int = 0
    created_count: int = 0
    queued_count: int = 0
    skipped_count: int = 0
    pages_read: int = 0
    complete: bool = False
    error: str | None = None
    watermark_before: dict | None = None
    watermark_after: dict | None = None


@dataclass(frozen=True)
class OfficialMonitorV2Result:
    run_id: UUID
    status: str
    observed_at: datetime
    symbols: tuple[SymbolMonitorV2Result, ...]

    def as_dict(self) -> dict:
        return {
            "run_id": str(self.run_id),
            "status": self.status,
            "observed_at": self.observed_at.isoformat(),
            "symbols": [asdict(item) for item in self.symbols],
        }


def run_once(
    *,
    db: Session,
    provider: SecEdgarProvider | None = None,
    observed_at: datetime | None = None,
    symbols: Iterable[str] = tuple(sorted(SUPPORTED_STOCKS)),
    max_pages: int = 3,
    max_filings: int = 1_000,
    received_at_factory: Callable[[], datetime] | None = None,
) -> OfficialMonitorV2Result:
    """Persist one bounded SEC scan and queue eligible V2 automatic revisions.

    A successful, complete scan advances only its own stock's watermark.  A
    failed or page-capped scan retains its prior watermark so the next run
    repeats a bounded overlapping date range rather than silently losing news.
    """
    instant = _utc(observed_at or datetime.now(UTC), "observed_at")
    received_clock = received_at_factory or (lambda: datetime.now(UTC))
    lock_connection = _try_monitor_lock()
    if lock_connection is None:
        run = OfficialMonitorRunV2(
            status="failed",
            started_at=instant,
            completed_at=_utc(received_clock(), "monitor completion time"),
            per_symbol_results={},
            error_summary={"monitor": "another V2 monitor invocation is running"},
            retry_reason="wait for the active monitor invocation to finish",
            next_due_at=instant + MONITOR_INTERVAL,
        )
        db.add(run)
        db.commit()
        return OfficialMonitorV2Result(run.id, "failed", instant, ())
    try:
        return _run_locked(
            db=db,
            provider=provider,
            observed_at=instant,
            symbols=symbols,
            max_pages=max_pages,
            max_filings=max_filings,
            received_clock=received_clock,
        )
    finally:
        _release_monitor_lock(lock_connection)


def _run_locked(
    *,
    db: Session,
    provider: SecEdgarProvider | None,
    observed_at: datetime,
    symbols: Iterable[str],
    max_pages: int,
    max_filings: int,
    received_clock: Callable[[], datetime],
) -> OfficialMonitorV2Result:
    """Run while the PostgreSQL session-level monitor lock is held."""
    instant = observed_at
    active_provider = provider or SecEdgarProvider()
    normalized_symbols = _symbols(symbols)
    run = OfficialMonitorRunV2(status="running", started_at=instant, per_symbol_results={})
    db.add(run)
    db.commit()
    db.refresh(run)

    prior_watermarks = _prior_watermarks(db=db, before_run_id=run.id)
    watermarks = dict(prior_watermarks)
    results: list[SymbolMonitorV2Result] = []
    for symbol in normalized_symbols:
        before = prior_watermarks.get(symbol)
        start_date = _scan_start(before, instant)
        try:
            coverage = active_provider.discover_between(
                symbol,
                start_date=start_date,
                end_date=instant.date(),
                max_pages=max_pages,
                max_filings=max_filings,
                resume_page=before.get("next_page") if before else None,
            )
            discovery_observed_at = _utc(received_clock(), "SEC discovery receive time")
            saved, created_count = _persist_discovered(
                db=db,
                symbol=symbol,
                filings=coverage.filings,
                observed_at=discovery_observed_at,
            )
            queued_count, skipped_count, source_errors = _queue_eligible_sources(
                db=db,
                symbol=symbol,
                filings=saved,
                observed_at=discovery_observed_at,
                provider=active_provider,
                received_at_factory=received_clock,
            )
            if not coverage.complete or source_errors:
                error = (
                    "SEC history retrieval reached its configured page or filing limit"
                    if not coverage.complete
                    else "; ".join(source_errors)
                )
                if not coverage.complete and coverage.next_page:
                    resumed = dict(before or {})
                    resumed["next_page"] = coverage.next_page
                    watermarks[symbol] = resumed
                results.append(
                    SymbolMonitorV2Result(
                        symbol=symbol,
                        status="failed" if source_errors else "incomplete",
                        start_date=start_date.isoformat(),
                        discovered_count=len(coverage.filings),
                        created_count=created_count,
                        queued_count=queued_count,
                        skipped_count=skipped_count,
                        pages_read=coverage.pages_read,
                        complete=False,
                        error=error,
                        watermark_before=before,
                    )
                )
                continue
            after = _advance_watermark(before=before, filings=saved, observed_at=instant)
            watermarks[symbol] = after
            results.append(
                SymbolMonitorV2Result(
                    symbol=symbol,
                    status="succeeded",
                    start_date=start_date.isoformat(),
                    discovered_count=len(coverage.filings),
                    created_count=created_count,
                    queued_count=queued_count,
                    skipped_count=skipped_count,
                    pages_read=coverage.pages_read,
                    complete=True,
                    watermark_before=before,
                    watermark_after=after,
                )
            )
        except Exception as exc:  # A stock failure must stay visible and isolated from its peers.
            db.rollback()
            results.append(
                SymbolMonitorV2Result(
                    symbol=symbol,
                    status="failed",
                    start_date=start_date.isoformat(),
                    error=str(exc),
                    watermark_before=before,
                )
            )

    failures = {item.symbol: item.error for item in results if item.status != "succeeded"}
    successful = sum(item.status == "succeeded" for item in results)
    status = "succeeded" if successful == len(results) else "failed" if successful == 0 else "partial"
    run.status = status
    completed_at = _utc(received_clock(), "monitor completion time")
    run.completed_at = completed_at
    run.per_symbol_results = {item.symbol: asdict(item) for item in results}
    run.error_summary = failures or None
    run.retry_reason = "retry incomplete or failed symbols from their retained watermark" if failures else None
    run.last_success_watermark = watermarks
    run.next_due_at = completed_at + MONITOR_INTERVAL

    # Persist the SEC outcome before attempting the independent evaluator.
    # If the evaluator raises and rolls back, this run must not be left as
    # ``running`` or lose its per-symbol failure information.
    db.commit()

    # Evaluation is deliberately independent from SEC discovery.  A failed
    # filing request for one symbol must not prevent an already mature version
    # from being evaluated against the price rows that are already present.
    # This cycle does not fetch Yahoo prices: target-period corporate actions
    # and price-basis corrections still require a separate P7-safe ingestion.
    try:
        run.evaluation_summary = {"status": "succeeded", **run_evaluation_batch(
            db=db, evaluated_at=completed_at
        ).as_dict()}
    except Exception as exc:  # Keep the SEC run auditable even if evaluation breaks.
        db.rollback()
        run = db.get(OfficialMonitorRunV2, run.id)
        if run is None:  # pragma: no cover - the run was committed above.
            raise RuntimeError("persisted monitor run disappeared before evaluation summary")
        run.evaluation_summary = {"status": "failed", "error": str(exc)}
    db.commit()
    db.refresh(run)
    return OfficialMonitorV2Result(run.id, status, instant, tuple(results))


def _try_monitor_lock():
    connection = engine.connect()
    acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": MONITOR_LOCK_KEY}))
    if acquired:
        return connection
    connection.close()
    return None


def _release_monitor_lock(connection) -> None:
    try:
        connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": MONITOR_LOCK_KEY})
    finally:
        connection.close()


def _symbols(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(value).strip().upper() for value in values}))
    unsupported = set(normalized) - SUPPORTED_STOCKS
    if not normalized or unsupported:
        raise ValueError("official V2 monitoring supports only: " + ", ".join(sorted(SUPPORTED_STOCKS)))
    return normalized


def _prior_watermarks(*, db: Session, before_run_id: UUID) -> dict[str, dict]:
    rows = db.scalars(
        select(OfficialMonitorRunV2)
        .where(OfficialMonitorRunV2.id != before_run_id, OfficialMonitorRunV2.completed_at.is_not(None))
        .order_by(OfficialMonitorRunV2.completed_at.desc(), OfficialMonitorRunV2.id.desc())
    )
    for row in rows:
        if isinstance(row.last_success_watermark, dict):
            return {str(symbol): value for symbol, value in row.last_success_watermark.items() if isinstance(value, dict)}
    return {}


def _scan_start(watermark: dict | None, observed_at: datetime) -> date:
    if watermark:
        raw = watermark.get("scanned_through_date")
        try:
            return date.fromisoformat(raw) - BACKFILL_OVERLAP
        except (TypeError, ValueError):
            pass
    return observed_at.date() - INITIAL_LOOKBACK


def _persist_discovered(
    *, db: Session, symbol: str, filings: Iterable[DiscoveredSecFiling], observed_at: datetime
) -> tuple[list[SecFilingInventory], int]:
    discovered = list(filings)
    accessions = {filing.accession_number for filing in discovered}
    existing = {
        row.accession_number: row
        for row in db.scalars(
            select(SecFilingInventory).where(
                SecFilingInventory.symbol == symbol,
                SecFilingInventory.accession_number.in_(accessions),
            )
        )
    }
    created = 0
    for filing in discovered:
        if filing.accession_number in existing:
            continue
        db.add(
            SecFilingInventory(
                symbol=symbol,
                cik=filing.cik,
                accession_number=filing.accession_number,
                form=filing.form,
                filed_at=filing.filed_at,
                accepted_at=filing.accepted_at,
                primary_document=filing.primary_document,
                source_url=filing.source_url,
                source=SEC_SOURCE,
                review_status="pending_review",
                observed_at=observed_at,
                content_status="not_fetched",
                content_truncated=False,
                related_attachment_status="not_checked" if filing.form == "8-K" else "not_applicable",
            )
        )
        created += 1
    db.commit()
    saved = list(
        db.scalars(
            select(SecFilingInventory)
            .where(SecFilingInventory.symbol == symbol, SecFilingInventory.accession_number.in_(accessions))
            .order_by(SecFilingInventory.filed_at.desc(), SecFilingInventory.accession_number.desc())
        )
    )
    return saved, created


def _queue_eligible_sources(
    *,
    db: Session,
    symbol: str,
    filings: Iterable[SecFilingInventory],
    observed_at: datetime,
    provider: SecEdgarProvider,
    received_at_factory: Callable[[], datetime],
) -> tuple[int, int, list[str]]:
    candidates = list(filings)
    root = _latest_observed_root(db=db, symbol=symbol, observed_at=observed_at)
    if root is None or not _root_is_open_at(root=root, observed_at=observed_at):
        return 0, len(candidates), []
    parent = _latest_version(db=db, root_id=root.id)
    if parent is None:  # defensive; a persisted root must itself be a version.
        return 0, len(candidates), []
    queued = 0
    skipped = 0
    source_errors: list[str] = []
    eligible_sources: list[SecFilingInventory] = []
    for filing in candidates:
        if not _eligible_source_timing(filing=filing, root=root, observed_at=observed_at):
            skipped += 1
            continue
        effective_observed_at = observed_at
        if not _has_usable_content(filing=filing, observed_at=observed_at):
            filing, _cache_hit = fetch_inventory_content(
                symbol=symbol,
                accession_number=filing.accession_number,
                db=db,
                provider=provider,
                observed_at=observed_at,
                content_observed_at_factory=received_at_factory,
            )
            if filing.content_observed_at is not None:
                effective_observed_at = _utc(filing.content_observed_at, "filing content receive time")
        if not _root_is_open_at(root=root, observed_at=effective_observed_at):
            skipped += 1
            continue
        if not _has_usable_content(filing=filing, observed_at=effective_observed_at):
            source_errors.append(f"{filing.accession_number}: official text is unavailable")
            continue
        eligible_sources.append(filing)
    if not eligible_sources:
        return queued, skipped, source_errors
    completed_source_ids = _completed_source_ids(db=db, root_id=root.id)
    unprocessed_sources = [item for item in eligible_sources if str(item.id) not in completed_source_ids]
    skipped += len(eligible_sources) - len(unprocessed_sources)
    if not unprocessed_sources:
        return queued, skipped, source_errors
    batch = unprocessed_sources[:MAX_NEW_DOCUMENTS]
    if len(unprocessed_sources) > MAX_NEW_DOCUMENTS:
        source_errors.append(
            f"{len(unprocessed_sources) - MAX_NEW_DOCUMENTS} official filings deferred by the V2 source limit"
        )
    source_refs = _source_refs(batch)
    existing = _recover_or_dedupe_source_job(
        db=db,
        root_id=root.id,
        parent_id=parent.id,
        source_refs=source_refs,
        content_observed_at=max(_utc(item.content_observed_at, "filing content_observed_at") for item in batch),
    )
    if existing == "deduped":
        return queued, skipped + len(batch), source_errors
    if existing == "recovered":
        return queued + 1, skipped, source_errors
    key = _automatic_idempotency_key(root_id=root.id, parent_id=parent.id, source_refs=source_refs)
    enqueue_job(
        db=db,
        symbol=symbol,
        kind="automatic_revision",
        idempotency_key=key,
        root_version_id=root.id,
        parent_version_id=parent.id,
        source_refs=source_refs,
    )
    queued += 1
    return queued, skipped, source_errors


def _latest_observed_root(*, db: Session, symbol: str, observed_at: datetime) -> ForecastVersionV2 | None:
    return db.scalar(
        select(ForecastVersionV2)
        .where(
            ForecastVersionV2.symbol == symbol,
            ForecastVersionV2.root_id == ForecastVersionV2.id,
            ForecastVersionV2.created_at <= observed_at,
        )
        .order_by(ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
        .limit(1)
    )


def _root_is_open_at(*, root: ForecastVersionV2, observed_at: datetime) -> bool:
    return (
        observed_at - _utc(root.decision_at, "root decision_at") <= AUTOMATIC_REVISION_WINDOW
        and _target_is_open(root.target_contract, observed_at)
    )


def _latest_version(*, db: Session, root_id: UUID) -> ForecastVersionV2 | None:
    return db.scalar(
        select(ForecastVersionV2)
        .where(ForecastVersionV2.root_id == root_id)
        .order_by(ForecastVersionV2.version_no.desc(), ForecastVersionV2.created_at.desc(), ForecastVersionV2.id.desc())
        .limit(1)
    )


def _eligible_source_timing(*, filing: SecFilingInventory, root: ForecastVersionV2, observed_at: datetime) -> bool:
    accepted_at = _accepted_at(filing.accepted_at)
    return bool(
        accepted_at
        and accepted_at > _utc(root.decision_at, "root decision_at")
        and accepted_at <= observed_at
    )


def _has_usable_content(*, filing: SecFilingInventory, observed_at: datetime) -> bool:
    return bool(
        filing.content_status == "fetched"
        and filing.content_observed_at is not None
        and _utc(filing.content_observed_at, "filing content_observed_at") <= observed_at
        and filing.content_excerpt
        and filing.content_excerpt_sha256
    )


def _recover_or_dedupe_source_job(
    *,
    db: Session,
    root_id: UUID,
    parent_id: UUID,
    source_refs: list[dict[str, str]],
    content_observed_at: datetime,
) -> str:
    """Return ``new``, ``deduped`` or a one-time ``recovered`` queue state."""
    jobs = db.scalars(
        select(ForecastJobV2).where(
            ForecastJobV2.kind == "automatic_revision",
            ForecastJobV2.root_version_id == root_id,
        )
    )
    for job in jobs:
        if job.source_refs != source_refs:
            continue
        if job.status in {"queued", "running", "succeeded", "succeeded_no_change"}:
            return "deduped"
        if job.parent_version_id != parent_id:
            continue
        content_was_refreshed = job.completed_at is None or content_observed_at > job.completed_at
        bounded_blocked_retry = (
            job.status == "blocked_data" and len(job.attempts or []) < MAX_BLOCKED_JOB_RECOVERY_ATTEMPTS
        )
        if (job.status == "failed" and content_was_refreshed) or bounded_blocked_retry:
            job.status = "queued"
            job.current_stage = "queued"
            job.error_type = None
            job.error_message = None
            job.completed_at = None
            job.next_attempt_at = None
            db.commit()
            return "recovered"
        return "deduped"
    return "new"


def _source_refs(filings: Iterable[SecFilingInventory]) -> list[dict[str, str]]:
    return [
        {"source_type": "official_filing", "source_id": str(filing.id)}
        for filing in sorted(filings, key=lambda item: str(item.id))
    ]


def _completed_source_ids(*, db: Session, root_id: UUID) -> set[str]:
    jobs = db.scalars(
        select(ForecastJobV2).where(
            ForecastJobV2.kind == "automatic_revision",
            ForecastJobV2.root_version_id == root_id,
            ForecastJobV2.status.in_(("succeeded", "succeeded_no_change")),
        )
    )
    return {
        str(ref.get("source_id"))
        for job in jobs
        for ref in (job.source_refs or [])
        if ref.get("source_type") == "official_filing" and ref.get("source_id")
    }


def _automatic_idempotency_key(*, root_id: UUID, parent_id: UUID, source_refs: list[dict[str, str]]) -> str:
    source_ids = ":".join(item["source_id"] for item in source_refs)
    value = f"{root_id}:{parent_id}:{source_ids}".encode("ascii")
    return "v2-auto-" + sha256(value).hexdigest()


def _advance_watermark(*, before: dict | None, filings: Iterable[SecFilingInventory], observed_at: datetime) -> dict:
    accepted = [item for item in (_accepted_at(filing.accepted_at) for filing in filings) if item is not None]
    prior_accepted = _accepted_at(before.get("accepted_at")) if before else None
    latest = max([*accepted, *([prior_accepted] if prior_accepted else [])], default=None)
    result = {"scanned_through_date": observed_at.date().isoformat()}
    if latest is not None:
        result["accepted_at"] = latest.isoformat()
    return result


def _target_is_open(contract: object, observed_at: datetime) -> bool:
    if not isinstance(contract, dict):
        return False
    raw = contract.get("target_end_date")
    try:
        target_end = raw if isinstance(raw, date) else date.fromisoformat(raw)
        return observed_at < xnys_session_close_at(target_end)
    except (TypeError, ValueError):
        return False


def _accepted_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one persisted V2 SEC monitoring pass")
    parser.add_argument("--at", help="Optional ISO UTC scan-start timestamp for controlled verification")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-filings", type=int, default=1_000)
    args = parser.parse_args(argv)
    if args.max_pages < 0 or args.max_filings < 1:
        parser.error("--max-pages must be nonnegative and --max-filings must be positive")
    observed_at = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else None
    try:
        with SessionLocal() as db:
            result = run_once(
                db=db,
                observed_at=observed_at,
                max_pages=args.max_pages,
                max_filings=args.max_filings,
            )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI.
    raise SystemExit(main())
