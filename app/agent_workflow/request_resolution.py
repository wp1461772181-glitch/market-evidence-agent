"""Validated intent and source slots. No model-generated entity is authoritative."""
from __future__ import annotations

import json
import re
from typing import Any

from ..symbols import normalize_market_symbol

INTENTS = frozenset({"saved_data", "source_qa", "analysis_qa", "comparison_research",
                     "web_research", "system_action", "general", "other"})
ALIASES = {"苹果": "AAPL", "蘋果": "AAPL", "apple": "AAPL", "微软": "MSFT", "microsoft": "MSFT",
           "英伟达": "NVDA", "nvidia": "NVDA", "超微": "AMD", "amd": "AMD",
           "谷歌": "GOOGL", "google": "GOOGL", "亚马逊": "AMZN", "amazon": "AMZN",
           "特斯拉": "TSLA", "tesla": "TSLA", "meta": "META"}
ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
SOURCE_ID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
TASK_ID = re.compile(r"\b[0-9a-fA-F]{64}\b")
PERIOD = re.compile(r"\b(?:20\d{2}\s*(?:Q[1-4]|FY)|FY\s*20\d{2}|Q[1-4]\s*20\d{2})\b|20\d{2}年(?:第?[一二三四1-4]季度|年报)", re.I)
TICKER = re.compile(r"(?<![A-Za-z])[A-Z][A-Z0-9.-]{0,11}(?![A-Za-z])")


def parse_classification(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise ValueError("invalid_classifier_json")
    stripped = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", stripped, re.I)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_classifier_json") from exc
    if (not isinstance(value, dict) or set(value) != {"intent", "confidence"}
            or value.get("intent") not in INTENTS or type(value.get("confidence")) not in (int, float)
            or not 0 <= value["confidence"] <= 1):
        raise ValueError("invalid_classifier_schema")
    return value


def extract_slots(question: str) -> dict[str, Any]:
    lower = question.lower()
    symbols: list[str] = []
    for alias, symbol in ALIASES.items():
        if (re.search(r"\b" + re.escape(alias) + r"\b", lower) if alias.isascii() else alias in lower):
            if symbol not in symbols:
                symbols.append(symbol)
    # Identifiers and filing forms have uppercase fragments that are not stocks.
    ticker_text = ACCESSION.sub(" ", SOURCE_ID.sub(" ", TASK_ID.sub(" ", question)))
    ticker_text = re.sub(r"(?<![A-Za-z0-9])(?:10-[QK]|8-K)(?![A-Za-z0-9])", " ", ticker_text, flags=re.I)
    ticker_text = PERIOD.sub(" ", ticker_text)
    for raw in TICKER.findall(ticker_text):
        if raw in {"AI", "SEC", "Q1", "Q2", "Q3", "Q4", "FY", "NEWS"}:
            continue
        try:
            symbol = normalize_market_symbol(raw)
        except (TypeError, ValueError):
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    # A misspelling is offered as a choice; the user's actual code is retained.
    suggestion = "AAPL" if "APPL" in symbols else None
    if suggestion:
        symbols.remove("APPL")
    form = next((form for form in ("10-K", "10-Q", "8-K") if form.lower() in lower), None)
    accession = ACCESSION.search(question)
    source_id = SOURCE_ID.search(question)
    period = PERIOD.search(question)
    explicit_web = any(word in lower for word in ("新闻", "网页", "联网", "公开搜索", "web", "news", "online"))
    summary = any(word in lower for word in ("摘要", "总结", "概括", "简介", "简要内容", "简要概括", "summary", "summarize", "overview"))
    analysis = any(word in lower for word in ("ai", "分析", "analysis"))
    return {"symbols": symbols[:5], "symbol": symbols[0] if len(symbols) == 1 else None,
            "suggestion": suggestion, "form": form, "accession_number": accession.group() if accession else None,
            "source_id": source_id.group() if source_id else None,
            "period": period.group() if period else None, "web_required": explicit_web,
            "analysis_required": analysis, "original_required": not analysis or not summary,
            "exact_summary": summary, "topic": question[:80]}


def resume_request(pending: dict[str, Any], reply: str) -> dict[str, Any]:
    slots = extract_slots(reply)
    old = list(pending.get("symbols") or [])
    if slots["symbols"] and pending.get("reason") in {None, "missing_stock_focus", "missing_comparison_symbol", "missing_action_symbol", "missing_action_source", "symbol_suggestion"}:
        # A plain stock reply fills the next comparison slot; an explicit new
        # request is resolved separately by the caller.
        for symbol in slots["symbols"]:
            if symbol not in old:
                old.append(symbol)
    return {**pending, "symbols": old[:5], "reply": reply,
            "accession_number": slots.get("accession_number") or pending.get("accession_number"),
            "source_id": slots.get("source_id") or pending.get("source_id"),
            "form": slots.get("form") or pending.get("form")}


def is_explicit_new_request(reply: str) -> bool:
    return bool(re.search(r"(?:^|\s)(?:new question|instead|forget that)\b|换个问题|另一个问题|不用比较|改问", reply, re.I)
                or re.search(r"(?:总结|概括|比较|对比|搜索|查询|生成|抓取|分析|创建).{2,}(?:财报|新闻|股票|营收|预测|资料|原文|报告)", reply, re.I))
