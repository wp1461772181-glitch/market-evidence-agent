from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Forecast, ForecastRevision, ForecastSnapshot
from .schemas import (
    ForecastRequest,
    ForecastResponse,
    ForecastSnapshotResponse,
    ForecastSnapshotTimelineEntry,
    ForecastSnapshotTimelineResponse,
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
