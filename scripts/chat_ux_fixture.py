"""Loopback-only fake chat backend for local visual acceptance; all market-like values are fictional."""
import asyncio
import json
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="Market Evidence Agent local UX fixture")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5179", "http://localhost:5179"], allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Accept", "Content-Type", "X-Chat-Session", "X-Fixture-Slow"], allow_credentials=False)
PERSONAL = "fixture-personal-identity-" + "p" * 40
DEMO_PROFILE = {"id": "demo-profile", "display_name": "Demo model (fixture)", "provider": "deepseek", "base_url": "https://example.invalid/v1", "model": "fixture-only", "api_key": "fixture-only-never-used", "tested_at": "2026-01-01T00:00:00Z"}
PROFILE_DOCUMENT = {"version": 1, "profiles": [DEMO_PROFILE], "selected_id": "demo-profile"}

@app.get("/health")
def health(): return {"status": "demo_fixture_only"}

@app.post("/agent-chat/personal-login")
def login(): return {"credential": PERSONAL}

@app.get("/agent-chat/local-model-profiles")
def profiles(): return {**PROFILE_DOCUMENT, "enabled": True, "path": "fixture memory only"}

@app.put("/agent-chat/local-model-profiles")
async def save_profiles(request: Request):
    global PROFILE_DOCUMENT
    payload = await request.json()
    PROFILE_DOCUMENT = {"version": 1, "profiles": payload.get("profiles", []), "selected_id": payload.get("selected_id")}
    return {**PROFILE_DOCUMENT, "enabled": True, "path": "fixture memory only"}

@app.post("/agent-chat/hello")
def hello(): return {"content": "Fixture connection ready. No model was called.", "model": "fixture-only"}

def identity(x_chat_session):
    if not x_chat_session: raise HTTPException(401, "chat_session_required")
    return "personal" if x_chat_session == PERSONAL else "guest"

def rows(owner):
    prefix = "personal" if owner == "personal" else "guest"
    return [
        {"id": f"fixture-{prefix}-aapl", "title": "Show the AAPL demo table", "created_at": "2026-10-10T10:00:00Z", "updated_at": "2026-10-11T10:00:00Z"},
        {"id": f"fixture-{prefix}-notes", "title": "Summarize fictional sample notes", "created_at": "2026-10-09T10:00:00Z", "updated_at": "2026-10-10T10:00:00Z"},
    ]

@app.get("/agent-chat/conversations")
def conversations(x_chat_session: str | None = Header(default=None), limit: int = 20, offset: int = 0):
    owner = identity(x_chat_session); items = rows(owner)[offset:offset + min(max(limit, 1), 50)]
    return {"conversations": items, "has_more": False}

def demo_answer():
    first = "\n".join(f"| Demo Q{i} | ${100+i*2}.00 | Fictional placeholder |" for i in range(1, 9))
    second = "\n".join(f"| Demo signal {i} | {i}/5 | Illustrative only |" for i in range(1, 6))
    return ("**DEMO ONLY — fictional values; not market data or evidence.**\n\n"
        "This long sample checks readable text, **bold emphasis**, links [fixture](https://example.invalid), and scrollable tables.\n\n"
        "| Period | Fictional close | Note |\n|:--|--:|:--|\n" + first + "\n\n"
        "| Fictional signal | Score | Detail |\n|:--|--:|:--|\n" + second + "\n\n"
        "- Placeholder list item.\n- No provider or live market data was called.")

@app.get("/agent-chat/conversations/{conversation_id}/messages")
def messages(conversation_id: str, x_chat_session: str | None = Header(default=None)):
    owner = identity(x_chat_session); known = {row["id"] for row in rows(owner)}
    if conversation_id not in known: raise HTTPException(404, "conversation_not_found")
    answer = demo_answer() if conversation_id.endswith("aapl") else "**Fixture notes only.**\n\nThree user turns are included to exercise the question rail."
    messages = [{"role": "user", "content": "Show the AAPL demo table"}, {"role": "assistant", "content": answer, "status": "complete", "tool_trace": [{"name": "saved_material_search", "status": "complete"}]}, {"role": "user", "content": "What does the fictional second table show?"}, {"role": "assistant", "content": "It shows five **demo signals**; these are placeholders."}, {"role": "user", "content": "Confirm this is a fixture."}, {"role": "assistant", "content": "Confirmed: this is a local fixture with no real market data."}]
    return {"messages": messages}

@app.post("/agent-chat/stream")
async def stream(request: Request):
    payload = await request.json(); text = demo_answer() if "AAPL" in str(payload.get("message", "")).upper() else "**Local fixture response.** No model was called."
    slow = "[slow]" in str(payload.get("message", ""))
    events = [("conversation", {"conversation_id": "fixture-guest-new"}), ("status", {"status": "writing_answer"}), ("tool_start", {"call_id": "fixture-call", "name": "saved_material_search", "arguments": {"query": "fictional fixture"}}), ("tool_result", {"call_id": "fixture-call", "result": {"ok": True, "data": []}})]
    events += [("delta", {"text": text[i:i+65]}) for i in range(0, len(text), 65)] + [("done", {"status": "complete"})]
    async def generate():
        for name, data in events:
            if slow and name == "delta": await asyncio.sleep(.18)
            yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
