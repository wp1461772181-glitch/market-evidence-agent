from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.agent_web_research import WebResearchArgs, extract_page, validate_public_url


def test_web_tool_accepts_one_query_or_https_url():
    assert WebResearchArgs(query_or_url="AAPL revenue outlook").query_or_url
    assert WebResearchArgs(query_or_url="https://example.com/report").query_or_url


@pytest.mark.parametrize("url", [
    "http://example.com/", "https://127.0.0.1/", "https://user@example.com/",
    "https://example.com:8443/", "https://localhost/", "file:///etc/passwd",
])
def test_public_url_validator_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_html_extractor_keeps_page_excerpt_and_does_not_invent_publish_time():
    html = "<html><head><title>Quarter results</title></head><body><script>ignore me</script><p>Revenue rose 12%.</p></body></html>"
    page = extract_page(html, "https://example.com/", datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert page["title"] == "Quarter results"
    assert "Revenue rose 12%." in page["excerpt"]
    assert "ignore me" not in page["excerpt"]
    assert page["published_at"] is None
    assert page["fetched_at"] == "2026-10-12T00:00:00+00:00"


def test_web_research_is_registered_as_one_read_only_agent_tool():
    from app.agent_tools import TOOL_REGISTRY

    assert "web_research" in TOOL_REGISTRY
    assert set(TOOL_REGISTRY["web_research"].args_schema.model_fields) == {"query_or_url"}


def test_literal_url_read_never_calls_search_backend(monkeypatch):
    import asyncio
    import app.agent_web_research as web
    async def forbidden(_query):raise AssertionError("Tavily search must not run")
    async def fetch(url,limit):
        assert url=="https://example.com/report"
        return url,"text/html",b"<html><title>Report</title><body><p>Report body.</p></body></html>"
    monkeypatch.setattr(web,"_search",forbidden)
    monkeypatch.setattr(web,"_get_public",fetch)
    result=asyncio.run(web._research("https://example.com/report"))
    assert result["ok"] and result["data"][0]["url"]=="https://example.com/report"


def test_web_citation_cap_never_leaks_uncited_excerpt():
    from app.agent_chat_stream import _number_web_sources
    citations=[{"ordinal":i,"source_type":"web","source_url":f"https://example.com/{i}","quote":"old"} for i in range(1,11)]
    result={"ok":True,"data":[{"url":"https://example.com/new","excerpt":"UNCITED_BODY_SENTINEL","title":"new"}]}
    _number_web_sources(result,citations)
    assert "UNCITED_BODY_SENTINEL" not in repr(result)
    assert result["error_code"]=="citation_budget_exhausted"
    assert len(citations)==10


def test_web_citation_history_restores_owned_snapshot_without_chunk_lookup(monkeypatch):
    from app.agent_chat_stream import _restore_citations

    def forbidden(*_args, **_kwargs):
        raise AssertionError("web citation must not query the knowledge chunk API")
    monkeypatch.setattr("app.knowledge.models.KnowledgeChunk", forbidden, raising=False)
    item={"ordinal": 1, "chunk_id": None, "document_id": None, "title": "Public report",
          "section": "", "source_url": "https://example.com/report", "quote": "Revenue grew 12%.",
          "observed_at": "2026-10-12T00:00:00+00:00", "published_at": None,
          "source_type": "web", "truncated": False}
    restored=_restore_citations([item], "owner-session")
    assert restored == [item]


def test_challenge_pages_are_reported_without_attempting_to_bypass_them():
    from app.agent_web_research import is_challenge_page

    assert is_challenge_page("<title>Verify you are human</title><p>Captcha required</p>")
    assert not is_challenge_page("<title>Annual report</title><p>Revenue increased.</p>")


def test_tavily_is_default_and_missing_key_does_not_fallback(monkeypatch):
    import asyncio
    from app.agent_web_research import _search
    monkeypatch.delenv("WEB_RESEARCH_BACKEND", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    async def run():
        with pytest.raises(ValueError, match="search_backend_key_missing"):
            await _search("AAPL revenue")
    asyncio.run(run())


def test_rfc2822_publisher_time_is_normalized_and_future_time_rejected():
    from app.agent_web_research import _valid_timestamp
    assert _valid_timestamp("Tue, 14 Jul 2026 00:00:00 GMT") == "2026-07-14T00:00:00+00:00"
    assert _valid_timestamp("Tue, 14 Jul 2099 00:00:00 GMT") is None


def test_tavily_json_null_does_not_become_none_evidence(monkeypatch):
    import asyncio
    import httpx
    import app.agent_chat_api as chat_api
    from app.agent_web_research import _search
    monkeypatch.setenv("TAVILY_API_KEY", "unit-test-only")
    monkeypatch.delenv("WEB_RESEARCH_BACKEND", raising=False)
    payload={"results": [{"url": None, "title": None, "content": None, "raw_content": None,
                           "published_date": "Tue, 14 Jul 2026 00:00:00 GMT"}]}
    async def handler(_request): return httpx.Response(200, json=payload)
    monkeypatch.setattr(chat_api, "PinnedPublicTransport", lambda **_kwargs: httpx.MockTransport(handler))
    rows=asyncio.run(_search("AMD news"))
    assert rows == [{"url": "", "title": "", "snippet": "", "raw_content": "",
                     "published_at": "2026-07-14T00:00:00+00:00"}]


def test_public_fetch_validates_every_redirect_hop_before_requesting(monkeypatch):
    import asyncio
    import httpx
    import app.agent_chat_api as chat_api
    from app.agent_web_research import _get_public

    requests=[]
    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={"location":"https://127.0.0.1/private"})
    monkeypatch.setattr(chat_api,"PinnedPublicTransport",lambda **_kwargs:httpx.MockTransport(handler))
    async def run():
        with pytest.raises(ValueError,match="unsafe_public_url"):
            await _get_public("https://example.com/start",limit=100)
    asyncio.run(run())
    assert requests==["https://example.com/start"]


def test_public_fetch_enforces_response_byte_limit(monkeypatch):
    import asyncio
    import httpx
    import app.agent_chat_api as chat_api
    from app.agent_web_research import _get_public

    async def handler(_request):return httpx.Response(200,headers={"content-type":"text/html"},content=b"12345")
    monkeypatch.setattr(chat_api,"PinnedPublicTransport",lambda **_kwargs:httpx.MockTransport(handler))
    async def run():
        with pytest.raises(ValueError,match="response_too_large"):
            await _get_public("https://example.com/",limit=4)
    asyncio.run(run())


def test_public_fetch_never_replays_cookies_from_page_responses(monkeypatch):
    import asyncio
    import httpx
    import app.agent_chat_api as chat_api
    from app.agent_web_research import _get_public

    seen=[]
    async def handler(request):
        seen.append(request.headers.get("cookie"))
        if len(seen)==1:return httpx.Response(302,headers={"location":"https://example.com/final","set-cookie":"session=private"})
        return httpx.Response(200,headers={"content-type":"text/html"},content=b"<p>Public</p>")
    monkeypatch.setattr(chat_api,"PinnedPublicTransport",lambda **_kwargs:httpx.MockTransport(handler))
    async def run():await _get_public("https://example.com/start",limit=100)
    asyncio.run(run())
    assert seen==[None,None]
