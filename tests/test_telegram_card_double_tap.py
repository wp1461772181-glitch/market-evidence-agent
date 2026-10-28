"""同意 on a fresh Telegram card runs the save and reports it; it never ends in 「过期了」 or 「无法与来源核实」.

Live 2026-10-28 11:57 (@fin_146_bot): 「都保存」 got a card with 同意/取消.  Right under it came
「本次表述暂无法与来源核实，请缩小问题或补充资料。」 and then 「这张确认卡已经过期了（每张卡只能用一次）」.

- The bridge processes updates one at a time and left the buttons on the card while the press's agent turn ran
  (seconds: the write, a resumed answer, the report).  A second tap waited behind it and, the card used up, got
  the expired message right after the first press's answer.
- That answer was the server's own report of the executed save.  The resumed answer had set the turn's route,
  so ``finish`` ran the report through the claim gate; with ``CLAIM_QUOTE_GATE=enforce`` a report naming a news
  title with a figure has no claims and every line was dropped.  The save had run; the owner was told nothing was
  verifiable.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import delete

from app.agent_chat_store import owner_hash
from app.database import SessionLocal
from app.agent_workflow.direct import Turn, run_direct_turn
from app.agent_workflow.models import AgentConsent, AgentWebSource
from app.agent_workflow.state import AgentRuntimeContext
from scripts import run_telegram_bridge as bridge
from tests.test_telegram_save_card_live_shape import _call, _run, _state, official_entity_metadata, owned  # noqa: F401

TITLE = "Tesla Q3 revenue rises 8% to $25.2 billion"
REPORT = f"已将 2026-10-22 的两条特斯拉（TSLA）新闻保存到资料库：《{TITLE}》和《Tesla robotaxi expands》。"
OWNER_ID = 4242


def _bridge(monkeypatch, answers):
    """The bridge with Telegram and the agent line recorded."""
    calls = {"posts": [], "sent": [], "asked": [], "toasts": []}

    class Client:
        async def post(self, url, json=None):
            calls["posts"].append((url.rsplit("/", 1)[-1], json))

    async def send_text(client, token, chat, text, inline_keyboard=None, *, markdown=True):
        calls["sent"].append(text)
        return 1

    async def answer_callback(client, token, cb_id, text=None):
        calls["toasts"].append(text)

    async def ask_agent(client, credential, bot_id, text, **press):
        calls["asked"].append(text)
        return next(answers), [], "c"

    class _Tg:
        def record_reply(self, *a):
            pass

    monkeypatch.setattr(bridge, "_send_text", send_text)
    monkeypatch.setattr(bridge, "_answer_callback", answer_callback)
    monkeypatch.setattr(bridge, "_ask_agent", ask_agent)
    monkeypatch.setattr(bridge, "_bot_token_for_send", lambda *a: "123:x")
    return Client(), calls, _Tg()


def _card():
    return {"id": str(uuid.uuid4()), "reason": "write_confirmation",
            "choices": [{"label": "同意", "value": "confirm:" + "t" * 40}, {"label": "换个操作", "value": "change:x"},
                        {"label": "取消", "value": "cancel:x"}]}


def _press(data, message_id=77):
    return {"id": uuid.uuid4().hex, "from": {"id": OWNER_ID}, "data": data,
            "message": {"message_id": message_id, "chat": {"id": OWNER_ID, "type": "private"}}}


def test_agree_takes_the_buttons_off_at_once_and_a_second_tap_is_not_told_expired(monkeypatch):
    client, calls, tg = _bridge(monkeypatch, iter(["已保存两条新闻。"]))
    pending = {}
    keyboard = bridge._make_keyboard([_card()], pending, "c", "owner", "b-1", "助理")
    agree = keyboard[0][0]["callback_data"]

    asyncio.run(bridge._handle_callback(client, _press(agree), pending, "o", "owner", OWNER_ID, OWNER_ID, tg))
    assert calls["asked"] == ["confirm:" + "t" * 40]
    assert calls["toasts"] == ["正在执行……"]
    assert calls["posts"] == [("editMessageReplyMarkup", {"chat_id": OWNER_ID, "message_id": 77,
                                                          "reply_markup": {"inline_keyboard": []}})]
    assert calls["sent"] == ["【助理】\n已保存两条新闻。"]

    # The double tap: queued behind the first, it runs nothing and adds no 「过期了」 under the answer.
    asyncio.run(bridge._handle_callback(client, _press(agree), pending, "o", "owner", OWNER_ID, OWNER_ID, tg))
    assert len(calls["asked"]) == 1 and calls["sent"] == ["【助理】\n已保存两条新闻。"]
    assert "处理过了" in calls["toasts"][-1]


def test_cancel_after_agree_on_the_same_card_runs_nothing(monkeypatch):
    client, calls, tg = _bridge(monkeypatch, iter(["已保存。"]))
    pending = {}
    (agree, cancel), = [[b["callback_data"] for b in row] for row in
                        bridge._make_keyboard([_card()], pending, "c", "owner", "b-1", "助理")]
    asyncio.run(bridge._handle_callback(client, _press(agree), pending, "o", "owner", OWNER_ID, OWNER_ID, tg))
    asyncio.run(bridge._handle_callback(client, _press(cancel), pending, "o", "owner", OWNER_ID, OWNER_ID, tg))
    assert len(calls["asked"]) == 1 and len(calls["sent"]) == 1


def test_a_first_cancel_cancels(monkeypatch):
    client, calls, tg = _bridge(monkeypatch, iter(["已取消，没有执行任何操作。"]))
    pending = {}
    keyboard = bridge._make_keyboard([_card()], pending, "c", "owner", "b-1", "助理")
    asyncio.run(bridge._handle_callback(client, _press(keyboard[0][1]["callback_data"]), pending, "o", "owner",
                                        OWNER_ID, OWNER_ID, tg))
    assert calls["asked"] == ["cancel:x"] and calls["toasts"] == ["正在取消……"]
    assert calls["sent"] == ["【助理】\n已取消，没有执行任何操作。"]


def test_a_button_the_bridge_never_minted_still_says_expired(monkeypatch):
    """After a bridge restart the in-memory ids are gone: that card really cannot be used from Telegram."""
    client, calls, tg = _bridge(monkeypatch, iter([]))
    asyncio.run(bridge._handle_callback(client, _press("ok:unknown-id"), {}, "o", "owner", OWNER_ID, OWNER_ID, tg))
    assert calls["asked"] == [] and calls["sent"] == ["这张确认卡已经过期了（每张卡只能用一次）。"]


@pytest.mark.parametrize("route", ["A", "D"])
def test_the_report_of_an_executed_save_survives_the_enforced_claim_gate(monkeypatch, route):
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    card = {"id": "req-1", "reason": "write_confirmation", "original_question": "都保存",
            "display": {"targets": [{"symbol": "TSLA", "title": TITLE, "web_date": "2026-10-22"}]},
            "choices": [{"label": "同意", "value": "confirm:tok"}]}

    async def model(messages, tools, **kwargs):
        return {"content": REPORT}

    turn = Turn("confirm:tok", AgentRuntimeContext("custom", "test", "dummy"), None, [],
                {"pending_request": card, "requested_pending_id": "req-1"})
    turn.model = model
    turn.route = route  # set by the resumed answer before it fell back to the report
    result = asyncio.run(turn.report_execution({"ok": True, "data": [
        {"status": "created", "title": TITLE, "source_id": "8c1f0f4e-0000-4000-8000-000000000000"}]}))
    assert result["status"] == "succeeded" and result["answer"] == REPORT


def test_agree_on_the_save_all_card_reports_the_save_under_the_enforced_gate(owned, monkeypatch):
    """The live sequence through Turn.run: 「都保存」 mints one card for both items, the press executes it."""
    monkeypatch.setenv("CLAIM_QUOTE_GATE", "enforce")
    state = _state()
    rows = state["result_sets"][0]["rows"]
    rows[0]["title"] = TITLE
    result, _ = _run([_call("ingest_web_source", url=rows[0]["url"]), _call("ingest_web_source", url=rows[1]["url"])],
                     state=state, question="都保存")
    card = result["pending_request"]
    assert card["reason"] == "write_confirmation" and "全部操作" in result["answer"]
    token = next(c["value"] for c in card["choices"] if c["value"].startswith("confirm:"))

    async def execute(name, args):
        return {"ok": True, "data": [{"status": "created", "title": TITLE,
                                      "source_id": "8c1f0f4e-0000-4000-8000-000000000000"}]}

    async def decide(messages, *_):
        return {"content": REPORT}

    context = AgentRuntimeContext(
        "custom", "test", "dummy", owner_hash=owner_hash("owner"), conversation_id="c", identity_credential="owner",
        classifier=lambda q: {"category": "D", "requested_actions": ["ingest_web_source"], "company": "Tesla",
                              "symbol": "TSLA"},
        decider=decide, executor=execute, answerer=lambda *_: REPORT, resolver=lambda _: {"relationship": "new"})
    pressed_state = {**state, "pending_request": card, "requested_pending_id": card["id"],
                     "previous_scope": result.get("previous_scope", {}),
                     "previous_category": result.get("previous_category")}
    try:
        pressed = asyncio.run(run_direct_turn(token, context, emit=lambda _: asyncio.sleep(0), state=pressed_state))
    finally:
        # The live-shape tests share conversation "c" and these pages' excerpt: leave no card or save behind.
        with SessionLocal() as db:
            db.execute(delete(AgentConsent).where(AgentConsent.conversation_id == "c"))
            db.execute(delete(AgentWebSource).where(AgentWebSource.owner_hash == owner_hash("owner")))
            db.commit()
    assert pressed["audit"]["write_outcome"] == "executed"
    assert "无法与来源核实" not in pressed["answer"] and pressed["status"] != "failed"
