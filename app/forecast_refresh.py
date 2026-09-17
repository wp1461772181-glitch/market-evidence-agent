"""Create one bounded, event-triggered rolling forecast refresh.

This is a historical-research demonstration.  The saved Week 4 artifact
calculates probabilities only from the two market-feature snapshots.  The LLM
extracts and reviews evidence, but never supplies or changes probabilities.
"""

from __future__ import annotations

import json
import hashlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .event_extraction import (
    DocumentInput,
    EventExtractionError,
    EventProvider,
    extract_document,
    load_document,
    project_events_for_review,
)
from .event_provider import DEFAULT_DEEPSEEK_MODEL
from .features import build_features_from_snapshot, metadata
from .forecast_archive import (
    RevisionEvidenceData,
    archive_forecast_snapshot,
    load_trusted_model_artifact,
)
from .market_time import normalize_utc, xnys_session_close_at
from .models import (
    ForecastRevision,
    ForecastRevisionEvidence,
    ForecastSnapshot,
    ResearchRun,
)
from .research_workflow import run_research
from .services import is_valid_symbol, normalize_symbol


REVISION_MODE = "rolling_refresh"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRUSTED_MODEL_DIRECTORY = PROJECT_ROOT / "artifacts" / "week4-2026-09-10"
WEEK8_DOCUMENT_DIRECTORY = PROJECT_ROOT / "data" / "week8-documents"
WEEK8_SOURCE_MANIFEST = PROJECT_ROOT / "docs" / "week8-sources.json"


class ForecastRefreshError(ValueError):
    """A safe request, source, or forecast-refresh failure."""


@dataclass(frozen=True)
class TargetWindow:
    start: date
    end: date

    def as_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class ForecastRefreshResult:
    original_snapshot: ForecastSnapshot
    revised_snapshot: ForecastSnapshot
    evidence: ForecastRevisionEvidence
    research_run: ResearchRun
    original_target: TargetWindow
    revised_target: TargetWindow

    def as_dict(self) -> dict[str, Any]:
        original = _snapshot_payload(self.original_snapshot)
        revised = _snapshot_payload(self.revised_snapshot)
        return {
            "revision_mode": REVISION_MODE,
            "original_snapshot": original,
            "revised_snapshot": revised,
            "probability_delta": {
                name: revised[f"{name}_probability"] - original[f"{name}_probability"]
                for name in ("bearish", "neutral", "bullish")
            },
            "target_windows": {
                "original": self.original_target.as_dict(),
                "revised": self.revised_target.as_dict(),
            },
            "trigger": {
                "reason": "A saved source became eligible after the original snapshot.",
                "document_id": self.evidence.event_document_id,
                "event_type": self.evidence.event_type,
                "event_date": self.evidence.event_date.isoformat(),
                "summary": self.evidence.event_summary,
                "evidence_quote": self.evidence.evidence_quote,
                "source_url": self.evidence.source_url,
                "event_cache_key": self.evidence.event_cache_key,
                "impact_direction_status": "review_required",
            },
            "research_run": _research_payload(self.research_run),
            "limitations": [
                "This is a rolling refresh: the revised forecast has a later 20-XNYS-session target window.",
                "The probability delta comes from different market-feature snapshots; it is not an LLM adjustment or a causal event estimate.",
                "Market data use historical_research initial-backfill assumptions, not a reconstructed live observed feed.",
                "The saved Week 4 model is an offline evaluation artifact and not an online production model.",
            ],
        }


def create_forecast_refresh_tables() -> None:
    """Create only the Week 8 dependencies in a safe foreign-key order."""
    from .database import engine
    from .event_extraction import create_event_extraction_table

    ForecastSnapshot.__table__.create(bind=engine, checkfirst=True)
    ForecastRevision.__table__.create(bind=engine, checkfirst=True)
    create_event_extraction_table()
    ResearchRun.__table__.create(bind=engine, checkfirst=True)
    ForecastRevisionEvidence.__table__.create(bind=engine, checkfirst=True)


def run_forecast_refresh(
    *,
    symbol: str,
    before_as_of_time: datetime,
    after_as_of_time: datetime,
    document_id: str,
    db: Session,
    extraction_provider_factory: Callable[[], EventProvider],
    research_provider_factory: Callable[[], EventProvider],
    model: str = DEFAULT_DEEPSEEK_MODEL,
    model_directory: Path = TRUSTED_MODEL_DIRECTORY,
    document_directory: Path = WEEK8_DOCUMENT_DIRECTORY,
    source_manifest: Path = WEEK8_SOURCE_MANIFEST,
) -> ForecastRefreshResult:
    """Run source extraction/research, then atomically append root and child.

    Event extraction and research runs are their own auditable records.  A
    failed research run deliberately leaves no forecast chain.  Once research
    succeeds, both forecast snapshots, their revision link, and their event
    sidecar are committed together.
    """
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise ForecastRefreshError("symbol must contain 1-5 ASCII letters")
    before = _require_session_close(before_as_of_time, "before_as_of_time")
    after = _require_session_close(after_as_of_time, "after_as_of_time")
    if after <= before:
        raise ForecastRefreshError("after_as_of_time must be later than before_as_of_time")
    if not model.strip():
        raise ForecastRefreshError("model must not be empty")

    document = _load_verified_document(document_id, document_directory, source_manifest)
    if document.ticker != normalized_symbol:
        raise ForecastRefreshError("document ticker does not match symbol")
    available_at = _document_available_at(document.published_date)
    if not (before < available_at <= after):
        raise ForecastRefreshError("document must become available after the original snapshot and by the refresh")

    original_target = target_window(before.date())
    revised_target = target_window(after.date())
    with tempfile.TemporaryDirectory(prefix="market-evidence-week8-") as directory:
        root_path = Path(directory) / "before.json"
        child_path = Path(directory) / "after.json"
        artifact = _preflight_trusted_inputs(
            model_directory=model_directory,
            symbol=normalized_symbol,
            before=before,
            after=after,
            root_path=root_path,
            child_path=child_path,
        )
        _reject_duplicate_or_stale_parent(
            db=db,
            symbol=normalized_symbol,
            before=before,
            document_id=document.document_id,
            expected_model_sha256=artifact.model_sha256,
            expected_manifest_sha256=artifact.manifest_sha256,
            expected_source=artifact.source,
            expected_mode=artifact.snapshot_mode,
        )
        extraction = extract_document(
            document,
            db=db,
            provider_factory=extraction_provider_factory,
            model=model,
        )
        event = _selected_event(project_events_for_review(extraction.batch), document)
        research_run = run_research(
            symbol=normalized_symbol,
            as_of_time=after,
            document_ids=[document.document_id],
            db=db,
            provider_factory=research_provider_factory,
            document_directory=document_directory,
            source_manifest=source_manifest,
            model=model,
        )
        if research_run.status != "succeeded":
            raise ForecastRefreshError("source-grounded research failed; no forecast revision was created")
        try:
            _lock_refresh_key(db, normalized_symbol, before)
            original = _reject_duplicate_or_stale_parent(
                db=db,
                symbol=normalized_symbol,
                before=before,
                document_id=document.document_id,
                expected_model_sha256=artifact.model_sha256,
                expected_manifest_sha256=artifact.manifest_sha256,
                expected_source=artifact.source,
                expected_mode=artifact.snapshot_mode,
            )
            if original is None:
                original = archive_forecast_snapshot(
                    model_dir=model_directory,
                    feature_path=root_path,
                    symbol=normalized_symbol,
                    trading_date=before.date(),
                    db=db,
                    commit=False,
                )
            context = RevisionEvidenceData(
                revision_mode=REVISION_MODE,
                parent_target_start=original_target.start,
                parent_target_end=original_target.end,
                child_target_start=revised_target.start,
                child_target_end=revised_target.end,
                event_document_id=document.document_id,
                event_document_sha256=document.sha256,
                event_cache_key=extraction.cache_key,
                event_type=event["event_type"],
                event_date=date.fromisoformat(event["event_date"]),
                event_summary=event["summary"],
                evidence_quote=event["evidence_quote"],
                source_url=event["source_url"],
                research_run_id=research_run.id,
            )
            revised = archive_forecast_snapshot(
                model_dir=model_directory,
                feature_path=child_path,
                symbol=normalized_symbol,
                trading_date=after.date(),
                db=db,
                revises=original.id,
                reason=f"Rolling refresh after source-grounded event {document.document_id}",
                revision_evidence=context,
                commit=False,
            )
            db.commit()
            db.refresh(original)
            db.refresh(revised)
            evidence = db.get(ForecastRevisionEvidence, revised.id)
            if evidence is None:  # pragma: no cover - protects the transaction contract.
                raise RuntimeError("rolling refresh evidence was not persisted")
        except Exception:
            db.rollback()
            raise
    return ForecastRefreshResult(
        original_snapshot=original,
        revised_snapshot=revised,
        evidence=evidence,
        research_run=research_run,
        original_target=original_target,
        revised_target=revised_target,
    )


def get_forecast_refresh_report(*, snapshot_id: UUID, db: Session) -> ForecastRefreshResult:
    """Rebuild one saved Week 8 report without rereading data or calling a model."""
    evidence = db.get(ForecastRevisionEvidence, snapshot_id)
    if evidence is None:
        raise ForecastRefreshError("forecast refresh report was not found")
    revised = db.get(ForecastSnapshot, snapshot_id)
    original = db.get(ForecastSnapshot, evidence.parent_snapshot_id)
    research_run = db.get(ResearchRun, evidence.research_run_id)
    revision = db.get(ForecastRevision, snapshot_id)
    if revised is None or original is None or research_run is None or revision is None:
        raise ForecastRefreshError("forecast refresh report is incomplete")
    if revision.parent_snapshot_id != original.id or evidence.parent_snapshot_id != original.id:
        raise ForecastRefreshError("forecast refresh report has an inconsistent parent")
    return ForecastRefreshResult(
        original_snapshot=original,
        revised_snapshot=revised,
        evidence=evidence,
        research_run=research_run,
        original_target=TargetWindow(evidence.parent_target_start, evidence.parent_target_end),
        revised_target=TargetWindow(evidence.child_target_start, evidence.child_target_end),
    )


def target_window(feature_date: date) -> TargetWindow:
    """Return the next twenty actual XNYS sessions for one rolling prediction."""
    if feature_date > date.max - timedelta(days=60):
        raise ForecastRefreshError("feature date is too close to the calendar limit")
    sessions: list[date] = []
    candidate = feature_date + timedelta(days=1)
    for _ in range(60):
        try:
            xnys_session_close_at(candidate)
        except ValueError:
            candidate += timedelta(days=1)
            continue
        sessions.append(candidate)
        if len(sessions) == 20:
            return TargetWindow(start=sessions[0], end=sessions[-1])
        candidate += timedelta(days=1)
    raise ForecastRefreshError("could not resolve twenty XNYS sessions within sixty calendar days")


def _require_session_close(value: datetime, name: str) -> datetime:
    try:
        normalized = normalize_utc(value, name=name)
    except ValueError as exc:
        raise ForecastRefreshError(f"{name} must include a timezone") from exc
    if normalized != xnys_session_close_at(normalized.date()):
        raise ForecastRefreshError(f"{name} must equal an XNYS session close")
    return normalized


def _load_verified_document(
    document_id: str, document_directory: Path, source_manifest: Path
) -> DocumentInput:
    if not document_id or Path(document_id).name != document_id:
        raise ForecastRefreshError("document_id must identify one saved document")
    try:
        manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        entries = manifest["documents"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ForecastRefreshError("Week 8 source manifest is unavailable") from exc
    entry = next((value for value in entries if isinstance(value, dict) and value.get("document_id") == document_id), None)
    if entry is None:
        raise ForecastRefreshError("document_id is not in the trusted Week 8 source manifest")
    try:
        document = load_document(document_directory / f"{document_id}.json")
    except EventExtractionError as exc:
        raise ForecastRefreshError(str(exc)) from exc
    for field in ("document_id", "company", "ticker", "source_url", "source_domain", "title", "sha256"):
        if entry.get(field) != getattr(document, field):
            raise ForecastRefreshError("saved document does not match its trusted manifest")
    if entry.get("published_date") != document.published_date.isoformat():
        raise ForecastRefreshError("saved document does not match its trusted manifest")
    return document


def _document_available_at(published_date: date) -> datetime:
    return datetime.combine(published_date + timedelta(days=1), time.min, tzinfo=UTC)


def _selected_event(projected: dict[str, list[dict[str, Any]]], document: DocumentInput) -> dict[str, str]:
    candidates = [
        event
        for event in projected["events"]
        if event["event_type"] == "earnings_release"
        and event["event_date"] == document.published_date.isoformat()
        and event["evidence_quote"] in document.text
    ]
    if len(candidates) != 1:
        raise ForecastRefreshError("source extraction must yield exactly one dated earnings_release event")
    return candidates[0]


def _reject_duplicate_or_stale_parent(
    *,
    db: Session,
    symbol: str,
    before: datetime,
    document_id: str,
    expected_model_sha256: str,
    expected_manifest_sha256: str,
    expected_source: str,
    expected_mode: str,
) -> ForecastSnapshot | None:
    roots = db.scalars(
        select(ForecastSnapshot)
        .outerjoin(ForecastRevision, ForecastRevision.snapshot_id == ForecastSnapshot.id)
        .where(
            ForecastSnapshot.symbol == symbol,
            ForecastSnapshot.feature_trading_date == before.date(),
            ForecastSnapshot.feature_as_of_time == before,
            ForecastRevision.snapshot_id.is_(None),
        )
    ).all()
    if len(roots) > 1:
        raise ForecastRefreshError("multiple matching original snapshots require manual review")
    if not roots:
        return None
    parent = roots[0]
    if (
        parent.model_sha256 != expected_model_sha256
        or parent.model_manifest_sha256 != expected_manifest_sha256
        or parent.feature_source != expected_source
        or parent.feature_snapshot_mode != expected_mode
    ):
        raise ForecastRefreshError("matching original snapshot does not match the trusted model or feature contract")
    duplicate = db.scalar(
        select(ForecastRevisionEvidence.snapshot_id).where(
            ForecastRevisionEvidence.parent_snapshot_id == parent.id,
            ForecastRevisionEvidence.event_document_id == document_id,
        )
    )
    if duplicate is not None:
        raise ForecastRefreshError("this event already has a rolling refresh for the selected forecast")
    existing_child = db.scalar(
        select(ForecastRevision.snapshot_id).where(ForecastRevision.parent_snapshot_id == parent.id)
    )
    if existing_child is not None:
        raise ForecastRefreshError("matching original snapshot already has a revision; review its latest version")
    return parent


def _lock_refresh_key(db: Session, symbol: str, before: datetime) -> None:
    material = f"{symbol}|{before.isoformat()}".encode("utf-8")
    lock_value = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_value)"), {"lock_value": lock_value})


def _preflight_trusted_inputs(
    *,
    model_directory: Path,
    symbol: str,
    before: datetime,
    after: datetime,
    root_path: Path,
    child_path: Path,
):
    try:
        artifact = load_trusted_model_artifact(model_directory)
        if before.date() < artifact.available_from or after.date() < artifact.available_from:
            raise ForecastRefreshError(
                "feature date predates this model's conservative availability bound "
                f"({artifact.available_from.isoformat()})"
            )
        _write_feature_export(root_path, symbol, before)
        _write_feature_export(child_path, symbol, after)
        for path, moment in ((root_path, before), (child_path, after)):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not any(
                row.get("symbol") == symbol and row.get("trading_date") == moment.date().isoformat()
                for row in payload.get("rows", [])
            ):
                raise ForecastRefreshError("trusted market data cannot build the requested feature row")
        return artifact
    except ForecastRefreshError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ForecastRefreshError(f"trusted model or feature input is unavailable: {exc}") from exc


def _write_feature_export(path: Path, symbol: str, as_of_time: datetime) -> None:
    report = build_features_from_snapshot(
        [symbol],
        as_of_time=as_of_time,
        mode="historical_research",
    )
    payload = {
        "metadata": {
            **metadata(as_of_time=as_of_time, source="yahoo-finance-chart", mode="historical_research"),
            "symbols": sorted({symbol, "SPY"}),
            "row_count": len(report.rows),
            "skipped_by_reason": report.skipped_by_reason,
        },
        "rows": [row.to_dict() | {"trading_date": row.trading_date.isoformat()} for row in report.rows],
        "skips": [
            {"symbol": item.symbol, "trading_date": item.trading_date.isoformat(), "reason": item.reason}
            for item in report.skips
        ],
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _snapshot_payload(snapshot: ForecastSnapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "symbol": snapshot.symbol,
        "feature_trading_date": snapshot.feature_trading_date.isoformat(),
        "feature_as_of_time": snapshot.feature_as_of_time.isoformat(),
        "model_version": snapshot.model_version,
        "bearish_probability": snapshot.bearish_probability,
        "neutral_probability": snapshot.neutral_probability,
        "bullish_probability": snapshot.bullish_probability,
    }


def _research_payload(run: ResearchRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status,
        "current_stage": run.current_stage,
        "report": run.report,
        "error": run.error,
    }
