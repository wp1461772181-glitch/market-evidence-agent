from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .dashboard import dashboard_evaluation, dashboard_snapshot_entries
from .models import Forecast, ForecastRevision, ForecastRevisionEvidence, ForecastSnapshot, ResearchRun
from .event_provider import EventProviderError, configured_deepseek_model, create_deepseek_provider_from_env
from .forecast_refresh import ForecastRefreshError, get_forecast_refresh_report, run_forecast_refresh
from .research_workflow import (
    DEFAULT_DOCUMENT_DIRECTORY,
    ResearchWorkflowError,
    run_research,
)
from .schemas import (
    ForecastRequest,
    ForecastRefreshRequest,
    ForecastRefreshResponse,
    ForecastResponse,
    DashboardResponse,
    ForecastSnapshotResponse,
    ForecastSnapshotTimelineEntry,
    ForecastSnapshotTimelineResponse,
    ResearchRunRequest,
    ResearchRunResponse,
)
from .services import MODEL_VERSION, is_valid_symbol, mock_forecast, normalize_symbol


app = FastAPI(title="Market Evidence Agent", version="0.1.0")


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


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
    }


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
    """Return the root-to-leaf chain containing ``snapshot``.

    The application only creates acyclic links.  The bounded traversal makes a
    manually corrupted database fail visibly instead of looping forever.
    """
    current = snapshot
    seen: set[UUID] = set()
    while True:
        if current.id in seen:
            raise HTTPException(status_code=500, detail="forecast revision history contains a cycle")
        seen.add(current.id)
        link = db.get(ForecastRevision, current.id)
        if link is None:
            root = current
            break
        parent = db.get(ForecastSnapshot, link.parent_snapshot_id)
        if parent is None:
            raise HTTPException(status_code=500, detail="forecast revision history has a missing parent")
        current = parent

    entries: list[ForecastSnapshotTimelineEntry] = []
    current = root
    version = 1
    while True:
        child_link = db.query(ForecastRevision).filter_by(parent_snapshot_id=current.id).one_or_none()
        parent_link = db.get(ForecastRevision, current.id)
        entries.append(
            ForecastSnapshotTimelineEntry(
                **ForecastSnapshotResponse.model_validate(current).model_dump(),
                version=version,
                root_snapshot_id=root.id,
                parent_snapshot_id=parent_link.parent_snapshot_id if parent_link else None,
                revision_reason=parent_link.reason if parent_link else None,
            )
        )
        if child_link is None:
            break
        if child_link.root_snapshot_id != root.id:
            raise HTTPException(status_code=500, detail="forecast revision history has an inconsistent root")
        child = db.get(ForecastSnapshot, child_link.snapshot_id)
        if child is None:
            raise HTTPException(status_code=500, detail="forecast revision history has a missing child")
        current = child
        version += 1
    return entries, root.id
