"""Run the owner's due tasks (launchd ``com.market-evidence-agent.agent-tasks``, every five minutes).

A **brief** task is an unattended agent turn in a new conversation of the owner: the classifier's write
grants are dropped, write tools are never exposed, and a write the model suggests with ``request_action``
lands in the inbox's 待你确认 without a capability (``AgentRuntimeContext.unattended``).  The answer goes
to the inbox's 简报 with the turn's citations as its numbered sources; an answer without citations is not
a brief, the inbox says so instead.

An **alerts** task calls no model: new SEC filings the monitor stored, news leads (``news_discovery``,
metadata only, labelled unverified) and price lines crossed by the latest close, each a 简报 entry with
its source.  The SEC monitor and the market refresh call the same checks right after they run.

Cost: a run stops gathering at the task's per-run token cap, never starts once today's usage reached the
daily cap, and a failed run is retried once only when it is safe: the retry is unattended again (reads
only) and a run that already queued a suggestion is not retried, so a write suggestion is never repeated.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text as sql

from . import agent_tasks as tasks
from .agent_chat_models import AgentTask
from .database import SessionLocal, engine

log = logging.getLogger(__name__)

RUNNER_LOCK = "agent-tasks-runner"
ALERT_LOOKBACK = timedelta(days=2)
ALERT_LOOKBACK_MAX = timedelta(days=7)
MAX_FILING_ALERTS = 20
MAX_NEWS_ALERTS = 10
BRIEF_MAX_CHARS = 6000
# Provider outages and stalls are worth one more (read-only) attempt; a refused key or model is not.
def retryable(reason) -> bool:
    from .agent_workflow.direct import RETRYABLE_PROVIDER
    return reason in RETRYABLE_PROVIDER or reason in {"no_progress", "workflow_incomplete"}
NO_EVIDENCE_BODY = "这次运行没有取得带来源的证据，所以没有生成简报。可以打开对话查看过程，或调整任务说明。"


def owner_credential() -> str | None:
    """The configured owner credential (data/personal-chat-login.json on this machine); tasks run as the
    owner because only the owner has tasks."""
    from .agent_chat_identity import _load_config
    config = _load_config()
    return config[3] if config else None


# --- an unattended turn ---------------------------------------------------------------------------

async def run_unattended_turn(credential: str, question: str, model: dict, *, token_budget: int, title: str) -> dict:
    """One agent turn with nobody watching, stored as an ordinary conversation of the owner so that the
    brief opens as a chat the owner can continue.  Returns answer, citations, audit, status, reason,
    conversation_id and the suggestions (``actions``) the model made."""
    from .agent_chat_store import (append_message, create_conversation, finish_conversation, owner_hash,
                                   set_conversation_title)
    from .agent_chat_stream import SYSTEM, _profile_block
    from .agent_workflow.graph import stream_workflow
    owner = owner_hash(credential)
    cid = await create_conversation(credential)
    turn = str(uuid.uuid4())
    await append_message(cid, turn, "user", question)
    await set_conversation_title(credential, cid, title)
    actions: list[dict] = []
    answer, done, genuine = "", None, set()
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    try:
        async for event in stream_workflow(question=question, messages=messages, provider=model["provider"],
                                           model=model["model"], api_key=model["api_key"], base_url=model.get("base_url"),
                                           owner_hash=owner, conversation_id=cid, identity_credential=credential,
                                           turn_id=turn, profile_block=await _profile_block(credential),
                                           unattended=True, token_budget=token_budget, action_sink=actions):
            kind = event.get("event")
            if kind == "assistant_tool_request":
                await append_message(cid, turn, "assistant", "", tool_calls=event["protocol"])
                genuine.update(call.get("id") for call in event["protocol"].get("calls", []) if isinstance(call, dict))
            elif kind == "tool_result" and event.get("call_id") in genuine:
                await append_message(cid, turn, "tool", json.dumps(event["result"], ensure_ascii=False, separators=(",", ":")),
                                     tool_call_id=event["call_id"])
            elif kind == "delta":
                answer += event["text"]
            elif kind == "done":
                done = event
    except Exception as exc:
        from fastapi import HTTPException
        code = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else type(exc).__name__
        await append_message(cid, turn, "assistant", answer or "这次定时运行没有完成。", status="failed")
        await finish_conversation(cid, "failed")
        return {"status": "failed", "reason": code, "answer": "", "citations": [], "audit": {},
                "conversation_id": cid, "actions": actions}
    if done is None:
        await append_message(cid, turn, "assistant", answer, status="failed")
        await finish_conversation(cid, "failed")
        return {"status": "failed", "reason": "workflow_incomplete", "answer": "", "citations": [], "audit": {},
                "conversation_id": cid, "actions": actions}
    protocol = {**done.get("final_protocol", {}), "audit": done.get("audit", {}), "unattended": True}
    citations = done.get("citations") or []
    if citations:
        protocol["citations"] = citations
    failed = done.get("status") == "failed"
    await append_message(cid, turn, "assistant", answer, tool_calls=protocol, status="failed" if failed else "complete")
    await finish_conversation(cid, "failed" if failed else "active")
    return {"status": done.get("status") or "complete", "reason": done.get("reason"), "answer": answer,
            "citations": citations, "audit": done.get("audit") or {}, "conversation_id": cid, "actions": actions,
            "pending": bool(done.get("pending_request"))}


def citation_sources(citations: list[dict]) -> list[dict]:
    """The numbered sources an inbox brief shows: the turn's own citation numbers, titles and links."""
    sources = []
    for item in citations:
        if not isinstance(item, dict) or type(item.get("ordinal")) is not int:
            continue
        sources.append({"ordinal": item["ordinal"], "title": str(item.get("title") or item.get("source_title") or "")[:300],
                        "url": item.get("url") or item.get("source_url"), "source_type": item.get("source_type"),
                        "source_id": str(item.get("source_id") or item.get("id") or "") or None,
                        "published_at": item.get("published_at") or item.get("filed_at")})
    return sorted(sources, key=lambda s: s["ordinal"])


def brief_question(task: AgentTask, symbols: list[str], now: datetime) -> str:
    local = now.astimezone(tasks._zone(task.timezone))
    scope = ("研究队列：" + "、".join(symbols)) if task.scope == "research_queue" else ("指定公司：" + "、".join(symbols))
    if not symbols:
        scope = "研究队列（目前为空）"
    return (f"{task.instruction}\n\n（定时任务「{task.name}」，{local:%Y-%m-%d %H:%M} {task.timezone}；范围：{scope}。"
            "回答里的事实请标注本轮来源编号。）")


# --- alerts ---------------------------------------------------------------------------------------

def filing_alerts(db, owner: str, task_id, symbols: list[str], since: datetime) -> int:
    """Filings the SEC monitor stored for the scope since ``since``; one entry per accession."""
    from .models import SecFilingInventory
    if not symbols:
        return 0
    rows = db.scalars(select(SecFilingInventory).where(SecFilingInventory.symbol.in_(symbols),
                                                       SecFilingInventory.observed_at > since)
                      .order_by(SecFilingInventory.observed_at.desc()).limit(MAX_FILING_ALERTS)).all()
    added = 0
    for row in rows:
        title = f"{row.symbol} 新申报 {row.form}（{row.filed_at.isoformat()}）"
        source = {"ordinal": 1, "title": f"{row.symbol} {row.form} {row.accession_number}", "url": row.source_url,
                  "source_type": "official_filing", "source_id": str(row.id), "published_at": row.filed_at.isoformat()}
        body = f"SEC EDGAR 收到 {row.symbol} 的 {row.form}，申报日 {row.filed_at.isoformat()} [1]。"
        added += tasks.add_item(owner, section="brief", kind="filing", title=title, body=body, sources=[source],
                                task_id=task_id, dedupe_key=f"filing:{row.cik}:{row.accession_number}", db=db)
    return added


def news_alerts(db, owner: str, task_id, symbols: list[str], *, sources=None, now: datetime | None = None) -> int:
    """News leads for the scope.  Metadata only (headline, outlet, link): a lead, not verified evidence."""
    from .news_discovery import discover_news_candidates
    if not symbols:
        return 0
    try:
        report = discover_news_candidates(symbols=symbols, sources=sources, discovered_at=now)
    except Exception:
        log.warning("news discovery failed", exc_info=False)
        return 0
    added = 0
    for candidate in report.candidates[:MAX_NEWS_ALERTS]:
        seen = candidate.published_at or candidate.source_seen_at
        source = {"ordinal": 1, "title": candidate.headline[:300], "url": candidate.source_url, "source_type": "news_lead",
                  "outlet": candidate.outlet, "published_at": seen.isoformat() if seen else None}
        body = f"{candidate.outlet}：{candidate.headline} [1]（新闻线索，未核实，不是证据）"
        added += tasks.add_item(owner, section="brief", kind="news", title=f"{candidate.symbol} 新闻线索",
                                body=body, sources=[source], task_id=task_id, dedupe_key=f"news:{candidate.id}", db=db)
    return added


def _last_two_closes(db, symbol: str):
    """The latest close and the one of the trading day before (one bar per day, the newest fetch)."""
    from .models import MarketPrice
    rows = db.scalars(select(MarketPrice).where(MarketPrice.symbol == symbol)
                      .order_by(MarketPrice.trading_date.desc(), MarketPrice.fetched_at.desc()).limit(10)).all()
    by_day = []
    for row in rows:
        if not by_day or by_day[-1].trading_date != row.trading_date:
            by_day.append(row)
    return by_day[:2]


def price_alerts(db, owner: str, task_id, lines: list[dict]) -> int:
    """A line is crossed when the latest close is on the other side of it from the close before."""
    added = 0
    for line in lines:
        bars = _last_two_closes(db, line["symbol"])
        if len(bars) < 2:
            continue
        latest, previous = bars
        for side in ("above", "below"):
            level = line.get(side)
            if level is None:
                continue
            crossed = previous.close < level <= latest.close if side == "above" else previous.close > level >= latest.close
            if not crossed:
                continue
            arrow = "↑ 向上突破" if side == "above" else "↓ 向下跌破"
            source = {"ordinal": 1, "title": f"{latest.symbol} 收盘价 {latest.trading_date.isoformat()}（{latest.source}）",
                      "url": None, "source_type": "market_price", "source_id": str(latest.id),
                      "published_at": latest.trading_date.isoformat()}
            body = (f"{latest.symbol} {latest.trading_date.isoformat()} 收盘 {latest.close:.2f}，前一交易日 {previous.close:.2f}，"
                    f"{arrow}你设的 {level:g} [1]。只是提醒，不是买卖建议。")
            added += tasks.add_item(owner, section="brief", kind="price", title=f"{latest.symbol} {arrow} {level:g}",
                                    body=body, sources=[source], task_id=task_id,
                                    dedupe_key=f"price:{task_id}:{latest.symbol}:{side}:{level:g}:{latest.trading_date.isoformat()}", db=db)
    return added


def run_alerts(task_id, *, now: datetime, news_sources=None, only: set[str] | None = None) -> dict:
    """The checks of one alerts task (``only`` limits them, for the monitor and refresh hooks)."""
    with SessionLocal() as db:
        task = db.get(AgentTask, task_id)
        if task is None:
            return {}
        config = task.config or {}
        symbols = tasks.scope_symbols(db, task)
        since = max(task.last_run_at or now - ALERT_LOOKBACK, now - ALERT_LOOKBACK_MAX)
        counts = {}
        if config.get("filings", True) and (only is None or "filings" in only):
            counts["filings"] = filing_alerts(db, task.owner_hash, task.id, symbols, since)
        if config.get("news", True) and (only is None or "news" in only):
            counts["news"] = news_alerts(db, task.owner_hash, task.id, symbols, sources=news_sources, now=now)
        if only is None or "prices" in only:
            counts["prices"] = price_alerts(db, task.owner_hash, task.id, list(config.get("price_lines") or []))
        db.commit()
        return counts


def run_alert_hooks(kind: str, *, now: datetime | None = None) -> None:
    """Called by the SEC monitor (``filings``) and the market refresh (``prices``) after they ran.  Never
    raises: an alert is a convenience and must not fail the job that feeds it."""
    try:
        now = now or datetime.now(UTC)
        with SessionLocal() as db:
            ids = db.scalars(select(AgentTask.id).where(AgentTask.kind == "alerts", AgentTask.enabled.is_(True))).all()
        for task_id in ids:
            run_alerts(task_id, now=now, only={kind})
    except Exception:
        log.warning("agent alert hook %s failed", kind, exc_info=False)


# --- one runner tick ------------------------------------------------------------------------------

def _finish(task_id, *, status: str, now: datetime, tokens: int = 0, conversation_id=None) -> None:
    with SessionLocal() as db:
        task = db.get(AgentTask, task_id)
        if task is None:
            return
        task.last_status, task.last_run_at = status, now
        tasks.add_usage(task, now, tokens)
        if conversation_id:
            task.last_conversation_id = uuid.UUID(str(conversation_id))
        db.commit()


def _claim(now: datetime) -> list[dict]:
    """Due tasks, each moved to its next slot before it runs: a crash never runs it twice, and the slots a
    sleeping Mac missed collapse into this one run."""
    claimed = []
    with SessionLocal() as db:
        due = db.scalars(select(AgentTask).where(AgentTask.enabled.is_(True), AgentTask.next_run_at <= now)
                         .order_by(AgentTask.next_run_at).with_for_update(skip_locked=True)).all()
        for task in due:
            claimed.append({"id": task.id, "missed": tasks.missed_slots(task, now)})
            tasks.advance(task, now)
            task.last_status = "running"
        db.commit()
    return claimed


async def run_brief(task_id, credential: str, *, now: datetime, missed: int = 1) -> str:
    from .agent_chat_store import owner_hash
    with SessionLocal() as db:
        task = db.get(AgentTask, task_id)
        if task is None:
            return "gone"
        if task.owner_hash != owner_hash(credential):
            status = "skipped_no_owner"
        else:
            status = None
        owner, name, channel = task.owner_hash, task.name, task.channel
        symbols = tasks.scope_symbols(db, task)
        question = brief_question(task, symbols, now)
        run_cap, daily_left = task.run_token_cap, task.daily_token_cap - tasks.tokens_used_today(task, now)
        local = now.astimezone(tasks._zone(task.timezone))
    if status:
        _finish(task_id, status=status, now=now)
        return status
    model = await asyncio.to_thread(tasks.load_background_model, owner)
    if model is None:
        _finish(task_id, status="skipped_no_model", now=now)
        return "skipped_no_model"
    used, result = 0, None
    for attempt in range(2):
        if daily_left - used <= 0:
            if result is None:
                _finish(task_id, status="skipped_daily_cap", now=now)
                return "skipped_daily_cap"
            break
        budget = max(1, min(run_cap, daily_left - used))
        result = await run_unattended_turn(credential, question, model, token_budget=budget,
                                           title=f"{name} · {local:%m-%d %H:%M}")
        used += int((result.get("audit") or {}).get("token_usage") or 0)
        retry = (result["status"] == "failed" and retryable(result.get("reason")) and not result["actions"]
                 and not result.get("citations"))
        if not retry:
            break
    del model
    conversation = result["conversation_id"]
    payload = {"task_name": name, **({"channel_pending": "telegram"} if channel == "telegram" else {}),
               **({"missed_slots": missed} if missed > 1 else {})}
    for action in result["actions"]:
        title = str((action.get("display") or {}).get("title") or "Agent 建议的操作")
        tasks.add_item(owner, section="confirm", kind="action", title=title[:200],
                       body=str((action.get("display") or {}).get("alternative") or ""),
                       payload={**payload, **action}, task_id=task_id, conversation_id=conversation)
    sources = citation_sources(result.get("citations") or [])
    if result["status"] == "failed":
        status = "failed"
    elif result.get("pending"):
        status = "needs_input"
    elif not sources:
        status = "no_evidence"
    else:
        status = "ok"
    body = result["answer"][:BRIEF_MAX_CHARS] if status in {"ok", "needs_input"} else NO_EVIDENCE_BODY
    if status == "failed":
        body = "这次定时运行没有完成（" + str(result.get("reason") or "failed") + "），没有生成简报。"
    tasks.add_item(owner, section="brief", kind="task_brief", title=f"{name} · {local:%Y-%m-%d %H:%M}", body=body,
                   sources=sources if status == "ok" else [], payload=payload, task_id=task_id,
                   conversation_id=conversation)
    _finish(task_id, status=status, now=now, tokens=used, conversation_id=conversation)
    return status


def run_alerts_task(task_id, *, now: datetime, news_sources=None) -> str:
    with SessionLocal() as db:
        task = db.get(AgentTask, task_id)
        if task is None:
            return "gone"
        empty = not tasks.scope_symbols(db, task) and not (task.config or {}).get("price_lines")
    if empty:
        _finish(task_id, status="skipped_no_scope", now=now)
        return "skipped_no_scope"
    try:
        run_alerts(task_id, now=now, news_sources=news_sources)
    except Exception:
        log.warning("alerts task failed", exc_info=False)
        _finish(task_id, status="failed", now=now)
        return "failed"
    _finish(task_id, status="ok", now=now)
    return "ok"


async def run_due(*, now: datetime | None = None, credential: str | None = None, news_sources=None) -> dict:
    """One tick: claim every due task and run it.  Returns {task_id: status}."""
    now = now or datetime.now(UTC)
    credential = credential if credential is not None else owner_credential()
    outcome = {}
    for item in _claim(now):
        with SessionLocal() as db:
            task = db.get(AgentTask, item["id"])
            kind = task.kind if task else None
        try:
            if kind == "alerts":
                outcome[str(item["id"])] = await asyncio.to_thread(run_alerts_task, item["id"], now=now, news_sources=news_sources)
            elif not credential:
                _finish(item["id"], status="skipped_no_owner", now=now)
                outcome[str(item["id"])] = "skipped_no_owner"
            else:
                outcome[str(item["id"])] = await run_brief(item["id"], credential, now=now, missed=item["missed"])
        except Exception:
            log.warning("agent task failed", exc_info=False)
            _finish(item["id"], status="failed", now=now)
            outcome[str(item["id"])] = "failed"
    return outcome


def tick() -> dict:
    """The launchd entry point: one process at a time (a session advisory lock), then ``run_due``."""
    with engine.connect() as connection:
        if not connection.execute(sql("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": RUNNER_LOCK}).scalar():
            return {"status": "busy"}
        try:
            return {"status": "ran", "tasks": asyncio.run(run_due())}
        finally:
            connection.execute(sql("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": RUNNER_LOCK})
