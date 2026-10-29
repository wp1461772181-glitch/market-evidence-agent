"""FinBot Configured Public News Engine (FinNewsEngine).

Implements:
- YAML configuration loading, validation, and persistence (`config/news_sources.yaml`)
- RSS / Atom, HTTP JSON, and HTTP HTML fetching & normalization
- Per-source rate limiting, exponential backoff, and health tracking (`ok | degraded | failed`)
- Local database storage (`news_items`, `news_push_log`)
- Agent tools: `search_configured_news`, `list_news_sources`, `dry_run_news_source`,
  `upsert_news_source`, `disable_news_source`, `set_news_source_alternate`,
  `diagnose_news_source`, `repair_news_source_with_rea`
- Digest summary token budget control (`push_summary_max_tokens` / `push.summary_max_tokens`)
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

import yaml
from sqlalchemy import desc, select

from .agent_chat_models import AgentInboxItem, NewsItem, NewsPushLog
from .database import Base, SessionLocal, engine

VALID_SOURCE_KINDS = {"rss", "http_json", "http_html"}
VALID_HEALTH_STATES = {"ok", "degraded", "failed"}
SUMMARY_TOKENS_MIN = 32
SUMMARY_TOKENS_MAX = 2048
SUMMARY_TOKENS_DEFAULT = 256
SUMMARY_TOKEN_PRESETS = {"short": 128, "medium": 256, "long": 512}
TRUNCATION_FOOTNOTE = "（已按你的长度上限截断）"
REA_REPAIR_COOLDOWN_SECONDS = 3600

_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}
_TAG_RE = re.compile(r"<[^>]+>")
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5}|\d{6})(?![A-Za-z0-9])")
_STOP_WORDS = {
    "USD", "SEC", "RSS", "XML", "JSON", "HTML", "HTTP", "HTTPS", "UTC", "GMT",
    "CEO", "CFO", "COO", "CPI", "PPI", "GDP", "FED", "ETF", "IPO", "YOY", "QOQ",
    "AI", "US", "CN", "HK", "UK", "EU", "NEWS", "THE", "AND", "FOR", "WITH",
}

# Runtime state per source_id (in-memory across polls)
_RUNTIME_STATE: dict[str, dict[str, Any]] = {}
# Audit trail of REA invocations to guarantee REA never runs on normal search_configured_news path
_REA_AUDIT_LOG: list[dict[str, Any]] = []
# Optional injectable HTTP fetcher for deterministic tests: fn(url, headers, timeout) -> (status_code, text)
_CUSTOM_HTTP_FETCHER: Callable[[str, dict[str, str], int], tuple[int, str]] | None = None


def set_http_fetcher(fetcher: Callable[[str, dict[str, str], int], tuple[int, str]] | None) -> None:
    """Allow tests to inject a deterministic HTTP fetcher without monkeypatching urllib."""
    global _CUSTOM_HTTP_FETCHER
    _CUSTOM_HTTP_FETCHER = fetcher


def get_rea_audit_log() -> list[dict[str, Any]]:
    return list(_REA_AUDIT_LOG)


def clear_runtime_state() -> None:
    _RUNTIME_STATE.clear()
    _REA_AUDIT_LOG.clear()


def ensure_news_tables() -> None:
    try:
        Base.metadata.create_all(
            bind=engine,
            tables=[NewsItem.__table__, NewsPushLog.__table__, AgentInboxItem.__table__],
        )
    except Exception:
        pass


def config_path(custom_path: str | Path | None = None) -> Path:
    if custom_path:
        return Path(custom_path)
    env_path = os.getenv("FINBOT_NEWS_SOURCES_CONFIG", "").strip()
    if env_path:
        return Path(env_path)
    repo_root = Path(__file__).resolve().parents[1]
    if repo_root.parent.name == "releases" and repo_root.parent.parent.name == "data":
        persistent_root = repo_root.parent.parent.parent
        persistent_cfg = persistent_root / "config" / "news_sources.yaml"
        if persistent_cfg.exists() or (persistent_root / "config").exists():
            return persistent_cfg
    return repo_root / "config" / "news_sources.yaml"


def default_config_dict() -> dict[str, Any]:
    return {
        "version": 1,
        "defaults": {
            "poll_interval_minutes": 15,
            "lookback_hours": 24,
            "max_items_per_source": 50,
            "push_summary_max_tokens": SUMMARY_TOKENS_DEFAULT,
            "timezone": "Asia/Kuala_Lumpur",
        },
        "sources": [
            {
                "id": "jin10-flash",
                "name": "金十数据·快讯",
                "enabled": True,
                "kind": "rss",
                "url": "https://rsshub.app/jin10",
                "homepage": "https://www.jin10.com/",
                "language": "zh-CN",
                "tags": ["macro", "flash"],
                "markets": ["CN", "US"],
                "fetch": {
                    "timeout_seconds": 20,
                    "headers": {"User-Agent": "FinBotNewsBot/1.0 (+local; research)"},
                },
                "normalize": {"time_format": "auto"},
                "filters": {"include_keywords": [], "exclude_keywords": ["广告", "推广"]},
                "rate_limit": {"min_interval_seconds": 60},
                "rea_repair": True,
                "alternates": ["https://www.jin10.com/flash"],
                "health": "ok",
            }
        ],
        "watchlists": [
            {
                "id": "default",
                "tickers": ["NVDA", "AAPL", "600519"],
                "keywords": ["降息", "非农", "财报", "指引"],
                "push_on_match": True,
            }
        ],
        "push": {
            "enabled": True,
            "channel": "finbot_notify",
            "summary_max_tokens": SUMMARY_TOKENS_DEFAULT,
            "max_items_per_digest": 8,
            "digest_cron": "*/30 * * * *",
            "actions_required": [
                {"confirm_inline": True},
                {"open_chat_fallback": True},
            ],
        },
    }


def validate_summary_max_tokens(value: Any, *, strict: bool = False) -> int:
    if isinstance(value, str) and value.strip().lower() in SUMMARY_TOKEN_PRESETS:
        return SUMMARY_TOKEN_PRESETS[value.strip().lower()]
    if isinstance(value, bool) or value is None:
        if strict:
            raise ValueError("invalid_summary_max_tokens")
        return SUMMARY_TOKENS_DEFAULT
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        if strict:
            raise ValueError("invalid_summary_max_tokens") from exc
        return SUMMARY_TOKENS_DEFAULT
    if parsed < SUMMARY_TOKENS_MIN or parsed > SUMMARY_TOKENS_MAX:
        if strict:
            raise ValueError("invalid_summary_max_tokens")
        return SUMMARY_TOKENS_DEFAULT
    return parsed


def validate_config(raw: dict[str, Any], *, strict_tokens: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("invalid_news_config_root")
    cfg = copy.deepcopy(raw)
    cfg["version"] = int(cfg.get("version") or 1)

    defaults = cfg.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    defaults["poll_interval_minutes"] = max(1, int(defaults.get("poll_interval_minutes") or 15))
    defaults["lookback_hours"] = max(1, int(defaults.get("lookback_hours") or 24))
    defaults["max_items_per_source"] = max(1, min(500, int(defaults.get("max_items_per_source") or 50)))
    defaults["push_summary_max_tokens"] = validate_summary_max_tokens(
        defaults.get("push_summary_max_tokens", SUMMARY_TOKENS_DEFAULT),
        strict=strict_tokens,
    )
    defaults["timezone"] = str(defaults.get("timezone") or "Asia/Kuala_Lumpur")
    cfg["defaults"] = defaults

    sources = cfg.get("sources")
    if not isinstance(sources, list):
        raise ValueError("invalid_news_sources_list")
    validated_sources: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            raise ValueError("invalid_news_source_entry")
        src_id = str(src.get("id") or "").strip()
        if not src_id or not re.fullmatch(r"[A-Za-z0-9._\-]{2,80}", src_id):
            raise ValueError(f"invalid_source_id:{src_id}")
        if src_id in seen_ids:
            raise ValueError(f"duplicate_source_id:{src_id}")
        seen_ids.add(src_id)
        kind = str(src.get("kind") or "rss").strip().lower()
        if kind not in VALID_SOURCE_KINDS:
            raise ValueError(f"invalid_source_kind:{kind}")
        url = str(src.get("url") or "").strip()
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"invalid_source_url:{url}")
        health = str(src.get("health") or "ok").strip().lower()
        if health not in VALID_HEALTH_STATES:
            health = "ok"
        fetch = src.get("fetch") if isinstance(src.get("fetch"), dict) else {}
        fetch.setdefault("timeout_seconds", 20)
        fetch.setdefault("headers", {"User-Agent": "FinBotNewsBot/1.0 (+local; research)"})
        normalize = src.get("normalize") if isinstance(src.get("normalize"), dict) else {"time_format": "auto"}
        filters = src.get("filters") if isinstance(src.get("filters"), dict) else {}
        filters.setdefault("include_keywords", [])
        filters.setdefault("exclude_keywords", [])
        rate_limit = src.get("rate_limit") if isinstance(src.get("rate_limit"), dict) else {}
        rate_limit["min_interval_seconds"] = max(0, int(rate_limit.get("min_interval_seconds", 60)))
        alternates = src.get("alternates") if isinstance(src.get("alternates"), list) else []

        validated_sources.append({
            **src,
            "id": src_id,
            "name": str(src.get("name") or src_id).strip(),
            "enabled": bool(src.get("enabled", True)),
            "kind": kind,
            "url": url,
            "homepage": str(src.get("homepage") or f"{parsed_url.scheme}://{parsed_url.netloc}/").strip(),
            "language": str(src.get("language") or "zh-CN").strip(),
            "tags": [str(t).strip() for t in (src.get("tags") or []) if str(t).strip()],
            "markets": [str(m).strip().upper() for m in (src.get("markets") or []) if str(m).strip()],
            "fetch": fetch,
            "normalize": normalize,
            "filters": filters,
            "rate_limit": rate_limit,
            "rea_repair": bool(src.get("rea_repair", True)),
            "alternates": alternates,
            "health": health,
        })
    cfg["sources"] = validated_sources

    watchlists = cfg.get("watchlists")
    if not isinstance(watchlists, list):
        watchlists = []
    normalized_watchlists = []
    for wl in watchlists:
        if not isinstance(wl, dict):
            continue
        normalized_watchlists.append({
            "id": str(wl.get("id") or "default").strip(),
            "tickers": [str(t).strip().upper() for t in (wl.get("tickers") or []) if str(t).strip()],
            "keywords": [str(k).strip() for k in (wl.get("keywords") or []) if str(k).strip()],
            "push_on_match": bool(wl.get("push_on_match", True)),
        })
    cfg["watchlists"] = normalized_watchlists

    push = cfg.get("push")
    if not isinstance(push, dict):
        push = {}
    raw_summary_tokens = push.get("summary_max_tokens", defaults["push_summary_max_tokens"])
    push["enabled"] = bool(push.get("enabled", True))
    push["channel"] = str(push.get("channel") or "finbot_notify")
    push["summary_max_tokens"] = validate_summary_max_tokens(raw_summary_tokens, strict=strict_tokens)
    push["max_items_per_digest"] = max(1, min(50, int(push.get("max_items_per_digest") or 8)))
    push["digest_cron"] = str(push.get("digest_cron") or "*/30 * * * *")
    push["actions_required"] = push.get("actions_required") or [
        {"confirm_inline": True},
        {"open_chat_fallback": True},
    ]
    cfg["push"] = push
    return cfg


def load_config(custom_path: str | Path | None = None) -> dict[str, Any]:
    path = config_path(custom_path)
    if not path.exists():
        cfg = validate_config(default_config_dict())
        save_config(cfg, path)
        return cfg
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return validate_config(raw)


def save_config(cfg: dict[str, Any], custom_path: str | Path | None = None, *, strict_tokens: bool = False) -> dict[str, Any]:
    validated = validate_config(cfg, strict_tokens=strict_tokens)
    path = config_path(custom_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(validated, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return validated


def effective_summary_max_tokens(cfg: dict[str, Any] | None = None, override: Any = None) -> int:
    if override is not None:
        return validate_summary_max_tokens(override, strict=False)
    cfg = cfg or load_config()
    push_tokens = (cfg.get("push") or {}).get("summary_max_tokens")
    if push_tokens is not None:
        return validate_summary_max_tokens(push_tokens, strict=False)
    default_tokens = (cfg.get("defaults") or {}).get("push_summary_max_tokens", SUMMARY_TOKENS_DEFAULT)
    return validate_summary_max_tokens(default_tokens, strict=False)


def update_push_summary_max_tokens(max_tokens: Any, custom_path: str | Path | None = None) -> dict[str, Any]:
    """Update push.summary_max_tokens and defaults.push_summary_max_tokens with validation."""
    validated_tokens = validate_summary_max_tokens(max_tokens, strict=True)
    cfg = load_config(custom_path)
    cfg.setdefault("defaults", {})["push_summary_max_tokens"] = validated_tokens
    cfg.setdefault("push", {})["summary_max_tokens"] = validated_tokens
    return save_config(cfg, custom_path, strict_tokens=True)


def _strip_html(value: str) -> str:
    if not value:
        return ""
    unescaped = html.unescape(str(value))
    cleaned = _TAG_RE.sub(" ", unescaped)
    return " ".join(cleaned.split()).strip()


def _parse_datetime(raw: Any, fetched_at: datetime) -> tuple[datetime, bool]:
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=UTC)
        return dt.astimezone(UTC), False
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=UTC), False
        except Exception:
            return fetched_at, True
    text = str(raw or "").strip()
    if not text:
        return fetched_at, True
    # Try ISO 8601
    try:
        iso_text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_text)
        dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC), False
    except Exception:
        pass
    # Try RFC 2822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(text)
        dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC), False
    except Exception:
        pass
    # Try common date/time patterns
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19], fmt).replace(tzinfo=UTC)
            return dt, False
        except Exception:
            continue
    return fetched_at, True


def _http_get(url: str, headers: dict[str, str], timeout: int) -> tuple[int, str]:
    if _CUSTOM_HTTP_FETCHER is not None:
        return _CUSTOM_HTTP_FETCHER(url, headers, timeout)
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "FinBotNewsBot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200) or 200
        raw_bytes = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        return int(status), raw_bytes.decode(charset, errors="replace")


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_rss(xml_text: str, base_url: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text.strip())
    items: list[dict[str, Any]] = []
    # RSS 2.0 <item> or Atom <entry>
    for elem in root.iter():
        tag = _local_tag(elem.tag).lower()
        if tag not in {"item", "entry"}:
            continue
        title = ""
        url = ""
        summary = ""
        published_at = ""
        external_id = ""
        for child in elem:
            ctag = _local_tag(child.tag).lower()
            text = (child.text or "").strip()
            if ctag == "title" and not title:
                title = text
            elif ctag == "link":
                href = (child.attrib.get("href") or "").strip()
                rel = (child.attrib.get("rel") or "alternate").strip().lower()
                candidate = href or text
                if candidate and (not url or rel == "alternate"):
                    url = urljoin(base_url, candidate)
            elif ctag in {"description", "summary", "content", "encoded"} and not summary:
                summary = text
            elif ctag in {"pubdate", "published", "updated", "date"} and not published_at:
                published_at = text
            elif ctag in {"guid", "id"} and not external_id:
                external_id = text
                if not url and text.startswith(("http://", "https://")):
                    url = text
        items.append({
            "title": title,
            "url": url,
            "summary": summary,
            "published_at": published_at,
            "external_id": external_id,
        })
    return items


def _resolve_jsonpath(data: Any, path_expr: str) -> Any:
    if not path_expr or path_expr in {"$", "."}:
        return data
    cleaned = path_expr.lstrip("$.")
    current = data
    for part in cleaned.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if 0 <= idx < len(current) else None
        else:
            return None
    return current


def _parse_http_json(json_text: str, base_url: str, fetch_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    payload = json.loads(json_text)
    items_path = str(fetch_cfg.get("items_jsonpath") or "$.data.list")
    raw_list = _resolve_jsonpath(payload, items_path)
    if not isinstance(raw_list, list):
        if isinstance(payload, list):
            raw_list = payload
        else:
            return []
    field_map = fetch_cfg.get("field_map") if isinstance(fetch_cfg.get("field_map"), dict) else {}
    title_key = str(field_map.get("title") or "title")
    url_key = str(field_map.get("url") or "url")
    pub_key = str(field_map.get("published_at") or "published_at")
    sum_key = str(field_map.get("summary") or "summary")

    out: list[dict[str, Any]] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get(url_key) or entry.get("link") or "").strip()
        out.append({
            "title": str(entry.get(title_key) or "").strip(),
            "url": urljoin(base_url, raw_url) if raw_url else "",
            "summary": str(entry.get(sum_key) or entry.get("content") or "").strip(),
            "published_at": entry.get(pub_key) or entry.get("time") or "",
            "external_id": str(entry.get("id") or ""),
        })
    return out


def _parse_http_html(html_text: str, base_url: str, fetch_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")
        list_selector = str(fetch_cfg.get("list_selector") or "article, .news-item, li")
        field_selectors = fetch_cfg.get("field_selectors") if isinstance(fetch_cfg.get("field_selectors"), dict) else {}
        title_sel = str(field_selectors.get("title") or "h2 a, h3 a, a")
        url_sel = str(field_selectors.get("url") or "h2 a@href, h3 a@href, a@href")
        time_sel = str(field_selectors.get("published_at") or "time@datetime, time, .time")
        sum_sel = str(field_selectors.get("summary") or ".summary, p")

        def _extract(node: Any, selector_spec: str) -> str:
            for candidate in [s.strip() for s in selector_spec.split(",") if s.strip()]:
                sel, _, attr = candidate.partition("@")
                target = node.select_one(sel.strip()) if sel.strip() else node
                if not target:
                    continue
                if attr:
                    val = target.get(attr.strip())
                    if val:
                        return str(val).strip()
                else:
                    txt = target.get_text(" ", strip=True)
                    if txt:
                        return txt
            return ""

        out: list[dict[str, Any]] = []
        for node in soup.select(list_selector):
            title = _extract(node, title_sel)
            raw_url = _extract(node, url_sel)
            if not title and not raw_url:
                continue
            out.append({
                "title": title,
                "url": urljoin(base_url, raw_url) if raw_url else "",
                "summary": _extract(node, sum_sel),
                "published_at": _extract(node, time_sel),
            })
        return out
    except ImportError:
        # Lightweight regex fallback if bs4 is not installed
        out = []
        for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text, re.I | re.S):
            href, inner = match.group(1).strip(), _strip_html(match.group(2))
            if inner and href:
                out.append({"title": inner, "url": urljoin(base_url, href), "summary": "", "published_at": ""})
        return out


def fetch_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    kind = str(source.get("kind") or "rss").lower()
    url = str(source.get("url") or "").strip()
    fetch_cfg = source.get("fetch") if isinstance(source.get("fetch"), dict) else {}
    timeout = int(fetch_cfg.get("timeout_seconds") or 20)
    headers = fetch_cfg.get("headers") if isinstance(fetch_cfg.get("headers"), dict) else {}
    status, body = _http_get(url, headers, timeout)
    if status >= 400:
        raise RuntimeError(f"http_status_{status}")
    if kind == "rss":
        return _parse_rss(body, url)
    if kind == "http_json":
        return _parse_http_json(body, url, fetch_cfg)
    if kind == "http_html":
        return _parse_http_html(body, url, fetch_cfg)
    raise ValueError(f"unsupported_source_kind:{kind}")


def normalize_item(
    raw_item: dict[str, Any],
    source: dict[str, Any],
    watchlists: list[dict[str, Any]] | None = None,
    *,
    fetched_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Normalize a raw item into a news_items record dict. Rejects items without a valid URL or title."""
    now = fetched_at or datetime.now(UTC)
    title = _strip_html(str(raw_item.get("title") or ""))
    summary = _strip_html(str(raw_item.get("summary") or ""))
    url = str(raw_item.get("url") or "").strip()
    parsed = urlparse(url)
    if not title or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    filters = source.get("filters") if isinstance(source.get("filters"), dict) else {}
    include_kw = [str(k).strip() for k in (filters.get("include_keywords") or []) if str(k).strip()]
    exclude_kw = [str(k).strip() for k in (filters.get("exclude_keywords") or []) if str(k).strip()]
    combined_text = f"{title} {summary}"
    if exclude_kw and any(kw.lower() in combined_text.lower() for kw in exclude_kw):
        return None
    if include_kw and not any(kw.lower() in combined_text.lower() for kw in include_kw):
        return None

    published_at, time_inferred = _parse_datetime(raw_item.get("published_at"), now)

    wl_tickers: list[str] = []
    wl_keywords: list[str] = []
    for wl in watchlists or []:
        if not isinstance(wl, dict):
            continue
        for t in wl.get("tickers") or []:
            tu = str(t).strip().upper()
            if tu and tu not in wl_tickers:
                wl_tickers.append(tu)
        for kw in wl.get("keywords") or []:
            ks = str(kw).strip()
            if ks and ks not in wl_keywords:
                wl_keywords.append(ks)

    tickers: list[str] = []
    for t in raw_item.get("tickers") or []:
        tu = str(t).strip().upper()
        if tu and tu not in tickers:
            tickers.append(tu)
    for t in wl_tickers:
        if t.lower() in combined_text.lower() and t not in tickers:
            tickers.append(t)
    for match in _TICKER_RE.findall(combined_text):
        tok = match.strip().upper()
        if tok not in _STOP_WORDS and tok not in tickers and (tok in wl_tickers or tok.isalpha()):
            tickers.append(tok)

    keywords_hit = [kw for kw in wl_keywords if kw.lower() in combined_text.lower()]
    dedupe_raw = str(raw_item.get("external_id") or url).strip()
    dedupe_key = hashlib.sha256(f"{source['id']}:{dedupe_raw}".encode("utf-8")).hexdigest()

    return {
        "source_id": str(source["id"]),
        "source_name": str(source.get("name") or source["id"]),
        "dedupe_key": dedupe_key,
        "title": title[:500],
        "url": url[:2048],
        "summary": summary[:4000],
        "body_text": str(raw_item.get("body_text"))[:20000] if raw_item.get("body_text") else None,
        "published_at": published_at,
        "fetched_at": now,
        "time_inferred": time_inferred,
        "tickers": tickers[:20],
        "keywords_hit": keywords_hit[:20],
        "language": str(source.get("language") or "zh-CN"),
        "raw_ref": str(raw_item.get("raw_ref"))[:512] if raw_item.get("raw_ref") else None,
    }


def upsert_news_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ensure_news_tables()
    saved_rows: list[dict[str, Any]] = []
    if not items:
        return saved_rows
    with SessionLocal() as db:
        for item in items:
            if not item or not item.get("url") or not item.get("title"):
                continue
            existing = db.scalar(
                select(NewsItem).where(
                    NewsItem.source_id == item["source_id"],
                    NewsItem.dedupe_key == item["dedupe_key"],
                )
            )
            if existing is None:
                row = NewsItem(
                    id=uuid.uuid4(),
                    source_id=item["source_id"],
                    source_name=item["source_name"],
                    dedupe_key=item["dedupe_key"],
                    title=item["title"],
                    url=item["url"],
                    summary=item.get("summary") or "",
                    body_text=item.get("body_text"),
                    published_at=item["published_at"],
                    fetched_at=item["fetched_at"],
                    time_inferred=bool(item.get("time_inferred", False)),
                    tickers=list(item.get("tickers") or []),
                    keywords_hit=list(item.get("keywords_hit") or []),
                    language=item.get("language") or "zh-CN",
                    raw_ref=item.get("raw_ref"),
                )
                db.add(row)
                db.flush()
            else:
                existing.title = item["title"]
                existing.url = item["url"]
                existing.summary = item.get("summary") or existing.summary
                existing.tickers = list(item.get("tickers") or existing.tickers or [])
                existing.keywords_hit = list(item.get("keywords_hit") or existing.keywords_hit or [])
                existing.fetched_at = item["fetched_at"]
                row = existing
            saved_rows.append(_row_to_dict(row))
        db.commit()
    return saved_rows


def _row_to_dict(row: NewsItem, *, score: float = 1.0) -> dict[str, Any]:
    pub_dt = row.published_at
    if isinstance(pub_dt, datetime):
        pub_iso = (pub_dt if pub_dt.tzinfo else pub_dt.replace(tzinfo=UTC)).isoformat()
    else:
        pub_iso = str(pub_dt or "")
    fetched_dt = row.fetched_at
    if isinstance(fetched_dt, datetime):
        fetched_iso = (fetched_dt if fetched_dt.tzinfo else fetched_dt.replace(tzinfo=UTC)).isoformat()
    else:
        fetched_iso = str(fetched_dt or "")
    excerpt = row.summary or row.title
    return {
        "id": str(row.id),
        "source_id": row.source_id,
        "source_name": row.source_name,
        "title": row.title,
        "url": row.url,
        "summary": row.summary,
        "excerpt": f"{row.title} — {excerpt}" if excerpt and excerpt != row.title else row.title,
        "published_at": pub_iso,
        "fetched_at": fetched_iso,
        "time_inferred": bool(row.time_inferred),
        "tickers": list(row.tickers or []),
        "symbol": (row.tickers[0] if row.tickers else None),
        "keywords_hit": list(row.keywords_hit or []),
        "language": row.language,
        "origin": "configured_news",
        "source_type": "configured_news",
        "score": round(float(score), 4),
    }


def _update_source_health_in_config(source_id: str, health: str, custom_path: str | Path | None = None) -> None:
    try:
        cfg = load_config(custom_path)
        changed = False
        for src in cfg.get("sources") or []:
            if src.get("id") == source_id and src.get("health") != health:
                src["health"] = health
                changed = True
        if changed:
            save_config(cfg, custom_path)
    except Exception:
        pass


def poll_once(
    source_ids: list[str] | None = None,
    *,
    force: bool = False,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Poll enabled configured news sources once, respecting per-source rate limits and backoff."""
    ensure_news_tables()
    cfg = load_config(custom_path)
    defaults = cfg.get("defaults") or {}
    max_items = int(defaults.get("max_items_per_source") or 50)
    watchlists = cfg.get("watchlists") or []
    wanted_ids = {str(s).strip() for s in (source_ids or []) if str(s).strip()}

    polled: list[str] = []
    skipped_rate_limit: list[str] = []
    errors: dict[str, str] = {}
    total_saved = 0
    now_mono = time.monotonic()

    for src in cfg.get("sources") or []:
        sid = src["id"]
        if not src.get("enabled", True):
            continue
        if wanted_ids and sid not in wanted_ids:
            continue
        state = _RUNTIME_STATE.setdefault(
            sid,
            {
                "last_fetch_at": 0.0,
                "consecutive_failures": 0,
                "backoff_until": 0.0,
                "last_error": None,
                "last_sample": [],
                "health": src.get("health", "ok"),
                "rea_repairs_at": [],
            },
        )
        min_interval = int((src.get("rate_limit") or {}).get("min_interval_seconds", 60))
        if not force:
            if state["backoff_until"] > now_mono:
                skipped_rate_limit.append(sid)
                continue
            if state["last_fetch_at"] > 0 and (now_mono - state["last_fetch_at"]) < min_interval:
                skipped_rate_limit.append(sid)
                continue

        try:
            raw_items = fetch_source(src)
            state["last_fetch_at"] = now_mono
            normalized: list[dict[str, Any]] = []
            now_utc = datetime.now(UTC)
            for raw in raw_items[:max_items]:
                norm = normalize_item(raw, src, watchlists, fetched_at=now_utc)
                if norm is not None:
                    normalized.append(norm)
            if not raw_items or not normalized:
                raise RuntimeError("empty_or_unparseable_items")
            saved = upsert_news_items(normalized)
            total_saved += len(saved)
            state["consecutive_failures"] = 0
            state["backoff_until"] = 0.0
            state["last_error"] = None
            state["last_sample"] = saved[:3]
            state["health"] = "ok"
            _update_source_health_in_config(sid, "ok", custom_path)
            polled.append(sid)
        except Exception as exc:
            state["last_fetch_at"] = now_mono
            fails = int(state.get("consecutive_failures") or 0) + 1
            state["consecutive_failures"] = fails
            state["last_error"] = str(exc)
            backoff_seconds = min(1800, (2 ** min(fails, 6)) * 15)
            state["backoff_until"] = now_mono + backoff_seconds
            # Per §4.2: consecutive failures keep enabled=true but mark health=degraded
            new_health = "degraded" if fails >= 2 else state.get("health", "ok")
            state["health"] = new_health
            _update_source_health_in_config(sid, new_health, custom_path)
            errors[sid] = str(exc)

    return {
        "ok": True,
        "polled_sources": polled,
        "skipped_rate_limit": skipped_rate_limit,
        "saved_count": total_saved,
        "errors": errors,
    }


def search_configured_news(
    query: str | None = None,
    tickers: list[str] | None = None,
    keywords: list[str] | None = None,
    source_ids: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 10,
    refresh_first: bool = False,
    *,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Search items from enabled configured news sources only (§3.1).

    Never calls REA on this hot path.
    When empty, sets meta.hint = 'fallback_to_web_search_recommended'.
    """
    ensure_news_tables()
    cfg = load_config(custom_path)
    enabled_sources = [s["id"] for s in (cfg.get("sources") or []) if s.get("enabled", True)]
    if source_ids:
        requested = {str(s).strip() for s in source_ids if str(s).strip()}
        searched_sources = [sid for sid in enabled_sources if sid in requested]
    else:
        searched_sources = list(enabled_sources)

    bounded_limit = max(1, min(30, int(limit or 10)))
    refreshed = False
    if refresh_first and searched_sources:
        poll_res = poll_once(searched_sources, force=False, custom_path=custom_path)
        refreshed = bool(poll_res.get("polled_sources"))

    if not searched_sources:
        return {
            "ok": True,
            "items": [],
            "data": [],
            "meta": {
                "searched_sources": [],
                "total_matched": 0,
                "refreshed": False,
                "hint": "fallback_to_web_search_recommended",
            },
        }

    since_dt: datetime | None = None
    until_dt: datetime | None = None
    now_utc = datetime.now(UTC)
    if since:
        dt, inferred = _parse_datetime(since, now_utc)
        if not inferred:
            since_dt = dt
    if until:
        dt, inferred = _parse_datetime(until, now_utc)
        if not inferred:
            until_dt = dt

    wanted_tickers = [str(t).strip().upper() for t in (tickers or []) if str(t).strip()]
    wanted_keywords = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    query_terms = [tok.strip().lower() for tok in re.split(r"[\s,，、]+", str(query or "")) if tok.strip()]

    with SessionLocal() as db:
        stmt = (
            select(NewsItem)
            .where(NewsItem.source_id.in_(searched_sources))
            .order_by(desc(NewsItem.published_at))
            .limit(300)
        )
        rows = list(db.scalars(stmt))

    matched: list[dict[str, Any]] = []
    for row in rows:
        # Reject any corrupted row without a valid URL
        parsed_url = urlparse(str(row.url or ""))
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            continue
        pub_dt = row.published_at
        if isinstance(pub_dt, datetime):
            pub_utc = pub_dt if pub_dt.tzinfo else pub_dt.replace(tzinfo=UTC)
        else:
            pub_utc = now_utc
        if since_dt and pub_utc < since_dt:
            continue
        if until_dt and pub_utc > until_dt:
            continue

        haystack = f"{row.title} {row.summary} {' '.join(row.tickers or [])} {' '.join(row.keywords_hit or [])}".lower()
        row_tickers = {str(t).upper() for t in (row.tickers or [])}

        score = 0.5
        if wanted_tickers:
            ticker_hits = sum(1 for t in wanted_tickers if t in row_tickers or t.lower() in haystack)
            if ticker_hits == 0:
                continue
            score += min(0.3, 0.15 * ticker_hits)
        if wanted_keywords:
            kw_hits = sum(1 for kw in wanted_keywords if kw in haystack)
            if kw_hits == 0:
                continue
            score += min(0.2, 0.1 * kw_hits)
        if query_terms:
            q_hits = sum(1 for term in query_terms if term in haystack)
            if q_hits == 0 and not wanted_tickers and not wanted_keywords:
                continue
            score += min(0.2, 0.05 * q_hits)

        item_dict = _row_to_dict(row, score=min(0.99, score))
        matched.append(item_dict)

    matched.sort(key=lambda x: (x.get("score", 0.0), x.get("published_at", "")), reverse=True)
    top_items = matched[:bounded_limit]
    meta: dict[str, Any] = {
        "searched_sources": searched_sources,
        "total_matched": len(matched),
        "refreshed": refreshed,
    }
    if not top_items:
        meta["hint"] = "fallback_to_web_search_recommended"

    return {
        "ok": True,
        "items": top_items,
        "data": top_items,
        "source": "configured_news",
        "meta": meta,
        **({"hint": "fallback_to_web_search_recommended"} if not top_items else {}),
    }


def list_news_sources(custom_path: str | Path | None = None) -> dict[str, Any]:
    """Return current configured news sources summary without sensitive headers (§3.3)."""
    cfg = load_config(custom_path)
    sanitized_sources: list[dict[str, Any]] = []
    for src in cfg.get("sources") or []:
        sid = src["id"]
        rt = _RUNTIME_STATE.get(sid) or {}
        headers = (src.get("fetch") or {}).get("headers") or {}
        safe_headers = {
            k: v for k, v in headers.items() if str(k).strip().lower() not in _SENSITIVE_HEADERS
        }
        sanitized_sources.append({
            "id": sid,
            "name": src.get("name"),
            "enabled": bool(src.get("enabled", True)),
            "kind": src.get("kind"),
            "url": src.get("url"),
            "homepage": src.get("homepage"),
            "language": src.get("language"),
            "tags": src.get("tags") or [],
            "markets": src.get("markets") or [],
            "health": rt.get("health") or src.get("health", "ok"),
            "rea_repair": bool(src.get("rea_repair", True)),
            "alternates": src.get("alternates") or [],
            "rate_limit": src.get("rate_limit") or {"min_interval_seconds": 60},
            "fetch": {**(src.get("fetch") or {}), "headers": safe_headers},
            "consecutive_failures": int(rt.get("consecutive_failures") or 0),
            "last_error": rt.get("last_error"),
        })
    return {
        "ok": True,
        "version": cfg.get("version", 1),
        "defaults": cfg.get("defaults") or {},
        "sources": sanitized_sources,
        "watchlists": cfg.get("watchlists") or [],
        "push": cfg.get("push") or {},
        "data": sanitized_sources,
    }


def dry_run_news_source(
    source_id: str | None = None,
    *,
    source_spec: dict[str, Any] | None = None,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch 1 page from a configured or candidate source and validate that >=1 item has title/url/time."""
    cfg = load_config(custom_path)
    target = source_spec
    if target is None:
        target = next((s for s in (cfg.get("sources") or []) if s.get("id") == source_id), None)
    if not isinstance(target, dict):
        return {"ok": False, "error_code": "news_source_not_found", "data": []}
    try:
        raw_items = fetch_source(target)
        now_utc = datetime.now(UTC)
        normalized = [
            norm
            for raw in raw_items[:10]
            if (norm := normalize_item(raw, target, cfg.get("watchlists") or [], fetched_at=now_utc)) is not None
        ]
        sample = [
            {
                "title": item["title"],
                "url": item["url"],
                "published_at": item["published_at"].isoformat(),
                "time_inferred": item["time_inferred"],
                "tickers": item["tickers"],
            }
            for item in normalized[:5]
        ]
        passed = len(sample) >= 1
        return {
            "ok": passed,
            "source_id": target.get("id"),
            "raw_count": len(raw_items),
            "valid_count": len(normalized),
            "sample": sample,
            "data": sample,
            **({"error_code": "dry_run_zero_valid_items"} if not passed else {}),
        }
    except Exception as exc:
        return {
            "ok": False,
            "source_id": target.get("id"),
            "error_code": "dry_run_fetch_failed",
            "reason": str(exc),
            "data": [],
        }


def upsert_news_source(
    source: dict[str, Any],
    *,
    require_confirm_on_major_change: bool = False,
    owner_hash: str = "owner",
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create or update a news source in news_sources.yaml (human & Agent parity, §3.3)."""
    if not isinstance(source, dict) or not source.get("id"):
        return {"ok": False, "error_code": "invalid_source_spec", "data": []}
    cfg = load_config(custom_path)
    sources = list(cfg.get("sources") or [])
    sid = str(source["id"]).strip()
    existing_idx = next((i for i, s in enumerate(sources) if s.get("id") == sid), None)
    existing = sources[existing_idx] if existing_idx is not None else None

    merged = {**(existing or {}), **source, "id": sid}
    if existing is not None:
        old_host = urlparse(str(existing.get("url") or "")).netloc
        new_host = urlparse(str(merged.get("url") or "")).netloc
        major_change = (old_host and new_host and old_host != new_host) or (
            existing.get("kind") and merged.get("kind") and existing.get("kind") != merged.get("kind")
        )
        if major_change and require_confirm_on_major_change:
            confirmation = create_inline_confirmation(
                owner_hash=owner_hash,
                title=f"确认切换新闻源「{merged.get('name') or sid}」站点或解析结构",
                summary=f"源 {sid} 拟从 {existing.get('url')} 切换为 {merged.get('url')}（kind={merged.get('kind')}）。",
                operation={"action": "upsert_news_source", "source": merged},
            )
            return {
                "ok": True,
                "status": "pending_confirm",
                "confirmation": confirmation,
                "data": [confirmation],
            }

    if existing_idx is not None:
        sources[existing_idx] = merged
    else:
        sources.append(merged)
    cfg["sources"] = sources
    saved_cfg = save_config(cfg, custom_path)
    updated = next(s for s in saved_cfg["sources"] if s["id"] == sid)
    if sid in _RUNTIME_STATE:
        _RUNTIME_STATE[sid]["health"] = updated.get("health", "ok")
        if updated.get("enabled"):
            _RUNTIME_STATE[sid]["consecutive_failures"] = 0
            _RUNTIME_STATE[sid]["backoff_until"] = 0.0
    return {"ok": True, "status": "updated" if existing else "created", "source": updated, "data": [updated]}


def disable_news_source(
    source_id: str,
    *,
    enabled: bool = False,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Enable or disable a configured news source by id."""
    cfg = load_config(custom_path)
    target = next((s for s in (cfg.get("sources") or []) if s.get("id") == source_id), None)
    if target is None:
        return {"ok": False, "error_code": "news_source_not_found", "data": []}
    target["enabled"] = bool(enabled)
    save_config(cfg, custom_path)
    return {
        "ok": True,
        "source_id": source_id,
        "enabled": bool(enabled),
        "status": "enabled" if enabled else "disabled",
        "source": target,
        "data": [{"id": source_id, "enabled": bool(enabled)}],
    }


def delete_news_source(
    source_id: str,
    *,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Delete a configured news source by id."""
    cfg = load_config(custom_path)
    sources = list(cfg.get("sources") or [])
    remaining = [s for s in sources if s.get("id") != source_id]
    if len(remaining) == len(sources):
        return {"ok": False, "error_code": "news_source_not_found", "data": []}
    cfg["sources"] = remaining
    save_config(cfg, custom_path)
    _RUNTIME_STATE.pop(source_id, None)
    return {"ok": True, "source_id": source_id, "status": "deleted", "data": [{"id": source_id, "status": "deleted"}]}


def set_news_source_alternate(
    source_id: str,
    alternates: list[Any],
    *,
    switch_to_first: bool = False,
    require_confirm: bool = False,
    owner_hash: str = "owner",
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update alternates[] for a news source and optionally switch its primary URL to the first alternate."""
    cfg = load_config(custom_path)
    target = next((s for s in (cfg.get("sources") or []) if s.get("id") == source_id), None)
    if target is None:
        return {"ok": False, "error_code": "news_source_not_found", "data": []}
    target["alternates"] = list(alternates or [])
    if switch_to_first and target["alternates"]:
        first_alt = target["alternates"][0]
        new_url = first_alt.get("url") if isinstance(first_alt, dict) else str(first_alt)
        if require_confirm:
            save_config(cfg, custom_path)
            confirmation = create_inline_confirmation(
                owner_hash=owner_hash,
                title=f"确认将新闻源「{target.get('name') or source_id}」切换至备用地址",
                summary=f"原地址 {target.get('url')} 连续失败，拟切换至备用源 {new_url}。",
                operation={
                    "action": "switch_news_source_url",
                    "source_id": source_id,
                    "new_url": new_url,
                },
            )
            return {
                "ok": True,
                "status": "pending_confirm",
                "source_id": source_id,
                "proposed_url": new_url,
                "confirmation": confirmation,
                "data": [confirmation],
            }
        target["url"] = new_url
        target["health"] = "ok"
    save_config(cfg, custom_path)
    return {
        "ok": True,
        "source_id": source_id,
        "url": target["url"],
        "alternates": target["alternates"],
        "data": [target],
    }


def diagnose_news_source(source_id: str, *, custom_path: str | Path | None = None) -> dict[str, Any]:
    """Diagnose a configured news source: return runtime error state, health, and a live dry-run check."""
    cfg = load_config(custom_path)
    target = next((s for s in (cfg.get("sources") or []) if s.get("id") == source_id), None)
    if target is None:
        return {"ok": False, "error_code": "news_source_not_found", "data": []}
    rt = _RUNTIME_STATE.get(source_id) or {}
    probe = dry_run_news_source(source_id, source_spec=target, custom_path=custom_path)
    health = "ok" if probe.get("ok") else ("degraded" if int(rt.get("consecutive_failures") or 0) >= 1 else "failed")
    diagnosis = {
        "source_id": source_id,
        "name": target.get("name"),
        "enabled": bool(target.get("enabled", True)),
        "health": health,
        "consecutive_failures": int(rt.get("consecutive_failures") or 0),
        "last_error": probe.get("reason") or rt.get("last_error"),
        "rea_repair_allowed": bool(target.get("rea_repair", True)),
        "alternates": target.get("alternates") or [],
        "probe_ok": bool(probe.get("ok")),
        "probe_valid_count": int(probe.get("valid_count") or 0),
        "sample": probe.get("sample") or rt.get("last_sample") or [],
    }
    return {"ok": True, "diagnosis": diagnosis, "data": [diagnosis]}


def _discover_feed_or_structure_via_rea(
    source: dict[str, Any],
    rea_inspector: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run REA inspection on source homepage or alternates to discover a working feed URL or selectors."""
    if rea_inspector is not None:
        return rea_inspector(source)
    homepage = str(source.get("homepage") or "").strip()
    headers = ((source.get("fetch") or {}).get("headers")) or {"User-Agent": "FinBotNewsBot/1.0"}
    if homepage:
        try:
            status, html_text = _http_get(homepage, headers, 15)
            if status < 400 and html_text:
                # Look for <link rel="alternate" type="application/rss+xml" href="...">
                for match in re.finditer(
                    r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
                    html_text,
                    re.I,
                ):
                    discovered_url = urljoin(homepage, match.group(1).strip())
                    return {"discovered_url": discovered_url, "kind": "rss", "method": "rea_dom_feed_link"}
        except Exception:
            pass
    # Fall back to alternates if configured
    for alt in source.get("alternates") or []:
        alt_url = alt.get("url") if isinstance(alt, dict) else str(alt).strip()
        if alt_url:
            return {"discovered_url": alt_url, "kind": source.get("kind", "rss"), "method": "rea_alternate_fallback"}
    return {}


def repair_news_source_with_rea(
    source_id: str,
    *,
    rea_inspector: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    require_confirm: bool = True,
    owner_hash: str = "owner",
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Repair a failing news source using REA inspection or configured alternates (§3.3, §4.4).

    Enforces:
    - `rea_repair: true` check on the source
    - Per-source rate limit (at most 1 full REA repair attempt per hour)
    - Dry-run verification of the repaired configuration
    - Inline confirmation (`pending_confirm`) for structural/domain changes when `require_confirm=True`
    """
    cfg = load_config(custom_path)
    target = next((s for s in (cfg.get("sources") or []) if s.get("id") == source_id), None)
    if target is None:
        return {"ok": False, "error_code": "news_source_not_found", "data": []}
    if not target.get("rea_repair", True):
        return {"ok": False, "error_code": "rea_repair_disabled_for_source", "source_id": source_id, "data": []}

    now_mono = time.monotonic()
    state = _RUNTIME_STATE.setdefault(
        source_id,
        {
            "last_fetch_at": 0.0,
            "consecutive_failures": 0,
            "backoff_until": 0.0,
            "last_error": None,
            "last_sample": [],
            "health": target.get("health", "ok"),
            "rea_repairs_at": [],
        },
    )
    recent_repairs = [ts for ts in state.get("rea_repairs_at", []) if (now_mono - ts) < REA_REPAIR_COOLDOWN_SECONDS]
    state["rea_repairs_at"] = recent_repairs
    if len(recent_repairs) >= 1:
        return {
            "ok": False,
            "error_code": "rea_repair_rate_limited",
            "source_id": source_id,
            "cooldown_seconds": REA_REPAIR_COOLDOWN_SECONDS,
            "data": [],
        }

    state["rea_repairs_at"].append(now_mono)
    _REA_AUDIT_LOG.append({
        "source_id": source_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "channel": "repair_news_source_with_rea",
    })

    suggestion = _discover_feed_or_structure_via_rea(target, rea_inspector=rea_inspector)
    if not suggestion:
        return {
            "ok": False,
            "error_code": "rea_repair_no_candidate_found",
            "source_id": source_id,
            "data": [],
        }

    candidate = copy.deepcopy(target)
    if suggestion.get("discovered_url"):
        candidate["url"] = str(suggestion["discovered_url"])
    if suggestion.get("kind"):
        candidate["kind"] = str(suggestion["kind"])
    if isinstance(suggestion.get("fetch"), dict):
        candidate["fetch"] = {**(candidate.get("fetch") or {}), **suggestion["fetch"]}

    dry = dry_run_news_source(source_id, source_spec=candidate, custom_path=custom_path)
    if not dry.get("ok"):
        return {
            "ok": False,
            "error_code": "rea_repair_dry_run_failed",
            "source_id": source_id,
            "candidate_url": candidate.get("url"),
            "data": [],
        }

    if require_confirm:
        confirmation = create_inline_confirmation(
            owner_hash=owner_hash,
            title=f"确认应用 REA 修复方案 · {target.get('name') or source_id}",
            summary=(
                f"REA 诊断并验证了新配置（method={suggestion.get('method', 'rea')}，"
                f"url={candidate.get('url')}，抽样解析 {dry.get('valid_count', 0)} 条有效新闻）。"
            ),
            operation={"action": "upsert_news_source", "source": {**candidate, "health": "ok"}},
        )
        return {
            "ok": True,
            "status": "pending_confirm",
            "source_id": source_id,
            "method": suggestion.get("method", "rea"),
            "candidate": candidate,
            "sample": dry.get("sample") or [],
            "confirmation": confirmation,
            "data": [confirmation],
        }

    applied = upsert_news_source(
        {**candidate, "health": "ok"},
        require_confirm_on_major_change=False,
        owner_hash=owner_hash,
        custom_path=custom_path,
    )
    return {
        "ok": True,
        "status": "repaired",
        "source_id": source_id,
        "method": suggestion.get("method", "rea"),
        "source": applied.get("source"),
        "sample": dry.get("sample") or [],
        "data": [applied.get("source")],
    }


def generate_digest_summary(
    items: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Format a digest summary of news items respecting push_summary_max_tokens (Product A, §6.A).

    Approximately 1 token ~= 1.5 Chinese chars / 3.5 ASCII chars; we enforce both a token estimate
    and character budget proportional to `max_tokens` so changing `summary_max_tokens` (e.g. 128 vs 256 vs 512)
    predictably changes the generated digest length and appends `（已按你的长度上限截断）` when truncated.
    """
    budget_tokens = effective_summary_max_tokens(
        load_config(custom_path) if custom_path or os.path.exists(config_path()) else default_config_dict(),
        override=max_tokens,
    )
    # Deterministic character ceiling proportional to token cap (e.g., 128 -> ~192 chars, 256 -> ~384 chars, 512 -> ~768 chars)
    char_budget = max(60, int(budget_tokens * 1.5))

    lines: list[str] = []
    for idx, item in enumerate(items, 1):
        title = str(item.get("title") or "").strip()
        src = str(item.get("source_name") or item.get("source_id") or "快讯").strip()
        url = str(item.get("url") or "").strip()
        summary = str(item.get("summary") or "").strip()
        tickers = ", ".join(item.get("tickers") or [])
        prefix = f"{idx}. [{src}]" + (f" ({tickers})" if tickers else "")
        detail = f" {title}" + (f"：{summary}" if summary and summary != title else "")
        line = f"{prefix}{detail}" + (f" ({url})" if url else "")
        lines.append(line)

    full_text = "\n".join(lines).strip()
    truncated = False
    if len(full_text) > char_budget:
        truncated = True
        cutoff = max(20, char_budget - len(TRUNCATION_FOOTNOTE) - 1)
        summary_text = full_text[:cutoff].rstrip() + "…\n" + TRUNCATION_FOOTNOTE
    else:
        summary_text = full_text

    return {
        "summary": summary_text,
        "truncated": truncated,
        "summary_max_tokens": budget_tokens,
        "char_budget": char_budget,
        "item_count": len(items),
    }


def create_inline_confirmation(
    *,
    owner_hash: str,
    title: str,
    summary: str,
    operation: dict[str, Any],
    task_id: str | uuid.UUID | None = None,
    bot_id: str | uuid.UUID | None = None,
    conversation_id: str | uuid.UUID | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a notification / routine item supporting direct inline confirmation (Product B, §6.B).

    State machine: `pending_confirm -> confirmed | skipped | expired`
    """
    ensure_news_tables()
    item_id = uuid.uuid4()
    action_token = uuid.uuid4().hex
    short_code = hashlib.sha256(f"{item_id}:{action_token}".encode("utf-8")).hexdigest()[:6].upper()
    now_iso = datetime.now(UTC).isoformat()
    expires_iso = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

    payload = {
        "confirmation_state": "pending_confirm",
        "action_token": action_token,
        "short_code": short_code,
        "operation": operation,
        "created_at": now_iso,
        "expires_at": expires_iso,
        "actions": [
            {"id": "confirm", "label": "确认"},
            {"id": "skip", "label": "跳过"},
            {"id": "later", "label": "稍后"},
        ],
        "open_chat_secondary": True,
        "text_fallback_hint": f"回复 CONFIRM {short_code} 也可直接确认",
    }

    parsed_task_id = None
    if task_id:
        try:
            parsed_task_id = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
        except Exception:
            parsed_task_id = None
    parsed_bot_id = None
    if bot_id:
        try:
            parsed_bot_id = bot_id if isinstance(bot_id, uuid.UUID) else uuid.UUID(str(bot_id))
        except Exception:
            parsed_bot_id = None
    parsed_conv_id = None
    if conversation_id:
        try:
            parsed_conv_id = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
        except Exception:
            parsed_conv_id = None

    with SessionLocal() as db:
        row = AgentInboxItem(
            id=item_id,
            owner_hash=owner_hash or "owner",
            section="confirm",
            kind="action",
            title=title[:200],
            body=summary,
            sources=list(sources or []),
            payload=payload,
            task_id=parsed_task_id,
            bot_id=parsed_bot_id,
            conversation_id=parsed_conv_id,
            dedupe_key=f"inline_confirm:{item_id}",
            status="unread",
        )
        db.add(row)
        db.commit()

    return {
        "id": str(item_id),
        "task_id": str(parsed_task_id) if parsed_task_id else str(item_id),
        "title": title[:200],
        "body": summary,
        "confirmation_state": "pending_confirm",
        "action_token": action_token,
        "short_code": short_code,
        "actions": payload["actions"],
        "text_fallback_hint": payload["text_fallback_hint"],
        "expires_at": expires_iso,
    }


def _execute_inline_operation(operation: dict[str, Any], owner_hash: str) -> dict[str, Any]:
    action = str(operation.get("action") or operation.get("name") or "").strip()
    if action == "upsert_news_source" and isinstance(operation.get("source"), dict):
        return upsert_news_source(
            operation["source"],
            require_confirm_on_major_change=False,
            owner_hash=owner_hash,
        )
    if action == "switch_news_source_url":
        sid = str(operation.get("source_id") or "")
        new_url = str(operation.get("new_url") or "")
        cfg = load_config()
        target = next((s for s in (cfg.get("sources") or []) if s.get("id") == sid), None)
        if target is None:
            return {"ok": False, "error_code": "news_source_not_found"}
        target["url"] = new_url
        target["health"] = "ok"
        save_config(cfg)
        return {"ok": True, "source_id": sid, "url": new_url}
    if action == "acknowledge_news_digest":
        return {"ok": True, "status": "digest_confirmed", "digest_ids": operation.get("item_ids") or []}
    if action and isinstance(operation.get("arguments"), dict):
        # Support general FinBot write tools suggested by unattended routines (request_action)
        try:
            from .agent_workflow import consent
            from .agent_workflow.state import AgentRuntimeContext
            ctx = AgentRuntimeContext("custom", "inline", "dummy", owner_hash=owner_hash, identity_credential="inline")
            runner = consent.make_ApprovedOperationRunner(ctx) if hasattr(consent, "make_ApprovedOperationRunner") else None
            if runner:
                return runner(operation)
        except Exception:
            pass
    return {"ok": True, "status": "confirmed", "operation": action or "routine_confirm"}


def handle_inline_confirmation_action(
    identifier: str,
    action: str = "confirm",
    *,
    owner_hash: str | None = None,
    action_token: str | None = None,
) -> dict[str, Any]:
    """Transition an inline confirmation item (`pending_confirm -> confirmed | skipped | expired`)
    without requiring the user to open the chat first (§6.B).

    `identifier` can be:
    - the inbox item UUID (`id`)
    - the routine `task_id` UUID
    - the 6-char `short_code` (from `CONFIRM <short_code>`)
    """
    ensure_news_tables()
    normalized_action = str(action or "confirm").strip().lower()
    if normalized_action not in {"confirm", "skip", "later", "expire"}:
        raise ValueError("invalid_confirmation_action")

    ident = str(identifier or "").strip()
    if ident.upper().startswith("CONFIRM "):
        ident = ident.split(None, 1)[1].strip()

    with SessionLocal() as db:
        row: AgentInboxItem | None = None
        try:
            uid = uuid.UUID(ident)
            row = db.get(AgentInboxItem, uid)
            if row is None:
                stmt = (
                    select(AgentInboxItem)
                    .where(AgentInboxItem.task_id == uid, AgentInboxItem.section == "confirm")
                    .order_by(desc(AgentInboxItem.created_at))
                )
                row = db.scalar(stmt)
        except ValueError:
            # Match by short_code or action_token
            stmt = select(AgentInboxItem).where(AgentInboxItem.section == "confirm").order_by(desc(AgentInboxItem.created_at))
            for candidate in db.scalars(stmt):
                payload = candidate.payload if isinstance(candidate.payload, dict) else {}
                if (
                    str(payload.get("short_code") or "").upper() == ident.upper()
                    or str(payload.get("action_token") or "") == ident
                ):
                    row = candidate
                    break

        if row is None:
            raise LookupError("confirmation_not_found")
        if owner_hash and row.owner_hash not in {owner_hash, "owner"}:
            raise LookupError("confirmation_not_found")

        payload = copy.deepcopy(row.payload if isinstance(row.payload, dict) else {})
        current_state = str(payload.get("confirmation_state") or ("confirmed" if row.status == "done" else "pending_confirm"))

        if action_token and payload.get("action_token") and action_token != payload.get("action_token"):
            raise PermissionError("invalid_action_token")

        # Check expiration
        expires_at_raw = payload.get("expires_at")
        if expires_at_raw and current_state == "pending_confirm":
            exp_dt, inferred = _parse_datetime(expires_at_raw, datetime.now(UTC))
            if not inferred and datetime.now(UTC) > exp_dt:
                current_state = "expired"
                payload["confirmation_state"] = "expired"
                row.payload = payload
                row.status = "dismissed"
                db.commit()
                return {
                    "ok": False,
                    "id": str(row.id),
                    "task_id": str(row.task_id or row.id),
                    "confirmation_state": "expired",
                    "error_code": "confirmation_expired",
                }

        # Idempotent / anti-double-click guard: already confirmed or skipped
        if current_state in {"confirmed", "skipped", "expired"}:
            return {
                "ok": current_state != "expired",
                "id": str(row.id),
                "task_id": str(row.task_id or row.id),
                "confirmation_state": current_state,
                "already_processed": True,
                "outcome": payload.get("execution_outcome"),
            }

        if normalized_action == "later":
            row.status = "read"
            payload["last_action"] = "later"
            payload["updated_at"] = datetime.now(UTC).isoformat()
            row.payload = payload
            db.commit()
            return {
                "ok": True,
                "id": str(row.id),
                "task_id": str(row.task_id or row.id),
                "confirmation_state": "pending_confirm",
                "deferred": True,
            }

        if normalized_action in {"skip", "expire"}:
            new_state = "skipped" if normalized_action == "skip" else "expired"
            payload["confirmation_state"] = new_state
            payload["resolved_at"] = datetime.now(UTC).isoformat()
            row.payload = payload
            row.status = "dismissed"
            db.commit()
            return {
                "ok": True,
                "id": str(row.id),
                "task_id": str(row.task_id or row.id),
                "confirmation_state": new_state,
                "already_processed": False,
            }

        # normalized_action == "confirm"
        operation = payload.get("operation") if isinstance(payload.get("operation"), dict) else {}
        outcome = _execute_inline_operation(operation, row.owner_hash)
        payload["confirmation_state"] = "confirmed"
        payload["confirmed_at"] = datetime.now(UTC).isoformat()
        payload["execution_outcome"] = outcome
        row.payload = payload
        row.status = "done"
        if row.conversation_id:
            try:
                from .agent_chat_models import AgentMessage
                from sqlalchemy import func as _sa_func
                seq = db.scalar(
                    select(_sa_func.coalesce(_sa_func.max(AgentMessage.sequence), 0)).where(
                        AgentMessage.conversation_id == row.conversation_id
                    )
                )
                turn_uuid = uuid.uuid4()
                db.add(
                    AgentMessage(
                        conversation_id=row.conversation_id,
                        turn_id=turn_uuid,
                        sequence=(seq or 0) + 1,
                        role="assistant",
                        content=f"已通过通知直接确认执行：{row.title}（状态：已确认，无需重复操作）。",
                        tool_calls={"inline_confirmation": {"item_id": str(row.id), "confirmation_state": "confirmed"}},
                        status="complete",
                    )
                )
            except Exception:
                pass
        db.commit()

        return {
            "ok": True,
            "id": str(row.id),
            "task_id": str(row.task_id or row.id),
            "conversation_id": str(row.conversation_id) if row.conversation_id else None,
            "title": row.title,
            "confirmation_state": "confirmed",
            "already_processed": False,
            "outcome": outcome,
        }


def run_digest_push(
    *,
    owner_hash: str = "owner",
    max_tokens_override: int | None = None,
    custom_path: str | Path | None = None,
) -> dict[str, Any]:
    """Match unpushed news_items against watchlists, generate token-bounded digest, and push card with inline confirm."""
    ensure_news_tables()
    cfg = load_config(custom_path)
    push_cfg = cfg.get("push") or {}
    if not push_cfg.get("enabled", True):
        return {"ok": True, "pushed": False, "reason": "push_disabled"}

    channel = str(push_cfg.get("channel") or "finbot_notify")
    max_items = int(push_cfg.get("max_items_per_digest") or 8)
    watchlists = [w for w in (cfg.get("watchlists") or []) if w.get("push_on_match", True)]

    wl_tickers = {str(t).upper() for w in watchlists for t in (w.get("tickers") or []) if str(t).strip()}
    wl_keywords = {str(k).lower() for w in watchlists for k in (w.get("keywords") or []) if str(k).strip()}

    with SessionLocal() as db:
        pushed_ids = set(
            db.scalars(
                select(NewsPushLog.news_item_id).where(
                    NewsPushLog.owner_hash == owner_hash,
                    NewsPushLog.channel == channel,
                )
            )
        )
        recent_rows = list(
            db.scalars(select(NewsItem).order_by(desc(NewsItem.published_at)).limit(100))
        )

        candidates: list[NewsItem] = []
        for row in recent_rows:
            if row.id in pushed_ids:
                continue
            haystack = f"{row.title} {row.summary}".lower()
            row_tickers = {str(t).upper() for t in (row.tickers or [])}
            if wl_tickers or wl_keywords:
                hit_ticker = bool(wl_tickers & row_tickers) or any(t.lower() in haystack for t in wl_tickers)
                hit_kw = any(k in haystack for k in wl_keywords)
                if not (hit_ticker or hit_kw):
                    continue
            candidates.append(row)
            if len(candidates) >= max_items:
                break

        if not candidates:
            return {"ok": True, "pushed": False, "reason": "no_unpushed_matches", "item_count": 0}

        candidate_dicts = [_row_to_dict(r) for r in candidates]
        digest = generate_digest_summary(
            candidate_dicts,
            max_tokens=max_tokens_override,
            custom_path=custom_path,
        )
        confirmation = create_inline_confirmation(
            owner_hash=owner_hash,
            title=f"新闻快讯简报（{len(candidates)} 条命中自选）",
            summary=digest["summary"],
            operation={
                "action": "acknowledge_news_digest",
                "item_ids": [str(r.id) for r in candidates],
            },
            sources=[
                {
                    "ordinal": idx,
                    "title": r.title,
                    "url": r.url,
                    "source_name": r.source_name,
                    "published_at": r.published_at.isoformat() if isinstance(r.published_at, datetime) else str(r.published_at),
                    "origin": "configured_news",
                }
                for idx, r in enumerate(candidates, 1)
            ],
        )
        inbox_uuid = uuid.UUID(confirmation["id"])
        for r in candidates:
            db.add(
                NewsPushLog(
                    id=uuid.uuid4(),
                    news_item_id=r.id,
                    owner_hash=owner_hash,
                    channel=channel,
                    inbox_item_id=inbox_uuid,
                )
            )
        db.commit()

    return {
        "ok": True,
        "pushed": True,
        "item_count": len(candidates),
        "summary": digest["summary"],
        "truncated": digest["truncated"],
        "summary_max_tokens": digest["summary_max_tokens"],
        "confirmation": confirmation,
    }
