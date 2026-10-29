"""Read and bind previously saved citation snapshots as current-turn evidence."""
from __future__ import annotations

import asyncio
import copy


def _failure(code: str) -> dict:
    return {"ok": False, "data": [], "error_code": code}


async def read_history_citation(args: dict, context) -> dict:
    turn_id, ordinal = args.get("turn_id"), args.get("ordinal")
    if not isinstance(turn_id, str) or isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        return _failure("history_citation_not_found")
    allowed = next((x for x in context.history_citation_catalogue
                   if isinstance(x, dict) and x.get("turn_id") == turn_id and any(
                       isinstance(s, dict) and not isinstance(s.get("ordinal"), bool)
                       and type(s.get("ordinal")) is int and s.get("ordinal") == ordinal
                       for s in x.get("sources", []) if isinstance(x.get("sources", []), (list, tuple)))), None)
    if allowed is None: return _failure("history_citation_not_found")
    from ..agent_chat_store import load_turn_citation
    found = await load_turn_citation(context.conversation_id, context.identity_credential, turn_id, ordinal)
    if not found.get("ok"): return _failure(found.get("error_code", "history_citation_not_found"))
    citation = found["citation"]
    calculation = citation.get("calculation") if isinstance(citation.get("calculation"), dict) else {}
    if citation.get("derived") or citation.get("source_type") in {"calculation", "formula_calculation"}:
        parents = citation.get("parents") or calculation.get("parents") or []
        return {
            **_failure("history_citation_derived"),
            "parents": parents,
            "hint": "这是计算来源，不能直接取回；先读取 parents 中的原始来源，再用 calculate 或 calculate_formula 重算。",
        }
    try:
        from ..agent_chat_stream import _restore_citations
        restored = await asyncio.to_thread(_restore_citations, [citation], context.identity_credential)
    except Exception:
        return _failure("history_citation_unavailable")
    if not restored or restored[0].get("source_type") == "unavailable" or not str(restored[0].get("quote") or "").strip():
        return _failure("history_citation_unavailable")
    snapshot = copy.deepcopy(restored[0])
    quote = str(snapshot.get("quote") or "")
    # Keep the verified full snapshot for this turn's manifest; bound only the tool payload.
    truncated = bool(snapshot.get("truncated")) or len(quote.encode("utf-8")) > 5200
    if truncated:
        quote = quote.encode("utf-8")[:5200].decode("utf-8", "ignore")
    data = {key: snapshot.get(key) for key in ("title", "source_type")}
    data.update(quote=quote, truncated=truncated, origin=found["origin"])
    return {"ok": True, "data": [data], "source": "history_citation", "_snapshot": snapshot}


def bind_history_citation(outcome: dict, citations: list[dict]) -> dict:
    if not outcome.get("ok") or not isinstance(outcome.get("_snapshot"), dict):
        return _failure(outcome.get("error_code", "history_citation_unavailable"))
    origin = outcome["data"][0]["origin"]
    existing = next((c for c in citations if c.get("origin") == origin), None)
    if existing is None:
        snapshot = copy.deepcopy(outcome["_snapshot"])
        snapshot["ordinal"] = len(citations) + 1
        snapshot["origin"] = origin
        citations.append(snapshot)
        ordinal = snapshot["ordinal"]
    else:
        ordinal = existing["ordinal"]
    row = outcome["data"][0]
    return {"ok": True, "data": [{"citation": f"[{ordinal}]", "title": row.get("title"),
        "source_type": row.get("source_type"), "quote": row.get("quote"), "truncated": row.get("truncated", False)}]}
