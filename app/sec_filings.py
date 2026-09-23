"""Small, review-only SEC EDGAR filing inventory.

This module uses only SEC's published company-ticker and submissions JSON
endpoints, then constructs document URLs from the resulting CIK and accession
number.  It deliberately does not crawl user-supplied links, make LLM calls,
or use a filing as forecast input.  Primary documents are fetched only after a
user explicitly asks for the already-inventoried filing, and their retained
text is bounded so a long 10-K cannot become an unbounded database payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import engine
from .models import SecFilingInventory
from .services import is_valid_symbol, normalize_symbol


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SEC_SOURCE = "sec-edgar"
SUPPORTED_SEC_TICKERS = frozenset({"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"})
SUPPORTED_FORMS = frozenset({"10-K", "10-Q", "8-K"})
DEFAULT_TIMEOUT_SECONDS = 20
# SEC's published upper limit is 10 requests/second.  This client waits at
# least 0.2 seconds between its own requests, staying below five/second.
MIN_REQUEST_INTERVAL_SECONDS = 0.2
MAX_FILING_BYTES = 5_000_000
MAX_EXCERPT_CHARACTERS = 80_000
MAX_DISCOVERED_FILINGS = 40
PRIMARY_DOCUMENT_CONTENT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
_SAFE_PRIMARY_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,511}$")
_SAFE_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_PROCESS_RATE_LOCK = Lock()
_NEXT_PROCESS_REQUEST_AT: float | None = None


class _NoRedirectHandler(HTTPRedirectHandler):
    """EDGAR discovery never needs a redirect; fail closed if one appears."""

    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


DEFAULT_SEC_OPENER = build_opener(_NoRedirectHandler()).open


class SecFilingsError(ValueError):
    """Safe, user-displayable SEC discovery or retrieval failure."""


class SecFilingNotFoundError(SecFilingsError):
    """The requested accession is not present for the requested symbol."""


@dataclass(frozen=True)
class DiscoveredSecFiling:
    cik: str
    accession_number: str
    form: str
    filed_at: date
    accepted_at: str | None
    primary_document: str
    source_url: str


@dataclass(frozen=True)
class SecFilingContent:
    excerpt: str
    excerpt_sha256: str
    truncated: bool


class _TextCollector(HTMLParser):
    """Drop markup, scripts, and styles while retaining only visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._parts).split())


class SecEdgarProvider:
    """A conservative, injectable stdlib client for the documented SEC APIs."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        opener: Callable = DEFAULT_SEC_OPENER,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        user_agent = user_agent if user_agent is not None else configured_sec_user_agent()
        if not user_agent.strip() or "@" not in user_agent:
            raise SecFilingsError("SEC user agent must identify a contact email address")
        self._user_agent = user_agent.strip()
        self._opener = opener
        self._sleeper = sleeper
        self._clock = clock

    def discover(self, symbol: str) -> list[DiscoveredSecFiling]:
        normalized_symbol = _supported_symbol(symbol)
        tickers = self._get_json(SEC_TICKERS_URL)
        cik = _cik_for_symbol(tickers, normalized_symbol)
        submissions = self._get_json(f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json")
        return _recent_filings(submissions, cik)

    def fetch_primary_document(self, filing: SecFilingInventory) -> SecFilingContent:
        if not filing.primary_document.casefold().endswith((".htm", ".html", ".txt")):
            raise SecFilingsError("SEC primary document format is not supported for text review")
        canonical_url = canonical_sec_filing_url(
            cik=filing.cik,
            accession_number=filing.accession_number,
            primary_document=filing.primary_document,
        )
        if filing.source_url != canonical_url:
            raise SecFilingsError("saved SEC filing URL does not match its official filing identity")
        raw = self._get_bytes(
            canonical_url,
            allowed_content_types=PRIMARY_DOCUMENT_CONTENT_TYPES,
            reject_nul_bytes=True,
        )
        decoded = raw.decode("utf-8", errors="replace")
        collector = _TextCollector()
        try:
            collector.feed(decoded)
            collector.close()
        except Exception as exc:
            raise SecFilingsError("SEC primary document could not be parsed") from exc
        text = collector.text()
        if not text:
            raise SecFilingsError("SEC primary document did not contain readable text")
        excerpt = text[:MAX_EXCERPT_CHARACTERS]
        return SecFilingContent(
            excerpt=excerpt,
            excerpt_sha256=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            truncated=len(text) > len(excerpt),
        )

    def _get_json(self, url: str) -> dict:
        raw = self._get_bytes(url, max_bytes=MAX_FILING_BYTES)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecFilingsError("SEC returned an invalid JSON response") from exc
        if not isinstance(payload, dict):
            raise SecFilingsError("SEC returned an invalid JSON response")
        return payload

    def _get_bytes(
        self,
        url: str,
        *,
        max_bytes: int = MAX_FILING_BYTES,
        allowed_content_types: frozenset[str] | None = None,
        reject_nul_bytes: bool = False,
    ) -> bytes:
        self._wait_for_rate_limit()
        request = Request(
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/json, text/html, text/plain;q=0.9, */*;q=0.1",
            },
        )
        try:
            with self._opener(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                raw = response.read(max_bytes + 1)
                content_type = _response_content_type(response)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise SecFilingsError("SEC request failed; no filing data was saved") from exc
        if len(raw) > max_bytes:
            raise SecFilingsError("SEC response exceeds the configured retrieval limit")
        if allowed_content_types is not None and content_type not in allowed_content_types:
            raise SecFilingsError("SEC primary document has an unsupported content type")
        if reject_nul_bytes and b"\x00" in raw:
            raise SecFilingsError("SEC primary document contains binary data")
        resolved_url = getattr(response, "geturl", lambda: url)()
        if resolved_url != url:
            raise SecFilingsError("SEC redirect was rejected")
        return raw

    def _wait_for_rate_limit(self) -> None:
        global _NEXT_PROCESS_REQUEST_AT
        with _PROCESS_RATE_LOCK:
            now = self._clock()
            next_allowed = _NEXT_PROCESS_REQUEST_AT if _NEXT_PROCESS_REQUEST_AT is not None else now
            remaining = max(0.0, next_allowed - now)
            _NEXT_PROCESS_REQUEST_AT = max(now, next_allowed) + MIN_REQUEST_INTERVAL_SECONDS
        if remaining > 0:
            self._sleeper(remaining)


def scan_sec_filings(
    *, symbol: str, db: Session, provider: SecEdgarProvider | None = None, observed_at: datetime | None = None
) -> tuple[list[SecFilingInventory], int, int]:
    """Discover official filing metadata and persist only new accessions."""
    normalized_symbol = _supported_symbol(symbol)
    observed_at = _require_aware_utc(observed_at or datetime.now(UTC))
    discovered = (provider or SecEdgarProvider()).discover(normalized_symbol)
    if not discovered:
        return [], 0, 0

    accessions = {filing.accession_number for filing in discovered}
    existing = {
        row.accession_number: row
        for row in db.query(SecFilingInventory)
        .filter(SecFilingInventory.symbol == normalized_symbol)
        .filter(SecFilingInventory.accession_number.in_(accessions))
        .all()
    }
    created = 0
    for filing in discovered:
        if filing.accession_number in existing:
            continue
        db.add(
            SecFilingInventory(
                symbol=normalized_symbol,
                cik=filing.cik,
                accession_number=filing.accession_number,
                form=filing.form,
                filed_at=filing.filed_at,
                accepted_at=filing.accepted_at,
                primary_document=filing.primary_document,
                source_url=filing.source_url,
                source=SEC_SOURCE,
                review_status="pending_review",
                human_review_note=None,
                reviewed_at=None,
                content_status="not_fetched",
                observed_at=observed_at,
                content_observed_at=None,
                content_excerpt=None,
                content_excerpt_sha256=None,
                content_truncated=False,
                content_error=None,
            )
        )
        created += 1
    db.commit()
    saved = (
        db.query(SecFilingInventory)
        .filter(SecFilingInventory.symbol == normalized_symbol)
        .filter(SecFilingInventory.accession_number.in_(accessions))
        .order_by(SecFilingInventory.filed_at.desc(), SecFilingInventory.accession_number.desc())
        .all()
    )
    return saved, created, len(discovered) - created


def inventory_for_symbol(*, symbol: str, db: Session) -> list[SecFilingInventory]:
    normalized_symbol = _supported_symbol(symbol)
    return (
        db.query(SecFilingInventory)
        .filter(SecFilingInventory.symbol == normalized_symbol)
        .order_by(SecFilingInventory.filed_at.desc(), SecFilingInventory.accession_number.desc())
        .all()
    )


def fetch_inventory_content(
    *,
    symbol: str,
    accession_number: str,
    db: Session,
    provider: SecEdgarProvider | None = None,
    observed_at: datetime | None = None,
) -> tuple[SecFilingInventory, bool]:
    """Fetch one existing official primary document, never an arbitrary URL."""
    normalized_symbol = _supported_symbol(symbol)
    filing = (
        db.query(SecFilingInventory)
        .filter(
            SecFilingInventory.symbol == normalized_symbol,
            SecFilingInventory.accession_number == accession_number,
        )
        .one_or_none()
    )
    if filing is None:
        raise SecFilingNotFoundError("SEC filing is not in this symbol's inventory; scan it first")
    if filing.content_status == "fetched" and filing.content_excerpt is not None:
        return filing, True

    observed_at = _require_aware_utc(observed_at or datetime.now(UTC))
    try:
        content = (provider or SecEdgarProvider()).fetch_primary_document(filing)
    except SecFilingsError as exc:
        filing.content_status = "unavailable"
        filing.content_error = str(exc)
        filing.content_observed_at = observed_at
        db.commit()
        db.refresh(filing)
        return filing, False

    filing.content_excerpt = content.excerpt
    filing.content_excerpt_sha256 = content.excerpt_sha256
    filing.content_truncated = content.truncated
    filing.content_status = "fetched"
    filing.content_error = None
    filing.content_observed_at = observed_at
    db.commit()
    db.refresh(filing)
    return filing, False


def review_inventory_filing(
    *,
    symbol: str,
    accession_number: str,
    decision: str,
    note: str,
    db: Session,
    reviewed_at: datetime | None = None,
) -> SecFilingInventory:
    """Persist one human source-relevance decision without changing source data."""
    normalized_symbol = _supported_symbol(symbol)
    if decision not in {"accepted", "rejected"}:
        raise SecFilingsError("review decision must be accepted or rejected")
    note = note.strip()
    if not note:
        raise SecFilingsError("review note must not be empty")
    filing = (
        db.query(SecFilingInventory)
        .filter(
            SecFilingInventory.symbol == normalized_symbol,
            SecFilingInventory.accession_number == accession_number,
        )
        .one_or_none()
    )
    if filing is None:
        raise SecFilingNotFoundError("SEC filing is not in this symbol's inventory; scan it first")
    if filing.review_status != "pending_review":
        raise SecFilingsError("SEC filing already has an immutable human review decision")
    filing.review_status = decision
    filing.human_review_note = note
    filing.reviewed_at = _require_aware_utc(reviewed_at or datetime.now(UTC))
    db.commit()
    db.refresh(filing)
    return filing


def create_sec_filing_inventory_table() -> None:
    """Create the inventory table and safely widen the first pre-review shape."""
    SecFilingInventory.__table__.create(bind=engine, checkfirst=True)
    # The project currently uses create_all rather than a general migration
    # framework. These two additions make the already-created first inventory
    # table compatible with the later human-review fields without touching
    # source metadata or existing pending rows.
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE sec_filing_inventory ALTER COLUMN accepted_at TYPE VARCHAR(40)")
        )
        connection.execute(
            text(
                "ALTER TABLE sec_filing_inventory "
                "ADD COLUMN IF NOT EXISTS human_review_note VARCHAR(700)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE sec_filing_inventory "
                "ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE"
            )
        )
        connection.execute(
            text("ALTER TABLE sec_filing_inventory DROP CONSTRAINT IF EXISTS ck_sec_filing_inventory_review_status")
        )
        connection.execute(
            text(
                "ALTER TABLE sec_filing_inventory "
                "ADD CONSTRAINT ck_sec_filing_inventory_review_status "
                "CHECK (review_status IN ('pending_review', 'accepted', 'rejected'))"
            )
        )


def configured_sec_user_agent() -> str:
    """Read the local project setting without overriding a process setting."""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    user_agent = os.getenv("SEC_EDGAR_USER_AGENT", "").strip()
    if not user_agent:
        raise SecFilingsError(
            "SEC_EDGAR_USER_AGENT must identify this application and a contact email address"
        )
    return user_agent


def canonical_sec_filing_url(*, cik: str, accession_number: str, primary_document: str) -> str:
    """Construct the only archive URL eligible for an inventoried filing."""
    if not re.fullmatch(r"\d{10}", cik):
        raise SecFilingsError("saved SEC filing has an invalid CIK")
    if not _SAFE_ACCESSION.fullmatch(accession_number):
        raise SecFilingsError("saved SEC filing has an invalid accession number")
    if not _SAFE_PRIMARY_DOCUMENT.fullmatch(primary_document):
        raise SecFilingsError("saved SEC filing has an invalid primary document")
    return f"{SEC_ARCHIVES_URL}/{int(cik)}/{accession_number.replace('-', '')}/{primary_document}"


def _supported_symbol(symbol: str) -> str:
    normalized_symbol = normalize_symbol(symbol)
    if not is_valid_symbol(normalized_symbol):
        raise SecFilingsError("symbol must contain 1-5 ASCII letters")
    if normalized_symbol not in SUPPORTED_SEC_TICKERS:
        raise SecFilingsError("SEC filing discovery currently supports AAPL, MSFT, GOOGL, AMZN, and NVDA")
    return normalized_symbol


def _cik_for_symbol(tickers: dict, symbol: str) -> str:
    for item in tickers.values():
        if not isinstance(item, dict):
            continue
        ticker = item.get("ticker")
        cik = item.get("cik_str")
        if ticker == symbol and isinstance(cik, int | str):
            try:
                return f"{int(cik):010d}"
            except (TypeError, ValueError) as exc:
                raise SecFilingsError("SEC ticker mapping is invalid") from exc
    raise SecFilingsError("SEC does not list this supported ticker")


def _recent_filings(payload: dict, cik: str) -> list[DiscoveredSecFiling]:
    recent = payload.get("filings", {}).get("recent")
    if not isinstance(recent, dict):
        raise SecFilingsError("SEC submissions response is missing recent filings")
    forms = recent.get("form")
    accession_numbers = recent.get("accessionNumber")
    filing_dates = recent.get("filingDate")
    acceptance_times = recent.get("acceptanceDateTime")
    primary_documents = recent.get("primaryDocument")
    if not all(isinstance(values, list) for values in (forms, accession_numbers, filing_dates, primary_documents)):
        raise SecFilingsError("SEC submissions response has invalid filing columns")
    if acceptance_times is None:
        acceptance_times = [None] * len(forms)
    if not isinstance(acceptance_times, list):
        raise SecFilingsError("SEC submissions response has invalid filing columns")
    if len({len(forms), len(accession_numbers), len(filing_dates), len(primary_documents), len(acceptance_times)}) != 1:
        raise SecFilingsError("SEC submissions response has inconsistent filing columns")

    filings: list[DiscoveredSecFiling] = []
    for form, accession, filed, accepted_at, primary_document in zip(
        forms, accession_numbers, filing_dates, acceptance_times, primary_documents, strict=True
    ):
        if form not in SUPPORTED_FORMS:
            continue
        if not all(isinstance(value, str) for value in (accession, filed, primary_document)):
            continue
        if not _SAFE_PRIMARY_DOCUMENT.fullmatch(primary_document):
            continue
        try:
            filed_at = date.fromisoformat(filed)
        except ValueError:
            continue
        accession_compact = accession.replace("-", "")
        if len(accession_compact) != 18 or not accession_compact.isdigit():
            continue
        if accepted_at is not None and not _is_supported_sec_acceptance_time(accepted_at):
            raise SecFilingsError("SEC submissions response has an invalid acceptance timestamp")
        filings.append(
            DiscoveredSecFiling(
                cik=cik,
                accession_number=accession,
                form=form,
                filed_at=filed_at,
                accepted_at=accepted_at,
                primary_document=primary_document,
                source_url=f"{SEC_ARCHIVES_URL}/{int(cik)}/{accession_compact}/{primary_document}",
            )
        )
        if len(filings) >= MAX_DISCOVERED_FILINGS:
            break
    return filings


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SecFilingsError("observed_at must include a timezone")
    return value.astimezone(UTC)


def _response_content_type(response: object) -> str | None:
    headers = getattr(response, "headers", None)
    get_content_type = getattr(headers, "get_content_type", None)
    if not callable(get_content_type):
        return None
    content_type = get_content_type()
    return content_type.casefold() if isinstance(content_type, str) else None


def _is_supported_sec_acceptance_time(value: object) -> bool:
    """Accept SEC's ISO-UTC field and the older compact EDGAR representation."""
    if not isinstance(value, str):
        return False
    if len(value) > 40:
        return False
    if re.fullmatch(r"\d{14}", value):
        try:
            datetime.strptime(value, "%Y%m%d%H%M%S")
        except ValueError:
            return False
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
