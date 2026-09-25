"""Real-input, non-numeric processor for V2 forecast jobs.

This is deliberately the smallest safe bridge from a durable V2 job to an
immutable version while the joint evidence model remains unvalidated.  It
refreshes the quote-close inputs, freezes only material visible to the system
at the decision cutoff, and persists the resulting provenance.  It never
turns research prose or a hand-written evidence weight into a probability.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from .evidence_context import EvidenceContext, EvidenceContextError, freeze_evidence_context
from .event_provider import EventProviderError, configured_deepseek_model, create_deepseek_provider_from_env
from .features import BENCHMARK_SYMBOL, FeatureRow, build_features
from .forecast_contract import (
    ForecastContractError,
    create_root_contract,
    latest_completed_xnys_session,
    revision_state,
    target_contract_from_dict,
)
from .forecast_v2 import ForecastDraft
from .forecast_v2_models import ForecastJobV2, ForecastVersionV2
from .market_data import MarketDataError, MarketDataFetchResult, YahooFinanceProvider, YAHOO_SOURCE
from .market_data_ingestion import ingest_market_data
from .market_data_snapshots import get_market_data
from .market_time import normalize_utc
from .research_workflow import ResearchRun
from .v2_research_bridge import V2ResearchBridgeError, run_context_research


MARKET_LOOKBACK_DAYS = 100
PROCESSOR_VERSION = "v2-research-only-worker-v1"


class ForecastInputError(RuntimeError):
    """A readable failure when an immutable V2 input cannot be prepared."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


class _MarketProvider(Protocol):
    def fetch_daily_prices_with_metadata(
        self, symbol: str, start_date: date, end_date: date
    ) -> MarketDataFetchResult: ...


class ResearchOnlyForecastProcessor:
    """Build an observed-input V2 draft with no numeric prediction.

    ``market_provider_factory`` and the two storage functions are injectable
    so this boundary is exercised with deterministic price/evidence fixtures.
    The default implementation refreshes the selected stock and SPY before it
    freezes ``decision_at``; a stale local bar can therefore never become a
    current prediction anchor.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        market_provider_factory: Callable[[], _MarketProvider] = YahooFinanceProvider,
        now_factory: Callable[[], datetime] | None = None,
        market_ingester: Callable[..., object] = ingest_market_data,
        market_loader: Callable[..., list[object]] = get_market_data,
        research_runner: Callable[..., ResearchRun] = run_context_research,
        research_model_factory: Callable[[], str] = configured_deepseek_model,
    ) -> None:
        self._session_factory = session_factory
        self._market_provider_factory = market_provider_factory
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._market_ingester = market_ingester
        self._market_loader = market_loader
        self._research_runner = research_runner
        self._research_model_factory = research_model_factory

    def __call__(self, job: ForecastJobV2) -> ForecastDraft:
        requested_at = _utc(job.created_at, "job created_at")
        fetch_started_at = _utc(self._now_factory(), "market refresh time")
        try:
            latest_session = latest_completed_xnys_session(fetch_started_at)
        except (ForecastContractError, ValueError) as exc:
            raise ForecastInputError("no_completed_market_session") from exc

        fetched = self._refresh_prices(job.symbol, latest_session, fetch_started_at)
        # The cutoff is taken only after the market responses were persisted.
        # Inputs discovered later than it will wait for the next job.
        decision_at = _utc(self._now_factory(), "decision_at")
        if decision_at < fetch_started_at:
            raise ForecastInputError("worker_clock_moved_backwards")

        stock_rows, benchmark_rows, feature, price_manifest = self._market_snapshot(
            symbol=job.symbol,
            latest_session=latest_session,
            decision_at=decision_at,
            fetched=fetched,
        )
        with self._session_factory() as db:
            parent = self._load_parent(db, job)
            context = self._freeze_context(db=db, job=job, parent=parent, decision_at=decision_at)
            research_report = self._run_frozen_research(db=db, context=context)

        if parent is None:
            contract = create_root_contract(
                anchor_date=latest_session,
                anchor_close=float(stock_rows[-1].close),
                price_source=YAHOO_SOURCE,
                price_version="observed-yahoo-ingestion-v1",
                price_hash=price_manifest["price_input_sha256"],
                price_basis_metadata=asdict(fetched[job.symbol].price_basis),
            ).as_dict()
            remaining_sessions = 20
            realized_return = 0.0
            trigger_type = "manual"
            change_reason = "new forecast with frozen observed market and evidence inputs"
        else:
            try:
                contract_object = target_contract_from_dict(parent.target_contract)
                state = revision_state(
                    contract_object,
                    current_market_date=latest_session,
                    current_close=float(stock_rows[-1].close),
                )
                state.require_revisable()
            except ForecastContractError as exc:
                raise ForecastInputError(exc.code) from exc
            contract = contract_object.as_dict()
            remaining_sessions = state.remaining_sessions
            realized_return = state.realized_return_from_anchor
            trigger_type = "manual_revision" if job.kind == "manual_revision" else "automatic_revision"
            change_reason = (
                f"{job.kind}: inherited frozen evidence plus {sum(event.is_new for event in context.events)} "
                "new or changed observed event(s)"
            )

        feature_snapshot = {
            **_json_safe(feature.to_dict()),
            "market_feature_mode": "observed",
            "market_cutoff_at": decision_at.isoformat(),
            "remaining_sessions": remaining_sessions,
            "realized_return_from_anchor": realized_return,
        }
        research_report = {
            **research_report,
            "time_mode": "observed",
            "decision_at": decision_at.isoformat(),
            "requested_at": requested_at.isoformat(),
            "evidence_event_count": len(context.events),
            "new_or_changed_event_count": sum(event.is_new for event in context.events),
            "coverage_incomplete": context.coverage_incomplete,
            "omitted_source_refs": list(context.omitted_source_refs),
            "automatic_selection": {
                "enabled": True,
                "policy": "initial_latest_10k_10q_plus_90d_or_parent_inheritance",
                "explicit_source_refs": list(job.source_refs or []),
                "backfill_events": sum(event.discovery_kind == "backfill_discovered" for event in context.events),
            },
        }
        return ForecastDraft(
            target_contract=contract,
            decision_at=decision_at,
            market_cutoff_at=decision_at,
            price_input_manifest=price_manifest,
            evidence_version_manifest=context.evidence_manifest(),
            feature_snapshot=feature_snapshot,
            baseline_probabilities=None,
            joint_probabilities=None,
            model_status="research_only",
            model_manifest={
                "processor_version": PROCESSOR_VERSION,
                "model_status": "research_only",
                "baseline_model_status": "unpublished_below_validation_gate",
                "joint_model_status": "unavailable",
                "numeric_prediction_status": "not_generated",
            },
            research_report=research_report,
            change_reason=change_reason,
            trigger_type=trigger_type,
        )

    def _refresh_prices(
        self, symbol: str, latest_session: date, requested_cutoff: datetime
    ) -> dict[str, MarketDataFetchResult]:
        start_date = latest_session - timedelta(days=MARKET_LOOKBACK_DAYS)
        provider = self._market_provider_factory()
        fetched: dict[str, MarketDataFetchResult] = {}
        try:
            for ticker in (symbol, BENCHMARK_SYMBOL):
                result = provider.fetch_daily_prices_with_metadata(ticker, start_date, latest_session)
                if not result.prices:
                    raise ForecastInputError("market_provider_returned_no_prices", retryable=True)
                fetched[ticker] = result
                self._market_ingester(
                    [ticker],
                    start_date,
                    latest_session,
                    requested_cutoff,
                    source=YAHOO_SOURCE,
                    fetcher=lambda *_args, rows=list(result.prices): rows,
                    now_factory=self._now_factory,
                )
        except ForecastInputError:
            raise
        except (MarketDataError, OSError, TimeoutError) as exc:
            raise ForecastInputError("market_refresh_failed", retryable=True) from exc
        except (ValueError, RuntimeError) as exc:
            raise ForecastInputError("market_refresh_invalid") from exc
        return fetched

    def _market_snapshot(
        self,
        *,
        symbol: str,
        latest_session: date,
        decision_at: datetime,
        fetched: dict[str, MarketDataFetchResult],
    ) -> tuple[list[object], list[object], FeatureRow, dict[str, Any]]:
        stock_visible = self._market_loader(symbol, as_of_time=decision_at, source=YAHOO_SOURCE, mode="observed")
        benchmark_visible = self._market_loader(
            BENCHMARK_SYMBOL, as_of_time=decision_at, source=YAHOO_SOURCE, mode="observed"
        )
        stock_rows, benchmark_rows = _aligned_recent_rows(stock_visible, benchmark_visible, latest_session)
        try:
            report = build_features({symbol: stock_rows, BENCHMARK_SYMBOL: benchmark_rows}, _latest_only=True)
        except ValueError as exc:
            raise ForecastInputError("market_feature_inputs_invalid") from exc
        feature = next(
            (row for row in report.rows if row.symbol == symbol and row.trading_date == latest_session), None
        )
        if feature is None:
            raise ForecastInputError("insufficient_observed_market_history")
        rows = {symbol: stock_rows, BENCHMARK_SYMBOL: benchmark_rows}
        manifest = {
            "schema_version": "v2-observed-market-input-v1",
            "source": YAHOO_SOURCE,
            "market_cutoff_at": decision_at.isoformat(),
            "latest_completed_session": latest_session.isoformat(),
            "rows": {ticker: [_price_row_manifest(row) for row in ticker_rows] for ticker, ticker_rows in rows.items()},
            "price_basis": {ticker: _json_safe(asdict(result.price_basis)) for ticker, result in fetched.items()},
        }
        manifest["price_input_sha256"] = _digest(manifest)
        return stock_rows, benchmark_rows, feature, manifest

    def _load_parent(self, db: Session, job: ForecastJobV2) -> ForecastVersionV2 | None:
        if job.kind == "new":
            return None
        if job.parent_version_id is None:
            raise ForecastInputError("revision_parent_missing")
        parent = db.get(ForecastVersionV2, job.parent_version_id)
        if parent is None or parent.root_id != job.root_version_id or parent.symbol != job.symbol:
            raise ForecastInputError("revision_parent_incompatible")
        return parent

    def _freeze_context(
        self,
        *,
        db: Session,
        job: ForecastJobV2,
        parent: ForecastVersionV2 | None,
        decision_at: datetime,
    ):
        previous_ids: list[str] = []
        previous_decision_at: datetime | None = None
        if parent is not None:
            previous_decision_at = _utc(parent.decision_at, "parent decision_at")
            for item in parent.evidence_version_manifest:
                if isinstance(item, dict):
                    event_id = item.get("event_version_id") or item.get("id")
                    if isinstance(event_id, str):
                        previous_ids.append(event_id)
        try:
            return freeze_evidence_context(
                db=db,
                symbol=job.symbol,
                decision_at=decision_at,
                mode="observed",
                previous_event_ids=previous_ids,
                previous_decision_at=previous_decision_at,
                source_refs=job.source_refs or [],
            )
        except EvidenceContextError as exc:
            raise ForecastInputError(exc.code) from exc

    def _run_frozen_research(self, *, db: Session, context: EvidenceContext) -> dict[str, Any]:
        """Run source-grounded pro/con research, retaining a safe failed run.

        The research workflow validates every returned quote against the
        frozen source text.  A provider/configuration failure is deliberately
        *not* a reason to discard otherwise valid inputs or manufacture a
        numeric forecast; the immutable version records that research remains
        incomplete and points at the durable run when one exists.
        """
        if not context.events:
            return {
                "status": "no_eligible_frozen_sources",
                "research_conclusions_status": "not_run",
                "research_conclusions_reason": "no_observed_evidence_selected",
            }
        try:
            model = self._research_model_factory()
            run = self._research_runner(
                context=context,
                db=db,
                provider_factory=lambda: create_deepseek_provider_from_env(model=model),
                event_provider_factory=lambda: create_deepseek_provider_from_env(model=model),
                model=model,
                event_model=model,
            )
        except (V2ResearchBridgeError, EventProviderError, ValueError) as exc:
            raise ForecastInputError("research_input_validation_failed") from exc
        report = run.report if isinstance(run.report, dict) else None
        if run.status != "succeeded" or report is None:
            # ``run_context_research`` has already stored its own bounded
            # failure audit.  Do not publish a successful V2 *research*
            # version that a UI could mistake for an evidence conclusion.
            raise ForecastInputError("research_run_failed")
        return {
            "status": run.status,
            "research_run_id": str(run.id),
            "research_conclusions_status": "ready",
            "research_conclusions_reason": None,
            "source_ids": list(run.source_ids),
            "source_snapshot": _json_safe(list(run.source_snapshot)),
            "report": _json_safe(report),
        }


def _aligned_recent_rows(stock_rows: list[object], benchmark_rows: list[object], latest_session: date) -> tuple[list[object], list[object]]:
    stock_by_date = {row.trading_date: row for row in stock_rows}
    benchmark_by_date = {row.trading_date: row for row in benchmark_rows}
    common_dates = sorted(set(stock_by_date) & set(benchmark_by_date))
    if not common_dates or common_dates[-1] != latest_session:
        raise ForecastInputError("market_data_stale")
    if len(common_dates) < 21:
        raise ForecastInputError("insufficient_observed_market_history")
    selected_dates = common_dates[-21:]
    return [stock_by_date[item] for item in selected_dates], [benchmark_by_date[item] for item in selected_dates]


def _price_row_manifest(row: object) -> dict[str, Any]:
    return {
        "trading_date": row.trading_date.isoformat(),
        "open": float(row.open),
        "high": float(row.high),
        "low": float(row.low),
        "close": float(row.close),
        "volume": int(row.volume),
        "source": row.source,
        "content_hash": row.content_hash,
        "revision_number": int(row.revision_number),
        "available_at": _utc(row.available_at, "market available_at").isoformat(),
        "observed_at": _utc(row.observed_at, "market observed_at").isoformat(),
    }


def _utc(value: datetime, name: str) -> datetime:
    try:
        return normalize_utc(value, name=name)
    except ValueError as exc:
        raise ForecastInputError("invalid_stored_time") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _json_safe(value: Any) -> Any:
    """Turn provider metadata into PostgreSQL JSON without hiding types."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
