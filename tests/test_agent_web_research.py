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


def test_oversized_search_response_retries_without_raw_content_instead_of_failing(monkeypatch):
    """S5 live acceptance: one long article makes Tavily's raw_content larger than the response
    cap (195 kB for 'Apple revenue news'), so the whole search used to fail as
    web_research_unavailable.  The retry asks for snippets only; each page is then read through the
    normal bounded public fetch."""
    import asyncio
    import app.agent_web_research as web
    asked = []

    async def search(query, include_raw_content=True):
        asked.append(include_raw_content)
        if include_raw_content:
            raise ValueError("response_too_large")
        return [{"url": "https://example.com/apple", "title": "Apple revenue", "snippet": "Apple revenue rose.",
                 "raw_content": "", "published_at": "2026-10-12T00:00:00+00:00"}]

    async def fetch(url, limit):
        return url, "text/html", b"<html><title>Apple revenue</title><body><p>Apple revenue rose 6%.</p></body></html>"

    monkeypatch.setattr(web, "_search", search)
    monkeypatch.setattr(web, "_get_public", fetch)
    result = asyncio.run(web._research("Apple revenue news"))
    assert result["ok"] and result["data"][0]["url"] == "https://example.com/apple"
    assert "Apple revenue rose 6%." in result["data"][0]["excerpt"]
    assert result["data"][0]["body_provenance"] == "public_page_fetch"
    assert asked == [True, False]


def test_other_search_failures_are_not_retried(monkeypatch):
    """GUARD: only the size failure has a degraded retry; an unavailable backend stays one call."""
    import asyncio
    import app.agent_web_research as web
    asked = []

    async def search(query, include_raw_content=True):
        asked.append(include_raw_content)
        raise ValueError("search_backend_unavailable")

    monkeypatch.setattr(web, "_search", search)
    with pytest.raises(ValueError, match="search_backend_unavailable"):
        asyncio.run(web._research("Apple revenue news"))
    assert asked == [True]


def test_search_can_ask_tavily_for_snippets_only(monkeypatch):
    """The degraded request really leaves raw content out, and keeps the news topic and bounds."""
    import asyncio
    import json
    import httpx
    import app.agent_chat_api as chat_api
    from app.agent_web_research import _search
    monkeypatch.setenv("TAVILY_API_KEY", "unit-test-only")
    monkeypatch.delenv("WEB_RESEARCH_BACKEND", raising=False)
    bodies = []

    async def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(chat_api, "PinnedPublicTransport", lambda **_kwargs: httpx.MockTransport(handler))
    asyncio.run(_search("AMD news"))
    asyncio.run(_search("AMD news", include_raw_content=False))
    assert [body["include_raw_content"] for body in bodies] == [True, False]
    assert all(body["topic"] == "news" and body["max_results"] == 5 for body in bodies)


# --- S6: relevance-based excerpt ----------------------------------------------------------------
# Live S5 failure: the tool fetches up to MAX_EXCERPT characters of page text, but the chat layer
# cuts every string field to its first 700 characters by position, so the sentence that names the
# requested metric never reached the evidence gate.  The tool now builds the (<= 700 character)
# excerpt from the passages that match the query; the gate itself is unchanged.

LEAD = "Cupertino hosted its annual developer event on Thursday."
APPLE_HIT = "Apple said revenue rose 6% to $94.9 billion in the quarter, led by services."


def _filler(index):
    return f"Paragraph {index} describes venue logistics, parking and session schedules for attendees."


def _long_text(hit, *, before=30, after=8):
    return " ".join([LEAD, *(_filler(i) for i in range(before)), hit, *(_filler(100 + i) for i in range(after))])


def _article_html(title, hit, *, before=30, after=8):
    paragraphs = [LEAD, *(_filler(i) for i in range(before)), hit, *(_filler(100 + i) for i in range(after))]
    return f"<html><head><title>{title}</title></head><body>" + "".join(f"<p>{p}</p>" for p in paragraphs) + "</body></html>"


def _fake_web(monkeypatch, rows, pages=None):
    import app.agent_web_research as web
    pages = pages or {}

    async def search(_query, include_raw_content=True):
        return rows

    async def fetch(url, limit):
        body = pages[url]
        if isinstance(body, Exception):
            raise body
        return url, "text/html", body.encode()

    monkeypatch.setattr(web, "_search", search)
    monkeypatch.setattr(web, "_get_public", fetch)
    return web


def _row(url, title, *, raw="", snippet="A search snippet that is only a lead."):
    return {"url": url, "title": title, "snippet": snippet, "raw_content": raw,
            "published_at": "2026-10-12T00:00:00+00:00"}


def _pieces_are_verbatim(excerpt, text):
    return all(piece in text for piece in excerpt.split(" … "))


def test_focus_excerpt_keeps_the_sentence_that_names_the_requested_metric():
    from app.agent_web_research import focus_excerpt
    text = _long_text(APPLE_HIT)
    assert text.index(APPLE_HIT) > 1500 and len(text) > 2000
    out = focus_excerpt(text, "Apple revenue news")
    assert len(out) <= 700
    assert APPLE_HIT in out
    assert out.startswith(LEAD)                      # the first sentence is kept while the budget allows
    assert out.index(LEAD) < out.index(APPLE_HIT)    # original order
    assert " … " in out                              # non-adjacent passages are marked, not glued
    assert _pieces_are_verbatim(out, text)


def test_focus_excerpt_is_unchanged_when_nothing_matches_or_no_term_is_usable():
    from app.agent_web_research import focus_excerpt
    text = _long_text("The keynote closed with a demonstration of new camera hardware.")
    assert focus_excerpt(text, "Apple revenue news") == text      # no sentence names Apple or revenue
    assert focus_excerpt(text, "latest news") == text             # only generic words: no usable term
    chatter = _long_text("The latest news from the venue is that parking is full.")
    assert focus_excerpt(chatter, "latest news") == chatter       # ... even when the text uses those words
    cn_chatter = _long_text("会场外的新闻报道称停车位已经满了。")
    assert focus_excerpt(cn_chatter, "最新 新闻报道") == cn_chatter
    assert focus_excerpt(cn_chatter, "新闻报道") == cn_chatter
    assert focus_excerpt(text, "") == text
    assert focus_excerpt("", "Apple revenue") == ""
    short = "Apple revenue rose 6%."
    assert focus_excerpt(short, "Apple revenue") == short         # already within the bound


def test_focus_excerpt_matches_case_insensitively_and_by_metric_synonym_group():
    from app.agent_web_research import focus_excerpt
    upper = _long_text("APPLE REPORTED REVENUE OF $94.9 BILLION.")
    assert "APPLE REPORTED REVENUE OF $94.9 BILLION." in focus_excerpt(upper, "apple revenue")
    # The evidence gate treats revenue / sales / 营收 / 收入 / 营业收入 as one group; so does the excerpt.
    sales = _long_text("Apple's net sales were $94.9 billion for the September quarter.")
    assert "Apple's net sales were $94.9 billion" in focus_excerpt(sales, "Apple revenue news")
    cjk_hit = "苹果公司本季度营业收入同比增长6%，达到949亿美元。"
    assert cjk_hit in focus_excerpt(_long_text(cjk_hit), "Apple revenue news")
    eps = _long_text("Apple's earnings per share came in at $1.64.")
    assert "Apple's earnings per share came in at $1.64." in focus_excerpt(eps, "Apple EPS")


def test_focus_excerpt_follows_the_gate_when_one_phrase_asks_for_two_metric_groups():
    """'earnings per share' makes the gate require both the profit and the EPS group."""
    from app.agent_web_research import focus_excerpt
    profit = "Apple's net income was $23.6 billion for the quarter."
    eps = "Apple's EPS came in at $1.64 for the period."
    text = " ".join(["Opening remarks came first.", *(_filler(i) for i in range(14)), profit,
                     *(_filler(50 + i) for i in range(14)), eps, *(_filler(90 + i) for i in range(6))])
    out = focus_excerpt(text, "Apple earnings per share", limit=250)
    assert len(out) <= 250 and profit in out and eps in out


def test_focus_excerpt_understands_cjk_terms_with_and_without_spaces():
    from app.agent_web_research import focus_excerpt
    lead = "苹果公司周四在库比蒂诺举行年度开发者活动。"
    filler = [f"第{i}段介绍了活动场地、停车安排和各场分会的日程安排，与财务数据无关。" for i in range(30)]
    hit = "苹果公司本季度营收同比增长6%，达到949亿美元，服务业务贡献明显。"
    text = lead + "".join(filler) + hit + "".join(f"补充{i}段继续介绍会场的其他安排。" for i in range(8))
    assert text.index(hit) > 1000
    for query in ("苹果 营收 新闻", "苹果营收新闻", "帮我找一下跟苹果营收相关的新闻报道"):
        out = focus_excerpt(text, query)
        assert hit in out, query
        assert len(out) <= 700 and out.startswith(lead) and _pieces_are_verbatim(out, text)
    assert hit in focus_excerpt(text, "AAPL revenue")             # ticker and metric words in another script


def test_focus_excerpt_covers_every_requested_term_before_it_fills_the_budget():
    from app.agent_web_research import focus_excerpt
    sentences = ["Apple opened the developer conference in Cupertino."]
    sentences += [f"Apple engineers presented session {i} about tooling and the new SDK." for i in range(12)]
    revenue_only = "Revenue grew 6% year over year, the company said."
    sentences.append(revenue_only)
    text = " ".join(sentences)
    out = focus_excerpt(text, "Apple revenue", limit=200)
    assert len(out) <= 200 and revenue_only in out and "Apple" in out
    assert _pieces_are_verbatim(out, text)


def test_focus_excerpt_prefers_the_passage_with_more_distinct_query_terms():
    from app.agent_web_research import focus_excerpt
    single = "Revenue was discussed briefly."
    double = "Apple revenue rose 6% on iPhone demand."
    text = " ".join(["Opening remarks came first.", single, "Unrelated filler about venue logistics here.",
                     double, "Closing remarks ended the call."])
    out = focus_excerpt(text, "Apple revenue", limit=len(double) + len("Opening remarks came first.") + 6)
    assert double in out and single not in out


def test_focus_excerpt_keeps_the_first_sentence_ahead_of_further_matches():
    from app.agent_web_research import focus_excerpt
    hit = "Revenue grew 6% year over year."
    text = " ".join([LEAD, *(f"Apple engineer {i} presented a session." for i in range(20)), hit])
    out = focus_excerpt(text, "Apple revenue", limit=170)
    assert len(out) <= 170 and out.startswith(LEAD) and hit in out


def test_focus_excerpt_does_not_cut_a_requested_term_in_two():
    from app.agent_web_research import focus_excerpt
    cjk = "介" * 347 + "营收增长明显" + "安" * 1500      # the hard cut would fall between 营 and 收
    out = focus_excerpt(cjk, "营收")
    assert len(out) <= 700 and "营收增长明显" in out
    latin = "Apple " + "ab " * 113 + "net income rose 6% in the period " + "cd " * 400   # a space cut would split the phrase
    out = focus_excerpt(latin, "Apple net income")
    assert len(out) <= 700 and "net income rose 6%" in out


def test_focus_excerpt_fills_the_budget_by_score_before_it_adds_plain_text():
    from app.agent_web_research import focus_excerpt
    hit, extra = "Apple revenue rose 6%.", "Apple also opened a new store."
    text = " ".join([LEAD, *(f"Filler line {i}." for i in range(10)), hit, *(f"Filler line {20 + i}." for i in range(5)),
                     extra, *(f"Filler line {40 + i}." for i in range(5))])
    out = focus_excerpt(text, "Apple revenue", limit=150)
    assert len(out) <= 150 and out.startswith(LEAD) and hit in out and extra in out


def test_focus_excerpt_expands_company_names_to_their_aliases_and_ticker():
    from app.agent_web_research import focus_excerpt
    english = "Apple opened a new store in Paris."
    chinese = "苹果公司在巴黎新开了一家零售店。"
    for hit, query in ((english, "AAPL news"), (chinese, "Apple news"), (english, "苹果 新闻")):
        out = focus_excerpt(_long_text(hit), query)
        assert len(out) <= 700 and hit in out, query


def test_focus_excerpt_short_terms_match_whole_words_only():
    from app.agent_web_research import focus_excerpt
    steps, real = "Epsilon Systems hired new engineers.", "EPS came in at $1.64."
    text = " ".join(["Opening line.", steps, *(f"Filler {i}." for i in range(30)), real])
    out = focus_excerpt(text, "EPS", limit=60)
    assert real in out and steps not in out and len(out) <= 60


def test_focus_excerpt_never_exceeds_the_bound_and_never_invents_text():
    from app.agent_web_research import focus_excerpt
    cases = {
        "one unpunctuated line": " ".join(["alpha"] * 700) + " revenue " + " ".join(["omega"] * 300),
        "cjk without punctuation": "介绍" * 800 + "营收增长" + "安排" * 300,
        "many short lines": "\n".join(("Apple revenue line %d" % i) if i % 7 == 0 else f"line {i}" for i in range(900)),
        "every sentence matches": " ".join(f"Apple revenue statement {i} was published today." for i in range(120)),
        "one huge matching sentence": "Apple " + "growth " * 400 + "revenue " + "growth " * 100,
        # G2-B1: the company-beside-the-metric rule must keep both properties as well
        "another company's revenue sentence beside an Apple lead": " ".join(
            [APPLE_LEAD, SAMSUNG, SUBJECTLESS, *(_filler(i) for i in range(40))]),
        "metric sentences that each need their neighbour": " ".join(
            f"Apple chief executive spoke on call {i}. The company's revenue rose {i}% in the period." for i in range(60)),
        "a metric sentence first, the company after it": " ".join(
            [SUBJECTLESS, APPLE_LEAD, *(_filler(i) for i in range(40))]),
    }
    for name, text in cases.items():
        for limit in (700, 300, 90):
            out = focus_excerpt(text, "Apple revenue news", limit=limit)
            assert 0 < len(out) <= limit, (name, limit, len(out))
            assert _pieces_are_verbatim(out, text), (name, limit)


def test_focus_excerpt_term_groups_are_literally_the_ones_the_evidence_gate_uses():
    """Drift guard: the synonym and entity-alias tables are read out of the gate's own source
    (the gate is not edited); a change on either side makes this test fail."""
    import ast
    import inspect
    import textwrap
    from app.agent_web_research import ENTITY_ALIASES, METRIC_GROUPS
    from app.agent_workflow.graph import deterministic_confidence_gate

    tree = ast.parse(textwrap.dedent(inspect.getsource(deterministic_confidence_gate)))
    literals = {target.id: ast.literal_eval(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name) and target.id in {"metric_groups", "web_aliases"}}
    assert METRIC_GROUPS == literals["metric_groups"]
    assert ENTITY_ALIASES == literals["web_aliases"]


def test_web_research_returns_the_matching_passages_not_the_leading_characters(monkeypatch):
    import asyncio
    url = "https://example.com/apple-results"
    web = _fake_web(monkeypatch, [_row(url, "Apple results: a closer look")],
                    {url: _article_html("Apple results: a closer look", APPLE_HIT)})
    result = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))
    row = result["data"][0]
    assert result["ok"] and row["body_provenance"] == "public_page_fetch"
    assert len(row["excerpt"]) <= 700
    assert APPLE_HIT in row["excerpt"] and row["excerpt"].startswith(LEAD)
    assert row["truncated"] is True                  # the excerpt is a selection, not the whole page


def test_web_research_applies_the_same_selection_to_extracted_and_fallback_content(monkeypatch):
    import asyncio
    raw_text = _long_text(APPLE_HIT)
    extracted, broken = "https://example.com/extracted", "https://example.com/broken"
    web = _fake_web(
        monkeypatch,
        [_row(extracted, "Extracted", raw=raw_text), _row(broken, "Broken page", raw=raw_text)],
        {extracted: _article_html("Extracted", "Unrelated filler only."), broken: ValueError("upstream_unavailable")})
    rows = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))["data"]
    assert [row["body_provenance"] for row in rows] == ["tavily_extracted_content"] * 2
    for row in rows:
        assert len(row["excerpt"]) <= 700 and APPLE_HIT in row["excerpt"] and row["truncated"] is True


def test_web_research_leaves_a_page_without_any_match_exactly_as_before(monkeypatch):
    import asyncio
    from app.agent_web_research import MAX_EXCERPT
    url = "https://example.com/camera"
    html = _article_html("Camera hardware", "The keynote closed with a demonstration of new camera hardware.")
    web = _fake_web(monkeypatch, [_row(url, "Camera hardware")], {url: html})
    row = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))["data"][0]
    assert 2000 < len(row["excerpt"]) <= MAX_EXCERPT
    assert row["excerpt"].startswith(LEAD)
    assert row["excerpt"] == web.extract_page(html, url, datetime(2026, 10, 12, tzinfo=timezone.utc))["excerpt"]


def test_web_research_exact_url_read_keeps_the_leading_characters(monkeypatch):
    import asyncio
    url = "https://example.com/apple-results"
    web = _fake_web(monkeypatch, [], {url: _article_html("Apple results", APPLE_HIT)})
    row = asyncio.run(web.web_research.coroutine(query_or_url=url))["data"][0]
    assert len(row["excerpt"]) > 2000 and row["excerpt"].startswith(LEAD)
    assert APPLE_HIT in row["excerpt"]               # still inside the page text; nothing was focused


def test_revenue_sentence_survives_bounding_and_the_unchanged_gate_accepts_it(monkeypatch):
    """The live S5 failure end to end at the unit level: three long pages whose revenue sentence is
    far from the top.  Bounded exactly as the chat layer bounds it, the unchanged evidence gate
    must see the metric (before the fix: requested_metric_missing:revenue)."""
    import asyncio
    from app.agent_chat_stream import bounded_tool_result
    from app.agent_workflow.graph import deterministic_confidence_gate
    urls = [f"https://news.example.com/apple-{n}" for n in (1, 2, 3)]
    rows = [_row(url, f"Apple headline {n}", snippet="s" * 900) for n, url in enumerate(urls, 1)]
    pages = {url: _article_html(f"Apple headline {n}", APPLE_HIT, before=25 + n) for n, url in enumerate(urls, 1)}
    web = _fake_web(monkeypatch, rows, pages)
    result = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))
    bounded = bounded_tool_result(result)
    assert all(APPLE_HIT in row["excerpt"] for row in bounded["data"]) and len(bounded["data"]) == 3
    assert deterministic_confidence_gate("Apple revenue news", bounded, [], ["AAPL"]) == (True, "heuristic_coverage_pass")


# --- G2-B1 / item 8: a metric sentence needs the company next to it ------------------------------
# The gate pools the text of all rows and asks for the metric and for the company anywhere in it,
# so an excerpt made of "an Apple headline" plus "the company's revenue rose 20%" (a sentence
# that, on the page, followed a sentence about another company) passed as Apple revenue.  The
# excerpt is therefore chosen so that, when the query names a company and a metric, a metric
# sentence is only shown together with a sentence that names the company.

APPLE_LEAD = "Apple unveiled its new iPhone lineup at an event in Cupertino on Tuesday."
SAMSUNG = "Samsung said on Wednesday that it would raise prices for its Galaxy phones next year."
SUBJECTLESS = "The company's revenue rose 20% year over year, helped by strong demand and pricing."
MENTION = "Apple chief executive Tim Cook spoke to analysts after the close on Thursday."
METRIC_NEXT = "The company's revenue rose 6% to $94.9 billion in the quarter, led by services."


def _fillers(count, start=0):
    return [_filler(start + index) for index in range(count)]


def test_focus_excerpt_does_not_select_a_metric_sentence_that_follows_another_companys_sentence():
    """RED (G2 item 8): Apple lead, a Samsung sentence, then 'the company's revenue rose 20%'.  The
    revenue sentence belongs to Samsung's paragraph; it must not be shown as Apple's."""
    from app.agent_web_research import focus_excerpt
    text = " ".join([APPLE_LEAD, SAMSUNG, SUBJECTLESS, *_fillers(30)])
    out = focus_excerpt(text, "Apple revenue news")
    assert "revenue rose 20%" not in out
    assert out.startswith(APPLE_LEAD) and len(out) <= 700 and _pieces_are_verbatim(out, text)


def test_focus_excerpt_keeps_a_metric_sentence_together_with_the_sentence_that_names_the_company():
    """RED: the sentence before the metric sentence names Apple.  It is eligible, and the sentence
    before it is carried along (verbatim, adjacent) even though plain fill would not reach it."""
    from app.agent_web_research import focus_excerpt
    apples = [f"Apple engineers presented session {i} about tooling and the new SDK." for i in range(14)]
    text = " ".join([LEAD, *apples, *_fillers(10), MENTION, METRIC_NEXT, *_fillers(8, 100)])
    out = focus_excerpt(text, "Apple revenue news")
    assert f"{MENTION} {METRIC_NEXT}" in out
    assert len(out) <= 700 and _pieces_are_verbatim(out, text)


def test_focus_excerpt_fill_never_adds_a_metric_sentence_without_the_company_beside_it():
    """RED: many metric sentences about other companies; the budget is large enough for all of
    them, and none is added by the fill phase."""
    from app.agent_web_research import focus_excerpt
    others = [f"Samsung revenue rose {n}% in the period, the company said." for n in range(8, 20)]
    text = " ".join([APPLE_LEAD, *_fillers(1), *others, *_fillers(30, 50)])
    out = focus_excerpt(text, "Apple revenue news")
    assert not any(sentence in out for sentence in others)
    assert out.startswith(APPLE_LEAD) and _pieces_are_verbatim(out, text)


def test_focus_excerpt_a_metric_sentence_that_names_the_company_itself_needs_no_neighbour():
    """GUARD: the existing case, kept (also with the company only as a ticker)."""
    from app.agent_web_research import focus_excerpt
    for hit, query in ((APPLE_HIT, "Apple revenue news"), ("AAPL revenue rose 6% in the quarter.", "Apple revenue"),
                       ("苹果公司本季度营收同比增长6%，达到949亿美元。", "Apple revenue")):
        text = _long_text(hit)
        assert hit in focus_excerpt(text, query), (hit, query)


def test_focus_excerpt_query_without_a_company_or_without_a_metric_is_unchanged():
    """GUARD: the neighbour rule needs a company AND a metric in the query."""
    from app.agent_web_research import focus_excerpt
    text = " ".join([LEAD, *_fillers(30), SUBJECTLESS, *_fillers(8, 100)])
    assert SUBJECTLESS in focus_excerpt(text, "revenue news")          # no company in the query
    assert SUBJECTLESS in focus_excerpt(text, "Samsung revenue")       # a company the gate has no alias for
    text = " ".join([LEAD, *_fillers(30), "Apple opened a new store in Paris.", *_fillers(8, 100)])
    assert "Apple opened a new store in Paris." in focus_excerpt(text, "Apple news")   # no metric in the query


def test_a_subjectless_revenue_sentence_beside_another_company_no_longer_passes_the_gate(monkeypatch):
    """RED, end to end with the unchanged gate: before, the excerpt held the Samsung paragraph's
    revenue sentence and the pooled-text gate called it Apple revenue evidence."""
    import asyncio
    from app.agent_chat_stream import bounded_tool_result
    from app.agent_workflow.graph import deterministic_confidence_gate
    url = "https://news.example.com/roundup"
    paragraphs = [APPLE_LEAD, SAMSUNG, SUBJECTLESS, *_fillers(30)]
    page = ("<html><head><title>Phone roundup</title></head><body>"
            + "".join(f"<p>{text}</p>" for text in paragraphs) + "</body></html>")
    web = _fake_web(monkeypatch, [_row(url, "Phone roundup")], {url: page})
    bounded = bounded_tool_result(asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news")))
    assert "revenue rose 20%" not in bounded["data"][0]["excerpt"]
    assert deterministic_confidence_gate("Apple revenue news", bounded, [], ["AAPL"]) == (
        False, "requested_metric_missing:revenue")


def test_focus_excerpt_never_keeps_a_leading_metric_sentence_that_names_no_company():
    """RED: the first sentence is normally kept as the lead; a metric sentence about nobody is not
    a lead.  Nothing precedes it, so no neighbour can make it eligible."""
    from app.agent_web_research import focus_excerpt
    text = " ".join([SUBJECTLESS, APPLE_LEAD, *_fillers(30)])
    out = focus_excerpt(text, "Apple revenue news")
    assert "revenue rose 20%" not in out and APPLE_LEAD in out


def test_focus_excerpt_is_empty_when_every_sentence_is_a_metric_sentence_about_nobody():
    """A page made only of revenue statements that never name the company has nothing that can be
    shown as that company's: the excerpt is empty (the row is then dropped as without excerpt),
    never the leading characters."""
    from app.agent_web_research import _focus_source, focus_excerpt
    text = " ".join(f"The company's revenue rose {i}% year over year." for i in range(60))
    assert len(text) > 700 and focus_excerpt(text, "Apple revenue news") == ""
    source = {"excerpt": text}
    _focus_source(source, "Apple revenue news")
    assert source == {"excerpt": "", "truncated": True}


def test_focus_excerpt_metric_sentences_always_come_with_the_company_beside_them():
    """Property (seeded): over random pages of Apple sentences, subjectless revenue sentences,
    other companies' revenue sentences and filler, every metric sentence that is shown either names
    Apple or follows a sentence that does, and in the second case that sentence is shown right
    before it.  Output stays within the bound and made of verbatim pieces."""
    import random
    from app.agent_web_research import focus_excerpt
    pools = {
        "apple": [f"Apple engineers presented session {i} about tooling and the new SDK." for i in range(40)],
        "apple_metric": [f"Apple revenue rose {i}% in the period, the company said." for i in range(40)],
        "subjectless": [f"The company's revenue rose {i}% year over year on strong demand." for i in range(40)],
        "other": [f"Samsung said revenue fell {i}% in the quarter on weak phone demand." for i in range(40)],
        "filler": [f"Paragraph {i} describes venue logistics, parking and schedules for attendees." for i in range(40)],
    }
    for seed in range(120):
        rng = random.Random(seed)
        kinds = [rng.choice(list(pools)) for _ in range(rng.randint(10, 80))]
        used = {kind: iter(rng.sample(pool, len(pool))) for kind, pool in pools.items()}
        sentences = [next(used[kind]) for kind in kinds]
        text = " ".join(sentences)
        for limit in (700, 300, 120):
            out = focus_excerpt(text, "Apple revenue news", limit=limit)
            assert len(out) <= max(limit, len(text) if len(text) <= limit else 0)
            assert _pieces_are_verbatim(out, text), (seed, limit)
            if len(text) <= limit:
                # A page that fits is held to the same rule (2026-10-14): only a metric sentence that
                # has no company beside it is left out; every other sentence stays, as it was.
                assert out == text or len(out) < len(text)
            for index, (kind, sentence) in enumerate(zip(kinds, sentences)):
                if kind not in {"subjectless", "other", "apple_metric"} or sentence not in out:
                    continue
                if kind == "apple_metric":
                    continue                                      # names the company itself
                assert index > 0 and kinds[index - 1] in {"apple", "apple_metric"}, (seed, limit, sentence)
                assert f"{sentences[index - 1]} {sentence}" in out, (seed, limit, sentence)


# --- G2-B1: the truncated flag of a fallback row is computed before the cut ----------------------

def test_search_says_when_it_cut_the_raw_content_of_a_result(monkeypatch):
    """RED: _search keeps MAX_EXCERPT characters of raw_content and the caller could no longer
    tell whether anything was cut (len(raw) > MAX_EXCERPT is false after the cut)."""
    import asyncio
    import httpx
    import app.agent_chat_api as chat_api
    from app.agent_web_research import MAX_EXCERPT, _search
    monkeypatch.setenv("TAVILY_API_KEY", "unit-test-only")
    monkeypatch.delenv("WEB_RESEARCH_BACKEND", raising=False)
    payload = {"results": [
        {"url": "https://example.com/long", "title": "Long", "content": "s", "raw_content": "a" * (MAX_EXCERPT + 700)},
        {"url": "https://example.com/exact", "title": "Exact", "content": "s", "raw_content": "b" * MAX_EXCERPT},
        {"url": "https://example.com/short", "title": "Short", "content": "s", "raw_content": "c" * 300}]}

    async def handler(_request):
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(chat_api, "PinnedPublicTransport", lambda **_kwargs: httpx.MockTransport(handler))
    long_row, exact_row, short_row = asyncio.run(_search("Apple news"))
    assert len(long_row["raw_content"]) == MAX_EXCERPT and long_row["raw_content_truncated"] is True
    assert "raw_content_truncated" not in exact_row and "raw_content_truncated" not in short_row


def test_fallback_row_is_truncated_exactly_when_the_search_text_was_cut(monkeypatch):
    """RED: a page that cannot be fetched falls back to the search engine's text.  The row said
    truncated=False for text that _search had cut at 5,000 characters."""
    import asyncio
    from app.agent_web_research import MAX_EXCERPT
    cut_url, whole_url = "https://example.com/cut", "https://example.com/whole"
    cut = {**_row(cut_url, "Cut", raw=("alpha " * 900)[:MAX_EXCERPT]), "raw_content_truncated": True}
    whole = _row(whole_url, "Whole", raw="alpha " * 100)
    web = _fake_web(monkeypatch, [cut, whole],
                    {cut_url: ValueError("upstream_unavailable"), whole_url: ValueError("upstream_unavailable")})
    rows = asyncio.run(web.web_research.coroutine(query_or_url="Apple revenue news"))["data"]
    assert [row["body_provenance"] for row in rows] == ["tavily_extracted_content"] * 2
    assert [row["truncated"] for row in rows] == [True, False]
