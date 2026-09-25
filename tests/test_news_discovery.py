from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import HTTPError

from app.news_discovery import (
    GdeltDocSource,
    SourceAttempt,
    TechCrunchRssSource,
    discover_news_candidates,
)


RUN_AT = datetime(2026, 9, 25, 2, 5, tzinfo=UTC)


def _gdelt_payload(articles: list[dict[str, str]]) -> bytes:
    import json

    return json.dumps({"articles": articles}).encode("utf-8")


def test_gdelt_filters_title_candidates_preserves_seen_time_and_never_claims_published_time():
    source = GdeltDocSource(
        http_get=lambda _url, _timeout: _gdelt_payload(
            [
                {
                    "title": "Apple shares gain after iPhone demand update",
                    "url": "https://news.example.test/apple?utm_source=feed&id=1",
                    "domain": "news.example.test",
                    "seendate": "20260925T010203Z",
                },
                {
                    "title": "Apple pie wins the county fair",
                    "url": "https://news.example.test/pie",
                    "domain": "news.example.test",
                    "seendate": "20260925T010203Z",
                },
            ]
        )
    )

    report = discover_news_candidates(symbols=("AAPL",), sources=(source,), discovered_at=RUN_AT)

    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.symbol == "AAPL"
    assert candidate.source_url == "https://news.example.test/apple?utm_source=feed&id=1"
    assert candidate.source_seen_at == datetime(2026, 9, 25, 1, 2, 3, tzinfo=UTC)
    assert candidate.published_at is None
    assert candidate.discovered_at == RUN_AT
    assert candidate.review_status == "candidate"


def test_deduplicates_tracking_url_variants_and_keeps_the_earliest_seen_time():
    source = GdeltDocSource(
        http_get=lambda _url, _timeout: _gdelt_payload(
            [
                {
                    "title": "NVIDIA introduces new CUDA tooling",
                    "url": "https://news.example.test/nvda?utm_campaign=a",
                    "domain": "news.example.test",
                    "seendate": "20260925T020000Z",
                },
                {
                    "title": "NVIDIA introduces new CUDA tooling",
                    "url": "https://news.example.test/nvda?fbclid=abc",
                    "domain": "news.example.test",
                    "seendate": "20260925T010000Z",
                },
            ]
        )
    )

    report = discover_news_candidates(symbols=("NVDA",), sources=(source,), discovered_at=RUN_AT)

    assert len(report.candidates) == 1
    assert report.candidates[0].source_url == "https://news.example.test/nvda?fbclid=abc"
    assert report.candidates[0].source_seen_at == datetime(2026, 9, 25, 1, tzinfo=UTC)


def test_http_failure_is_visible_and_does_not_stop_an_independent_source_or_symbol():
    def rate_limited(_url: str, _timeout: float) -> bytes:
        raise HTTPError(_url, 429, "Too Many Requests", {}, None)

    class UsefulFallback:
        name = "fixture_source"

        def discover(self, *, symbol: str, discovered_at: datetime) -> SourceAttempt:
            return SourceAttempt(source_name=self.name, symbol=symbol, status="empty", source_url="https://fixture.test")

    report = discover_news_candidates(
        symbols=("AAPL", "MSFT"),
        sources=(GdeltDocSource(http_get=rate_limited), UsefulFallback()),
        discovered_at=RUN_AT,
    )

    assert [(attempt.source_name, attempt.symbol, attempt.status) for attempt in report.attempts] == [
        ("gdelt_doc", "AAPL", "error"),
        ("gdelt_doc", "MSFT", "error"),
        ("fixture_source", "AAPL", "empty"),
        ("fixture_source", "MSFT", "empty"),
    ]
    assert all("HTTPError: HTTP Error 429" in attempt.detail for attempt in report.attempts[:2])


def test_techcrunch_rss_keeps_its_publisher_time_separate_from_seen_time():
    source = TechCrunchRssSource(
        http_get=lambda _url, _timeout: b"""<?xml version=\"1.0\"?><rss><channel><item>
        <title>Apple announces a developer update</title>
        <link>https://techcrunch.com/2026/09/25/apple-update/?utm_source=rss</link>
        <pubDate>Fri, 25 Sep 2026 01:00:00 +0000</pubDate>
        </item></channel></rss>"""
    )
    report = discover_news_candidates(
        symbols=("AAPL",), sources=(source,), discovered_at=RUN_AT
    )
    assert report.attempts[0].status == "success"
    candidate = report.candidates[0]
    assert candidate.outlet == "TechCrunch"
    assert candidate.source_seen_at is None
    assert candidate.published_at == datetime(2026, 9, 25, 1, tzinfo=UTC)
    assert candidate.source_url == "https://techcrunch.com/2026/09/25/apple-update/?utm_source=rss"


def test_techcrunch_rss_excludes_tagged_item_without_a_company_or_product_title_match():
    source = TechCrunchRssSource(
        http_get=lambda _url, _timeout: b"""<?xml version=\"1.0\"?><rss><channel>
        <item><title>TechCrunch Disrupt tickets are now available</title>
        <link>https://techcrunch.com/2026/09/25/disrupt/</link>
        <pubDate>Fri, 25 Sep 2026 01:00:00 +0000</pubDate></item>
        <item><title>NVIDIA announces a CUDA update</title>
        <link>https://techcrunch.com/2026/09/25/nvidia-cuda/</link>
        <pubDate>Fri, 25 Sep 2026 02:00:00 +0000</pubDate></item>
        </channel></rss>"""
    )
    report = discover_news_candidates(symbols=("NVDA",), sources=(source,), discovered_at=RUN_AT)
    assert report.attempts[0].status == "success"
    assert [candidate.headline for candidate in report.candidates] == ["NVIDIA announces a CUDA update"]
    assert report.candidates[0].matched_terms == ("nvidia", "cuda")


def test_cross_source_duplicate_prefers_direct_rss_published_metadata_over_gdelt_seen_time():
    gdelt = GdeltDocSource(
        http_get=lambda _url, _timeout: _gdelt_payload(
            [
                {
                    "title": "Apple announces a developer update",
                    "url": "https://techcrunch.com/2026/09/25/apple-update/?utm_source=gdelt",
                    "domain": "techcrunch.com",
                    "seendate": "20260925T000000Z",
                }
            ]
        )
    )
    techcrunch = TechCrunchRssSource(
        http_get=lambda _url, _timeout: b"""<?xml version=\"1.0\"?><rss><channel><item>
        <title>Apple announces a developer update</title>
        <link>https://techcrunch.com/2026/09/25/apple-update/?utm_source=rss</link>
        <pubDate>Fri, 25 Sep 2026 01:00:00 +0000</pubDate>
        </item></channel></rss>"""
    )
    report = discover_news_candidates(
        symbols=("AAPL",), sources=(gdelt, techcrunch), discovered_at=RUN_AT
    )
    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.source_name == "techcrunch_rss"
    assert candidate.published_at == datetime(2026, 9, 25, 1, tzinfo=UTC)
    assert candidate.source_seen_at is None


def test_candidates_are_newest_first_by_published_or_seen_time_with_undated_last():
    source = TechCrunchRssSource(
        http_get=lambda _url, _timeout: b"""<?xml version=\"1.0\"?><rss><channel>
        <item><title>Apple announces the older update</title><link>https://techcrunch.com/old/</link>
        <pubDate>Fri, 25 Sep 2026 01:00:00 +0000</pubDate></item>
        <item><title>Apple announces the newer update</title><link>https://techcrunch.com/new/</link>
        <pubDate>Fri, 25 Sep 2026 02:00:00 +0000</pubDate></item>
        <item><title>Apple announces an undated update</title><link>https://techcrunch.com/undated/</link></item>
        </channel></rss>"""
    )
    report = discover_news_candidates(symbols=("AAPL",), sources=(source,), discovered_at=RUN_AT)
    assert [candidate.headline for candidate in report.candidates] == [
        "Apple announces the newer update",
        "Apple announces the older update",
        "Apple announces an undated update",
    ]
