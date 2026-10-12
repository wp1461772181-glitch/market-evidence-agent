from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlsplit

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PAGE_BYTES = 1_000_000
MAX_SEARCH_BYTES = 180_000
MAX_EXCERPT = 5_000
MAX_REDIRECTS = 2


class WebResearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_or_url: str = Field(min_length=1, max_length=512)

    @field_validator("query_or_url")
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


def is_challenge_page(text: str) -> bool:
    lowered=text.lower()
    markers=("captcha", "verify you are human", "unusual traffic", "robot check",
             "checking your browser", "enable javascript to continue")
    return any(marker in lowered for marker in markers)


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


async def _search(query: str) -> list[dict]:
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
                                     json={"query": query[:300], "max_results": 3, "search_depth": "basic",
                                           "include_raw_content": True, "topic": "news",
                                           **({"time_range": "week"} if re.search(r"(?:\b(?:latest|today|recent|this week)\b|最新|今天|近期)", query, re.I) else {})}) as response:
                if response.status_code >= 400:
                    raise ValueError("search_backend_unavailable")
                raw = await _read_bounded(response, MAX_SEARCH_BYTES)
        payload = json.loads(raw)
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        def text_field(row, name, limit):
            value = row.get(name)
            return value[:limit] if isinstance(value, str) else ""
        return [{"url": text_field(row, "url", 2048), "title": text_field(row, "title", 300),
                 "snippet": text_field(row, "content", 1000),
                 "raw_content": text_field(row, "raw_content", MAX_EXCERPT),
                 "published_at": _valid_timestamp(row.get("published_date") or "")}
                for row in rows[:3] if isinstance(row, dict)]
    if backend != "bing_rss":
        raise ValueError("search_backend_unavailable")
    url = "https://www.bing.com/search?q=" + quote_plus(query[:300]) + "&format=rss"
    _final_url, _content_type, raw = await _get_public(url, limit=MAX_SEARCH_BYTES)
    if b"<!DOCTYPE" in raw[:2048].upper() or b"<!ENTITY" in raw[:2048].upper():
        raise ValueError("search_response_invalid")
    root = ET.fromstring(raw)
    rows = []
    for item in root.findall(".//item")[:3]:
        link = (item.findtext("link") or "").strip()
        try:
            validate_public_url(link)
        except ValueError:
            continue
        rows.append({"url": link, "title": (item.findtext("title") or "")[:300],
                     "snippet": re.sub(r"<[^>]+>", " ", item.findtext("description") or "")[:1000]})
    return rows


async def _research(query_or_url: str) -> dict:
    fetched_at = datetime.now(UTC)
    is_url = query_or_url.lower().startswith("https://")
    candidates = [{"url": validate_public_url(query_or_url), "title": "", "snippet": ""}] if is_url else await _search(query_or_url)
    sources = []
    for result in candidates[:3]:
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
            page["published_at"] = page.get("published_at") or result.get("published_at")
            sources.append(page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_url:
                return {"ok":False,"data":[],"source":"public webpage excerpt","as_of":fetched_at.isoformat(),"error_code":"web_page_unavailable"}
            raw_content = result.get("raw_content")
            if isinstance(raw_content, str) and raw_content.strip():
                sources.append({"title": result.get("title") or result["url"], "url": result["url"],
                                "excerpt": raw_content[:MAX_EXCERPT], "search_snippet": result.get("snippet") or None,
                                "fetched_at": fetched_at.isoformat(), "published_at": result.get("published_at"),
                                "truncated": len(raw_content) > MAX_EXCERPT,
                                "body_provenance": "tavily_extracted_content"})
                continue
            failure=str(exc) if str(exc)=="challenge_page" else "page_unavailable"
            sources.append({"title": result.get("title") or result["url"], "url": result["url"],
                            "excerpt": "", "search_snippet": result.get("snippet") or None,
                            "fetched_at": fetched_at.isoformat(), "published_at": result.get("published_at"), "fetch_error": failure})
    return {"ok": bool(sources), "data": sources,
            "source": "public web search and fetched page excerpts" if not is_url else "public webpage excerpt",
            "as_of": fetched_at.isoformat(), "error_code": None if sources else "web_research_unavailable"}


@tool(args_schema=WebResearchArgs)
async def web_research(query_or_url: str) -> dict:
    """Search public web sources or read a public HTTPS webpage excerpt. Search snippets are leads, not full-source evidence."""
    try:
        async with asyncio.timeout(40):
            return await _research(query_or_url)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return {"ok": False, "data": [], "source": "public web research", "as_of": None, "error_code": "web_research_timeout"}
    except Exception as exc:
        code = str(exc) if str(exc) in {"search_backend_key_missing", "search_backend_unavailable", "web_research_unavailable", "web_page_unavailable"} else "web_research_unavailable"
        return {"ok": False, "data": [], "source": "public web research", "as_of": None, "error_code": code}
