from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Forecast, ForecastSnapshot
from .schemas import ForecastRequest, ForecastResponse, ForecastSnapshotResponse
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
