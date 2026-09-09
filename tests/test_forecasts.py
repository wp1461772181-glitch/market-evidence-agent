import itertools
from math import isclose
from string import ascii_uppercase
from uuid import UUID

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Forecast
from app.services import mock_forecast


def test_create_forecast_returns_probabilities(client):
    response = client.post("/forecasts", json={"symbol": " aapl "})
    assert response.status_code == 201
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["model_version"] == "mock-v1"
    probabilities = [body[key] for key in ("bullish_probability", "neutral_probability", "bearish_probability")]
    assert all(0 <= value <= 1 for value in probabilities)
    assert isclose(sum(probabilities), 1.0)
    assert body["id"]


def test_same_symbol_is_deterministic(client):
    first = client.post("/forecasts", json={"symbol": "MSFT"}).json()
    second = client.post("/forecasts", json={"symbol": "msft"}).json()
    keys = ["bullish_probability", "neutral_probability", "bearish_probability", "model_version"]
    assert {key: first[key] for key in keys} == {key: second[key] for key in keys}


def test_created_forecast_is_stored_in_database(client):
    response = client.post("/forecasts", json={"symbol": " GOOG "})
    assert response.status_code == 201
    body = response.json()

    with SessionLocal() as db:
        forecast = db.scalar(select(Forecast).where(Forecast.id == UUID(body["id"])))

    assert forecast is not None
    assert forecast.symbol == body["symbol"] == "GOOG"
    assert forecast.bullish_probability == body["bullish_probability"]
    assert forecast.neutral_probability == body["neutral_probability"]
    assert forecast.bearish_probability == body["bearish_probability"]
    assert forecast.model_version == body["model_version"] == "mock-v1"


def test_one_and_two_letter_symbols_have_valid_probabilities():
    symbols = list(ascii_uppercase) + [
        "".join(pair) for pair in itertools.product(ascii_uppercase, repeat=2)
    ]
    assert len(symbols) == 702

    for symbol in symbols:
        probabilities = mock_forecast(symbol)
        assert all(0 <= value <= 1 for value in probabilities), symbol
        assert isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-12), symbol


def test_invalid_symbol_returns_clear_4xx(client):
    response = client.post("/forecasts", json={"symbol": "AAPL!"})
    assert 400 <= response.status_code < 500
    assert "symbol" in response.json()["detail"]
