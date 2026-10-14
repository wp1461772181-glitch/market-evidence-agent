import base64, hashlib, json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import agent_chat_identity as identity


def client():
    app=FastAPI();app.include_router(identity.router);return TestClient(app,client=("192.0.2.8",5000))


def test_personal_login_constant_safe_result_and_stable_credential(tmp_path,monkeypatch):
    salt=b"s"*32; code="fixture-code"; credential="c"*72
    path=tmp_path/"private-login.json"
    path.write_text(json.dumps({"salt":base64.b64encode(salt).decode(),"hash":base64.b64encode(hashlib.pbkdf2_hmac("sha256",code.encode(),salt,100000)).decode(),"iterations":100000,"chat_credential":credential}))
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH",str(path));identity._attempts.clear()
    with client() as c:
        first=c.post("/agent-chat/personal-login",json={"code":code})
        second=c.post("/agent-chat/personal-login",json={"code":code})
        invalid=c.post("/agent-chat/personal-login",json={"code":"wrong"})
    assert first.status_code==second.status_code==200
    assert first.json()==second.json()=={"credential":credential}
    assert first.headers["cache-control"]=="no-store"
    assert invalid.status_code==401 and invalid.json()=={"detail":"login_unavailable"}
    assert credential not in invalid.text and code not in invalid.text


def test_personal_login_unavailable_rate_limit_and_no_config_disclosure(tmp_path,monkeypatch):
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH",str(tmp_path/"missing.json"));identity._attempts.clear()
    with client() as c:
        unavailable=c.post("/agent-chat/personal-login",json={"code":"anything"})
        get_file=c.get("/personal-chat-login.json")
        monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH",str(tmp_path/"valid.json"))
        salt=b"x"*32;(tmp_path/"valid.json").write_text(json.dumps({"salt":base64.b64encode(salt).decode(),"hash":base64.b64encode(b"0"*32).decode(),"iterations":100000,"chat_credential":"z"*64}))
        statuses=[c.post("/agent-chat/personal-login",json={"code":"wrong"}).status_code for _ in range(9)]
    assert unavailable.status_code==503 and unavailable.json()=={"detail":"login_unavailable"}
    assert get_file.status_code==404
    assert statuses[-1]==429


def test_conversation_list_requires_credential_and_is_bounded(monkeypatch):
    seen=[]
    async def fake(credential,limit,offset):seen.append((credential,limit,offset));return [{"id":"one","title":"first question"}]
    monkeypatch.setattr(identity,"list_conversations",fake)
    with client() as c:
        missing=c.get("/agent-chat/conversations")
        listed=c.get("/agent-chat/conversations?limit=50&offset=10",headers={"X-Chat-Session":"g"*48})
        invalid=c.get("/agent-chat/conversations?limit=51",headers={"X-Chat-Session":"g"*48})
    assert missing.status_code==401
    assert listed.status_code==200 and listed.json()["conversations"][0]["title"]=="first question"
    assert seen==[("g"*48,51,10)]
    assert invalid.status_code==422


def test_owner_header_with_non_ascii_is_rejected_without_server_error(tmp_path, monkeypatch):
    salt=b"s"*32;credential="o"*72;path=tmp_path/"generated-login.json"
    path.write_text(json.dumps({"salt":base64.b64encode(salt).decode(),"hash":base64.b64encode(b"h"*32).decode(),"iterations":100000,"chat_credential":credential}))
    monkeypatch.setenv("PERSONAL_CHAT_LOGIN_PATH",str(path))
    assert identity.owner_credential_matches("访客"+"x"*64) is False


def test_message_restore_rejects_missing_and_cross_owner(monkeypatch):
    from app import agent_chat_stream
    from app.agent_chat_middleware import create_agent_chat_access_middleware
    app=FastAPI();app.include_router(agent_chat_stream.router)
    app.middleware("http")(create_agent_chat_access_middleware(False,""))
    async def not_owned(conversation_id,credential): return None
    monkeypatch.setattr(agent_chat_stream,"owned_messages",not_owned)
    with TestClient(app) as c:
        missing=c.get("/agent-chat/conversations/00000000-0000-0000-0000-000000000001/messages")
        foreign=c.get("/agent-chat/conversations/00000000-0000-0000-0000-000000000001/messages",headers={"X-Chat-Session":"guest-identity-"+"x"*32})
    assert missing.status_code==401
    assert foreign.status_code==404


def test_list_store_query_is_owner_scoped_stable_and_bounded(monkeypatch):
    from datetime import datetime, timezone
    from app import agent_chat_store
    captured={}
    class Rows:
        def all(self): return [("conversation-a",datetime(2026,1,2,tzinfo=timezone.utc),datetime(2026,1,3,tzinfo=timezone.utc),"Q"*140,None)]
    class SessionStub:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def execute(self,statement): captured["statement"]=statement;return Rows()
    monkeypatch.setattr(agent_chat_store,"Session",SessionStub)
    result=__import__("asyncio").run(agent_chat_store.list_conversations("owner-credential",51,20))
    sql=str(captured["statement"].compile(compile_kwargs={"literal_binds":True}))
    assert "owner_hash" in sql and "ORDER BY agent_conversations.updated_at DESC, agent_conversations.id DESC" in sql
    assert "LIMIT 51" in sql and "OFFSET 20" in sql
    assert result[0]["title"]=="Q"*120
