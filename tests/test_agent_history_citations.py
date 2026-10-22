"""History citation lookup uses only owned final-answer manifests and current allowlists."""
import asyncio
from uuid import uuid4

from app.agent_workflow.history_citations import bind_history_citation, read_history_citation
from app.agent_workflow.state import AgentRuntimeContext


def test_history_citation_store_is_owner_scoped_and_reads_manifest_ordinal(disposable_database):
    from sqlalchemy.orm import Session
    from app.database import Base, SessionLocal, engine
    from app.agent_chat_models import AgentConversation, AgentMessage
    from app.agent_chat_store import load_turn_citation, owner_hash

    Base.metadata.create_all(bind=engine)
    conversation, turn = uuid4(), uuid4()
    with SessionLocal() as db:
        db.add(AgentConversation(id=conversation, owner_hash=owner_hash("owner")))
        db.add_all([
            AgentMessage(conversation_id=conversation, turn_id=turn, sequence=1, role="user", content="q", status="complete"),
            AgentMessage(conversation_id=conversation, turn_id=turn, sequence=2, role="assistant", content="", status="complete",
                         tool_calls={"calls": [], "citations": [{"ordinal": 7, "title": "wrong"}]}),
            AgentMessage(conversation_id=conversation, turn_id=turn, sequence=3, role="assistant", content="answer [7]", status="complete",
                         tool_calls={"citations": [{"ordinal": 7, "title": "other", "source_type": "official_filing", "quote": "other"},
                                       {"ordinal": 2, "title": "right", "source_type": "official_filing", "quote": "snapshot"}]})])
        noise_turn=uuid4()
        db.add_all([AgentMessage(conversation_id=conversation,turn_id=noise_turn,sequence=10+i,role="tool",
            content="older-window-noise",status="complete") for i in range(510)])
        db.commit()
    found = asyncio.run(load_turn_citation(str(conversation), "owner", str(turn), 2))
    assert found["ok"] and found["citation"]["quote"] == "snapshot" and found["origin"]["ordinal"] == 2
    assert asyncio.run(load_turn_citation(str(conversation), "guest", str(turn), 2)) == {
        "ok": False, "error_code": "history_citation_not_found"}


def test_duplicate_final_records_and_corrupt_manifest_fail_closed(disposable_database):
    from app.database import Base, SessionLocal, engine
    from app.agent_chat_models import AgentConversation, AgentMessage
    from app.agent_chat_store import load_turn_citation, owner_hash
    Base.metadata.create_all(bind=engine)
    conversation, turn, broken_turn = uuid4(), uuid4(), uuid4()
    with SessionLocal() as db:
        db.add(AgentConversation(id=conversation,owner_hash=owner_hash("owner")))
        for n in (1,2):
            db.add(AgentMessage(conversation_id=conversation,turn_id=turn,sequence=n,role="assistant",
                content="same final",status="complete",tool_calls={"citations":[{"ordinal":1,"title":"x","source_type":"filing","quote":"q"}]}))
        db.add(AgentMessage(conversation_id=conversation,turn_id=broken_turn,sequence=3,role="assistant",
            content="broken final",status="complete",tool_calls={"citations":[{"ordinal":3,"title":"missing fields"}]}))
        db.commit()
    assert asyncio.run(load_turn_citation(str(conversation),"owner",str(turn),1))["error_code"]=="history_citation_invalid"
    assert asyncio.run(load_turn_citation(str(conversation),"owner",str(broken_turn),3))["error_code"]=="history_citation_invalid"


def test_history_citation_requires_an_allowlisted_handle_and_binds_once(monkeypatch):
    from app.agent_chat_store import load_turn_citation
    from app.agent_chat_stream import _restore_citations
    import app.agent_chat_stream as stream

    async def loaded(*_):
        return {"ok": True, "citation": {"ordinal": 2, "title": "Filing", "source_type": "official_filing", "quote": "4.2%"},
                "origin": {"turn_id": "turn-a", "ordinal": 2}}
    monkeypatch.setattr("app.agent_chat_store.load_turn_citation", loaded)
    monkeypatch.setattr(stream, "_restore_citations", lambda items, _credential: items)
    context = AgentRuntimeContext("p", "m", "k", conversation_id="conversation", identity_credential="credential",
        history_citation_catalogue=({"turn_id": "turn-a", "sources": [{"ordinal": 2, "source_type": "official_filing", "available": True}]},))
    missing = asyncio.run(read_history_citation({"turn_id": "turn-b", "ordinal": 2}, context))
    assert missing["error_code"] == "history_citation_not_found"
    result = asyncio.run(read_history_citation({"turn_id": "turn-a", "ordinal": 2}, context))
    bound = []
    first = bind_history_citation(result, bound)
    second = bind_history_citation(result, bound)
    assert len(bound) == 1 and first["data"][0]["citation"] == second["data"][0]["citation"] == "[1]"
    assert "_snapshot" not in first


def test_derived_history_source_returns_parent_hint_without_loading(monkeypatch):
    import app.agent_chat_store as store
    async def loaded(*_):
        return {"ok": True, "citation": {"ordinal": 3, "title": "derived", "source_type": "calculation",
            "derived": True, "calculation": {"parents": [1, 2]}, "quote": "computed"},
            "origin": {"turn_id": "turn-a", "ordinal": 3}}
    monkeypatch.setattr(store, "load_turn_citation", loaded)
    context = AgentRuntimeContext("p", "m", "k", history_citation_catalogue=({"turn_id": "turn-a",
        "sources": [{"ordinal": 3, "source_type": "calculation", "derived": True, "parents": [1, 2]}]},))
    result = asyncio.run(read_history_citation({"turn_id": "turn-a", "ordinal": 3}, context))
    assert result["error_code"] == "history_citation_derived" and result["parents"] == [1, 2]


def test_turn_derived_parent_hint_uses_matching_history_handle_not_current_ordinal():
    from app.agent_workflow.direct import Turn
    context=AgentRuntimeContext("custom","m","k",history_citation_catalogue=(
        {"turn_id":"old-turn","sources":[{"ordinal":3,"derived":True,"parents":[1]}]},))
    async def emit(_): pass
    turn=Turn("q",context,emit,[],{})
    turn.citations.append({"ordinal":3,"derived":True,"parents":[99]})
    turn.accept("read_history_citation",{"turn_id":"old-turn","ordinal":3},
        {"ok":False,"error_code":"history_citation_derived","parents":[99]})
    assert turn.feedback[-1]["result"]["parents"]==[1]


def test_allowlist_is_only_a_handle_and_loaded_snapshot_controls_derived_status(monkeypatch):
    import app.agent_chat_store as store
    async def loaded(*_):
        return {"ok": True, "citation": {"ordinal": 1, "title": "derived", "source_type": "official_filing",
            "derived": True, "parents": [4], "quote": "computed"},
            "origin": {"turn_id": "turn-a", "ordinal": 1}}
    monkeypatch.setattr(store, "load_turn_citation", loaded)
    context=AgentRuntimeContext("p","m","k",conversation_id="c",identity_credential="owner",
        history_citation_catalogue=({"turn_id":"turn-a","sources":[{"ordinal":1,"source_type":"official_filing","available":True}]},))
    result=asyncio.run(read_history_citation({"turn_id":"turn-a","ordinal":1},context))
    assert result["error_code"]=="history_citation_derived" and result["parents"]==[4]


def test_wrong_owner_cannot_read_even_if_catalogue_has_derived_handle(monkeypatch):
    import app.agent_chat_store as store
    async def denied(*_): return {"ok":False,"error_code":"history_citation_not_found"}
    monkeypatch.setattr(store,"load_turn_citation",denied)
    context=AgentRuntimeContext("p","m","k",conversation_id="c",identity_credential="guest",
        history_citation_catalogue=({"turn_id":"turn-a","sources":[{"ordinal":3,"source_type":"calculation","derived":True,"parents":[1]}]},))
    result=asyncio.run(read_history_citation({"turn_id":"turn-a","ordinal":3},context))
    assert result["error_code"]=="history_citation_not_found"


def test_latest_answer_without_ordinal_never_falls_back_to_earlier_answer(monkeypatch):
    import app.agent_chat_store as store
    async def must_not_load(*_): raise AssertionError("a handle absent from the latest answer is not authorized")
    monkeypatch.setattr(store,"load_turn_citation",must_not_load)
    context=AgentRuntimeContext("p","m","k",conversation_id="c",identity_credential="owner",
        history_citation_catalogue=(
            {"turn_id":"latest","sources":[]},
            {"turn_id":"older","sources":[{"ordinal":2,"available":True}]},))
    result=asyncio.run(read_history_citation({"turn_id":"latest","ordinal":2},context))
    assert result["error_code"]=="history_citation_not_found"


def test_long_cjk_quote_is_bounded_for_tool_but_full_in_snapshot_and_deduplicated(monkeypatch):
    import app.agent_chat_store as store
    import app.agent_chat_stream as stream
    quote="数"*5000
    citation={"ordinal":1,"title":"长引用","source_type":"official_filing","quote":quote,"custom_meta":"kept"}
    async def loaded(*_): return {"ok":True,"citation":citation,"origin":{"turn_id":"turn-a","ordinal":1}}
    monkeypatch.setattr(store,"load_turn_citation",loaded)
    monkeypatch.setattr(stream,"_restore_citations",lambda items,_credential:items)
    context=AgentRuntimeContext("p","m","k",conversation_id="c",identity_credential="owner",
        history_citation_catalogue=({"turn_id":"turn-a","sources":[{"ordinal":1,"available":True}]},))
    result=asyncio.run(read_history_citation({"turn_id":"turn-a","ordinal":1},context))
    assert result["data"][0]["truncated"] and result["data"][0]["quote"]
    assert result["_snapshot"]["quote"]==quote and result["_snapshot"]["custom_meta"]=="kept"
    saved=[]
    bind_history_citation(result,saved);bind_history_citation(result,saved)
    assert len(saved)==1 and saved[0]["quote"]==quote


def test_registered_tool_schema_is_narrow_and_dispatches(monkeypatch):
    from app.agent_tool_catalog import definitions, schemas
    from app.agent_workflow import tools
    policy = definitions()["read_history_citation"]
    assert set(policy.args_schema.model_fields) == {"turn_id", "ordinal"}
    schema = next(item for item in schemas(owner=False) if item["function"]["name"] == "read_history_citation")
    assert set(schema["function"]["parameters"]["properties"]) == {"turn_id", "ordinal"}

    async def lookup(args, _context):
        return {"ok": True, "data": [args], "source": "history_citation"}
    monkeypatch.setattr("app.agent_workflow.history_citations.read_history_citation", lookup)
    context = AgentRuntimeContext("p", "m", "k")
    result = asyncio.run(tools._dispatch("read_history_citation", {"turn_id": "turn-a", "ordinal": 2}, context, False, None))
    assert result["ok"] and result["data"][0]["ordinal"] == 2


def test_invalidated_or_empty_restored_quote_is_unavailable(monkeypatch):
    import app.agent_chat_stream as stream
    import app.agent_chat_store as store

    async def loaded(*_):
        return {"ok": True, "citation": {"ordinal": 1, "title": "Filing", "source_type": "official_filing", "quote": "saved"},
                "origin": {"turn_id": "turn-a", "ordinal": 1}}
    monkeypatch.setattr(store, "load_turn_citation", loaded)
    context = AgentRuntimeContext("p", "m", "k", history_citation_catalogue=({"turn_id":"turn-a",
        "sources":[{"ordinal":1,"source_type":"official_filing","available":True}]},))
    for restored in ([{"source_type":"unavailable","quote":""}], [{"source_type":"official_filing","quote":"  "}]):
        monkeypatch.setattr(stream, "_restore_citations", lambda *_args, value=restored: value)
        result=asyncio.run(read_history_citation({"turn_id":"turn-a","ordinal":1},context))
        assert result["error_code"]=="history_citation_unavailable"
