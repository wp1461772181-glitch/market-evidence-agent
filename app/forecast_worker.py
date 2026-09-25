"""Single-process durable V2 forecast worker.

The default processor freezes observed market and evidence inputs into a
``research_only`` version.  It deliberately leaves every probability field
empty until a validated model is available.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from .database import SessionLocal
from .forecast_jobs import claim_next_job, finish_job, heartbeat_job
from .forecast_v2 import ForecastDraft, ForecastPublicationError, publish_forecast_version
from .forecast_v2_models import ForecastJobV2
from .forecast_v2_processor import ForecastInputError, ResearchOnlyForecastProcessor


LEASE_DURATION = timedelta(minutes=5)
HEARTBEAT_SECONDS = 30


class ForecastDataBlocked(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ForecastRetryableError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


Processor = Callable[[ForecastJobV2], ForecastDraft]
SessionFactory = Callable[[], Session]


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        job_id,
        worker_id: str,
        lease_epoch: int,
        interval_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_epoch = lease_epoch
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self.lost_lease = False
        self._thread = threading.Thread(target=self._run, name="forecast-v2-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=min(self._interval_seconds + 1, 10))

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                with self._session_factory() as db:
                    if not heartbeat_job(
                        db=db,
                        job_id=self._job_id,
                        worker_id=self._worker_id,
                        lease_epoch=self._lease_epoch,
                        stage="processing",
                        lease_duration=LEASE_DURATION,
                    ):
                        self.lost_lease = True
                        return
            except Exception:
                # A transient DB error need not destroy the work. Publication
                # still checks the lease epoch and expiry in one transaction.
                continue


def run_once(
    *,
    processor: Processor | None = None,
    session_factory: SessionFactory = SessionLocal,
    worker_id: str | None = None,
    job_id: UUID | None = None,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> dict[str, str | None]:
    """Claim one durable job and process it, returning a compact status."""
    identity = worker_id or f"{socket.gethostname()}-{uuid4().hex[:12]}"
    with session_factory() as db:
        job = claim_next_job(db=db, worker_id=identity, job_id=job_id, lease_duration=LEASE_DURATION)
    if job is None:
        return {"status": "idle", "job_id": None, "result_version_id": None}

    heart = _LeaseHeartbeat(
        session_factory=session_factory,
        job_id=job.id,
        worker_id=identity,
        lease_epoch=job.lease_epoch,
        interval_seconds=heartbeat_seconds,
    )
    heart.start()
    try:
        if processor is None:
            processor = ResearchOnlyForecastProcessor(session_factory=session_factory)
        draft = processor(job)
        if heart.lost_lease:
            return {"status": "lease_lost", "job_id": str(job.id), "result_version_id": None}
        # Stop before final publication so heartbeat cannot race the terminal
        # status transition. The publisher still verifies lease ownership.
        heart.stop()
        with session_factory() as db:
            version, created = publish_forecast_version(
                db=db,
                job_id=job.id,
                worker_id=identity,
                lease_epoch=job.lease_epoch,
                draft=draft,
            )
        return {
            "status": "succeeded" if created else "succeeded_no_change",
            "job_id": str(job.id),
            "result_version_id": str(version.id),
        }
    except ForecastDataBlocked as exc:
        heart.stop()
        return _record_failure(
            session_factory, job, identity, status="blocked_data", error_type=exc.reason,
            error_message=str(exc), retryable=False,
        )
    except ForecastRetryableError as exc:
        heart.stop()
        return _record_failure(
            session_factory, job, identity, status="failed", error_type=exc.reason,
            error_message=str(exc), retryable=True,
        )
    except ForecastInputError as exc:
        heart.stop()
        return _record_failure(
            session_factory,
            job,
            identity,
            status="failed" if exc.retryable else "blocked_data",
            error_type=exc.reason,
            error_message=str(exc),
            retryable=exc.retryable,
        )
    except ForecastPublicationError as exc:
        heart.stop()
        if exc.code == "stale_lease":
            return {"status": "lease_lost", "job_id": str(job.id), "result_version_id": None}
        return _record_failure(
            session_factory, job, identity, status="blocked_data", error_type=exc.code,
            error_message=str(exc), retryable=False,
        )
    except Exception:
        heart.stop()
        # Internal details go to server logs in later observability work; the
        # durable API state deliberately exposes a small safe error contract.
        return _record_failure(
            session_factory, job, identity, status="failed", error_type="processor_error",
            error_message="forecast processing failed", retryable=False,
        )
    finally:
        heart.stop()


def _record_failure(
    session_factory: SessionFactory,
    job: ForecastJobV2,
    worker_id: str,
    *,
    status: str,
    error_type: str,
    error_message: str,
    retryable: bool,
) -> dict[str, str | None]:
    with session_factory() as db:
        accepted = finish_job(
            db=db,
            job_id=job.id,
            worker_id=worker_id,
            lease_epoch=job.lease_epoch,
            status=status,
            error_type=error_type,
            error_message=error_message,
            retryable=retryable,
        )
        if not accepted:
            return {"status": "lease_lost", "job_id": str(job.id), "result_version_id": None}
        actual_status = db.get(ForecastJobV2, job.id).status
    return {"status": actual_status, "job_id": str(job.id), "result_version_id": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process durable V2 forecast jobs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process at most one queued job")
    mode.add_argument("--poll-seconds", type=float, help="keep polling for jobs")
    args = parser.parse_args(argv)
    if args.poll_seconds is not None and args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    if args.poll_seconds is None:
        print(json.dumps(run_once(), sort_keys=True))
        return 0
    while True:
        result = run_once()
        if result["status"] == "idle":
            time.sleep(args.poll_seconds)
            continue
        # A persistent worker polls frequently; logging ordinary empty polls
        # would add 43,200 unhelpful lines per day at the default two seconds.
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
