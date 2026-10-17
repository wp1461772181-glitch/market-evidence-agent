"""Reference format parsers and server-owned result snapshots; no routing tables."""
from __future__ import annotations
import hashlib,json,re,unicodedata
from typing import Any
ACCESSION=re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
SOURCE_ID=re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
TASK_ID=re.compile(r"\b[0-9a-fA-F]{64}\b")
class SemanticError(ValueError):
    def __init__(self, code: str, *, cost: int = 0, hint: str | None = None):
        super().__init__(code)
        self.cost = cost
        self.hint = hint  # which clarification text fits (the reason code alone is not enough)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def catalog(result_sets: list[dict], owner: str, conversation: str) -> list[dict]:
    """The conversation's saved result sets, oldest first, each labelled with its position and the
    question that produced it so that 最开始 / 最近 can be read off the list."""
    mine = [entry for entry in result_sets[-6:]
            if entry.get("owner") == owner and entry.get("conversation") == conversation]
    return [{"id": entry["id"], "position": number, "age": ("oldest" if number == 1 else "newest" if number == len(mine)
                                                           else "middle"),
             **({"asked": str(entry["asked"])[:120]} if entry.get("asked") else {}),
             "scope": entry["scope"], "turn": entry["turn"],
             "items": [{"id": item["id"], "title": item["title"][:80]} for item in entry["items"]]}
            for number, entry in enumerate(mine, 1)]


PERIOD_TEXT = re.compile(r"(?<![A-Za-z])FY\s?'?\d{2,4}(?!\d)|(?<!\d)(?:19|20)\d{2}\s*财年"
                         r"|(?<![A-Za-z])[QH][1-4](?![A-Za-z0-9])|第?[1-4]\s*季度|(?<!\d)(?:19|20)\d{2}(?!\d)", re.I)


_URL = re.compile(r"https?://\S+", re.I)


_ZH_NUMBER = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


_NUM = r"[零〇一二两三四五六七八九十\d]{1,3}"


_RECORD_ZH = "新闻|报道|文章|消息|资讯|链接|来源|结果|公告|研报|报告|记录|条目|网页|页面|财报|申报|文件|资料|材料"


_UNIT = (r"(?:条|篇|则|份|项|个(?=\s*(?:" + _RECORD_ZH + r"|的|呢|吧|吗|啊|呀|讲|说|是|内容|详情|详细|怎么|什么|如何)"
         r"|\s*[，。,.;；!?！？]|\s*$))")


_ORDINAL_ZH = re.compile(rf"第\s*({_NUM})\s*{_UNIT}")


_FIRST_ZH = re.compile(rf"前\s*({_NUM})\s*{_UNIT}")


_LAST_ZH = re.compile(rf"最后\s*(?:那|这)?\s*[一1]?\s*{_UNIT}")


_RECORD = r"(?:one|article|story|item|record|result|piece|news|source|link|entry|report|headline|post|filing|document|file)s?"


_EN_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


_ORDINAL_EN = re.compile(rf"\b(first|second|third|fourth|fifth|sixth|\d{{1,2}}(?:st|nd|rd|th))\s+{_RECORD}\b", re.I)


_FIRST_EN = re.compile(r"\bfirst\s+(two|three|four|five|six|\d{1,2})\b"
                       r"(?!\s+(?:quarters?|months?|weeks?|days?|years?|hours?|minutes?|seconds?|halves|half|trading))", re.I)


_LAST_EN = re.compile(rf"\blast\s+{_RECORD}\b", re.I)


def _zh_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    head, ten, tail = text.partition("十")
    if not ten:
        return _ZH_NUMBER.get(text)
    if (head and head not in _ZH_NUMBER) or (tail and tail not in _ZH_NUMBER):
        return None
    return (_ZH_NUMBER[head] if head else 1) * 10 + (_ZH_NUMBER[tail] if tail else 0)


def ordinal_positions(words: str, total: int) -> list[int] | None:
    """The 1-based positions in a result set of ``total`` records that the user's words name
    (第二条, 前两条, 最后一条, the second article, the first two, last one, 2nd one), or None when
    they name none.  A position outside the set is reported as an unavailable item."""
    text = _URL.sub(" ", words)
    found: list[int | None] = []
    asked = False
    for match in _ORDINAL_ZH.finditer(text):
        asked = True
        found.append(_zh_number(match.group(1)))
    for match in _FIRST_ZH.finditer(text):
        asked = True
        count = _zh_number(match.group(1))
        found.extend(range(1, count + 1) if count else [None])
    for _ in _LAST_ZH.finditer(text):
        asked = True
        found.append(total)
    for match in _ORDINAL_EN.finditer(text):
        asked = True
        word = match.group(1).lower()
        found.append(_EN_WORDS.get(word) or int(re.sub(r"\D", "", word)))
    for match in _FIRST_EN.finditer(text):
        asked = True
        word = match.group(1).lower()
        count = _EN_WORDS.get(word) or int(word)
        found.extend(range(1, count + 1) if count else [None])
    for _ in _LAST_EN.finditer(text):
        asked = True
        found.append(total)
    if not asked:
        return None
    if not found or any(position is None or not 1 <= position <= total for position in found):
        raise SemanticError("reference_item_unavailable")
    return sorted(set(found))


_COUNT_ZH = re.compile(rf"[这那]\s*({_NUM})\s*(?:条|篇|份|个|则|项)")


_COUNT_EN = re.compile(r"\b(?:those|these|the)\s+(two|three|four|five|six|\d{1,2})\b"
                       r"(?!\s+(?:quarters?|months?|weeks?|days?|years?|hours?|minutes?))", re.I)


def stated_count(words: str) -> int | None:
    """How many records the words say they mean ("那两条", "those two"), or None when they state no
    count (an ordinal or "那些" names none)."""
    text = _URL.sub(" ", words)
    match = _COUNT_ZH.search(text)
    if match:
        return _zh_number(match.group(1))
    match = _COUNT_EN.search(text)
    if match:
        word = match.group(1).lower()
        return _EN_WORDS.get(word) or int(word)
    return None


_ASKED_ZH = re.compile(rf"(?<![第\d.])({_NUM})\s*(?:条|篇|则)|(?<![第\d.])({_NUM})\s*(?:个|份)\s*(?:{_RECORD_ZH})")


_EN_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                   "nine": 9, "ten": 10}


_ASKED_EN = re.compile(
    r"\b(?:top\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:(?:latest|recent|news|financial|top)\s+)*(?:articles?|stories|results?|headlines?|links?|items?|posts?|sources?|reports?|news)\b",
    re.I)


_ASKED_TOP = re.compile(r"\btop\s+(\d{1,2})\b", re.I)


def requested_result_count(words: str) -> int | None:
    """How many results a search request asks for ("找两条新闻", "3 篇报道", "top 3", "two articles"),
    or None.  An ordinal ("第二条") and a time window ("近3个月") state none.  Used to keep the saved
    result set and the cards at the number the user asked for."""
    text = _URL.sub(" ", unicodedata.normalize("NFKC", words or ""))
    value: int | None = None
    match = _ASKED_ZH.search(text)
    if match:
        value = _zh_number(match.group(1) or match.group(2))
    if value is None:
        match = _ASKED_EN.search(text)
        if match:
            word = match.group(1).lower()
            value = _EN_COUNT_WORDS.get(word) or (int(word) if word.isdigit() else None)
    if value is None:
        match = _ASKED_TOP.search(text)
        if match:
            value = int(match.group(1))
    return value if value is not None and 1 <= value <= 10 else None


_MAGNITUDE = {"万亿": 1e12, "千万": 1e7, "百万": 1e6, "亿": 1e8, "万": 1e4, "千": 1e3, "trillion": 1e12, "billion": 1e9,
              "million": 1e6, "thousand": 1e3, "bn": 1e9, "mn": 1e6, "b": 1e9, "k": 1e3, "m": 1e6}


_FIGURE = re.compile(r"(\d+(?:\.\d+)?)\s*(万亿|千万|百万|亿|万|千|trillion|billion|million|thousand|bn|mn|b|k|m)?(?![A-Za-z])", re.I)


def figure_values(text: str, *, zh: bool = False) -> set[float]:
    """The amounts the text states, magnitude words applied (100亿 and 10 billion agree), for digits
    that are no part of a report period, an accession, a source id or a task id.  ``zh`` also reads
    Chinese numerals ("两条" is 2): used for what the user wrote, never for the words to be kept."""
    text = unicodedata.normalize("NFKC", text).replace("萬", "万").replace("億", "亿")
    text = re.sub(r"(?<=\d)T(?=\d{2}:)", " ", text)
    text = PERIOD_TEXT.sub(" ", ACCESSION.sub(" ", SOURCE_ID.sub(" ", TASK_ID.sub(" ", text))))
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    values = {round(float(number) * _MAGNITUDE.get(unit.lower(), 1.0), 4) for number, unit in _FIGURE.findall(text)}
    if zh:
        for run in re.findall(r"[零〇一二两三四五六七八九十]+", text):
            value = _zh_number(run)
            if value is not None:
                values.add(float(value))
    return values


_CITATION_MARKER = re.compile(r"\[([0-9]{1,9})\]")


_CARD_ORDINAL = re.compile(r"[0-9]{1,9}")


def unmatched_citation_markers(answer: Any, citations: Any) -> list[int]:
    """The distinct ``[n]`` markers of ``answer`` (ascending) that match no ordinal of ``citations``, the
    cards of the turn.  It only reads: nothing is renumbered, stripped or re-linked, and no input makes
    it raise.  The audit of a turn carries the result so that a marker pointing nowhere is visible."""
    cards: set[int] = set()
    for card in citations if isinstance(citations, list) else []:
        ordinal = card.get("ordinal") if isinstance(card, dict) else None
        if isinstance(ordinal, int) and not isinstance(ordinal, bool):
            cards.add(ordinal)
        elif isinstance(ordinal, str) and _CARD_ORDINAL.fullmatch(ordinal):
            cards.add(int(ordinal))
    text = answer if isinstance(answer, str) else ""
    return sorted({int(found) for found in _CITATION_MARKER.findall(text)} - cards)


def save_result_set(existing: list[dict], result: dict, citations: list[dict], *, owner: str,
                    conversation: str, turn: str, task: str, scope: dict, asked: str = "") -> list[dict]:
    if not result.get("ok") or not isinstance(result.get("data"), list):
        return existing
    by_url = {c.get("source_url"): c for c in citations if isinstance(c, dict) and c.get("source_type") != "unavailable"}
    by_source = {str(c.get("source_id")): c for c in citations if isinstance(c, dict) and c.get("source_id")}
    items = []
    for row in result["data"][:6]:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or row.get("source_url") or row.get("content_source_url")
        source_id = str(row.get("source_id") or row.get("id") or "")
        citation = by_url.get(url) or by_source.get(source_id)
        excerpt = row.get("excerpt") or row.get("summary") or row.get("content")
        if not citation or not isinstance(excerpt, str) or not excerpt.strip():
            continue
        if citation.get("source_type") == "web":
            from ..agent_web_research import validate_public_url
            try:
                url = validate_public_url(url)
            except (ValueError, TypeError):
                continue
            if not row.get("body_provenance") or not row.get("fetched_at") or not citation.get("quote"):
                continue
        excerpt = re.sub(r"^\[\d+\]\s*", "", excerpt)[:5000]
        if citation.get("source_type") == "web" and excerpt != citation["quote"][:5000]:
            continue
        shortened = len(excerpt) > 2500
        excerpt = excerpt[:2500]
        saved_citation = {**citation, "truncated": bool(citation.get("truncated") or shortened)}
        if citation.get("source_type") == "web":
            saved_citation["quote"] = excerpt
        items.append({"id": _id(owner, conversation, turn, task, source_id or url or "", str(len(items))),
                      "title": str(row.get("title") or citation.get("title") or source_id)[:300],
                      "source_url": url, "source_id": source_id or None, "excerpt": excerpt,
                      "excerpt_hash": hashlib.sha256(excerpt.encode()).hexdigest(),
                      "published_at": row.get("published_at"), "observed_at": row.get("fetched_at") or row.get("observed_at"),
                      "citation": saved_citation, "source_type": citation.get("source_type")})
    if not items:
        return existing
    entry = {"id": _id(owner, conversation, turn, task), "owner": owner, "conversation": conversation,
             "turn": turn, "task": task, "scope": scope, "items": items,
             **({"asked": asked.strip()[:200]} if asked and asked.strip() else {})}
    saved = [item for item in existing if item.get("id") != entry["id"]][-5:] + [entry]
    while saved and _json_size(saved) > 65536:
        saved.pop(0)
    return saved


def select_result_items(result_sets: list[dict], set_id: str, item_ids: list[str], *, owner: str, conversation: str) -> list[dict]:
    entry = next((item for item in result_sets if item.get("id") == set_id and item.get("owner") == owner
                  and item.get("conversation") == conversation), None)
    if entry is None:
        raise SemanticError("reference_unavailable")
    known = {item["id"]: item for item in entry["items"]}
    if any(item not in known for item in item_ids):
        raise SemanticError("reference_item_unavailable")
    return [known[item] for item in (item_ids or list(known))]


def hydrate_legacy_web_sets(rows: list[dict], *, owner: str, conversation: str) -> list[dict]:
    """Only completed server evidence with a successful gate/trace can seed old web turns."""
    from datetime import datetime
    from ..agent_web_research import validate_public_url

    saved: list[dict] = []
    for row in rows:
        if row.get("role") != "assistant" or row.get("status") != "complete":
            continue
        protocol = row.get("tool_calls")
        if not isinstance(protocol, dict):
            continue
        audit = protocol.get("audit")
        tasks = audit.get("tasks") if isinstance(audit, dict) else None
        traces = protocol.get("tool_trace")
        citations = protocol.get("citations")
        if not isinstance(tasks, list) or not isinstance(traces, list) or not isinstance(citations, list) or not audit.get("gate_passed"):
            continue
        web_tasks = [task for task in tasks if isinstance(task, dict) and task.get("route") == "public_web_research"
                     and task.get("status") == "succeeded"]
        web_traces = [trace for trace in traces if isinstance(trace, dict) and trace.get("name") == "web_research"
                      and trace.get("status") == "complete" and isinstance(trace.get("result"), dict)
                      and trace["result"].get("ok") is True]
        # Old combined web turns did not bind individual citations to tasks.
        if len(web_tasks) != 1 or len(web_traces) != 1:
            continue
        web_citations = [citation for citation in citations if isinstance(citation, dict)
                         and citation.get("source_type") == "web"]
        count = web_traces[0]["result"].get("count")
        if not web_citations or not isinstance(count, int) or count < len(web_citations):
            continue
        source_rows = []
        for citation in web_citations:
            try:
                url = validate_public_url(citation["source_url"])
                quote = citation["quote"]
                observed = citation["observed_at"]
                if not isinstance(quote, str) or not quote or len(quote) > 5000 or not isinstance(observed, str):
                    raise ValueError
                datetime.fromisoformat(observed.replace("Z", "+00:00"))
            except (ValueError, TypeError, KeyError):
                source_rows = []
                break
            source_rows.append({"url": url, "title": citation.get("title"), "excerpt": quote,
                                "fetched_at": observed, "published_at": citation.get("published_at"),
                                "body_provenance": "verified_server_gate"})
        if not source_rows:
            continue
        result = {"ok": True, "source": "public web search and fetched page excerpts", "data": source_rows}
        trace_id = str(web_traces[0].get("call_id") or "")
        task_id = str(web_tasks[0].get("task_id") or trace_id or "legacy-web")
        saved = save_result_set(saved, result, web_citations, owner=owner, conversation=conversation,
                                turn=str(row.get("turn_id") or ""), task=task_id,
                                scope=web_tasks[0].get("scope") or {})
    return saved


def legacy_web_source_choices(rows: list[dict]) -> list[dict]:
    """Offer URL pointers from old server sidecars without treating their quotes as evidence."""
    from ..agent_web_research import validate_public_url

    choices: list[dict] = []
    seen: set[str] = set()
    for row in reversed(rows):
        if row.get("role") != "assistant" or row.get("status") != "complete":
            continue
        protocol = row.get("tool_calls")
        citations = protocol.get("citations") if isinstance(protocol, dict) else None
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict) or citation.get("source_type") != "web":
                continue
            try:
                url = validate_public_url(citation.get("source_url"))
            except (TypeError, ValueError):
                continue
            if url in seen:
                continue
            seen.add(url)
            title = str(citation.get("title") or "原网页")[:42]
            choices.append({"id": "source:" + _id(url), "label": f"{title} · {url}"[:100],
                            "url": url})
            if len(choices) >= 10:
                return choices
    return choices


def named_companies(text: str, limit: int = 64) -> set[str]:
    """Literal entity handles for summary grounding, without a company directory."""
    latin=set(re.findall(r"\b[A-Z][A-Za-z]+\b",text)) - {'The','This','User','Assistant','Revenue','FY','AM','PM','URL','HTTP','HTTPS','SEC','CEO','CFO','EPS','USD'}
    from ..agent_web_research import _GENERIC_LATIN
    grammar=set(_GENERIC_LATIN)|{'summarize','summarise','explain','import','save','analyse','analyze','compare','review','read','fetch','refresh'}
    latin={word for word in latin if word.casefold() not in grammar}
    cjk=set()
    # Grammar anchors identify an entity span; no company alias or ticker table is used.
    for match in re.finditer(r"(?:想看|关注|研究|问过|已问|还问|想问|询问)([\u4e00-\u9fff]{2,12}?)(?=的|最近|去年|[、，。;；\s]|$)",text):cjk.add(match[1])
    for match in re.finditer(r"(?:^|[、，\n])([\u4e00-\u9fff]{2,8}?)(?=的资本|公司|集团)",text):cjk.add(match[1])
    return set(sorted(latin|cjk)[:limit])
