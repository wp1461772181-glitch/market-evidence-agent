from datetime import UTC, datetime
from uuid import UUID

from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .dashboard import dashboard_evaluation, dashboard_price_history, dashboard_snapshot_entries
from .evidence_revision import (
    EvidenceRevisionError,
    create_evidence_revision,
    create_evidence_revision_tables,
    evidence_revisions_for_symbol,
)
from .models import Forecast, ForecastRevision, ForecastRevisionEvidence, ForecastSnapshot, ResearchRun
from .manual_evidence import (
    MAX_UPLOAD_BYTES,
    ManualEvidenceError,
    content_preview,
    create_uploaded_evidence,
    create_uploaded_evidence_table,
    uploaded_evidence_for_symbol,
)
from .event_provider import EventProviderError, configured_deepseek_model, create_deepseek_provider_from_env
from .forecast_refresh import ForecastRefreshError, get_forecast_refresh_report, run_forecast_refresh, target_window
from .on_demand_forecast import OnDemandForecastError, create_on_demand_forecast
from .research_workflow import (
    DEFAULT_DOCUMENT_DIRECTORY,
    ResearchWorkflowError,
    run_research,
)
from .sec_filings import (
    SecFilingNotFoundError,
    SecFilingsError,
    create_sec_filing_inventory_table,
    fetch_inventory_content,
    inventory_for_symbol,
    review_inventory_filing,
    scan_sec_filings,
)
from .schemas import (
    ForecastRequest,
    ForecastRunResponse,
    ForecastRunRequest,
    ForecastRefreshRequest,
    ForecastRefreshResponse,
    ForecastResponse,
    DashboardResponse,
    EvidenceRevisionInventoryResponse,
    EvidenceRevisionRequest,
    EvidenceRevisionResponse,
    ForecastSnapshotResponse,
    ForecastSnapshotTimelineEntry,
    ForecastSnapshotTimelineResponse,
    ResearchRunRequest,
    ResearchRunResponse,
    SecFilingContentResponse,
    SecFilingInventoryItem,
    SecFilingInventoryResponse,
    SecFilingReviewRequest,
    SecFilingScanResponse,
    UploadedEvidenceItem,
    UploadedEvidenceResponse,
)
from .services import MODEL_VERSION, is_valid_symbol, mock_forecast, normalize_symbol


app = FastAPI(title="Market Evidence Agent", version="0.1.0")


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    create_sec_filing_inventory_table()
    create_uploaded_evidence_table()
    create_evidence_revision_tables()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/research-runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_research_run(payload: ResearchRunRequest, db: Session = Depends(get_db)) -> ResearchRun:
    try:
        model = configured_deepseek_model()
        return run_research(
            symbol=payload.symbol,
            as_of_time=payload.as_of_time,
            document_ids=payload.document_ids,
            db=db,
            model=model,
            document_directory=DEFAULT_DOCUMENT_DIRECTORY,
            provider_factory=lambda: create_deepseek_provider_from_env(model=model),
        )
    except (ResearchWorkflowError, EventProviderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/research-runs/{run_id}", response_model=ResearchRunResponse)
def get_research_run(run_id: UUID, db: Session = Depends(get_db)) -> ResearchRun:
    run = db.get(ResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="research run not found")
    return run


@app.post("/forecast-refresh-runs", response_model=ForecastRefreshResponse, status_code=status.HTTP_201_CREATED)
def create_forecast_refresh_run(
    payload: ForecastRefreshRequest, db: Session = Depends(get_db)
) -> dict:
    """Run one saved-event rolling refresh using only fixed trusted local inputs."""
    try:
        model = configured_deepseek_model()
        result = run_forecast_refresh(
            symbol=payload.symbol,
            before_as_of_time=payload.before_as_of_time,
            after_as_of_time=payload.after_as_of_time,
            document_id=payload.document_id,
            db=db,
            extraction_provider_factory=lambda: create_deepseek_provider_from_env(model=model),
            research_provider_factory=lambda: create_deepseek_provider_from_env(model=model),
            model=model,
        )
        return result.as_dict()
    except (ForecastRefreshError, EventProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/forecast-refresh-runs/{snapshot_id}", response_model=ForecastRefreshResponse)
def get_forecast_refresh_run(snapshot_id: UUID, db: Session = Depends(get_db)) -> dict:
    try:
        return get_forecast_refresh_report(snapshot_id=snapshot_id, db=db).as_dict()
    except ForecastRefreshError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/dashboard/{symbol}", response_model=DashboardResponse)
def get_dashboard(symbol: str, db: Session = Depends(get_db)) -> dict:
    """Read saved forecast evidence for the simple Week 9 dashboard.

    This endpoint deliberately never creates a mock forecast, loads a model,
    calls an LLM, or modifies a database row.
    """
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise HTTPException(status_code=422, detail="symbol must contain 1-5 ASCII letters")

    snapshots = (
        db.query(ForecastSnapshot)
        .filter(ForecastSnapshot.symbol == normalized_symbol)
        .order_by(ForecastSnapshot.feature_as_of_time, ForecastSnapshot.created_at, ForecastSnapshot.id)
        .all()
    )
    evidence_rows = (
        db.query(ForecastRevisionEvidence)
        .join(ForecastSnapshot, ForecastRevisionEvidence.snapshot_id == ForecastSnapshot.id)
        .filter(ForecastSnapshot.symbol == normalized_symbol)
        .order_by(ForecastSnapshot.feature_as_of_time, ForecastSnapshot.created_at, ForecastSnapshot.id)
        .all()
    )
    refresh_reports: list[dict] = []
    for evidence in evidence_rows:
        try:
            refresh_reports.append(get_forecast_refresh_report(snapshot_id=evidence.snapshot_id, db=db).as_dict())
        except ForecastRefreshError as exc:
            raise HTTPException(status_code=500, detail="saved forecast refresh report is incomplete") from exc

    return {
        "symbol": normalized_symbol,
        "snapshots": dashboard_snapshot_entries(snapshots, db),
        "refresh_reports": refresh_reports,
        "evaluation": dashboard_evaluation(),
        "price_history": dashboard_price_history(normalized_symbol, db),
    }


@app.post(
    "/filing-inventories/{symbol}/scan",
    response_model=SecFilingScanResponse,
    status_code=status.HTTP_201_CREATED,
)
def scan_filing_inventory(symbol: str, db: Session = Depends(get_db)) -> dict:
    """Discover recent official SEC metadata for one supported symbol.

    The scan does not download filing bodies, call a model, or create a
    prediction.  A filing body is fetched only by the explicit per-filing
    endpoint below.
    """
    observed_at = datetime.now(UTC)
    try:
        filings, created_count, skipped_count = scan_sec_filings(
            symbol=symbol, db=db, observed_at=observed_at
        )
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized_symbol = normalize_symbol(symbol)
    return {
        "symbol": normalized_symbol,
        "cik": filings[0].cik if filings else None,
        "discovered_count": len(filings),
        "created_count": created_count,
        "skipped_count": skipped_count,
        "observed_at": observed_at,
        "filings": filings,
    }


@app.get("/filing-inventories/{symbol}", response_model=SecFilingInventoryResponse)
def get_filing_inventory(symbol: str, db: Session = Depends(get_db)) -> dict:
    try:
        filings = inventory_for_symbol(symbol=symbol, db=db)
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"symbol": normalize_symbol(symbol), "filings": filings}


@app.post(
    "/filing-inventories/{symbol}/{accession_number}/fetch",
    response_model=SecFilingContentResponse,
)
def fetch_filing_content(symbol: str, accession_number: str, db: Session = Depends(get_db)) -> dict:
    """Fetch a bounded text excerpt from one already-inventoried SEC URL."""
    try:
        filing, cache_hit = fetch_inventory_content(
            symbol=symbol,
            accession_number=accession_number,
            db=db,
            observed_at=datetime.now(UTC),
        )
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = SecFilingInventoryItem.model_validate(filing).model_dump()
    return {**item, "content_excerpt": filing.content_excerpt, "cache_hit": cache_hit}


@app.post(
    "/filing-inventories/{symbol}/{accession_number}/review",
    response_model=SecFilingInventoryItem,
)
def review_filing_inventory(
    symbol: str,
    accession_number: str,
    payload: SecFilingReviewRequest,
    db: Session = Depends(get_db),
) -> object:
    """Record a human decision about source relevance only."""
    try:
        return review_inventory_filing(
            symbol=symbol,
            accession_number=accession_number,
            decision=payload.decision,
            note=payload.note,
            db=db,
            reviewed_at=datetime.now(UTC),
        )
    except SecFilingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SecFilingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/uploaded-evidence/{symbol}",
    response_model=UploadedEvidenceItem,
    status_code=status.HTTP_201_CREATED,
)
async def upload_manual_evidence(
    symbol: str,
    response: Response,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str, Form(...)],
    source_url: Annotated[str, Form(...)],
    published_at: Annotated[datetime, Form(...)],
    credibility_stars: Annotated[int, Form(...)],
    credibility_reason: Annotated[str, Form(...)],
    impact_severity: Annotated[str, Form(...)],
    db: Session = Depends(get_db),
) -> dict:
    """Store user-selected media material as unconfirmed review evidence only."""
    raw_content = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        evidence, created = create_uploaded_evidence(
            symbol=symbol,
            title=title,
            source_url=source_url,
            published_at=published_at,
            credibility_stars=credibility_stars,
            credibility_reason=credibility_reason,
            impact_severity=impact_severity,
            filename=file.filename,
            content=raw_content,
            db=db,
        )
    except ManualEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
    if not created:
        response.status_code = status.HTTP_200_OK
    return _uploaded_evidence_item(evidence)


@app.get("/uploaded-evidence/{symbol}", response_model=UploadedEvidenceResponse)
def get_uploaded_evidence(symbol: str, db: Session = Depends(get_db)) -> dict:
    try:
        items = uploaded_evidence_for_symbol(symbol=symbol, db=db)
    except ManualEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"symbol": normalize_symbol(symbol), "items": [_uploaded_evidence_item(item) for item in items]}


def _uploaded_evidence_item(evidence: object) -> dict:
    item = UploadedEvidenceItem.model_validate(evidence).model_dump()
    item["content_preview"] = content_preview(getattr(evidence, "content_text"))
    return item


@app.post(
    "/evidence-revisions/{symbol}",
    response_model=EvidenceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_selected_evidence_revision(
    symbol: str, payload: EvidenceRevisionRequest, db: Session = Depends(get_db)
) -> dict:
    """Bind one later source to the exact saved forecast version selected by the user."""
    try:
        normalized_symbol = normalize_symbol(symbol)
        if not is_valid_symbol(normalized_symbol):
            raise EvidenceRevisionError("symbol must contain 1-5 ASCII letters")
        parent = db.get(ForecastSnapshot, payload.parent_snapshot_id)
        if parent is None:
            raise EvidenceRevisionError("selected forecast snapshot was not found")
        if parent.symbol != normalized_symbol:
            raise EvidenceRevisionError("selected forecast symbol does not match the request path")
        model = configured_deepseek_model()
        result = create_evidence_revision(
            parent_snapshot_id=payload.parent_snapshot_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            mode=payload.mode,
            db=db,
            provider_factory=lambda: create_deepseek_provider_from_env(model=model),
            model=model,
        )
        if result.revision.symbol != normalized_symbol:
            raise EvidenceRevisionError("selected forecast symbol does not match the request path")
        return result.as_dict()
    except (EvidenceRevisionError, EventProviderError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/evidence-revisions/{symbol}", response_model=EvidenceRevisionInventoryResponse)
def get_evidence_revisions(symbol: str, db: Session = Depends(get_db)) -> dict:
    try:
        normalized_symbol = normalize_symbol(symbol)
        return {
            "symbol": normalized_symbol,
            "revisions": [result.as_dict() for result in evidence_revisions_for_symbol(symbol=normalized_symbol, db=db)],
        }
    except EvidenceRevisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/forecasts", response_model=ForecastResponse, status_code=status.HTTP_201_CREATED)
def create_forecast(payload: ForecastRequest, db: Session = Depends(get_db)) -> Forecast:
    symbol = normalize_symbol(payload.symbol)
    if not is_valid_symbol(symbol):
        raise HTTPException(status_code=422, detail="symbol must contain 1-5 ASCII letters")
    bullish, neutral, bearish = mock_forecast(symbol)
    forecast = Forecast(
        symbol=symbol,
        bullish_probability=bullish,
        neutral_probability=neutral,
        bearish_probability=bearish,
        model_version=MODEL_VERSION,
    )
    db.add(forecast)
    db.commit()
    db.refresh(forecast)
    return forecast


@app.post("/forecast-runs", response_model=ForecastRunResponse, status_code=status.HTTP_201_CREATED)
def create_forecast_run(payload: ForecastRunRequest, db: Session = Depends(get_db)) -> dict:
    """Refresh observed daily bars and append one real numeric forecast snapshot."""
    try:
        snapshot = create_on_demand_forecast(symbol=payload.symbol, db=db)
        return {
            **ForecastSnapshotResponse.model_validate(snapshot).model_dump(),
            "cutoff_date": snapshot.feature_trading_date,
            "target_window": target_window(snapshot.feature_trading_date).as_dict(),
            "model_status": "experimental_offline_model",
            "limitations": [
                "This is an experimental offline market-feature model, not investment advice or a price target.",
                "The model uses observed daily bars only; SEC filings and other evidence are not automatically used in this numeric forecast.",
            ],
        }
    except OnDemandForecastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/forecast-snapshots/{snapshot_id}", response_model=ForecastSnapshotResponse)
def get_forecast_snapshot(snapshot_id: UUID, db: Session = Depends(get_db)) -> ForecastSnapshot:
    snapshot = db.get(ForecastSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="forecast snapshot not found")
    return snapshot


@app.get("/forecast-snapshots/{snapshot_id}/timeline", response_model=ForecastSnapshotTimelineResponse)
def get_forecast_snapshot_timeline(
    snapshot_id: UUID, db: Session = Depends(get_db)
) -> ForecastSnapshotTimelineResponse:
    snapshot = db.get(ForecastSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="forecast snapshot not found")

    entries, root_snapshot_id = _forecast_timeline(snapshot, db)
    return ForecastSnapshotTimelineResponse(root_snapshot_id=root_snapshot_id, snapshots=entries)


def _forecast_timeline(
    snapshot: ForecastSnapshot, db: Session
) -> tuple[list[ForecastSnapshotTimelineEntry], UUID]:
    """Return every saved branch sharing this snapshot's root.

    Legacy ``ForecastRevision`` links are linear, while evidence-led review can
    create several alternatives from the same parent.  Reuse the dashboard's
    validated parent-link traversal so a side-table evidence child never
    appears as a separate root through this endpoint.
    """
    symbol_snapshots = (
        db.query(ForecastSnapshot)
        .filter(ForecastSnapshot.symbol == snapshot.symbol)
        .order_by(ForecastSnapshot.feature_as_of_time, ForecastSnapshot.created_at, ForecastSnapshot.id)
        .all()
    )
    entries = dashboard_snapshot_entries(symbol_snapshots, db)
    selected = next((entry for entry in entries if entry.id == snapshot.id), None)
    if selected is None:  # pragma: no cover - the selected row came from this query.
        raise HTTPException(status_code=500, detail="forecast timeline is missing its selected snapshot")
    root_snapshot_id = selected.root_snapshot_id
    return [entry for entry in entries if entry.root_snapshot_id == root_snapshot_id], root_snapshot_id
