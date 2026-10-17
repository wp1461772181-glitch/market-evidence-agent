"""How recent a question wants what it finds.

Three readers use one vocabulary: the web search (a time range for the search backend and a cut of
what comes back), the evidence gate (a "recent" question needs dated sources) and the planner's
check that its query kept the user's freshness words.  "最近" and "半个月" were in none of them, so
"最近美光的新闻" searched without any time limit and returned March.

Only an explicit span is a window ("半个月", "三天", "两周", "last 3 days"); "最近", "latest" and the
like only ask for the fresh end of the news.  A bare "3月" is March, "第二周" is the second week and
"5日" is a date, so none of them is a span.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_ZH = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_UNIT_DAYS = {"天": 1, "周": 7, "星期": 7, "礼拜": 7, "个月": 30}
_SPAN = re.compile(r"(?<![第\d.])(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(天|周|星期|礼拜|个月)(?!年|前|以前|之前)")
_HALF_MONTH = re.compile(r"半个月|半月")
_EN_SPAN = re.compile(r"\b(?:last|past|previous|within(?: the)?(?: last| past)?)\s+(\d{1,3}|a|one|two|three|four)\s+(day|week|month)s?\b",
                      re.I)
_EN_NUMBER = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}
_EN_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _number(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    if text in _ZH:
        return _ZH[text]
    if len(text) == 2 and text[0] == "十" and text[1] in _ZH:      # 十五
        return 10 + _ZH[text[1]]
    if len(text) == 2 and text[1] == "十" and text[0] in _ZH:      # 二十
        return _ZH[text[0]] * 10
    if len(text) == 3 and text[1] == "十" and text[0] in _ZH and text[2] in _ZH:   # 二十五
        return _ZH[text[0]] * 10 + _ZH[text[2]]
    return None


def window_days(text: str) -> int | None:
    """The span of days the words name ("半个月" 15, "两周" 14, "last 3 days" 3), else None."""
    text = text or ""
    if _HALF_MONTH.search(text):
        return 15
    days = None
    for match in _SPAN.finditer(text):
        count = _number(match.group(1))
        if count:
            span = count * _UNIT_DAYS[match.group(2)]
            days = span if days is None else max(days, span)
    for match in _EN_SPAN.finditer(text):
        raw = match.group(1).lower()
        count = int(raw) if raw.isdigit() else _EN_NUMBER.get(raw)
        if count:
            span = count * _EN_UNIT_DAYS[match.group(2).lower()]
            days = span if days is None else max(days, span)
    return days if days and days <= 366 else None



def published_within(published_at: object, days: int, now: datetime) -> bool:
    """Whether a source's publication time is inside the span.  A source with no readable date cannot be
    shown to be inside it."""
    if not isinstance(published_at, str) or not published_at.strip():
        return False
    try:
        moment = datetime.fromisoformat(published_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    return now - timedelta(days=days) <= moment <= now


def requested_years(text: str, now: datetime | None = None) -> set[str]:
    """Literal/relative year grounding for validation; never selects a route or tool."""
    from datetime import UTC
    years=set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',text or ''))
    if re.search(r'去年|\bLAST YEAR\b',text or '',re.I):years.add(str((now or datetime.now(UTC)).year-1))
    return years
