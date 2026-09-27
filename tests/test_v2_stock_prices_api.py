from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.forecast_v2_api as api
from app.database import Base, SessionLocal, engine
from app.market_data import YAHOO_SOURCE
from app.models import MarketPrice, MarketPriceRevision


@pytest.fixture(autouse=True)
def price_tables(disposable_database):
    Base.metadata.create_all(bind=engine)


@pytest.fixture()
def client():
    test_app = FastAPI()
    test_app.include_router(api.router)
    test_app.dependency_overrides[api.get_v2_db] = _test_db
    with TestClient(test_app) as test_client:
        yield test_client


def _test_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_prices_returns_empty_dashboard_shape_for_supported_symbol(client):
    supported = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")
    with SessionLocal() as db:
        populated = {
            symbol for (symbol,) in db.query(MarketPrice.symbol).distinct().all()
        } | {
            symbol for (symbol,) in db.query(MarketPriceRevision.symbol).distinct().all()
        }
    empty_symbol = next((symbol for symbol in supported if symbol not in populated), None)
    if empty_symbol is None:  # pragma: no cover - a clean disposable DB always has one.
        pytest.skip("no supported symbol has empty price history")

    response = client.get(f"/v2/stocks/{empty_symbol.lower()}/prices")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == empty_symbol
    assert payload["price_history"]["source"] == YAHOO_SOURCE
    assert payload["price_history"]["latest_trading_date"] is None
    assert payload["price_history"]["candles"] == []


def test_prices_rejects_unsupported_symbol(client):
    response = client.get("/v2/stocks/TSLA/prices")

    assert response.status_code == 422
    assert "symbol is not supported" in response.json()["detail"]


def test_prices_reuses_visible_ohlc_and_matching_spy_close_excluding_future_observation(client):
    trading_date = date(1900, 1, 2)
    now = datetime.now(UTC)
    visible_at = now - timedelta(hours=1)
    rows = [
        MarketPriceRevision(
            symbol="AAPL", trading_date=trading_date, open=10.0, high=12.0, low=9.0,
            close=11.0, volume=100, source=YAHOO_SOURCE, revision_number=1,
            content_hash="a" * 64, available_at=visible_at, observed_at=visible_at,
        ),
        MarketPriceRevision(
            symbol="AAPL", trading_date=trading_date, open=90.0, high=999.0, low=80.0,
            close=999.0, volume=999, source=YAHOO_SOURCE, revision_number=2,
            content_hash="b" * 64, available_at=visible_at, observed_at=now + timedelta(days=1),
        ),
        MarketPriceRevision(
            symbol="SPY", trading_date=trading_date, open=499.0, high=502.0, low=498.0,
            close=501.0, volume=1000, source=YAHOO_SOURCE, revision_number=1,
            content_hash="c" * 64, available_at=visible_at, observed_at=visible_at,
        ),
    ]
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()
        row_ids = [row.id for row in rows]

    try:
        response = client.get("/v2/stocks/aapl/prices")
        assert response.status_code == 200
        payload = response.json()
        assert payload["symbol"] == "AAPL"
        history = payload["price_history"]
        assert history["latest_trading_date"] == trading_date.isoformat()
        assert history["candles"] == [{
            "trading_date": trading_date.isoformat(),
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 100,
            "benchmark_close": 501.0,
        }]
    finally:
        with SessionLocal() as db:
            db.query(MarketPriceRevision).filter(MarketPriceRevision.id.in_(row_ids)).delete(
                synchronize_session=False
            )
            db.commit()


def test_prices_returns_safe_503_when_database_read_fails(client, monkeypatch):
    def fail_read(_symbol, _db):
        raise OperationalError("select", {}, RuntimeError("private database detail"))

    monkeypatch.setattr(api, "dashboard_price_history", fail_read)
    response = client.get("/v2/stocks/MSFT/prices")

    assert response.status_code == 503
    assert response.json() == {"detail": "V2 price history is unavailable"}
