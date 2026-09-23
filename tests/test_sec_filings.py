from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

import app.sec_filings as sec_filings
from app.database import SessionLocal
from app.models import SecFilingInventory
from app.sec_filings import (
    SEC_ARCHIVES_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    SecEdgarProvider,
    SecFilingsError,
    create_sec_filing_inventory_table,
    fetch_inventory_content,
    scan_sec_filings,
)


class FakeResponse:
    def __init__(self, body: bytes, resolved_url: str | None = None, content_type: str = "application/json"):
        self._body = body
        self._resolved_url = resolved_url
        self.headers = type("FakeHeaders", (), {"get_content_type": lambda _: content_type})()

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self._body if amount < 0 else self._body[:amount]

    def geturl(self) -> str | None:
        return self._resolved_url


class FakeSecOpener:
    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.urls: list[str] = []
        self.user_agents: list[str | None] = []
        self.redirects: dict[str, str] = {}
        self.content_types: dict[str, str] = {}

    def __call__(self, request, *, timeout: int):
        assert timeout == 20
        self.urls.append(request.full_url)
        self.user_agents.append(request.get_header("User-agent"))
        return FakeResponse(
            self.responses[request.full_url],
            self.redirects.get(request.full_url, request.full_url),
            self.content_types.get(
                request.full_url,
                "text/html" if request.full_url.endswith((".htm", ".html")) else "application/json",
            ),
        )


def _responses() -> tuple[FakeSecOpener, str]:
    cik = "0000320193"
    primary_url = f"{SEC_ARCHIVES_URL}/320193/000032019326000010/aapl-20260930.htm"
    ticker_payload = {"0": {"ticker": "AAPL", "title": "Apple Inc.", "cik_str": 320193}}
    submissions_payload = {
        "filings": {
            "recent": {
                "form": ["10-K", "8-K", "10-Q", "4"],
                "accessionNumber": [
                    "0000320193-26-000010",
                    "0000320193-26-000011",
                    "0000320193-26-000012",
                    "0000320193-26-000013",
                ],
                "filingDate": ["2026-10-30", "2026-11-01", "2026-08-01", "2026-07-01"],
                "acceptanceDateTime": [
                    "2026-10-30T21:03:04.000Z",
                    "20261101153210",
                    "2026-08-01T19:32:55.000Z",
                    "2026-07-01T10:00:00.000Z",
                ],
                "primaryDocument": [
                    "aapl-20260930.htm",
                    "aapl-8k.htm",
                    "aapl-20260630.htm",
                    "ownership.xml",
                ],
            }
        }
    }
    document_body = (
        "<html><style>ignore</style><body><p>Official revenue discussion.</p>"
        f"<script>ignore</script><p>{'x' * 80_100}</p></body></html>"
    ).encode()
    return (
        FakeSecOpener(
            {
                SEC_TICKERS_URL: json.dumps(ticker_payload).encode(),
                f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json": json.dumps(submissions_payload).encode(),
                primary_url: document_body,
                f"{SEC_ARCHIVES_URL}/320193/000032019326000011/aapl-8k.htm": document_body,
                f"{SEC_ARCHIVES_URL}/320193/000032019326000012/aapl-20260630.htm": document_body,
            }
        ),
        primary_url,
    )


def _provider(opener: FakeSecOpener) -> SecEdgarProvider:
    return SecEdgarProvider(
        user_agent="Market Evidence Agent test@example.com",
        opener=opener,
        sleeper=lambda _: None,
        clock=lambda: 0.0,
    )


def _clear_inventory() -> None:
    with SessionLocal() as db:
        db.query(SecFilingInventory).delete(synchronize_session=False)
        db.commit()


def test_discovers_only_supported_forms_from_official_json_and_declares_user_agent():
    opener, _ = _responses()

    filings = _provider(opener).discover(" aapl ")

    assert [filing.form for filing in filings] == ["10-K", "8-K", "10-Q"]
    assert filings[0].cik == "0000320193"
    assert filings[0].accepted_at == "2026-10-30T21:03:04.000Z"
    assert filings[1].accepted_at == "20261101153210"  # legacy compact form remains supported
    assert filings[0].source_url.endswith("/000032019326000010/aapl-20260930.htm")
    assert opener.urls == [SEC_TICKERS_URL, f"{SEC_SUBMISSIONS_URL}/CIK0000320193.json"]
    assert all(user_agent == "Market Evidence Agent test@example.com" for user_agent in opener.user_agents)


def test_scan_is_idempotent_and_keeps_official_metadata_only():
    create_sec_filing_inventory_table()
    _clear_inventory()
    opener, _ = _responses()
    observed_at = datetime(2026, 11, 2, 9, tzinfo=UTC)

    with SessionLocal() as db:
        first, created, skipped = scan_sec_filings(
            symbol="AAPL", db=db, provider=_provider(opener), observed_at=observed_at
        )
        second, second_created, second_skipped = scan_sec_filings(
            symbol="AAPL", db=db, provider=_provider(opener), observed_at=observed_at
        )
        count = db.scalar(select(func.count()).select_from(SecFilingInventory))

    assert len(first) == len(second) == 3
    assert (created, skipped) == (3, 0)
    assert (second_created, second_skipped) == (0, 3)
    assert count == 3
    assert next(row for row in first if row.form == "10-K").accepted_at == "2026-10-30T21:03:04.000Z"
    assert all(row.review_status == "pending_review" for row in first)
    assert all(row.content_status == "not_fetched" for row in first)
    assert all(row.observed_at == observed_at for row in first)


def test_fetches_a_bounded_cached_excerpt_only_from_an_inventoried_official_url():
    create_sec_filing_inventory_table()
    _clear_inventory()
    opener, _ = _responses()
    provider = _provider(opener)

    with SessionLocal() as db:
        rows, _, _ = scan_sec_filings(symbol="AAPL", db=db, provider=provider)
        selected_url = rows[0].source_url
        filing, first_cache_hit = fetch_inventory_content(
            symbol="AAPL",
            accession_number=rows[0].accession_number,
            db=db,
            provider=provider,
            observed_at=datetime(2026, 11, 2, 10, tzinfo=UTC),
        )
        cached, second_cache_hit = fetch_inventory_content(
            symbol="AAPL",
            accession_number=rows[0].accession_number,
            db=db,
            provider=_provider(opener),
        )

    assert first_cache_hit is False
    assert second_cache_hit is True
    assert filing.source_url == selected_url
    assert filing.content_status == cached.content_status == "fetched"
    assert filing.content_excerpt is not None and "Official revenue discussion." in filing.content_excerpt
    assert "ignore" not in filing.content_excerpt
    assert len(filing.content_excerpt) == 80_000
    assert filing.content_truncated is True
    assert filing.content_excerpt_sha256 is not None
    assert opener.urls.count(selected_url) == 1


def test_marks_pdf_primary_document_unavailable_without_requesting_it():
    create_sec_filing_inventory_table()
    _clear_inventory()
    opener, _ = _responses()
    with SessionLocal() as db:
        rows, _, _ = scan_sec_filings(symbol="AAPL", db=db, provider=_provider(opener))
        row = rows[0]
        row.primary_document = "annual-report.pdf"
        db.commit()
        before = len(opener.urls)
        result, cache_hit = fetch_inventory_content(
            symbol="AAPL",
            accession_number=row.accession_number,
            db=db,
            provider=_provider(opener),
        )

    assert cache_hit is False
    assert result.content_status == "unavailable"
    assert "format is not supported" in (result.content_error or "")
    assert len(opener.urls) == before


def test_marks_non_text_or_binary_primary_document_unavailable():
    create_sec_filing_inventory_table()
    _clear_inventory()
    opener, _ = _responses()
    with SessionLocal() as db:
        rows, _, _ = scan_sec_filings(symbol="AAPL", db=db, provider=_provider(opener))
        first = rows[0]
        opener.content_types[first.source_url] = "application/octet-stream"
        unsupported, _ = fetch_inventory_content(
            symbol="AAPL", accession_number=first.accession_number, db=db, provider=_provider(opener)
        )
        second = rows[1]
        opener.responses[second.source_url] = b"<html>not text\x00</html>"
        binary, _ = fetch_inventory_content(
            symbol="AAPL", accession_number=second.accession_number, db=db, provider=_provider(opener)
        )

    assert unsupported.content_status == "unavailable"
    assert "unsupported content type" in (unsupported.content_error or "")
    assert binary.content_status == "unavailable"
    assert "binary data" in (binary.content_error or "")


def test_endpoint_returns_clear_config_error_when_sec_contact_is_not_configured(client, monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "")

    response = client.post("/filing-inventories/AAPL/scan")

    assert response.status_code == 422
    assert "SEC_EDGAR_USER_AGENT" in response.json()["detail"]


def test_endpoint_exposes_inventory_and_fetched_content_with_no_llm_calls(client, monkeypatch):
    import app.main as main_module

    opener, _ = _responses()
    _clear_inventory()

    def scan_with_fake_sec(**kwargs):
        return scan_sec_filings(**kwargs, provider=_provider(opener))

    def fetch_with_fake_sec(**kwargs):
        return fetch_inventory_content(**kwargs, provider=_provider(opener))

    monkeypatch.setattr(main_module, "scan_sec_filings", scan_with_fake_sec)
    monkeypatch.setattr(main_module, "fetch_inventory_content", fetch_with_fake_sec)

    scanned = client.post("/filing-inventories/AAPL/scan")
    assert scanned.status_code == 201
    body = scanned.json()
    assert body["created_count"] == 3
    accession = body["filings"][0]["accession_number"]
    assert body["filings"][0]["human_review_note"] is None
    assert "does not validate claims" in body["filings"][0]["review_scope_note"]
    assert body["filings"][0]["content_status"] == "not_fetched"

    fetched = client.post(f"/filing-inventories/AAPL/{accession}/fetch")
    assert fetched.status_code == 200
    fetched_body = fetched.json()
    assert fetched_body["content_status"] == "fetched"
    assert fetched_body["content_excerpt_sha256"]
    assert fetched_body["content_excerpt"]
    assert fetched_body["cache_hit"] is False

    inventory = client.get("/filing-inventories/AAPL")
    assert inventory.status_code == 200
    listed = next(item for item in inventory.json()["filings"] if item["accession_number"] == accession)
    assert listed["content_status"] == "fetched"
    assert listed["content_excerpt_sha256"] == fetched_body["content_excerpt_sha256"]


def test_rejects_unknown_symbols_and_unconfigured_provider(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "")
    try:
        SecEdgarProvider()
    except SecFilingsError as exc:
        assert "SEC_EDGAR_USER_AGENT" in str(exc)
    else:
        raise AssertionError("missing explicit SEC contact must fail closed")

    opener, _ = _responses()
    try:
        _provider(opener).discover("TSLA")
    except SecFilingsError as exc:
        assert "currently supports" in str(exc)
    else:
        raise AssertionError("unsupported ticker must fail before external access")


def test_reconstructs_canonical_source_url_and_rejects_tampered_or_redirected_destinations():
    create_sec_filing_inventory_table()
    _clear_inventory()
    opener, _ = _responses()
    with SessionLocal() as db:
        rows, _, _ = scan_sec_filings(symbol="AAPL", db=db, provider=_provider(opener))
        row = rows[0]
        row.source_url = "https://example.invalid/changed.html"
        db.commit()
        before = len(opener.urls)
        result, _ = fetch_inventory_content(
            symbol="AAPL", accession_number=row.accession_number, db=db, provider=_provider(opener)
        )
    assert result.content_status == "unavailable"
    assert "does not match" in (result.content_error or "")
    assert len(opener.urls) == before

    redirected, _ = _responses()
    redirected.redirects[SEC_TICKERS_URL] = "https://example.invalid/redirected"
    with pytest.raises(SecFilingsError, match="redirect was rejected"):
        _provider(redirected).discover("AAPL")


def test_process_wide_rate_limiter_reserves_a_shared_request_slot(monkeypatch):
    opener, _ = _responses()
    current_time = [0.0]
    waits: list[float] = []

    def sleep(duration: float) -> None:
        waits.append(duration)
        current_time[0] += duration

    monkeypatch.setattr(sec_filings, "_NEXT_PROCESS_REQUEST_AT", None)
    first = SecEdgarProvider(
        user_agent="Market Evidence Agent test@example.com",
        opener=opener,
        sleeper=sleep,
        clock=lambda: current_time[0],
    )
    second = SecEdgarProvider(
        user_agent="Market Evidence Agent test@example.com",
        opener=opener,
        sleeper=sleep,
        clock=lambda: current_time[0],
    )

    first._get_json(SEC_TICKERS_URL)
    second._get_json(SEC_TICKERS_URL)

    assert waits == [0.2]


def test_review_endpoint_persists_one_human_source_relevance_decision(client, monkeypatch):
    import app.main as main_module

    opener, _ = _responses()
    _clear_inventory()
    monkeypatch.setattr(
        main_module,
        "scan_sec_filings",
        lambda **kwargs: scan_sec_filings(**kwargs, provider=_provider(opener)),
    )
    scanned = client.post("/filing-inventories/AAPL/scan")
    accession = scanned.json()["filings"][0]["accession_number"]
    original_url = scanned.json()["filings"][0]["source_url"]

    blank_note = client.post(
        f"/filing-inventories/AAPL/{accession}/review",
        json={"decision": "accepted", "note": "   "},
    )
    assert blank_note.status_code == 422

    reviewed = client.post(
        f"/filing-inventories/AAPL/{accession}/review",
        json={"decision": "accepted", "note": "Relevant quarterly source for later review."},
    )
    assert reviewed.status_code == 200
    body = reviewed.json()
    assert body["review_status"] == "accepted"
    assert body["human_review_note"] == "Relevant quarterly source for later review."
    assert body["reviewed_at"]
    assert body["source_url"] == original_url
    assert "does not validate claims" in body["review_scope_note"]

    repeated = client.post(
        f"/filing-inventories/AAPL/{accession}/review",
        json={"decision": "rejected", "note": "Trying to overwrite."},
    )
    assert repeated.status_code == 422
    missing = client.post(
        "/filing-inventories/AAPL/0000320193-26-999999/review",
        json={"decision": "accepted", "note": "Missing record."},
    )
    assert missing.status_code == 404
