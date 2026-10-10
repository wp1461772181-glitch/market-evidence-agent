"""Explicit local-only chat demo with a labelled upstream mock and disposable test DB."""
from __future__ import annotations
import asyncio, json, os, re, sys
from pathlib import Path
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
DATABASE_URL = os.environ.get("AGENT_DEMO_DATABASE_URL", "")
parsed = urlsplit(DATABASE_URL)
database_name = (parsed.path or "").lstrip("/")
if parsed.scheme not in {"postgresql+psycopg", "postgresql"} or not database_name.startswith("test_agent_chat_demo"):
    raise SystemExit("Set AGENT_DEMO_DATABASE_URL to an existing disposable PostgreSQL database named test_agent_chat_demo*.")
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:4178")

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from app.database import Base, SessionLocal, engine
from app.models import MarketPrice, SecFilingInventory
from app.agent_chat_models import AgentFAQ
from app.agent_faq_seed import seed_faq
from app.main import app as production_app
from app import agent_chat_api, agent_chat_provider, agent_chat_stream
from app.agent_chat_api import PROVIDERS
import uvicorn

mock = FastAPI(title="DEMO ONLY: Agent mock provider")

@mock.post("/chat/completions")
async def completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    prompt = str(messages[-1].get("content", "")) if messages else ""
    if body.get("tools") and body.get("tool_choice") != "none":
        if not body.get("stream"):
            symbol_match = re.search(r"\b(AAPL|AMD|BRK[.-]?[A-Z]?)\b", prompt, re.I)
            symbol = (symbol_match.group(1).upper().replace("-", ".") if symbol_match else "AAPL")
            if "财报" in prompt or "SEC" in prompt.upper(): name, args = "query_sec_filings", {"symbol": symbol}
            elif "可信度" in prompt or "费用" in prompt or "价钱" in prompt:
                keyword = "费用" if "费用" in prompt else "价钱" if "价钱" in prompt else "可信度"
                name, args = "query_research_faq", {"keyword": keyword}
            elif "材料" in prompt: name, args = "query_materials", {"symbol": symbol, "keyword": "earnings"}
            else: name, args = "query_stock_prices", {"symbol": symbol, "limit": 5}
            msg = {"role":"assistant","content":None,"tool_calls":[{"id":"demo-call-1","type":"function","function":{"name":name,"arguments":json.dumps(args,ensure_ascii=False)}}]}
            return {"model":"demo-mock","choices":[{"message":msg,"finish_reason":"tool_calls"}]}
    if not body.get("stream"):
        return {"model": body.get("model", "demo-mock"), "choices": [{"message": {"role": "assistant", "content": "DEMO MOCK: connection check succeeded."}}]}
    if body.get("tools"):
        symbol_match = re.search(r"\b(AAPL|AMD|BRK[.-]?[A-Z]?)\b", prompt, re.I)
        symbol = (symbol_match.group(1).upper().replace("-", ".") if symbol_match else "AAPL")
        if "财报" in prompt or "SEC" in prompt.upper():
            name, args = "query_sec_filings", {"symbol": symbol}
        elif "可信度" in prompt or "费用" in prompt or "价钱" in prompt:
            keyword = "费用" if "费用" in prompt else "价钱" if "价钱" in prompt else "可信度"
            name, args = "query_research_faq", {"keyword": keyword}
        elif "材料" in prompt:
            name, args = "query_materials", {"symbol": symbol, "keyword": "earnings"}
        else:
            name, args = "query_stock_prices", {"symbol": symbol, "limit": 5}
        msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "demo-call-1", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
        return {"model": "demo-mock", "choices": [{"message": msg, "finish_reason": "tool_calls"}]}
    tool_data = ""
    for message in messages:
        if message.get("role") == "tool": tool_data = str(message.get("content", ""))
    try: result = json.loads(tool_data) if tool_data else {}
    except ValueError: result = {}
    records = result.get("data") or []
    if not records:
        answer = f"DEMO MOCK: 该固定演示数据库没有匹配的已保存记录；不会生成行情或材料。来源：{result.get('source') or '本地演示记录'}；as-of：{result.get('as_of') or '无匹配记录'}。"
    elif isinstance(records[0], dict) and "close" in records[0]:
        prices = "；".join(f"{row.get('trading_date')}: close {row.get('close')}" for row in records[:5])
        answer = f"DEMO MOCK: 以下为本地已保存行情（非实时）：{prices}。来源：market_prices；数据抓取时间：{result.get('as_of') or '未记录'}。"
    elif isinstance(records[0], dict) and "form" in records[0]:
        filing = records[0]
        answer = f"DEMO MOCK: 本地已保存 SEC 清单包含 {filing.get('form')}，提交日 {filing.get('filed_at')}。未访问 SEC。来源：{filing.get('source_url')}；系统观察时间：{result.get('as_of') or '未记录'}。"
    elif isinstance(records[0], dict) and "answer" in records[0]:
        answer = "DEMO MOCK: " + str(records[0].get("answer", ""))
    else:
        answer = "DEMO MOCK: 以下内容来自隔离演示数据库的已保存记录。"
    async def chunks():
        pieces=(answer[:22],answer[22:])
        for index,piece in enumerate(pieces):
            event = {"choices": [{"delta": {"content": piece}, "finish_reason": "stop" if index==len(pieces)-1 else None}]}
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0.35)
        yield "data: [DONE]\n\n"
    return StreamingResponse(chunks(), media_type="text/event-stream")


def prepare_fixture_database():
    # Only the explicitly named disposable database is ever touched.
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_faq(session)
        now = datetime.now(timezone.utc)
        for day, close in [(date(2026,10,5),250.5),(date(2026,10,6),251.0),(date(2026,10,7),251.5),(date(2026,10,8),252.0),(date(2026,10,9),252.5)]:
            exists = session.query(MarketPrice).filter_by(symbol="AAPL",trading_date=day,source="fixed-demo-v1").first()
            if not exists: session.add(MarketPrice(symbol="AAPL",trading_date=day,open=249.0,high=254.0,low=248.0,close=close,volume=40000000,source="fixed-demo-v1",fetched_at=now))
        accession="0000320193-26-000001"
        if not session.query(SecFilingInventory).filter_by(cik="0000320193",accession_number=accession).first():
            session.add(SecFilingInventory(symbol="AAPL",cik="0000320193",accession_number=accession,form="10-Q",filed_at=date(2026,8,1),primary_document="aapl-20260630.htm",source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-20260630.htm",source="fixed-demo-v1",review_status="pending_review",observed_at=now,content_status="fetched",content_excerpt="Fixed isolated demo filing excerpt.",content_truncated=False))
        session.commit()

async def main():
    mock_port=int(os.environ.get("AGENT_DEMO_MOCK_PORT","8899")); api_port=int(os.environ.get("AGENT_DEMO_API_PORT","8018"))
    if not (1024<=mock_port<=65535 and 1024<=api_port<=65535): raise SystemExit("Demo ports must be in 1024..65535")
    local=f"http://127.0.0.1:{mock_port}/chat/completions"
    # Demo-only routing deliberately ignores every UI-supplied preset/custom URL.
    # The browser's fake key and model ID still pass through unchanged.
    for name in ("deepseek","kimi","minimax","qwen","zhipu","doubao","hunyuan","baidu","siliconflow","openrouter","custom"):
        PROVIDERS[name]=local
    original_call=agent_chat_api._call
    async def demo_call(payload,messages,max_tokens):
        return await original_call(payload.model_copy(update={"provider":"deepseek","base_url":None}),messages,max_tokens)
    agent_chat_api._call=demo_call
    original_decision=agent_chat_provider.decision_request
    original_answer=agent_chat_provider.stream_answer
    async def demo_decision(provider,model,api_key,messages,schemas,base_url=None):
        return await original_decision("deepseek",model,api_key,messages,schemas,None)
    async def demo_answer(provider,model,api_key,messages,reasoning_collector=None,base_url=None):
        async for piece in original_answer("deepseek",model,api_key,messages,reasoning_collector,None): yield piece
    agent_chat_provider.decision_request=demo_decision
    agent_chat_provider.stream_answer=demo_answer
    agent_chat_stream.decision_request=demo_decision
    agent_chat_stream.stream_answer=demo_answer
    prepare_fixture_database()
    print(f"DEMO ONLY: local incremental mock provider on :{mock_port}; fixed disposable PostgreSQL fixture DB; no paid provider calls.")
    print(f"DEMO ONLY: API http://127.0.0.1:{api_port}; mock provider :{mock_port}")
    mock_server = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=mock_port, log_level="warning"))
    app_server = uvicorn.Server(uvicorn.Config(production_app, host="127.0.0.1", port=api_port, log_level="info"))
    await asyncio.gather(mock_server.serve(), app_server.serve())

if __name__ == "__main__": asyncio.run(main())
