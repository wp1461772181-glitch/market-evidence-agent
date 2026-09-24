"""Durable V2 forecast job admission and short-lived worker leases.

This module deliberately does not run market or LLM work inside a database
transaction. A worker claims a job, releases the transaction, and later uses
the lease epoch as a fencing token when recording its result.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .forecast_v2_models import ForecastJobV2
from .services import normalize_symbol


SUPPORTED_STOCKS = frozenset({"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"})
JOB_KINDS = frozenset({"new", "manual_revision", "automatic_revision"})
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))


class ForecastJobError(ValueError):
    """An invalid or conflicting request that must not be queued."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


def request_digest(
    *, symbol: str,
    kind: str,
    source_refs: list[dict[str, Any]],
    root_version_id: UUID | None,
    parent_version_id: UUID | None,
) -> str:
    payload = {
        "kind": kind,
        "parent_version_id": str(parent_version_id) if parent_version_id else None,
        "root_version_id": str(root_version_id) if root_version_id else None,
        "source_refs": source_refs,
        "symbol": symbol,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def enqueue_job(
    *,
    db: Session,
    symbol: str,
    kind: str,
    idempotency_key: str,
    source_refs: list[dict[str, Any]] | None = None,
    root_version_id: UUID | None = None,
    parent_version_id: UUID | None = None,
) -> ForecastJobV2:
    """Admit one request; identical retries return the already durable job."""
    normalized = normalize_symbol(symbol)
    if normalized not in SUPPORTED_STOCKS:
        raise ForecastJobError("symbol is not supported for V2 forecasting")
    if kind not in JOB_KINDS:
        raise ForecastJobError("unsupported V2 job kind")
    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise ForecastJobError("Idempotency-Key must be 1-128 nonblank characters")
    refs = source_refs or []
    if kind == "new" and (root_version_id is not None or parent_version_id is not None):
        raise ForecastJobError("a new forecast cannot name a parent or root")
    if kind != "new" and (root_version_id is None or parent_version_id is None):
        raise ForecastJobError("revision job requires a root and a parent")
    digest = request_digest(
        symbol=normalized,
        kind=kind,
        source_refs=refs,
        root_version_id=root_version_id,
        parent_version_id=parent_version_id,
    )
    existing = db.scalar(select(ForecastJobV2).where(ForecastJobV2.idempotency_key == key))
    if existing is not None:
        if existing.request_fingerprint != digest:
            raise ForecastJobError("Idempotency-Key already belongs to another request", code="conflict")
        return existing

    job = ForecastJobV2(
        symbol=normalized,
        kind=kind,
        root_version_id=root_version_id,
        parent_version_id=parent_version_id,
        source_refs=refs,
        status="queued",
        current_stage="queued",
        attempts=[],
        idempotency_key=key,
        request_fingerprint=digest,
        lease_epoch=0,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent request may have won the unique-key race.
        existing = db.scalar(select(ForecastJobV2).where(ForecastJobV2.idempotency_key == key))
        if existing is None:
            raise
        if existing.request_fingerprint != digest:
            raise ForecastJobError("Idempotency-Key already belongs to another request", code="conflict")
        return existing
    db.refresh(job)
    return job


def claim_next_job(
    *,
    db: Session,
    worker_id: str,
    job_id: UUID | None = None,
    now: datetime | None = None,
    lease_duration: timedelta = timedelta(minutes=5),
) -> ForecastJobV2 | None:
    """Claim one queued or expired job with PostgreSQL SKIP LOCKED."""
    instant = _utc(now)
    if not worker_id.strip() or lease_duration <= timedelta(0):
        raise ValueError("worker_id and positive lease_duration are required")
    statement = (
        select(ForecastJobV2)
        .where(
            or_(
                and_(
                    ForecastJobV2.status == "queued",
                    or_(ForecastJobV2.next_attempt_at.is_(None), ForecastJobV2.next_attempt_at <= instant),
                ),
                and_(
                    ForecastJobV2.status == "running",
                    ForecastJobV2.lease_expires_at <= instant,
                ),
            )
        )
        .order_by(ForecastJobV2.created_at, ForecastJobV2.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_id is not None:
        statement = statement.where(ForecastJobV2.id == job_id)
    row = db.scalar(statement)
    if row is None:
        db.rollback()
        return None
    row.status = "running"
    row.current_stage = "claimed"
    row.lease_owner = worker_id
    row.lease_epoch += 1
    row.lease_expires_at = instant + lease_duration
    row.next_attempt_at = None
    row.started_at = row.started_at or instant
    row.attempts = [*(row.attempts or []), {"epoch": row.lease_epoch, "started_at": instant.isoformat()}]
    db.commit()
    db.refresh(row)
    return row


def heartbeat_job(
    *,
    db: Session,
    job_id: UUID,
    worker_id: str,
    lease_epoch: int,
    stage: str,
    now: datetime | None = None,
    lease_duration: timedelta = timedelta(minutes=5),
) -> bool:
    """Extend only the lease held by this execution; stale workers get False."""
    instant = _utc(now)
    row = db.get(ForecastJobV2, job_id, with_for_update=True)
    if row is None or not _owns_live_lease(row, worker_id, lease_epoch, instant):
        db.rollback()
        return False
    row.lease_expires_at = instant + lease_duration
    row.current_stage = stage
    db.commit()
    return True


def finish_job(
    *,
    db: Session,
    job_id: UUID,
    worker_id: str,
    lease_epoch: int,
    status: str,
    now: datetime | None = None,
    result_version_id: UUID | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    retryable: bool = False,
) -> bool:
    """Finish or schedule a bounded retry, fenced against a stale worker.

    A version publication should happen in the same transaction as the final
    status transition; that dedicated operation will be added with P1 replay.
    """
    instant = _utc(now)
    row = db.get(ForecastJobV2, job_id, with_for_update=True)
    if row is None or not _owns_live_lease(row, worker_id, lease_epoch, instant):
        db.rollback()
        return False
    attempt_count = len(row.attempts or [])
    if retryable and attempt_count <= len(RETRY_DELAYS):
        row.status = "queued"
        row.current_stage = "retry_wait"
        row.next_attempt_at = instant + RETRY_DELAYS[attempt_count - 1]
    else:
        if status not in {"succeeded", "succeeded_no_change", "blocked_data", "failed"}:
            raise ValueError("invalid terminal job status")
        row.status = status
        row.current_stage = status
        row.completed_at = instant
        row.result_version_id = result_version_id
    row.error_type = error_type
    row.error_message = error_message[:280] if error_message else None
    row.lease_owner = None
    row.lease_expires_at = None
    db.commit()
    return True


def _owns_live_lease(row: ForecastJobV2, worker_id: str, lease_epoch: int, now: datetime) -> bool:
    return (
        row.status == "running"
        and row.lease_owner == worker_id
        and row.lease_epoch == lease_epoch
        and row.lease_expires_at is not None
        and row.lease_expires_at > now
    )


def _utc(value: datetime | None) -> datetime:
    instant = value or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return instant.astimezone(UTC)
