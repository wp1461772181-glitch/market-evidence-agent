"""Validated intent and source slots. No model-generated entity is authoritative."""
from __future__ import annotations

import json
import re
from typing import Any

from ..company_directory import ALIASES
from ..symbols import normalize_market_symbol

INTENTS = frozenset({"saved_data", "source_qa", "analysis_qa", "comparison_research",
                     "web_research", "system_action", "general", "other"})
ACCESSION = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
SOURCE_ID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
TASK_ID = re.compile(r"\b[0-9a-fA-F]{64}\b")
PERIOD = re.compile(r"\b(?:20\d{2}\s*(?:Q[1-4]|FY)|FY\s*20\d{2}|Q[1-4]\s*20\d{2})\b|20\d{2}年(?:第?[一二三四1-4]季度|年报)", re.I)
TICKER = re.compile(r"(?<![A-Za-z])[A-Z][A-Z0-9.-]{0,11}(?![A-Za-z])")
ACTION_CHOICES = {"fetch_sec": "抓取 SEC 原文", "analyze": "生成 AI 分析",
                  "import": "导入已保存资料", "index": "建立索引",
                  "forecast": "创建预测", "status": "查询任务状态", "refresh_sec": "刷新 SEC 原文"}


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


def extract_slots(question: str, max_symbols: int = 5) -> dict[str, Any]:
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
    return {"symbols": symbols[:max_symbols], "symbol": symbols[0] if len(symbols) == 1 else None,
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
    selected = action_reply(reply) if pending.get("reason") == "missing_action_type" else None
    return {**pending, "symbols": old[:5], "reply": reply,
            "selected_action": selected or pending.get("selected_action"),
            "accession_number": slots.get("accession_number") or pending.get("accession_number"),
            "source_id": slots.get("source_id") or pending.get("source_id"),
            "form": slots.get("form") or pending.get("form")}


def is_explicit_new_request(reply: str) -> bool:
    return bool(re.search(r"(?:^|\s)(?:new question|instead|forget that)\b|换个问题|另一个问题|不用比较|改问", reply, re.I)
                or re.search(r"(?:总结|概括|比较|对比|搜索|查询|生成|抓取|分析|创建).{2,}(?:财报|新闻|股票|营收|预测|资料|原文|报告)", reply, re.I))


def action_reply(reply: str) -> str | None:
    value = reply.strip().lower()
    if value in ACTION_CHOICES:
        return value
    aliases = {"抓取sec原文": "fetch_sec", "生成ai分析": "analyze", "导入已保存资料": "import",
               "建立索引": "index", "创建预测": "forecast", "查询任务状态": "status", "刷新sec原文": "refresh_sec"}
    return aliases.get(re.sub(r"\s+", "", value))


def _standalone_symbol(reply: str) -> bool:
    candidate = re.sub(r"^(?:就是|是|选|选用|用|it's|it is)\s*", "", reply, flags=re.I).strip(" 。.!！")
    candidate = re.sub(r"(?:公司|集团|股份|股票)$", "", candidate).strip()  # "苹果公司" names the company too
    if candidate.lower() in ALIASES:
        return True
    if candidate != candidate.upper() or not re.fullmatch(r"[A-Z][A-Z.-]{0,7}", candidate):
        return False
    slots = extract_slots(candidate)
    return bool(slots["symbols"] or slots["suggestion"])


def is_pending_reply(pending: dict[str, Any], reply: str) -> bool:
    """Only a bounded slot value may inherit an earlier question or action."""
    text = reply.strip()
    if not text:
        return False
    reason = pending.get("reason")
    if reason == "missing_action_type":
        return action_reply(text) is not None
    if is_explicit_new_request(text):
        return False
    if reason in {"missing_stock_focus", "missing_comparison_symbol", "missing_action_symbol", "symbol_suggestion",
                  "reference_company_required", "company_not_in_queue"}:
        return _standalone_symbol(text)
    if reason == "missing_action_source":
        stripped = SOURCE_ID.sub("", ACCESSION.sub("", text)).strip(" ,，。:：")
        if re.fullmatch(r"(?:10-[KQ]|8-K)", text, re.I):
            return True
        if _standalone_symbol(text):
            return True
        return bool(SOURCE_ID.search(text) or ACCESSION.search(text)) and (not stripped or
            _standalone_symbol(stripped))
    if reason == "missing_task_id":
        return bool(re.fullmatch(r"(?:[0-9a-f]{64}|[0-9a-f]{8}-[0-9a-f-]{27})", text, re.I))
    if reason == "period_requires_accession":
        return bool(ACCESSION.fullmatch(text))
    if reason == "ambiguous_web_url":
        return bool(re.fullmatch(r"https://[^\s]{4,2048}", text, re.I))
    return False
