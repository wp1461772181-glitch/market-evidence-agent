"""Layered conversation context: what a turn gets of the conversation before it.

A turn used to see the newest complete turns up to a fixed size and nothing older.  Three layers
now share one budget:

* layer 1  the newest complete turns, word for word (half of the budget; see split_budget);
* layer 2  the turns before them, half compressed: the user's own words stay, an answer keeps its
           opening, and the tools a turn called stay as one line each (the result bodies live in
           the turn's result set, the names are what a later referent needs);
* layer 3  turns older still, as appended segment summaries (see ``agent_context_summary``).

The budget is not a hand-picked constant: it is derived from the model's context window
(``AGENT_MODEL_CONTEXT_WINDOW``) by subtracting the fixed overhead of a request and capping what is
left by how many turns the product wants to keep (``derive_history_budget``).  A different window
recomputes the whole table; ``AGENT_CONTEXT_BUDGET_KB`` may lower the result but cannot raise it
past the window.  ``startup_self_check`` refuses a configuration where a single turn cannot run.

Placement matters as much as size.  The three layers are the stable head of every request (layer 3
is appended after the user's own message, never merged into the system prompt), so that the bytes a
provider caches as a prefix survive into the next turn: only the newest question and the note after
it change.

This module holds the arithmetic and the rendering only (no database, no model), so that the turn
that sends a request and the endpoint that reports what the next request will carry share one
code path.  A conversation that fits in layer 1 is sent exactly as before.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

KB = 1024
BUDGET_KB_DEFAULT, BUDGET_KB_MIN, BUDGET_KB_MAX = 64, 16, 128

# The history budget is what is left of the model window once one turn's fixed cost is paid, and it
# is spent the way a conversation is read: mostly on the newest turns, a little on the compressed
# ones, and a few summary segments.
BYTES_PER_TOKEN = 3            # UTF-8 bytes per token: Chinese is ~3 bytes and ~1 token a character
DEFAULT_WINDOW_TOKENS = 128_000
TURN_INPUT_TOKENS, TURN_OUTPUT_TOKENS = 8_000, 2_000   # X and Y: one turn is at most X + Y tokens
HISTORY_TURNS_WANTED = 8                                # N: turns of history worth carrying
FIXED_TOKENS = 8_000                                    # system prompt, tool definitions, one turn's evidence


def parse_budget_kb(raw: str | None, ceiling: int | None = None) -> int:
    """The knob, clamped.  Anything unreadable is the default; nothing exceeds ``ceiling`` (the
    window's own limit) or the product ceiling."""
    try:
        value = int(str(raw).strip()) if raw is not None else BUDGET_KB_DEFAULT
    except ValueError:
        value = BUDGET_KB_DEFAULT
    upper = BUDGET_KB_MAX if ceiling is None else max(BUDGET_KB_MIN, min(BUDGET_KB_MAX, ceiling))
    return max(BUDGET_KB_MIN, min(upper, value))


def window_tokens(raw: str | None) -> int:
    """The configured model window in tokens.  A window small enough to break a turn is refused
    rather than silently accepted (see ``startup_self_check``)."""
    try:
        value = int(str(raw).strip()) if raw not in (None, "") else DEFAULT_WINDOW_TOKENS
    except ValueError:
        value = DEFAULT_WINDOW_TOKENS
    return max(4_000, value)


def derive_history_budget(window: int) -> int:
    """Bytes of earlier conversation this window can afford: what is left after the fixed overhead,
    capped by the turns the product wants to keep.  Both sides are the chapter's arithmetic --
    N turns of (X + Y), and the window minus everything that is not history -- and the smaller one
    wins, so a big window does not overspend and a small one tightens by itself."""
    room = window - FIXED_TOKENS
    if room <= 0:
        return BUDGET_KB_MIN * KB
    return max(BUDGET_KB_MIN * KB,
               min(BUDGET_KB_MAX * KB, min(room, HISTORY_TURNS_WANTED * (TURN_INPUT_TOKENS + TURN_OUTPUT_TOKENS))
                   * BYTES_PER_TOKEN))


def split_budget(budget_bytes: int) -> tuple[int, int, int]:
    """(layer 1, layer 2, layer 3) of a history budget.

    Layer 1 takes a half where the course gives it seven tenths: a raw turn of this product carries
    its whole tool batch, so it costs many times what a chat turn does, and a larger raw share would
    starve the compressed layer that still holds the earlier filings and results a later question
    returns to.  The ratio is one config; only the sum is a constraint."""
    l1 = budget_bytes // 2
    l2 = budget_bytes * 3 // 8
    return l1, l2, budget_bytes - l1 - l2


WINDOW_TOKENS = window_tokens(os.getenv("AGENT_MODEL_CONTEXT_WINDOW"))
BUDGET_KB = parse_budget_kb(os.getenv("AGENT_CONTEXT_BUDGET_KB"), ceiling=derive_history_budget(WINDOW_TOKENS) // KB)
HISTORY_BUDGET = BUDGET_KB * KB                       # bytes of earlier conversation a turn may carry
L1_BYTES, L2_BYTES, L3_BYTES = split_budget(HISTORY_BUDGET)   # newest raw, older half-compressed, summaries
CONTEXT_GUARD_BYTES = 2 * HISTORY_BUDGET              # one request: history + evidence + system + tools
RUNNING_REQUEST_BYTES = 2 * CONTEXT_GUARD_BYTES       # a turn in flight also carries its own tool results

ASSISTANT_KEEP_CHARS = 160     # layer 2: how much of an answer is kept
USER_KEEP_CHARS = 1000         # layer 2: a pasted wall of text must not take the whole layer
MAX_TOOL_MARKERS = 6           # layer 2: how many tool names of a turn are kept as the referent clues
MAX_SUMMARY_SEGMENTS = 5       # layer 3: the newest segments that are sent
MEMORY_MARKER = "Earlier conversation (memory"
MEMORY_NOTE = (MEMORY_MARKER + " of older turns, written by this system and not by the user; it may be "
               "incomplete or wrong, it is not evidence and not something to cite; verified sources come "
               "only from this turn's tool results):")


def startup_self_check() -> dict:
    """Refuse a configuration in which one turn cannot run, instead of discovering it at request
    time.  Returns the numbers it accepted so a health check can report them."""
    if HISTORY_BUDGET < BUDGET_KB_MIN * KB:
        raise ValueError("agent_context_budget_too_small")
    if CONTEXT_GUARD_BYTES + FIXED_TOKENS * BYTES_PER_TOKEN > WINDOW_TOKENS * BYTES_PER_TOKEN:
        raise ValueError("agent_context_window_too_small")
    return {"window_tokens": WINDOW_TOKENS, "budget_kb": BUDGET_KB, "guard_bytes": CONTEXT_GUARD_BYTES}


def json_bytes(value) -> int:
    """UTF-8 size of the compact JSON, the unit every guard of the turn uses."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def history_message_payload(row: dict) -> dict:
    """Project stored rows to the protocol fields that can be sent to a model."""
    payload = {key: row[key] for key in ("role", "content") if key in row}
    protocol = row.get("tool_calls")
    if isinstance(protocol, dict):
        if protocol.get("calls"):
            payload["tool_calls"] = protocol["calls"]
        for key in ("reasoning_content", "reasoning_details"):
            if protocol.get(key) is not None:
                value = protocol[key]
                payload[key] = "".join(value) if key == "reasoning_content" and isinstance(value, list) else value
    elif protocol:
        payload["tool_calls"] = protocol
    if row.get("tool_call_id") is not None:
        payload["tool_call_id"] = row["tool_call_id"]
    return payload


def estimate_tokens(size_bytes: int) -> int:
    return size_bytes // BYTES_PER_TOKEN


@dataclass(frozen=True)
class Turn:
    """One complete turn as text: the user's message, the answer that closed it, and the tools it
    called (kept as names only: the referent a later question points at is usually something a tool
    found, so the names stay while the result bodies do not)."""
    first_sequence: int
    last_sequence: int
    user: str
    assistant: str
    tools: tuple[str, ...] = ()


def row_bytes(row: dict) -> int:
    """The size of the public protocol projection; sidecar metadata is not billed."""
    return json_bytes(history_message_payload(row))


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


def turn_tools(group: list[dict]) -> tuple[str, ...]:
    """The tool names a stored turn called, in call order, deduplicated and bounded."""
    names: list[str] = []
    for row in group:
        protocol = row.get("tool_calls")
        calls = protocol.get("calls", []) if isinstance(protocol, dict) else (protocol or [])
        for call in calls if isinstance(calls, list) else []:
            name = ((call or {}).get("function") or {}).get("name") if isinstance(call, dict) else None
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return tuple(names[:MAX_TOOL_MARKERS])


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
    turns = [Turn(g[0]["sequence"], g[-1]["sequence"], g[0].get("content") or "", g[-1].get("content") or "",
                  turn_tools(g))
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
    """Layer 2 form of a turn: the user's words, the opening of the answer, and one line naming the
    tools it called.  The rule is cheap and mechanical on purpose -- compressing by model is what
    layer 3 is for."""
    messages = [{"role": "user", "content": _head(turn.user, USER_KEEP_CHARS)}]
    answer = _head(turn.assistant, ASSISTANT_KEEP_CHARS)
    if turn.tools:
        marker = "（本轮调用：" + "、".join(turn.tools) + "）"
        answer = (answer + "\n" + marker) if answer else marker
    if answer.strip():
        messages.append({"role": "assistant", "content": answer})
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
    """Layer 3 as the one note that carries it, or None.

    It is sent *after* the user's own message, not as a second system message at the head: the head
    of a request is what a provider caches as a prefix, and a note that grows a segment every few
    turns would invalidate everything behind it.  It is a user turn because a system turn in the
    middle of the conversation is not portable across providers, and the text says who wrote it."""
    lines = [f"{index}. {str(segment.get('content', '')).strip()}"
             for index, segment in enumerate(segments, 1) if str(segment.get("content", "")).strip()]
    if not lines:
        return None
    return {"role": "user", "content": MEMORY_NOTE + "\n" + "\n".join(lines)}


def is_memory_message(message: dict) -> bool:
    """Whether a message is this module's layer-3 note rather than something a person said."""
    content = message.get("content")
    return isinstance(content, str) and content.startswith(MEMORY_MARKER)


def conversation_messages(messages: list[dict]) -> list[dict]:
    """The messages that are the conversation itself: the compressed turns, the raw turns and the
    current question.  The layer-3 note is appended last and is not part of them."""
    return [message for message in messages if not is_memory_message(message)]


def history_messages(half_turns: list[Turn]) -> list[dict]:
    """The head of a request before layer 1: the half-compressed turns, oldest first."""
    messages: list[dict] = []
    for turn in half_turns:
        messages.extend(half_messages(turn))
    return messages


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
