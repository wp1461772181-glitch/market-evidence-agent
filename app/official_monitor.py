"""One bounded official-SEC monitoring pass for the five configured stocks.

The module deliberately contains no scheduler.  A caller may invoke ``run_once``
once per hour, or invoke this module as a CLI.  It discovers and stores new SEC
accessions, fetches their bounded official text, and only then asks an injected
revision callback to create a review-pending evidence revision.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from .database import SessionLocal
from .evidence_revision import EvidenceRevisionError, create_evidence_revision
from .event_provider import EventProviderError, configured_deepseek_model, create_deepseek_provider_from_env
from .evidence_revision_models import EvidenceRevision
from .models import ForecastRevision, ForecastSnapshot, SecFilingInventory
from .sec_filings import (
    SUPPORTED_SEC_TICKERS,
    SecEdgarProvider,
    SecFilingsError,
    fetch_inventory_content,
    scan_sec_filings,
)


AUTOMATIC_REVISION_WINDOW = timedelta(hours=72)


class RevisionCallback(Protocol):
    def __call__(
        self,
        *,
        symbol: str,
        parent_snapshot: ForecastSnapshot,
        filing: SecFilingInventory,
        observed_at: datetime,
        db: Session,
    ) -> object: ...


@dataclass(frozen=True)
class MonitoredFiling:
    accession_number: str
    content_status: str
    revision_status: str
    error: str | None = None


@dataclass(frozen=True)
class SymbolMonitorResult:
    symbol: str
    discovered_count: int = 0
    created_count: int = 0
    skipped_count: int = 0
    filings: list[MonitoredFiling] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class OfficialMonitorResult:
    observed_at: datetime
    symbols: list[SymbolMonitorResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "symbols": [
                {
                    **asdict(symbol),
                    "filings": [asdict(filing) for filing in symbol.filings],
                }
                for symbol in self.symbols
            ],
        }


def run_once(
    *,
    db: Session,
    provider: SecEdgarProvider | None = None,
    revision_callback: RevisionCallback | None = None,
    observed_at: datetime | None = None,
    symbols: Iterable[str] = tuple(sorted(SUPPORTED_SEC_TICKERS)),
) -> OfficialMonitorResult:
    """Scan every supported ticker once and attempt only eligible revisions.

    A source is eligible only if it is a newly discovered, successfully fetched
    official filing whose SEC acceptance time falls strictly after the most
    recent observed root forecast's creation time and no later than this run.
    That root may be at most 72 hours old.  Failures are recorded in the
    returned result and never synthesized into a forecast revision.
    """
    run_time = _as_utc(observed_at or datetime.now(UTC), "observed_at")
    active_provider = provider or SecEdgarProvider()
    callback = revision_callback or _default_revision_callback
    normalized_symbols = tuple(sorted(set(symbols)))
    unsupported = set(normalized_symbols) - SUPPORTED_SEC_TICKERS
    if unsupported:
        raise ValueError(f"official monitoring supports only: {', '.join(sorted(SUPPORTED_SEC_TICKERS))}")

    results: list[SymbolMonitorResult] = []
    for symbol in normalized_symbols:
        existing_accessions = {
            accession
            for (accession,) in db.query(SecFilingInventory.accession_number)
            .filter(SecFilingInventory.symbol == symbol)
            .all()
        }
        try:
            filings, created_count, skipped_count = scan_sec_filings(
                symbol=symbol,
                db=db,
                provider=active_provider,
                observed_at=run_time,
            )
        except SecFilingsError as exc:
            results.append(SymbolMonitorResult(symbol=symbol, error=str(exc)))
            continue

        new_filings = [filing for filing in filings if filing.accession_number not in existing_accessions]
        monitored: list[MonitoredFiling] = []
        parent = _latest_observed_root(symbol=symbol, observed_at=run_time, db=db)
        candidates: dict[str, SecFilingInventory] = {}
        for filing in new_filings:
            if _is_recent_official_publication(filing=filing, observed_at=run_time):
                candidates[filing.accession_number] = filing
            else:
                # Discovery metadata is still useful in the review inventory,
                # but a historical backfill must not consume a body fetch.
                monitored.append(
                    MonitoredFiling(
                        accession_number=filing.accession_number,
                        content_status=filing.content_status,
                        revision_status="not_eligible",
                        error=_non_recent_fetch_reason(filing=filing, observed_at=run_time),
                    )
                )
        if parent is not None:
            for filing in filings:
                if (
                    filing.accession_number not in candidates
                    and _timing_eligibility_error(parent_snapshot=parent, filing=filing, observed_at=run_time) is None
                    and not _has_evidence_revision(parent_snapshot=parent, filing=filing, db=db)
                ):
                    candidates[filing.accession_number] = filing
        for filing in candidates.values():
            try:
                if filing.content_status == "fetched" and filing.content_excerpt:
                    fetched = filing
                else:
                    fetched, _ = fetch_inventory_content(
                        symbol=symbol,
                        accession_number=filing.accession_number,
                        db=db,
                        provider=active_provider,
                        observed_at=run_time,
                    )
            except SecFilingsError as exc:
                monitored.append(
                    MonitoredFiling(
                        accession_number=filing.accession_number,
                        content_status="unavailable",
                        revision_status="not_attempted",
                        error=str(exc),
                    )
                )
                continue

            eligibility_error = _automatic_eligibility_error(
                parent_snapshot=parent,
                filing=fetched,
                observed_at=run_time,
            )
            if eligibility_error is not None:
                monitored.append(
                    MonitoredFiling(
                        accession_number=fetched.accession_number,
                        content_status=fetched.content_status,
                        revision_status="not_eligible",
                        error=eligibility_error,
                    )
                )
                continue
            try:
                accession_number = fetched.accession_number
                content_status = fetched.content_status
                callback(
                    symbol=symbol,
                    parent_snapshot=parent,
                    filing=fetched,
                    observed_at=run_time,
                    db=db,
                )
            except Exception as exc:  # callback failures must be visible and must not become synthetic revisions.
                db.rollback()
                monitored.append(
                    MonitoredFiling(
                        accession_number=accession_number,
                        content_status=content_status,
                        revision_status="failed",
                        error=str(exc),
                    )
                )
            else:
                monitored.append(
                    MonitoredFiling(
                        accession_number=accession_number,
                        content_status=content_status,
                        revision_status="created",
                    )
                )
        results.append(
            SymbolMonitorResult(
                symbol=symbol,
                discovered_count=len(filings),
                created_count=created_count,
                skipped_count=skipped_count,
                filings=monitored,
            )
        )
    return OfficialMonitorResult(observed_at=run_time, symbols=results)


def _latest_observed_root(
    *, symbol: str, observed_at: datetime, db: Session
) -> ForecastSnapshot | None:
    """Return the latest user-created observed root, excluding either revision kind."""
    return (
        db.query(ForecastSnapshot)
        .outerjoin(ForecastRevision, ForecastRevision.snapshot_id == ForecastSnapshot.id)
        .outerjoin(EvidenceRevision, EvidenceRevision.revised_snapshot_id == ForecastSnapshot.id)
        .filter(
            ForecastSnapshot.symbol == symbol,
            ForecastSnapshot.feature_snapshot_mode == "observed",
            ForecastSnapshot.created_at <= observed_at,
            ForecastRevision.snapshot_id.is_(None),
            EvidenceRevision.revised_snapshot_id.is_(None),
        )
        .order_by(ForecastSnapshot.created_at.desc(), ForecastSnapshot.id.desc())
        .first()
    )


def _automatic_eligibility_error(
    *,
    parent_snapshot: ForecastSnapshot | None,
    filing: SecFilingInventory,
    observed_at: datetime,
) -> str | None:
    timing_error = _timing_eligibility_error(
        parent_snapshot=parent_snapshot,
        filing=filing,
        observed_at=observed_at,
    )
    if timing_error is not None:
        return timing_error
    if filing.content_status != "fetched" or not filing.content_excerpt or not filing.content_excerpt_sha256:
        return "official filing has no usable fetched text"
    return None


def _timing_eligibility_error(
    *,
    parent_snapshot: ForecastSnapshot | None,
    filing: SecFilingInventory,
    observed_at: datetime,
) -> str | None:
    if parent_snapshot is None:
        return "no observed root forecast is available"
    parent_created_at = _as_utc(parent_snapshot.created_at, "parent forecast created_at")
    if observed_at - parent_created_at > AUTOMATIC_REVISION_WINDOW:
        return "latest observed root forecast is older than 72 hours"
    accepted_at = _accepted_at(filing)
    if accepted_at is None:
        return "official filing has no valid SEC acceptance timestamp"
    if accepted_at <= parent_created_at:
        return "official filing was accepted before the selected forecast"
    if accepted_at > observed_at:
        return "official filing acceptance time is later than this monitor run"
    return None


def _has_evidence_revision(*, parent_snapshot: ForecastSnapshot, filing: SecFilingInventory, db: Session) -> bool:
    return (
        db.query(EvidenceRevision.id)
        .filter(
            EvidenceRevision.parent_snapshot_id == parent_snapshot.id,
            EvidenceRevision.source_type == "official_filing",
            EvidenceRevision.source_id == filing.id,
        )
        .first()
        is not None
    )


def _is_recent_official_publication(*, filing: SecFilingInventory, observed_at: datetime) -> bool:
    accepted_at = _accepted_at(filing)
    return accepted_at is not None and accepted_at <= observed_at and observed_at - accepted_at <= AUTOMATIC_REVISION_WINDOW


def _non_recent_fetch_reason(*, filing: SecFilingInventory, observed_at: datetime) -> str:
    accepted_at = _accepted_at(filing)
    if accepted_at is None:
        return "official filing has no valid SEC acceptance timestamp"
    if accepted_at > observed_at:
        return "official filing acceptance time is later than this monitor run"
    return "official filing is older than 72 hours; metadata was saved without body fetch"


def _accepted_at(filing: SecFilingInventory) -> datetime | None:
    value = filing.accepted_at
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(UTC)
    if len(value) == 14 and value.isdigit():
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _default_revision_callback(
    *,
    symbol: str,
    parent_snapshot: ForecastSnapshot,
    filing: SecFilingInventory,
    observed_at: datetime,
    db: Session,
) -> object:
    """Use the normal evidence revision path only after monitor eligibility passes."""
    model = configured_deepseek_model()
    return create_evidence_revision(
        parent_snapshot_id=parent_snapshot.id,
        source_type="official_filing",
        source_id=filing.id,
        mode="automatic",
        db=db,
        provider_factory=lambda: create_deepseek_provider_from_env(model=model),
        model=model,
        checked_at=observed_at,
    )


def _as_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run one official SEC monitoring pass.")
    parser.add_argument("--at", help="Optional ISO UTC test timestamp; omit for the current time")
    args = parser.parse_args(argv)
    observed_at = datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else None
    try:
        with SessionLocal() as db:
            result = run_once(db=db, observed_at=observed_at)
    except (SecFilingsError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result.as_dict(), sort_keys=True))


if __name__ == "__main__":  # pragma: no cover - exercised through the installed CLI command.
    main()
