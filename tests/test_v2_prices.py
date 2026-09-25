import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.market_data import CorporateAction, DailyPrice, MarketDataFetchResult, PriceBasisMetadata


_SCRIPT = Path(__file__).parents[1] / "scripts" / "build_v2_prices.py"
_SPEC = importlib.util.spec_from_file_location("build_v2_prices", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
prices = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prices)


class FixtureProvider:
    def __init__(self):
        self.calls: list[str] = []

    def fetch(self, symbol, start_date, end_date):
        self.calls.append(symbol)
        first = date(2021, 1, 4)
        second = date(2021, 1, 5)
        return MarketDataFetchResult(
            prices=(
                DailyPrice(symbol, first, 100.0, 101.0, 99.0, 100.5, 1000),
                DailyPrice(symbol, second, 101.0, 102.0, 100.0, 101.5, 1100),
            ),
            price_basis=PriceBasisMetadata(
                adjusted_close_present=True,
                provider_behavior_verified=True,
                corporate_actions_available=True,
                corporate_actions_response_shape="events_object",
                corporate_actions=(
                    CorporateAction("split", date(2021, 1, 5), True, numerator=4, denominator=1),
                    CorporateAction("cash_dividend", date(2021, 1, 5), True, amount=0.22),
                ),
                verification_notes=("fixture quote close",),
                adjusted_close_matches_quote=False,
            ),
        )


def test_plan_is_default_read_only_and_does_not_call_provider(tmp_path):
    provider = FixtureProvider()

    plan = prices.plan_price_snapshots(output_dir=tmp_path / "snapshots", symbols=("AAPL", "SPY"), max_symbols=1)

    assert plan["operation"] == "plan"
    assert plan["writes"] is False
    assert plan["next_symbols"] == ["AAPL"]
    assert provider.calls == []
    assert not (tmp_path / "snapshots").exists()


def test_apply_saves_hashable_quote_close_snapshot_and_resumes_by_symbol(tmp_path, monkeypatch):
    provider = FixtureProvider()
    output = tmp_path / "snapshots"
    monkeypatch.setattr(prices, "_xnys_sessions", lambda start, end: {date(2021, 1, 4), date(2021, 1, 5)})
    observed_at = datetime(2026, 9, 24, 12, tzinfo=UTC)

    first = prices.build_price_snapshots(
        output_dir=output,
        resume=False,
        max_symbols=1,
        fetcher=provider.fetch,
        now_factory=lambda: observed_at,
        symbols=("AAPL", "SPY"),
    )
    manifest = json.loads((output / prices.MANIFEST_NAME).read_text())
    entry = manifest["price_snapshot_files"][0]
    snapshot = json.loads((output / entry["filename"]).read_text())

    assert first["created_symbols"] == ["AAPL"]
    assert provider.calls == ["AAPL"]
    assert manifest["price_snapshot_contract"]["close_field"] == "indicators.quote.close"
    assert entry["observed_at"] == observed_at.isoformat()
    assert entry["sha256"] == prices._sha256((output / entry["filename"]).read_bytes())
    assert snapshot["mode"] == "historical_research"
    assert snapshot["price_basis"]["basis"] == "provider_quote_close_v1"
    assert snapshot["price_basis"]["cash_dividend_reinvestment_included"] is False
    assert snapshot["price_basis"]["corporate_actions"][0]["kind"] == "split"
    assert snapshot["price_basis"]["corporate_actions"][1] == {
        "amount": 0.22,
        "denominator": None,
        "effective_date": "2021-01-05",
        "kind": "cash_dividend",
        "known": True,
        "numerator": None,
    }
    assert snapshot["prices"][0]["close"] == 100.5

    second = prices.build_price_snapshots(
        output_dir=output,
        resume=True,
        max_symbols=1,
        fetcher=provider.fetch,
        now_factory=lambda: observed_at,
        symbols=("AAPL", "SPY"),
    )
    assert second["created_symbols"] == ["SPY"]
    assert provider.calls == ["AAPL", "SPY"]

    complete = prices.build_price_snapshots(
        output_dir=output,
        resume=True,
        max_symbols=1,
        fetcher=provider.fetch,
        now_factory=lambda: observed_at,
        symbols=("AAPL", "SPY"),
    )
    assert complete["created_symbols"] == []
    assert provider.calls == ["AAPL", "SPY"]


def test_resume_rejects_modified_price_snapshot_before_a_provider_call(tmp_path, monkeypatch):
    provider = FixtureProvider()
    output = tmp_path / "snapshots"
    monkeypatch.setattr(prices, "_xnys_sessions", lambda start, end: {date(2021, 1, 4), date(2021, 1, 5)})
    prices.build_price_snapshots(
        output_dir=output, resume=False, max_symbols=1, fetcher=provider.fetch, symbols=("AAPL",)
    )
    manifest = json.loads((output / prices.MANIFEST_NAME).read_text())
    (output / manifest["price_snapshot_files"][0]["filename"]).write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        prices.build_price_snapshots(
            output_dir=output, resume=True, max_symbols=1, fetcher=provider.fetch, symbols=("AAPL",)
        )
    assert provider.calls == ["AAPL"]


def test_apply_records_incomplete_coverage_instead_of_fabricating_complete_data(tmp_path, monkeypatch):
    provider = FixtureProvider()
    monkeypatch.setattr(
        prices, "_xnys_sessions", lambda start, end: {date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6)}
    )
    result = prices.build_price_snapshots(
        output_dir=tmp_path / "snapshots", resume=False, max_symbols=1, fetcher=provider.fetch, symbols=("AAPL",)
    )
    manifest = json.loads((tmp_path / "snapshots" / prices.MANIFEST_NAME).read_text())

    assert result["created_symbols"] == ["AAPL"]
    assert manifest["price_snapshot_files"][0]["coverage_complete"] is False
    snapshot = json.loads((tmp_path / "snapshots" / manifest["price_snapshot_files"][0]["filename"]).read_text())
    assert snapshot["missing_sessions"] == ["2021-01-06"]


def test_xnys_sessions_accepts_fixed_calendar_bounds_that_start_on_a_holiday():
    sessions = prices._xnys_sessions(date(2021, 1, 1), date(2021, 1, 8))

    assert min(sessions) == date(2021, 1, 4)
    assert max(sessions) == date(2021, 1, 8)
    assert date(2021, 1, 1) not in sessions


def test_apply_exit_code_is_nonzero_when_any_selected_symbol_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        prices,
        "build_price_snapshots",
        lambda **kwargs: {"operation": "apply", "errors": [{"symbol": "AAPL", "reason": "fixture failure"}]},
    )

    assert prices.main(["--apply", "--output-dir", str(tmp_path), "--max-symbols", "1"]) == 1
