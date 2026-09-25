"""Bounded discovery of stock-news candidates from public metadata feeds.

This module intentionally retrieves only source metadata: headline, article
URL, outlet, and GDELT's *seen* timestamp.  It does not download article
pages, retain article body text, write to the database, call a model, or
change a forecast.  A candidate is a lead for a human review, not verified
evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


SUPPORTED_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_ABOUT_URL = "https://gdeltproject.org/about.html"
TECHCRUNCH_RSS_TERMS_URL = "https://techcrunch.com/rss-terms-of-use/"
TECHCRUNCH_RSS_BASE_URL = "https://techcrunch.com/tag"

# These are discovery terms, not a claim that every matching article is about
# the listed company.  Candidates remain review-pending by design.
_SYMBOL_QUERIES = {
    "AAPL": ("Apple", ("apple", "iphone", "ipad", "mac", "app store")),
    "MSFT": ("Microsoft", ("microsoft", "azure", "xbox", "windows", "linkedin")),
    "GOOGL": ("Google OR Alphabet", ("google", "alphabet", "youtube", "android", "waymo")),
    "AMZN": ("Amazon", ("amazon", "aws", "amazon.com", "prime video")),
    "NVDA": ("NVIDIA", ("nvidia", "geforce", "cuda", "jensen huang")),
}
_TRACKING_QUERY_PARAMETERS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
_OBVIOUS_FALSE_POSITIVES = {
    "AMZN": ("rainforest", "rain forest", "amazon river"),
    "AAPL": ("apple pie", "apple tree", "apple orchard"),
}
_TECHCRUNCH_TAGS = {"AAPL": "apple", "MSFT": "microsoft", "GOOGL": "google", "AMZN": "amazon", "NVDA": "nvidia"}

AttemptStatus = Literal["success", "empty", "error", "unsupported"]
HttpGet = Callable[[str, float], bytes]


@dataclass(frozen=True)
class NewsCandidate:
    """Metadata for one review-pending article candidate.

    ``published_at`` is deliberately ``None`` for GDELT DOC results because
    this endpoint's artlist response provides its own discovery/seen time,
    not a verified original publisher time.
    """

    id: str
    symbol: str
    headline: str
    source_url: str
    outlet: str
    source_name: str
    source_seen_at: datetime | None
    published_at: datetime | None
    discovered_at: datetime
    matched_terms: tuple[str, ...]
    dedup_key: str = field(repr=False, compare=False)
    review_status: Literal["candidate"] = "candidate"

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for field in ("source_seen_at", "published_at", "discovered_at"):
            value = payload[field]
            payload[field] = value.isoformat().replace("+00:00", "Z") if value else None
        payload["matched_terms"] = list(self.matched_terms)
        payload.pop("dedup_key")
        return payload


@dataclass(frozen=True)
class SourceAttempt:
    source_name: str
    symbol: str | None
    status: AttemptStatus
    source_url: str | None
    candidates: tuple[NewsCandidate, ...] = ()
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "source_name": self.source_name,
            "symbol": self.symbol,
            "status": self.status,
            "source_url": self.source_url,
            "candidate_count": len(self.candidates),
            "detail": self.detail,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class DiscoveryReport:
    discovered_at: datetime
    attempts: tuple[SourceAttempt, ...]
    candidates: tuple[NewsCandidate, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": "metadata_only_review_candidates",
            "discovered_at": self.discovered_at.isoformat().replace("+00:00", "Z"),
            "attempts": [attempt.as_dict() for attempt in self.attempts],
            "candidate_count": len(self.candidates),
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


class NewsDiscoverySource(Protocol):
    name: str

    def discover(self, *, symbol: str, discovered_at: datetime) -> SourceAttempt: ...


class GdeltDocSource:
    """GDELT DOC artlist metadata source with a deliberately small request cap."""

    name = "gdelt_doc"

    def __init__(self, *, timeout_seconds: float = 8.0, max_records: int = 10, http_get: HttpGet | None = None) -> None:
        if not 1 <= max_records <= 250:
            raise ValueError("max_records must be between 1 and 250")
        self.timeout_seconds = timeout_seconds
        self.max_records = max_records
        self._http_get = http_get or _default_http_get

    def discover(self, *, symbol: str, discovered_at: datetime) -> SourceAttempt:
        normalized_symbol = _validate_symbol(symbol)
        query, terms = _SYMBOL_QUERIES[normalized_symbol]
        request_url = self._request_url(query)
        try:
            payload = json.loads(self._http_get(request_url, self.timeout_seconds).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return SourceAttempt(
                source_name=self.name,
                symbol=normalized_symbol,
                status="error",
                source_url=request_url,
                detail=f"{type(exc).__name__}: {exc}",
            )

        articles = payload.get("articles")
        if not isinstance(articles, list):
            return SourceAttempt(
                source_name=self.name,
                symbol=normalized_symbol,
                status="error",
                source_url=request_url,
                detail="GDELT response has no articles list",
            )
        candidates = tuple(
            candidate
            for candidate in (
                _candidate_from_gdelt_article(
                    article=article,
                    symbol=normalized_symbol,
                    matched_terms=terms,
                    discovered_at=discovered_at,
                )
                for article in articles
            )
            if candidate is not None
        )
        return SourceAttempt(
            source_name=self.name,
            symbol=normalized_symbol,
            status="success" if candidates else "empty",
            source_url=request_url,
            candidates=candidates,
            detail=(
                "GDELT returned metadata only; original publisher time is unavailable in this response."
                if candidates
                else "No relevant metadata candidates after title-level filtering."
            ),
        )

    def _request_url(self, query: str) -> str:
        parameters = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(self.max_records),
            "timespan": "1d",
        }
        return f"{GDELT_DOC_URL}?{urlencode(parameters)}"


class TechCrunchRssSource:
    """TechCrunch's documented RSS tag feed, limited to candidate metadata."""

    name = "techcrunch_rss"

    def __init__(self, *, timeout_seconds: float = 15.0, http_get: HttpGet | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self._http_get = http_get or _default_http_get

    def discover(self, *, symbol: str, discovered_at: datetime) -> SourceAttempt:
        normalized_symbol = _validate_symbol(symbol)
        request_url = f"{TECHCRUNCH_RSS_BASE_URL}/{_TECHCRUNCH_TAGS[normalized_symbol]}/feed/"
        try:
            root = ElementTree.fromstring(self._http_get(request_url, self.timeout_seconds))
        except (HTTPError, URLError, TimeoutError, OSError, ElementTree.ParseError) as exc:
            return SourceAttempt(
                source_name=self.name,
                symbol=normalized_symbol,
                status="error",
                source_url=request_url,
                detail=f"{type(exc).__name__}: {exc}",
            )
        candidates = tuple(
            candidate
            for candidate in (
                _candidate_from_techcrunch_item(item=item, symbol=normalized_symbol, discovered_at=discovered_at)
                for item in root.findall("./channel/item")
            )
            if candidate is not None
        )
        return SourceAttempt(
            source_name=self.name,
            symbol=normalized_symbol,
            status="success" if candidates else "empty",
            source_url=request_url,
            candidates=candidates,
            detail=(
                "TechCrunch RSS metadata only; retain attribution and the original link for candidate display."
                if candidates
                else "No RSS items with both a title and HTTPS article link."
            ),
        )


def discover_news_candidates(
    *,
    symbols: Iterable[str] = SUPPORTED_SYMBOLS,
    sources: Iterable[NewsDiscoverySource] | None = None,
    discovered_at: datetime | None = None,
) -> DiscoveryReport:
    """Run every source/symbol attempt independently and return de-duplicated leads.

    One source or symbol failure is represented in the returned report and
    never prevents the remaining attempts from running.
    """

    run_at = _aware_utc(discovered_at or datetime.now(UTC), "discovered_at")
    normalized_symbols = tuple(_validate_symbol(symbol) for symbol in symbols)
    configured_sources = tuple(sources) if sources is not None else (TechCrunchRssSource(), GdeltDocSource())
    attempts: list[SourceAttempt] = []
    for source in configured_sources:
        for symbol in normalized_symbols:
            try:
                attempts.append(source.discover(symbol=symbol, discovered_at=run_at))
            except Exception as exc:  # Defensive boundary around independently configured sources.
                attempts.append(
                    SourceAttempt(
                        source_name=getattr(source, "name", type(source).__name__),
                        symbol=symbol,
                        status="error",
                        source_url=None,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
    return DiscoveryReport(
        discovered_at=run_at,
        attempts=tuple(attempts),
        candidates=tuple(_deduplicate_candidates(candidate for attempt in attempts for candidate in attempt.candidates)),
    )


def _candidate_from_gdelt_article(
    *, article: object, symbol: str, matched_terms: tuple[str, ...], discovered_at: datetime
) -> NewsCandidate | None:
    if not isinstance(article, dict):
        return None
    title = article.get("title")
    raw_url = article.get("url")
    if not isinstance(title, str) or not title.strip() or not isinstance(raw_url, str) or not raw_url.strip():
        return None
    dedup_key = _canonical_url(raw_url)
    if dedup_key is None or not _title_is_relevant(symbol=symbol, title=title, terms=matched_terms):
        return None
    source_url = raw_url
    outlet = article.get("domain")
    if not isinstance(outlet, str) or not outlet.strip():
        outlet = urlsplit(dedup_key).hostname or "unknown"
    seen_at = _parse_gdelt_seen_at(article.get("seendate"))
    matched = tuple(term for term in matched_terms if term in title.casefold())
    candidate_id = hashlib.sha256(f"gdelt_doc|{symbol}|{dedup_key}".encode("utf-8")).hexdigest()
    return NewsCandidate(
        id=candidate_id,
        symbol=symbol,
        headline=title,
        source_url=source_url,
        outlet=outlet.strip(),
        source_name="gdelt_doc",
        source_seen_at=seen_at,
        published_at=None,
        discovered_at=discovered_at,
        matched_terms=matched,
        dedup_key=dedup_key,
    )


def _candidate_from_techcrunch_item(
    *, item: ElementTree.Element, symbol: str, discovered_at: datetime
) -> NewsCandidate | None:
    title = item.findtext("title")
    raw_url = item.findtext("link")
    if not title or not raw_url:
        return None
    dedup_key = _canonical_url(raw_url)
    if dedup_key is None:
        return None
    terms = _SYMBOL_QUERIES[symbol][1]
    if not _title_is_relevant(symbol=symbol, title=title, terms=terms):
        return None
    source_url = raw_url
    published_at = _parse_rss_published_at(item.findtext("pubDate"))
    candidate_id = hashlib.sha256(f"techcrunch_rss|{symbol}|{dedup_key}".encode("utf-8")).hexdigest()
    return NewsCandidate(
        id=candidate_id,
        symbol=symbol,
        headline=title,
        source_url=source_url,
        outlet="TechCrunch",
        source_name="techcrunch_rss",
        source_seen_at=None,
        published_at=published_at,
        discovered_at=discovered_at,
        matched_terms=tuple(term for term in terms if term in title.casefold()),
        dedup_key=dedup_key,
    )


def _title_is_relevant(*, symbol: str, title: str, terms: tuple[str, ...]) -> bool:
    normalized = " ".join(title.casefold().split())
    if any(phrase in normalized for phrase in _OBVIOUS_FALSE_POSITIVES.get(symbol, ())):
        return False
    return any(term in normalized for term in terms)


def _deduplicate_candidates(candidates: Iterable[NewsCandidate]) -> list[NewsCandidate]:
    unique: dict[tuple[str, str], NewsCandidate] = {}
    for candidate in candidates:
        key = (candidate.symbol, candidate.dedup_key)
        previous = unique.get(key)
        if previous is None or _dedup_preference(candidate) < _dedup_preference(previous):
            unique[key] = candidate
    return sorted(
        unique.values(),
        key=lambda candidate: (candidate.symbol, *_candidate_display_order(candidate), candidate.dedup_key),
    )


def _dedup_preference(candidate: NewsCandidate) -> tuple[int, datetime]:
    """Keep direct publisher metadata over an aggregator's seen-time lead."""

    if candidate.published_at is not None:
        return (0, candidate.published_at)
    return (1, _sort_timestamp(candidate.source_seen_at))


def _candidate_display_order(candidate: NewsCandidate) -> tuple[int, int, int, int, int, int, int, int]:
    """Sort dated candidates newest-first, with records lacking a date last."""

    timestamp = candidate.published_at or candidate.source_seen_at
    if timestamp is None:
        return (1, 0, 0, 0, 0, 0, 0, 0)
    return (
        0,
        -timestamp.year,
        -timestamp.month,
        -timestamp.day,
        -timestamp.hour,
        -timestamp.minute,
        -timestamp.second,
        -timestamp.microsecond,
    )


def _sort_timestamp(value: datetime | None) -> datetime:
    return value or datetime.max.replace(tzinfo=UTC)


def _canonical_url(value: str) -> str | None:
    split = urlsplit(value.strip())
    if split.scheme != "https" or not split.netloc:
        return None
    kept_parameters = sorted(
        (key, item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_PARAMETERS
    )
    path = split.path.rstrip("/") or "/"
    return urlunsplit((split.scheme.casefold(), split.netloc.casefold(), path, urlencode(kept_parameters), ""))


def _parse_gdelt_seen_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def _parse_rss_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _default_http_get(url: str, timeout_seconds: float) -> bytes:
    request = Request(url, headers={"User-Agent": "MarketEvidenceAgent/0.1 metadata-only"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _validate_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized not in SUPPORTED_SYMBOLS:
        raise ValueError(f"unsupported symbol: {symbol}")
    return normalized


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
