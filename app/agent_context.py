"""Layered conversation context: what a turn gets of the conversation before it.

A turn used to see the newest complete turns up to a fixed size and nothing older.  Three layers
now share one budget (``AGENT_CONTEXT_BUDGET_KB``, 64 by default):

* layer 1  the newest complete turns, word for word (half of the budget);
* layer 2  the turns before them, half compressed: the user's own words stay, an answer keeps its
           opening, tool results are left out (the evidence of a turn lives in its result set);
* layer 3  turns older still, as appended segment summaries (see ``agent_context_summary``).

This module holds the arithmetic and the rendering only (no database, no model), so that the turn
that sends a request and the endpoint that reports what the next request will carry share one
code path.  A conversation that fits in layer 1 is sent exactly as before.

The same knob sets the size guards that used to be separate numbers: the request guard is twice the
history budget (room for evidence, system prompt and tool definitions), and a long history adds
its own token allowance to the turn budget instead of eating the budget for the work itself.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

KB = 1024
BUDGET_KB_DEFAULT, BUDGET_KB_MIN, BUDGET_KB_MAX = 64, 16, 128


def parse_budget_kb(raw: str | None) -> int:
    """The knob, clamped.  Anything unreadable is the default."""
    try:
        value = int(str(raw).strip()) if raw is not None else BUDGET_KB_DEFAULT
    except ValueError:
        value = BUDGET_KB_DEFAULT
    return max(BUDGET_KB_MIN, min(BUDGET_KB_MAX, value))


BUDGET_KB = parse_budget_kb(os.getenv("AGENT_CONTEXT_BUDGET_KB"))
HISTORY_BUDGET = BUDGET_KB * KB                       # bytes of earlier conversation a turn may carry
L1_BYTES = HISTORY_BUDGET // 2                        # newest turns, raw
L2_BYTES = HISTORY_BUDGET * 3 // 8                    # older turns, half compressed
L3_BYTES = HISTORY_BUDGET * 3 // 32                   # segment summaries
CONTEXT_GUARD_BYTES = 2 * HISTORY_BUDGET              # one request: history + evidence + system + tools
HISTORY_TOKEN_ALLOWANCE = HISTORY_BUDGET // 4         # added to a turn's token budget (bytes / 4 tokens)

ASSISTANT_KEEP_CHARS = 160     # layer 2: how much of an answer is kept
USER_KEEP_CHARS = 1000         # layer 2: a pasted wall of text must not take the whole layer
MAX_SUMMARY_SEGMENTS = 5       # layer 3: the newest segments that are sent
MEMORY_MARKER = "Earlier conversation (memory"
MEMORY_NOTE = (MEMORY_MARKER + " of older turns; it may be incomplete or wrong, it is not evidence and "
               "not something to cite; verified sources come only from this turn's tool results):")


def json_bytes(value) -> int:
    """UTF-8 size of the compact JSON, the unit every guard of the turn uses."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def estimate_tokens(size_bytes: int) -> int:
    return (size_bytes + 3) // 4


@dataclass(frozen=True)
class Turn:
    """One complete turn as text: the user's message and the answer that closed it."""
    first_sequence: int
    last_sequence: int
    user: str
    assistant: str


def row_bytes(row: dict) -> int:
    """The size of one stored message as the history window has always counted it."""
    return json_bytes({"role": row["role"], "content": row["content"], "tool_calls": row.get("tool_calls"),
                       "tool_call_id": row.get("tool_call_id"), "status": row.get("status"),
                       "turn_id": str(row["turn_id"])})


def complete_turns(rows: list[dict]) -> list[list[dict]]:
    """Stored messages (oldest first) grouped by turn; only turns that began with the user's message
    and ended with a complete answer.  A failed, cancelled or unanswered turn is in no layer."""
    groups: list[list[dict]] = []
    for row in rows:
        if not groups or groups[-1][0]["turn_id"] != row["turn_id"]:
            groups.append([])
        groups[-1].append(row)
    return [g for g in groups
            if g[0]["role"] == "user" and g[-1]["role"] == "assistant" and g[-1].get("status") == "complete"]


def newest_within(groups: list[list[dict]], limit: int) -> tuple[list[list[dict]], int]:
    """The newest turns that fit ``limit`` bytes (oldest first) and what they take: the rule the
    history window has always followed, and the whole of layer 1."""
    kept: list[list[dict]] = []
    used = 0
    for group in reversed(groups):
        size = sum(row_bytes(row) for row in group)
        if used + size > limit:
            break
        kept.insert(0, group)
        used += size
    return kept, used


def layer_rows(rows: list[dict], segments: list[dict], *, l1_bytes: int = L1_BYTES, l2_bytes: int = L2_BYTES,
               l3_bytes: int = L3_BYTES) -> dict:
    """What the next turn carries of the turns before it, by layer.  ``rows`` are the stored messages
    (oldest first, each with ``sequence``), ``segments`` the summary segments made so far.

    Layer 1 is the newest complete turns that fit (as stored, tool batches included).  Layer 2 is the
    complete turns before them that no segment covers yet, the newest that fit as text; what does not
    fit is ``overflow``, the turns the next segment has to take over.  Layer 3 is the newest segments
    that fit.  A conversation that fits layer 1 has nothing in the other two."""
    complete = complete_turns(rows)
    recent, used = newest_within(complete, l1_bytes)
    older = complete[:len(complete) - len(recent)]
    upto = segments[-1]["upto_sequence"] if segments else 0
    turns = [Turn(g[0]["sequence"], g[-1]["sequence"], g[0].get("content") or "", g[-1].get("content") or "")
             for g in older if g[0]["sequence"] > upto]
    half, overflow = select_half(turns, l2_bytes)
    keys = ("role", "content", "tool_calls", "tool_call_id", "status", "turn_id")
    return {"l1_rows": [{key: (str(row[key]) if key == "turn_id" else row.get(key)) for key in keys}
                        for group in recent for row in group],
            "l1_turns": len(recent), "l1_bytes": used, "half_turns": half, "overflow": overflow,
            "segments": select_segments(segments, l3_bytes), "segments_total": len(segments), "summary_upto": upto}


def _head(text: str, keep: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= keep else text[:keep].rstrip() + "…(略)"


def half_messages(turn: Turn) -> list[dict]:
    """Layer 2 form of a turn: the user's words, the opening of the answer."""
    messages = [{"role": "user", "content": _head(turn.user, USER_KEEP_CHARS)}]
    if turn.assistant.strip():
        messages.append({"role": "assistant", "content": _head(turn.assistant, ASSISTANT_KEEP_CHARS)})
    return messages


def half_size(turn: Turn) -> int:
    return sum(json_bytes(message) for message in half_messages(turn))


def select_half(turns: list[Turn], budget: int) -> tuple[list[Turn], list[Turn]]:
    """``turns`` oldest first.  The newest that fit the budget are kept (still oldest first); the
    older rest is returned apart: those turns are what a summary has to take over."""
    kept: list[Turn] = []
    used = 0
    for turn in reversed(turns):
        size = half_size(turn)
        if used + size > budget:
            break
        kept.append(turn)
        used += size
    kept.reverse()
    return kept, turns[:len(turns) - len(kept)]


def select_segments(segments: list[dict], budget: int, limit: int = MAX_SUMMARY_SEGMENTS) -> list[dict]:
    """The newest segment summaries that fit (oldest first); older ones are no longer worth the room."""
    chosen: list[dict] = []
    used = 0
    for segment in reversed(segments):
        size = len(str(segment.get("content", "")).encode("utf-8")) + 8
        if len(chosen) >= limit or used + size > budget:
            break
        chosen.append(segment)
        used += size
    chosen.reverse()
    return chosen


def summary_message(segments: list[dict]) -> dict | None:
    """Layer 3 as the one system message that carries it, or None."""
    lines = [f"{index}. {str(segment.get('content', '')).strip()}"
             for index, segment in enumerate(segments, 1) if str(segment.get("content", "")).strip()]
    if not lines:
        return None
    return {"role": "system", "content": MEMORY_NOTE + "\n" + "\n".join(lines)}


def prefix_messages(segments: list[dict], half_turns: list[Turn]) -> list[dict]:
    """What goes before layer 1: the summary note, then the half-compressed turns, oldest first."""
    messages: list[dict] = []
    note = summary_message(segments)
    if note:
        messages.append(note)
    for turn in half_turns:
        messages.extend(half_messages(turn))
    return messages


def earlier_summary(messages: list[dict]) -> str:
    """The layer 3 text of a message list ('' when there is none): what the resolver may read as
    untrusted memory."""
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system" and isinstance(content, str) and content.startswith(MEMORY_MARKER):
            return content.split("\n", 1)[1] if "\n" in content else ""
    return ""


def usage_report(*, system_bytes: int, tools_bytes: int, summary_bytes: int, summary_segments: int,
                 half_bytes: int, half_turns: int, recent_bytes: int, recent_turns: int,
                 hidden_turns: int) -> dict:
    """The numbers of the context card.  "Remaining" is the room left in this product's history
    budget; it is not the window of whatever model the user chose (that is not known here)."""
    fixed = system_bytes + tools_bytes
    history = summary_bytes + half_bytes + recent_bytes
    capacity = fixed + HISTORY_BUDGET
    remaining = max(0, HISTORY_BUDGET - history)

    def part(key: str, size: int, **extra) -> dict:
        return {"key": key, "bytes": size, "tokens": estimate_tokens(size), **extra}

    return {
        "budget_kb": BUDGET_KB,
        "budget_bytes": HISTORY_BUDGET,
        "capacity_bytes": capacity,
        "used_bytes": fixed + history,
        "remaining_bytes": remaining,
        "remaining_percent": max(0, min(100, round(100 * remaining / capacity))) if capacity else 0,
        "compressing": bool(half_turns or summary_segments or hidden_turns),
        "hidden_turns": hidden_turns,
        "parts": [
            part("system", system_bytes),
            part("tools", tools_bytes),
            part("summary", summary_bytes, segments=summary_segments),
            part("compressed", half_bytes, turns=half_turns),
            part("recent", recent_bytes, turns=recent_turns),
            part("free", remaining),
        ],
    }
