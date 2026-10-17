from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlsplit

import httpx
from langchain_core.tools import tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from .freshness import published_within, window_days

def _body_limit(name,default):
    try:return max(32_000,min(10_000_000,int(os.getenv(name,default))))
    except ValueError:return default

MAX_PAGE_BYTES = _body_limit('AGENT_WEB_PAGE_MAX_BYTES',2_000_000)
MAX_SEARCH_BYTES = _body_limit('AGENT_WEB_SEARCH_MAX_BYTES',512_000)
MAX_EXCERPT = 5_000
MAX_REDIRECTS = 2


class WebResearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=512, validation_alias=AliasChoices("query", "query_or_url"))
    company: str | None = Field(default=None,max_length=160)
    days_back: int | None = Field(default=None, ge=1, le=366, description="News time window in days. Recent/latest means 7; half a month means 15. Omit only if the user requested no freshness window.")
    max_results: int = Field(default=5, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def bounded_input(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query_or_url is empty")
        if value.startswith(("http://", "https://")):
            validate_public_url(value)
        return value


def validate_public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
        try:
            ip = ipaddress.ip_address(host) if host else None
        except ValueError:
            ip = None
    except (ValueError, TypeError):
        raise ValueError("unsafe_public_url") from None
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.fragment or port not in (None, 443)):
        raise ValueError("unsafe_public_url")
    if ip and (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
        raise ValueError("unsafe_public_url")
    if host.lower() in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ValueError("unsafe_public_url")
    return value


class _ReadableHTML(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"}
    DROP = {"script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = False
        self.title_done = False
        self.drop_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.published_at: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.DROP:
            self.drop_depth += 1
        if tag == "title":
            self.title = True
        if tag == "meta" and not self.published_at:
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"article:published_time", "datepublished", "date"}:
                candidate = attrs.get("content", "")[:64]
                self.published_at = _valid_timestamp(candidate)
        if tag in self.BLOCK and not self.drop_depth:
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.title = False
            self.title_done = True
        if tag in self.DROP and self.drop_depth:
            self.drop_depth -= 1
        if tag in self.BLOCK and not self.drop_depth:
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self.title and not self.title_done:
            self.title_parts.append(data)
        elif not self.drop_depth:
            self.text_parts.append(data)


def _valid_timestamp(value: str) -> str | None:
    try:
        if not isinstance(value, str) or len(value) > 80:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        if parsed.astimezone(UTC) > datetime.now(UTC):
            return None
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, TypeError, IndexError, OverflowError):
        return None


# --- relevance-based excerpt ---------------------------------------------------------------
# bounded_tool_result (agent_chat_stream) cuts every string field of an oversized tool result to
# its first 700 characters by position, and a page's leading text rarely holds the sentence that
# names the metric the user asked about.  Search rows therefore carry the passages of the page
# text that match the query, within that same bound.  This only chooses which fetched text is
# shown; the evidence gate still decides what counts as evidence, and nothing here adds text.
FOCUS_LIMIT = 700
_GAP = " … "
# The tables of deterministic_confidence_gate (agent_workflow/graph.py); a test reads that source
# and fails when either side drifts.
METRIC_GROUPS = {
    "revenue": ("revenue", "sales", "营收", "收入", "营业收入"),
    "profit": ("profit", "earnings", "net income", "利润", "净利润"),
    "eps": ("eps", "earnings per share", "每股收益"),
    "cash_flow": ("cash flow", "现金流"),
    "debt": ("debt", "借款", "负债"),
}
_GENERIC_LATIN = frozenset((
    "a an the and or of in on at to by for with from about into over than then that this these those is are was "
    "were be been has have had do does did can will would not no but its it as if so up what when where which "
    "who whom how why also please news report reports article articles latest recent recently today current "
    "update updates related search list source sources web online page pages content company companies only "
    "use show tell find give").split())
_GENERIC_CJK = re.compile("新闻|报道|消息|资讯|最新|最近|近期|近日|今天|当前|相关|有关|关于|文章|公司|集团|查找|搜索|寻找"
                          "|帮我|一下|一些|几条|两条|三条|几篇|两篇|怎么样|怎样|如何|多少|哪些|什么|是否|的|跟|和|与|及|或|在|是"
                          "|对|请|给|把|找|查|搜|条|篇")
_LATIN_TOKEN = re.compile(r"[a-z0-9][a-z0-9$%&.'’-]*")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_SENTENCE_BREAK = re.compile(r"[。！？；]+[\"'”’」』）)\]]*|[.!?]+[\"'”’)\]]*(?=\s+(?![a-z])|$)")
_CLAUSE_BREAK = re.compile(r"[\s,，、;；:：]")
_MAX_GROUPS = 12


# A company the sentence names that the query did not ask for: its figures are not the asked company's,
# whatever sentence or headline comes before them.
def _member_pattern(member: str, *, whole_word: bool = False) -> str:
    escaped = re.escape(member)
    if not member.isascii():
        return escaped                       # CJK terms have no word boundaries
    end = r"(?![A-Za-z0-9])" if whole_word or len(member) <= 3 else ""
    return r"(?<![A-Za-z0-9])" + escaped + end


def _term_regex(term: str) -> re.Pattern:
    return re.compile(re.escape(term) if not term.isascii()
                      else r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def _query_groups(query: str, company: str | None = None) -> tuple[list[re.Pattern], list[str]]:
    """One pattern per distinct thing the query asks for, with its role: a "metric" group, an
    "entity" group (a company the gate knows) or a plain "term"."""
    lowered = query.lower()
    groups: dict[tuple[str, ...], str] = {}
    asked: list[str] = []
    # Metric groups are recognised exactly like the gate does it: a synonym anywhere in the question.
    for members in METRIC_GROUPS.values():
        found = [member for member in members if member in lowered]
        if found:
            groups[members] = "metric"
            asked += found
    working = lowered
    for member in sorted(set(asked), key=len, reverse=True):
        working = _term_regex(member).sub(" ", working)
    for token in _LATIN_TOKEN.findall(working):
        token = token.strip(".'’-&$%")
        if token.endswith(("'s", "’s")):
            token = token[:-2]
        if len(token) >= 2 and token not in _GENERIC_LATIN:
            groups.setdefault((token,), "entity" if any(c.isalpha() for c in token) else "term")
    for run in _CJK_RUN.findall(working):
        for piece in _GENERIC_CJK.split(run):
            if len(piece) >= 2:
                groups.setdefault((piece,), "entity")
    if company:groups[(company.lower(),)]="entity"
    kept = list(groups.items())[:_MAX_GROUPS]
    return ([re.compile("|".join(_member_pattern(member, whole_word=role == "entity") for member in members), re.I)
             for members, role in kept], [role for _, role in kept])


def _passage_spans(text: str, cap: int, guard: re.Pattern) -> list[tuple[int, int]]:
    """Sentences and lines as (start, end) offsets into text; none longer than cap characters."""
    spans: list[tuple[int, int]] = []

    def emit(start: int, end: int) -> None:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        while end - start > cap:
            lo, hi = start + cap // 2, start + cap
            breaks = list(_CLAUSE_BREAK.finditer(text, lo, hi + 1))
            cut = breaks[-1].end() if breaks else hi
            for term in guard.finditer(text, start, min(end, hi + 64)):
                if term.start() < cut < term.end():          # never cut a requested term in two
                    cut = max(term.start(), start + 1)
                    break
            spans.append((start, cut))
            start = cut
            while start < end and text[start].isspace():
                start += 1
        if start < end:
            spans.append((start, end))

    for line in re.finditer(r"[^\n]+", text):
        position = line.start()
        for sentence in _SENTENCE_BREAK.finditer(text, line.start(), line.end()):
            emit(position, sentence.end())
            position = sentence.end()
        emit(position, line.end())
    return spans


def focus_excerpt(text: str, query: str, limit: int = FOCUS_LIMIT, heading: str = "", company: str | None = None) -> str:
    """The passages of text that best match query, in original order, within limit characters.

    Sentences and lines are scored by the distinct query terms they hold (case-insensitive; CJK
    terms; the metric groups and company aliases the evidence gate knows).  Selection first covers
    every requested term, then keeps the first sentence, then fills the remaining budget by score
    and position.  Every piece is a verbatim substring of text; non-adjacent pieces are joined by
    an ellipsis.  text comes back unchanged when it already fits, when the query holds no usable
    term, or when nothing in it matches, so the caller keeps today's leading characters.

    When the query names a company the gate knows AND a metric, a sentence that holds the metric
    is shown only if it names the company itself or the sentence right before it (in the original
    text) does, and then together with that sentence.  The gate pools the text of all rows, so a
    revenue sentence of another company's paragraph beside an Apple headline would otherwise be
    accepted as Apple's revenue.  When nothing eligible is left (a page of revenue statements that
    never name the company) the result is empty and the row is dropped for lack of an excerpt.

    A text that already fits is held to the same rule: it comes back unchanged unless the query
    asks for a company and a metric and a metric sentence of it has no company beside it, and then
    only that sentence is left out.  ``heading`` (the page's title, which is no part of the text)
    counts as the sentence before the first one for that rule."""
    if not isinstance(text, str) or not isinstance(query, str):
        return text
    fits = len(text) <= limit
    groups, roles = _query_groups(query,company)
    if not groups:
        return text
    # Company filtering is grounded in the explicit query terms. There is no global
    # company directory or "other company" list in this retrieval path.
    entity = frozenset(n for n,role in enumerate(roles) if role=="entity")
    metric = frozenset(n for n, role in enumerate(roles) if role == "metric")
    strict = bool(entity and metric)
    if fits and not strict:
        return text
    spans = _passage_spans(text, max(1, (limit - len(_GAP)) // 2),
                           re.compile("|".join(group.pattern for group in groups), re.I))
    hits = [frozenset(n for n, group in enumerate(groups) if group.search(text, start, end)) for start, end in spans]
    if not any(hits):
        return text
    titled = bool(entity and isinstance(heading, str) and heading
                  and any(groups[n].search(heading) for n in entity))

    def needs_neighbour(index: int) -> bool:
        """A metric sentence that does not name the company itself."""
        return strict and bool(hits[index] & metric) and not hits[index] & entity

    def other_company(index: int) -> bool:
        """The sentence names a company the query did not ask for (and not the asked one)."""
        start, end = spans[index]
        passage=text[start:end]
        subjects=re.findall(r"\b([A-Z][A-Za-z]+)(?:'s)?\s+(?:said|reported|revenue|sales|earnings|announced|introduced)\b",passage)
        return any(not any(groups[n].search(subject) for n in entity) and subject.casefold() not in _GENERIC_LATIN for subject in subjects)

    def eligible(index: int) -> bool:
        if not needs_neighbour(index):
            return True
        if other_company(index):   # its own subject is another company: nothing before it can change that
            return False
        return (index > 0 and bool(hits[index - 1] & entity)) or (index == 0 and titled)

    def render(indices: list[int]) -> str:
        parts: list[str] = []
        previous = None
        for index in indices:
            start, end = spans[index]
            if previous is not None:
                parts.append(text[spans[previous][1]:start] if index == previous + 1 else _GAP)
            parts.append(text[start:end])
            previous = index
        return "".join(parts)

    if fits:  # nothing is chosen from it: only a metric sentence with no company beside it is left out
        left = [index for index in range(len(spans)) if eligible(index)]
        return text if len(left) == len(spans) else render(left)

    chosen: set[int] = set()

    def add(index: int) -> bool:
        if index in chosen or not eligible(index):
            return False
        # The neighbour that makes a metric sentence eligible travels with it (both or neither).
        needed = {index, index - 1} if needs_neighbour(index) and index > 0 else {index}
        if len(render(sorted(chosen | needed))) > limit:
            return False
        chosen.update(needed)
        return True

    covered: set[int] = set()
    while True:
        candidates = sorted((index for index in range(len(spans)) if index not in chosen and hits[index] - covered),
                            key=lambda index: (-len(hits[index] - covered), index))
        picked = next((index for index in candidates if add(index)), None)
        if picked is None:
            break
        covered |= hits[picked]
    add(0)
    for index in sorted(range(len(spans)), key=lambda index: (-len(hits[index]), index)):
        add(index)
    return render(sorted(chosen))


def _focus_source(source: dict, query: str | None, company: str | None = None) -> None:
    """Replace a search row's leading-characters excerpt by the passages that match the query."""
    text = source.get("excerpt")
    if not query or not isinstance(text, str):
        return
    focused = focus_excerpt(text, query, heading=str(source.get("title") or ""),company=company)
    if focused != text:
        source["excerpt"] = focused
        source["truncated"] = True            # a selection: the page text is no longer complete


def is_challenge_page(text: str) -> bool:
    if len(text) > 1500:
        return False
    lowered=text.lower()
    markers=("captcha", "verify you are human", "unusual traffic", "robot check",
             "checking your browser", "enable javascript to continue")
    return any(marker in lowered for marker in markers)


_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)|https?://\S+")
BOILERPLATE_MARKERS = ("oops, something went wrong", "access denied", "403 forbidden", "404 not found",
                       "page not found", "enable javascript", "please enable cookies", "sign in to continue",
                       "are you a robot", "just a moment", "request blocked", "this page isn't available",
                       "页面不存在", "访问被拒绝", "请开启 javascript", "请启用 javascript")


def is_boilerplate_page(text: object) -> bool:
    """True for the text of an error, block or navigation page: it opens with an error phrase and holds
    hardly any prose once links are taken out, or it is mostly links.  Such text is not what a page says
    and must not be listed as a source (a news page that merely mentions an error is not touched)."""
    if not isinstance(text, str) or not text.strip():
        return False
    prose = re.sub(r"[\s#*_>|\-–—•·]+", " ", _MARKDOWN_LINK.sub(" ", text)).strip()
    if any(marker in text[:400].lower() for marker in BOILERPLATE_MARKERS) and len(prose) < 600:
        return True
    link_chars = len(text) - len(_MARKDOWN_LINK.sub("", text))
    return len(text) >= 300 and link_chars / len(text) >= 0.6 and len(prose) < 300


def extract_page(body: str, url: str, fetched_at: datetime) -> dict:
    parser = _ReadableHTML()
    parser.feed(body[:MAX_PAGE_BYTES])
    excerpt = re.sub(r"[ \t]+", " ", " ".join(parser.text_parts))
    excerpt = re.sub(r"\s*\n\s*", "\n", html.unescape(excerpt)).strip()[:MAX_EXCERPT]
    return {
        "title": " ".join(" ".join(parser.title_parts).split())[:300] or urlsplit(url).hostname,
        "url": url,
        "excerpt": excerpt,
        "fetched_at": fetched_at.astimezone(UTC).isoformat(),
        "published_at": parser.published_at,
        "truncated": len(body.encode("utf-8", "ignore")) > MAX_EXCERPT,
    }


async def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes(16_384):
        total += len(chunk)
        if total > limit:
            raise ValueError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _get_public(url: str, *, limit: int) -> tuple[str, str, bytes]:
    from .agent_chat_api import PinnedPublicTransport

    target = validate_public_url(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(18), follow_redirects=False,
                                 trust_env=False,
                                 transport=PinnedPublicTransport(allow_internal_query=True)) as client:
        for hop in range(MAX_REDIRECTS + 1):
            # Public research is anonymous: never carry Set-Cookie state to another hop.
            client.cookies.clear()
            async with client.stream("GET", target, headers={
                "User-Agent": "MarketEvidenceAgent/1.0 (public research excerpt)",
                "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.1",
                "Accept-Encoding": "identity",
            }) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop == MAX_REDIRECTS:
                        raise ValueError("redirect_limit")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect_invalid")
                    target = validate_public_url(urljoin(target, location))
                    continue
                if response.status_code >= 400:
                    raise ValueError("upstream_unavailable")
                content_type = response.headers.get("content-type", "").lower()
                if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "application/rss+xml", "application/xml", "text/xml")):
                    raise ValueError("unsupported_content_type")
                body = await _read_bounded(response, limit)
                return target, content_type, body
    raise ValueError("upstream_unavailable")


def _tavily_body(query: str, include_raw_content: bool, max_results: int = 5, days_back: int | None = None) -> dict:
    """The search request.  A question for the recent end of the news (or for a named span) carries the
    backend's nearest time range, so that old articles are not what comes back."""
    time_range = ("day" if days_back <= 1 else "week" if days_back <= 7 else "month" if days_back <= 31 else "year") if days_back else None
    topic = "general" if any(k in query.lower() for k in ("sec", "filing", "10-k", "10-q", "wikipedia", "wiki", "definition", "overview")) else "news"
    return {"query": query[:300], "max_results": max_results, "search_depth": "basic",
            "include_raw_content": include_raw_content, "topic": topic,
            **({"time_range": time_range} if time_range else {})}


async def _search(query: str, include_raw_content: bool = True, max_results: int = 5, days_back: int | None = None) -> list[dict]:
    backend = os.getenv("WEB_RESEARCH_BACKEND", "tavily").strip().lower()
    if backend == "tavily":
        key = os.getenv("TAVILY_API_KEY")
        if not key:
            raise ValueError("search_backend_key_missing")
        from .agent_chat_api import PinnedPublicTransport
        async with httpx.AsyncClient(timeout=httpx.Timeout(18), follow_redirects=False,
                                     trust_env=False, transport=PinnedPublicTransport(allow_internal_query=True)) as client:
            async with client.stream("POST", "https://api.tavily.com/search",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     json=_tavily_body(query, include_raw_content, max_results, days_back)) as response:
                if response.status_code == 429:raise ValueError("rate_limited")
                if response.status_code >= 500:raise ValueError("http_5xx")
                if response.status_code >= 400:raise ValueError("search_backend_invalid_request_or_key")
                raw = await _read_bounded(response, MAX_SEARCH_BYTES)
        payload = json.loads(raw)
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        def text_field(row, name, limit):
            value = row.get(name)
            return value[:limit] if isinstance(value, str) else ""
        # The cut is made here, so whether anything was cut can only be told here.
        return [{"url": text_field(row, "url", 2048), "title": text_field(row, "title", 300),
                 "snippet": text_field(row, "content", 1000),
                 "raw_content": text_field(row, "raw_content", MAX_EXCERPT),
                 "published_at": _valid_timestamp(row.get("published_date") or ""),
                 **({"raw_content_truncated": True} if isinstance(row.get("raw_content"), str)
                    and len(row["raw_content"]) > MAX_EXCERPT else {})}
                for row in rows[:max_results] if isinstance(row, dict)]
    if backend != "bing_rss":
        raise ValueError("search_backend_unavailable")
    url = "https://www.bing.com/search?q=" + quote_plus(query[:300]) + "&format=rss"
    _final_url, _content_type, raw = await _get_public(url, limit=MAX_SEARCH_BYTES)
    if b"<!DOCTYPE" in raw[:2048].upper() or b"<!ENTITY" in raw[:2048].upper():
        raise ValueError("search_response_invalid")
    root = ET.fromstring(raw)
    rows = []
    for item in root.findall(".//item")[:5]:
        link = (item.findtext("link") or "").strip()
        try:
            validate_public_url(link)
        except ValueError:
            continue
        rows.append({"url": link, "title": (item.findtext("title") or "")[:300],
                     "snippet": re.sub(r"<[^>]+>", " ", item.findtext("description") or "")[:1000]})
    return rows


async def _research(query_or_url: str, days_back: int | None = None, max_results: int = 5, company: str | None = None) -> dict:
    fetched_at = datetime.now(UTC)
    is_url = query_or_url.lower().startswith("https://")
    if is_url:
        candidates = [{"url": validate_public_url(query_or_url), "title": "", "snippet": ""}]
    else:
        try:
            candidates = await _search(query_or_url, max_results=max_results, days_back=days_back)
        except ValueError as exc:
            if str(exc) != "response_too_large":
                raise
            # One long article can push Tavily's raw_content past the response cap; ask for
            # snippets only and read each page through the normal bounded public fetch.
            candidates = await _search(query_or_url, include_raw_content=False, max_results=max_results, days_back=days_back)
    # Time filtering is controlled by the model's validated days_back argument.
    # freshness.py remains the bounded parser fallback for legacy direct callers.
    span = days_back if days_back is not None else window_days(query_or_url)
    if not is_url and span is not None:
        # A span the user named ("半个月内"): the search backend's range is only the nearest one that holds it,
        # so what is older than the span, or cannot be dated, is left out.
        candidates = [item for item in candidates if item.get("published_at") is None or published_within(item.get("published_at"), span, fetched_at)]
        if not candidates:
            return {"ok": True, "data": [], "source": "public web search and fetched page excerpts",
                    "as_of": fetched_at.isoformat(), "error_code": None}
    sources = []
    focus_query = None if is_url else query_or_url      # an exact URL read keeps its leading text
    started = time.monotonic()
    for result in candidates[:5]:
        usable = sum(1 for source in sources if not source.get("fetch_error"))
        if usable >= max_results or (usable and len(sources) >= max_results and time.monotonic() - started > 24):
            break
        try:
            _url, content_type, raw = await _get_public(result["url"], limit=MAX_PAGE_BYTES)
            if "html" not in content_type:
                raise ValueError("unsupported_content_type")
            page_html=raw.decode("utf-8", "replace")
            if is_challenge_page(page_html):raise ValueError("challenge_page")
            page = extract_page(page_html, _url, fetched_at)
            page["body_provenance"] = "public_page_fetch"
            page["search_snippet"] = result.get("snippet") or None
            if result.get("raw_content"):
                page["excerpt"] = result["raw_content"][:MAX_EXCERPT]
                page["truncated"] = len(result["raw_content"]) >= MAX_EXCERPT
                page["body_provenance"] = "tavily_extracted_content"
            if is_boilerplate_page(page["excerpt"]):
                raise ValueError("boilerplate_page")
            page["published_at"] = page.get("published_at") or result.get("published_at")
            _focus_source(page, focus_query,company)
            sources.append(page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_url:
                return {"ok":False,"data":[],"source":"public webpage excerpt","as_of":fetched_at.isoformat(),"error_code":"web_page_unavailable"}
            raw_content = result.get("raw_content")
            if isinstance(raw_content, str) and raw_content.strip() and not is_boilerplate_page(raw_content):
                fallback = {"title": result.get("title") or result["url"], "url": result["url"],
                            "excerpt": raw_content[:MAX_EXCERPT], "search_snippet": result.get("snippet") or None,
                            "fetched_at": fetched_at.isoformat(), "published_at": result.get("published_at"),
                            "truncated": bool(result.get("raw_content_truncated")) or len(raw_content) > MAX_EXCERPT,
                            "body_provenance": "tavily_extracted_content"}
                _focus_source(fallback, focus_query,company)
                sources.append(fallback)
                continue
            failure=str(exc) if str(exc) in {"challenge_page", "boilerplate_page"} else "page_unavailable"
            sources.append({"title": result.get("title") or result["url"], "url": result["url"],
                            "excerpt": "", "search_snippet": result.get("snippet") or None,
                            "fetched_at": fetched_at.isoformat(), "published_at": result.get("published_at"), "fetch_error": failure})
    # Pages that gave text come first (at most three); a failed one is reported only to fill the list.
    sources = ([source for source in sources if not source.get("fetch_error")][:max_results]
               + [source for source in sources if source.get("fetch_error")])[:max_results]
    if span is not None:
        sources=[r for r in sources if published_within(r.get("published_at"),span,fetched_at)]
    return {"ok": bool(sources), "data": sources,
            "source": "public web search and fetched page excerpts" if not is_url else "public webpage excerpt",
            "as_of": fetched_at.isoformat(), "error_code": None if sources else "web_research_unavailable"}


@tool(args_schema=WebResearchArgs)
async def web_research(query: str = "", days_back: int | None = None, max_results: int = 5, company: str | None = None, query_or_url: str | None = None) -> dict:
    """Search public web sources or read a public HTTPS page. ALWAYS set days_back for news freshness: recent/latest=7, half a month=15; preserve the interpreted request window. Supply company as named in page text. Search snippets are leads, not evidence."""
    try:
        async with asyncio.timeout(40):
            return await _research(query or query_or_url or "", days_back=days_back, max_results=max_results,company=company)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return {"ok": False, "data": [], "source": "public web research", "as_of": None, "error_code": "web_research_timeout"}
    except Exception as exc:
        code = str(exc) if str(exc) in {"search_backend_key_missing", "search_backend_unavailable", "web_research_unavailable", "web_page_unavailable", "rate_limited", "http_5xx", "search_backend_invalid_request_or_key"} else "web_research_unavailable"
        return {"ok": False, "data": [], "source": "public web research", "as_of": None, "error_code": code}
