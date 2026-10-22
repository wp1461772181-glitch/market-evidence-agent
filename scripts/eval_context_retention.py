"""How much of the early conversation reaches the model: today's 14 KB window, a plain window of the
same size as the layers, and the three layers (``app.agent_context.layer_rows``, the code a turn runs).

The conversation is built, not recorded: about 40 research turns with tool batches, ten facts planted
in the first third (nine words of the user, two sentences of the assistant), then filler.  For each
budget the context a turn would carry is assembled by every arm, and two things are measured:

* ``in context``  how many planted facts are in the text the model is sent (deterministic);
* ``recall``      how many a model answers when it is asked for them (a real model, a few runs).

Segment summaries (layer 3) are made with the product's own summary prompt and checks.  Nothing is
stored.  Usage::

    PYTHONPATH=. python scripts/eval_context_retention.py --offline            # no model, no key
    PYTHONPATH=. python scripts/eval_context_retention.py --runs 2 --report docs/context-retention-report.md

The key is read from the project's ``.env`` into memory only and never printed or written.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import agent_context as layers  # noqa: E402
from app import agent_context_summary as summary  # noqa: E402

KB = 1024
TODAY_WINDOW = 14_000          # what the history window was before layers (bytes)

# fact id -> (where, token the answer must contain, the planted sentence, the question)
FACTS: dict[str, dict] = {
    "occasion": {"turn": 2, "who": "user", "token": "投资例会",
                 "text": "先说明一下，这些材料是给周五的投资例会准备的，所以每次结论先行。",
                 "question": "我说过这些材料是为了什么场合准备的？"},
    "filing": {"turn": 3, "who": "user", "token": "10-K",
               "text": "申报文件我只看年报，也就是 10-K，季报和新闻稿都不要。",
               "question": "我要求只看哪种申报文件？"},
    "metric": {"turn": 5, "who": "user", "token": "净收入留存率",
               "text": "我最关心的指标是 Snowflake 的净收入留存率，其他指标可以放一边。",
               "question": "我说我最关心的指标是什么？"},
    "period": {"turn": 6, "who": "user", "token": "2024 财年第四季度",
               "text": "分析口径统一用 2024 财年第四季度，不要混用别的期间。",
               "question": "我定的统一分析期间是什么？"},
    "accession": {"turn": 7, "who": "user", "token": "0001045810-24-000316",
                  "text": "我关注的那份申报文件的 accession 是 0001045810-24-000316，后面都按它来。",
                  "question": "我之前提到的 accession 编号是多少？"},
    "format": {"turn": 8, "who": "user", "token": "保留一位小数",
               "text": "金额一律用亿美元，并且保留一位小数，表格里也一样。",
               "question": "我对金额的单位和精度有什么要求？"},
    "correction": {"turn": 9, "who": "user", "token": "数据中心业务",
                   "text": "纠正一下，我说的是数据中心业务，不是游戏业务，别看错了。",
                   "question": "我纠正过我关注的是哪一块业务？"},
    "deadline": {"turn": 13, "who": "user", "token": "周三下午三点",
                 "text": "这份材料周三下午三点前要交，时间上请留意。",
                 "question": "这份材料的截止时间是什么时候？"},
    "titles": {"turn": 4, "who": "assistant", "token": "云数据平台客户留存分析",
               "text": "已找到两篇相关文章：《Snowflake 季度业绩说明》和《云数据平台客户留存分析》。",
               "question": "你当时找到的两篇文章里，第二篇的标题是什么？"},
    "conclusion": {"turn": 12, "who": "assistant", "token": "只有总毛利率",
                   "text": "结论：公开资料里没有分业务毛利率，只有总毛利率可供引用。",
                   "question": "你当时说公开资料里毛利率方面是什么情况？"},
}

_COMPANIES = ["苹果", "微软", "亚马逊", "谷歌", "特斯拉", "博通", "英特尔", "AMD"]
_TOPICS = ["营收增长的主要来源", "研发费用占比的变化", "自由现金流的走势", "资本开支指引", "毛利率变化的原因",
           "库存周转情况", "海外收入占比", "回购与分红安排", "管理层对下季度的展望", "风险因素披露的新增内容",
           "员工人数与人均产出", "递延收入的变化"]
_SENTENCES = [
    "公司披露的口径在不同季度之间存在细微调整，比较时需要先对齐统计范围。",
    "管理层在电话会上提到，需求端的节奏取决于客户预算的释放时点，具体数字以正式文件为准。",
    "从已保存的申报文件摘录来看，相关科目的变化幅度与上一期相比较为平缓。",
    "需要注意的是，该摘录只是原文的一部分，不能当作完整报告来引用。",
    "同行之间的横向比较要看各家对同一指标的定义是否一致，否则结论容易失真。",
    "与上年同期相比，变动的主要驱动因素在附注中有进一步说明，可作为后续核对的线索。",
    "目前保存的资料不足以判断该趋势是否会延续，建议在新的季度材料出来后再复核。",
    "表中数据按报告期末口径列示，未做汇率与会计政策变化的追溯调整。",
]


def _filler_turn(rng: random.Random) -> tuple[str, str, str]:
    company, topic = rng.choice(_COMPANIES), rng.choice(_TOPICS)
    user = f"{company}最近一期的{topic}是怎样的？请结合已保存的材料说明。"
    body = "\n\n".join(f"- {rng.choice(_SENTENCES)}{rng.choice(_SENTENCES)}" for _ in range(4))
    assistant = f"{company}在{topic}方面整体变化不大，细节如下。\n\n{(body + chr(10) + chr(10)) * 20}以上依据本轮检索到的已保存摘录，仅供研究参考。"
    excerpt = "".join(f"{rng.choice(_SENTENCES)}" for _ in range(14))
    tool = json.dumps({"ok": True, "source": "saved filing excerpts", "data": [
        {"title": f"{company} 申报文件摘录 {i}", "excerpt": excerpt, "observed_at": "2026-10-14"} for i in range(1, 4)]},
        ensure_ascii=False, separators=(",", ":"))
    return user, assistant, tool


def build_conversation(turns: int = 40, seed: int = 20261015) -> tuple[list[dict], dict[str, str]]:
    """The stored messages of the conversation (oldest first, with ``sequence``) and the planted facts."""
    rng = random.Random(seed)
    rows: list[dict] = []
    sequence = 0

    def add(turn_id: str, role: str, content: str, **extra) -> None:
        nonlocal sequence
        sequence += 1
        rows.append({"role": role, "content": content, "tool_calls": extra.get("tool_calls"),
                     "tool_call_id": extra.get("tool_call_id"), "status": "complete", "turn_id": turn_id,
                     "sequence": sequence})

    by_turn = {fact["turn"]: fact for fact in FACTS.values()}
    for n in range(1, turns + 1):
        turn_id = str(uuid.UUID(int=n))
        user, assistant, tool = _filler_turn(rng)
        planted = by_turn.get(n)
        if planted and planted["who"] == "user":
            user = planted["text"]
        if planted and planted["who"] == "assistant":
            assistant = planted["text"] + "\n\n" + assistant
        call_id = f"call-{n}"
        add(turn_id, "user", user)
        add(turn_id, "assistant", "", tool_calls={"calls": [{"id": call_id, "type": "function",
                                                              "function": {"name": "query_sec_filings", "arguments": "{}"}}]})
        add(turn_id, "tool", tool, tool_call_id=call_id)
        add(turn_id, "assistant", assistant)
    return rows, {key: fact["token"] for key, fact in FACTS.items()}


# --- arms: what each way of carrying the conversation sends ------------------------------------------

def _as_messages(rows: list[dict]) -> list[dict]:
    return [{"role": r["role"], "content": r["content"] or ""} for r in rows if r["role"] in {"user", "assistant", "tool"}]


def arm_today(rows: list[dict]) -> list[dict]:
    kept, _ = layers.newest_within(layers.complete_turns(rows), TODAY_WINDOW)
    return _as_messages([row for group in kept for row in group])


def arm_window(rows: list[dict], total_bytes: int) -> list[dict]:
    kept, _ = layers.newest_within(layers.complete_turns(rows), total_bytes)
    return _as_messages([row for group in kept for row in group])


def arm_layers(rows: list[dict], budget_kb: int, segments: list[dict]) -> tuple[list[dict], dict]:
    parts = budget_parts(budget_kb)
    context = layers.layer_rows(rows, segments, l1_bytes=parts["l1"], l2_bytes=parts["l2"], l3_bytes=parts["l3"])
    prefix = layers.history_messages(context["half_turns"])
    note = layers.summary_message(context["segments"])
    recent = [message for group in layers.complete_turns(context["l1_rows"])
              for message in layers.project_turn_history(group)]
    catalogue = layers.render_citation_catalogue(context.get("catalogue", []))
    return prefix + recent + ([note] if note else []) + ([catalogue] if catalogue else []), context


def budget_parts(budget_kb: int) -> dict:
    budget = budget_kb * KB
    l1, l2, l3 = layers.split_budget(budget)
    return {"budget": budget, "l1": l1, "l2": l2, "l3": l3, "total": budget}


def transcript(messages: list[dict]) -> str:
    names = {"user": "用户", "assistant": "助手", "tool": "工具结果", "system": "系统"}
    return "\n".join(f"{names.get(m['role'], m['role'])}：{m['content']}" for m in messages if m.get("content"))


def message_bytes(messages: list[dict]) -> int:
    return sum(layers.json_bytes(m) for m in messages)


def facts_in(text: str, tokens: dict[str, str]) -> dict[str, bool]:
    return {key: token in text for key, token in tokens.items()}


# --- the model ---------------------------------------------------------------------------------------

def _load_key(env_file: str | None = None, name: str = "DEEPSEEK_API_KEY") -> str:
    """The provider key, from the env file (default: the project's .env) into memory only."""
    from dotenv import dotenv_values
    path = Path(env_file) if env_file else ROOT / ".env"
    key = (dotenv_values(path).get(name) or os.environ.get(name) or "").strip()
    if not key:
        raise SystemExit(f"{name} is not set in {path}")
    return key


async def make_segments(rows: list[dict], budget_kb: int, call, *, rounds: int = 6,
                        attempts: int = 3) -> tuple[list[dict], dict]:
    """The segments the background task would have made by now: summarize what fell out of both
    layers, oldest first, until nothing is waiting.  A refused or failed text is tried again, up to
    ``attempts`` times for one segment (a turn later the product tries again the same way)."""
    parts = budget_parts(budget_kb)
    segments: list[dict] = []
    stats = {"calls": 0, "rejected": [], "chars": []}
    for _ in range(rounds):
        context = layers.layer_rows(rows, segments, l1_bytes=parts["l1"], l2_bytes=parts["l2"], l3_bytes=parts["l3"])
        batch = summary.pending_batch(context["overflow"])
        if not batch or sum(layers.half_size(t) for t in batch) < summary.MIN_PENDING_BYTES:
            break
        payload = summary.build_payload(batch, context["segments"])
        source = "\n".join([*payload["earlier_segments"], *(f"{x['user']}\n{x['assistant_start']}" for x in payload["turns"])])
        text = None
        for _attempt in range(max(1, attempts)):
            stats["calls"] += 1
            try:
                raw = await call([{"role": "system", "content": summary.SUMMARY_PROMPT},
                                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}])
            except RuntimeError as exc:
                stats["rejected"].append(f"call_failed:{exc}")
                continue
            try:
                text = summary.validate_summary(raw, source)
                break
            except summary.SummaryRejected as exc:
                stats["rejected"].append(str(exc))
        if text is None:
            break
        segments.append({"seq": len(segments) + 1, "from_sequence": batch[0].first_sequence,
                         "upto_sequence": batch[-1].last_sequence, "content": text})
        stats["chars"].append(len(text))
    return segments, stats


RECALL_SYSTEM = ("You answer questions about the conversation so far, using only what is in it. If the conversation "
                 "does not contain the answer, say you do not know. Answer in one short sentence.")


async def recall_batch(messages: list[dict], call) -> dict[str, str]:
    """All ten questions in one call (one request instead of ten), answered as one JSON object."""
    numbered = "\n".join(f"{i}. {fact['question']}" for i, fact in enumerate(FACTS.values(), 1))
    prompt = ("以下是此前的对话记录：\n" + transcript(messages) + "\n\n请只依据上面的对话回答下面的问题；对话里没有的写“不知道”。"
              "只输出一个 JSON 对象，键是题号（\"1\" 到 \"" + str(len(FACTS)) + "\"），值是简短的答案。\n" + numbered)
    raw = await call([{"role": "system", "content": RECALL_SYSTEM}, {"role": "user", "content": prompt}])
    start, end = raw.find("{"), raw.rfind("}")
    try:
        answers = json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
    except ValueError:
        answers = {}
    return {key: str(answers.get(str(i), "")) for i, key in enumerate(FACTS, 1)}


async def recall(messages: list[dict], question: str, call) -> str:
    sent = [{"role": "system", "content": RECALL_SYSTEM},
            {"role": "user", "content": "以下是此前的对话记录：\n" + transcript(messages) + f"\n\n问题：{question}"}]
    return await call(sent)


def make_caller(provider: str, model: str, key: str, limit: int, retries: int = 3):
    from app.agent_chat_provider import decision_request
    gate = asyncio.Semaphore(limit)

    async def call(messages: list[dict]) -> str:
        async with gate:
            last: Exception | None = None
            for attempt in range(max(1, retries)):
                try:
                    _response, decision = await decision_request(provider, model, key, messages, [], None)
                    return str((decision or {}).get("content") or "")
                except Exception as exc:  # a failed call is counted apart, never as a wrong answer
                    last = exc
                    await asyncio.sleep(1.5 * (attempt + 1))
            detail = getattr(last, "detail", "") if last else ""
            raise RuntimeError(f"{type(last).__name__ if last else 'unknown'}:{str(detail)[:60]}")

    return call


# --- the experiment ----------------------------------------------------------------------------------

async def run(args) -> dict:
    rows, tokens = build_conversation(args.turns)
    complete = layers.complete_turns(rows)
    total_bytes = sum(layers.row_bytes(r) for r in rows)
    result: dict = {"turns": len(complete), "total_bytes": total_bytes, "facts": len(tokens), "budgets": {},
                    "model": None if args.offline else f"{args.provider}/{args.model}", "runs": 0 if args.offline else args.runs}
    call = None if args.offline else make_caller(args.provider, args.model, _load_key(args.env_file, args.key_name),
                                                 args.concurrency, args.retries)
    questions = {key: fact["question"] for key, fact in FACTS.items()}

    async def score(name: str, messages: list[dict], extra: dict | None = None, ask: bool = True) -> dict:
        text = transcript(messages)
        present = facts_in(text, tokens)
        entry = {"bytes": message_bytes(messages), "messages": len(messages), "in_context": sum(present.values()),
                 "present": present, **(extra or {})}
        if call is not None and ask and name in args.recall_arms:
            async def one(key: str) -> tuple[str, bool | None]:
                try:
                    return key, tokens[key] in await recall(messages, questions[key], call)
                except RuntimeError:
                    return key, None

            async def batch() -> dict[str, bool | None]:
                try:
                    answers = await recall_batch(messages, call)
                    return {key: tokens[key] in answers[key] for key in tokens}
                except RuntimeError:
                    return {key: None for key in tokens}
            runs = []
            for _ in range(args.runs):
                if args.batch_recall:
                    runs.append(await batch())
                else:
                    runs.append(dict(await asyncio.gather(*(one(key) for key in tokens))))
            failed = sum(1 for run_ in runs for value in run_.values() if value is None)
            entry["recall_per_run"] = [sum(1 for v in run_.values() if v) for run_ in runs]
            entry["recall_avg"] = sum(entry["recall_per_run"]) / len(runs)
            entry["recall_by_fact"] = {key: sum(1 for run_ in runs if run_[key]) / len(runs) for key in tokens}
            entry["recall_failed_calls"] = failed
        return entry

    result["today"] = await score("today", arm_today(rows), {"window_bytes": TODAY_WINDOW})
    for budget_kb in args.budgets:
        parts = budget_parts(budget_kb)
        block: dict = {"history_total_bytes": parts["total"]}
        ask = budget_kb in args.recall_budgets
        block["window"] = await score("window", arm_window(rows, parts["total"]), ask=ask)
        started = time.perf_counter()
        messages, context = arm_layers(rows, budget_kb, [])
        block["assemble_ms"] = round((time.perf_counter() - started) * 1000, 2)
        extra = {"half_turns": len(context["half_turns"]), "overflow_turns": len(context["overflow"]),
                 "recent_turns": context["l1_turns"]}
        block["layers_no_summary"] = await score("layers_no_summary", messages, extra, ask=ask)
        segments, stats = ([], {"calls": 0, "rejected": [], "chars": []})
        if call is not None:
            segments, stats = await make_segments(rows, budget_kb, call, attempts=args.summary_attempts)
        messages, context = arm_layers(rows, budget_kb, segments)
        block["layers"] = await score("layers", messages, {
            "segments": len(context["segments"]), "segment_chars": stats["chars"], "summary_calls": stats["calls"],
            "summary_rejected": stats["rejected"], "half_turns": len(context["half_turns"]),
            "overflow_turns": len(context["overflow"]), "recent_turns": context["l1_turns"],
            "segment_texts": [s["content"] for s in context["segments"]]}, ask=ask)
        result["budgets"][str(budget_kb)] = block
    return result


def _section(result: dict, offline: bool) -> list[str]:
    def cell(entry: dict) -> str:
        text = f"{entry['in_context']}/{result['facts']}"
        if not offline and "recall_avg" in entry:
            text += f"　{entry['recall_avg']:.1f}/{result['facts']}"
        return text

    lines = [f"## 构造对话 {result['turns']} 轮（{result['total_bytes'] / KB:.0f} KB，含工具结果）", "",
             "| 预算 | 现状窗口（14 KB） | 同预算纯滑窗 | 分层（仅层 1 + 层 2） | 分层（含层 3 摘要） |",
             "| --- | --- | --- | --- | --- |"]
    today = result["today"]
    for budget_kb, block in result["budgets"].items():
        lines.append(f"| {budget_kb} KB 档（历史合计 {block['history_total_bytes'] / KB:.0f} KB） | {cell(today)} | "
                     f"{cell(block['window'])} | {cell(block['layers_no_summary'])} | {cell(block['layers'])} |")
    default = result["budgets"].get("64") or next(iter(result["budgets"].values()))
    lines += ["", f"每个事实（{'64' if '64' in result['budgets'] else next(iter(result['budgets']))} KB 档）：", "",
              "| 事实 | 来源 | 现状窗口 | 纯滑窗 | 分层（含摘要） |", "| --- | --- | --- | --- | --- |"]
    for key, fact in FACTS.items():
        def mark(entry: dict) -> str:
            base = "在" if entry["present"][key] else "—"
            if "recall_by_fact" in entry:
                base += f" · 答对 {entry['recall_by_fact'][key]:.0%}"
            return base
        lines.append(f"| {key}（第 {fact['turn']} 轮） | {'用户' if fact['who'] == 'user' else '助手'} | {mark(today)} | "
                     f"{mark(default['window'])} | {mark(default['layers'])} |")
    lines += [""]
    for budget_kb, block in result["budgets"].items():
        entry = block["layers"]
        lines.append(f"- {budget_kb} KB 档：半压 {entry['half_turns']} 轮，近期原文 {entry['recent_turns']} 轮，"
                     f"摘要 {entry['segments']} 段（{entry['summary_calls']} 次调用，被拒 {len(entry['summary_rejected'])} 次"
                     f"{'，长度 ' + '/'.join(str(n) for n in entry['segment_chars']) + ' 字' if entry['segment_chars'] else ''}）；"
                     f"发送 {entry['bytes'] / KB:.0f} KB（纯滑窗 {block['window']['bytes'] / KB:.0f} KB）；拼装耗时 {block['assemble_ms']} ms。")
    return lines + [""]


def render_report(results: list[dict]) -> str:
    offline = all(result["model"] is None for result in results)
    first = results[0]
    lines = ["# 上下文保留率评测报告（构造对话）", "",
             "> 自 2026-10-22 起，分层第 1、2 层只按问答投影和引用目录计量；旧工具批次与侧车不再重放。", "",
             f"- 每段对话埋 {first['facts']} 个早期事实（8 个来自用户原话，2 个来自助手答复的开头），之后是研究类问答与工具结果。",
             f"- 模型：{first['model'] or '未调用（只测“事实是否在上下文里”）'}"
             + (f"；每个事实一问，{first['runs']} 轮取平均。" if not offline else "。"),
             "- 单元格写法：`事实在上下文里 / 总数`" + ("　`模型答对（平均）/ 总数`。" if not offline else "。"),
             "- 四种携带方式：**现状窗口**（分层之前的 14 KB 窗口）、**同预算纯滑窗**（和分层一样大的窗口，只留最新轮次）、"
             "**分层（仅层 1 + 层 2）**、**分层（含层 3 摘要）**（线上实际的做法）。", ""]
    for result in results:
        lines += _section(result, offline)
    lines += ["## 怎么读这份数字", "",
              "- 这是**构造的**对话，用来比较同一预算下几种携带方式；不是线上数据。线上历史字节需在隔离影子库只读测量，不能从本评测推断。",
              "- 层 2 把每一轮压到约 1 KB（用户原话 + 答复开头），且相关回答的目录项按预算计入；层 3 的作用在更长的对话和更小的预算上。",
              "- 层 3 只注入最近 5 段摘要，更早的段落会被丢弃，所以它有一个视野，不是无限记忆。",
              "- 助手答复的内容只保留开头约 160 字，更早的只剩摘要，而摘要按设计不写数字结论（记忆不是证据），所以助手更早的结论会丢，这是取舍，不是缺陷。",
              "- 召回由真实模型回答，同一问题多轮取平均；调用失败单独计数，不算答错。判定是“答案文本里出现了预埋事实的关键词”，不做语义判分。",
              "- 召回测的是“把同样的上下文交给模型，它读不读得到”。产品里读这段历史的是指代消解阶段；回答阶段只读本轮证据（证据隔离，设计如此），所以分层提升的是指代消解看到的早期约束，不是让回答凭记忆作答。各阶段读什么见 `docs/agent-context.md`。",
              "- 指代消解只读最新的约 9,000 字（旋钮调高按比例多读），所以下表“在上下文里”是拼进请求的内容，不等于指代消解读到的；默认 64 KB 档的实测是 40 轮 10/10、80 轮（含摘要）9/10。",
              "- 事实不在上下文里时，非零的“答对”只能是猜中（例如 10-K 是常见答案），不是记住；所以“事实在上下文里”那一格才是确定性的，召回那一格用来确认模型确实用上了它。",
              "- 层 3 的摘要由同一个模型按线上的提示词和校验生成，一段最多尝试 3 次（线上由之后的轮次重试）；被拒的次数写在每档下面。",
              "- 只有一个模型、一段构造对话、少数几轮：这是一次对比，不是统计结论。",
              "- 简历或文档引用时，只能写成「在构造的长研究对话上、同预算下」的保留率。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--turns", type=int, default=40)
    parser.add_argument("--budgets", type=lambda s: [int(x) for x in s.split(",")], default=[32, 64, 128])
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--env-file", help="where the key is read from (default: .env of the project)")
    parser.add_argument("--key-name", default="DEEPSEEK_API_KEY", help="which variable holds the key")
    parser.add_argument("--retries", type=int, default=3, help="attempts per call (1 = fail fast)")
    parser.add_argument("--batch-recall", action="store_true", help="ask the ten questions in one call per arm")
    parser.add_argument("--summary-attempts", type=int, default=3, help="tries per segment (the product retries on later turns)")
    parser.add_argument("--recall-arms", type=lambda s: s.split(","), default=["today", "window", "layers_no_summary", "layers"])
    parser.add_argument("--recall-budgets", type=lambda s: [int(x) for x in s.split(",")], default=[32, 64],
                        help="budgets (KB) where the model is asked; the others are measured without it")
    parser.add_argument("--merge", nargs="+", help="render a report from earlier --json files instead of running")
    parser.add_argument("--offline", action="store_true", help="no model: only whether the facts are in the context")
    parser.add_argument("--json", help="write the raw result here")
    parser.add_argument("--report", help="write the Markdown report here")
    args = parser.parse_args()
    if args.merge:
        results = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.merge]
    else:
        results = [asyncio.run(run(args))]
    if args.json and not args.merge:
        Path(args.json).write_text(json.dumps(results[0], ensure_ascii=False, indent=1), encoding="utf-8")
    report = render_report(results)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    assert not re.search(r"sk-[A-Za-z0-9]{12,}", report), "the report must not contain a key"


if __name__ == "__main__":
    main()
