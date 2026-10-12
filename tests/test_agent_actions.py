import pytest

from app.agent_workflow import actions


def test_owner_action_idempotency_and_guest_denial(disposable_database, monkeypatch):
    from runpy import run_path
    import app.knowledge.api as knowledge_api
    run_path("scripts/migrate_agent_actions.py")["apply"]()
    calls=[]
    monkeypatch.setattr(actions, "owner_credential_matches", lambda value: value in {"owner-session", "other-owner"})
    monkeypatch.setattr(knowledge_api, "run_index", lambda payload, x_chat_session: calls.append(payload.max_tasks) or {"ok":True,"indexed":1})
    with pytest.raises(actions.ActionError, match="owner_login_required"):
        actions.execute_action(credential="guest-session", request_key="index-1", action="index", payload={"max_tasks":2})
    first=actions.execute_action(credential="owner-session", request_key="index-1", action="index", payload={"max_tasks":2})
    second=actions.execute_action(credential="owner-session", request_key="index-1", action="index", payload={"max_tasks":2})
    assert first["status"] == second["status"] == "succeeded" and calls == [2]
    with pytest.raises(actions.ActionError, match="idempotency_conflict"):
        actions.execute_action(credential="owner-session", request_key="index-1", action="index", payload={"max_tasks":3})
    with pytest.raises(actions.ActionError, match="task_not_found"):
        actions.execute_action(credential="other-owner", request_key="index-1", action="status", payload={})


def test_selected_index_scope_is_bound_to_idempotent_payload(disposable_database, monkeypatch):
    from runpy import run_path
    from uuid import uuid4
    run_path("scripts/migrate_agent_actions.py")["apply"]()
    source_id = str(uuid4())
    calls = []
    monkeypatch.setattr(actions, "owner_credential_matches", lambda value: value in {"owner-session", "other-owner"})
    monkeypatch.setattr(actions, "_index_selected", lambda db, source, identity, limit:
                        calls.append((str(source), identity, limit)) or {"status": "succeeded", "indexed": 1})
    payload = {"source_id": source_id, "max_tasks": 3}
    first = actions.execute_action(credential="owner-session", request_key="selected-1", action="index", payload=payload)
    second = actions.execute_action(credential="owner-session", request_key="selected-1", action="index", payload=payload)
    assert first["status"] == second["status"] == "succeeded"
    assert len(calls) == 1 and calls[0][0] == source_id
    with pytest.raises(actions.ActionError, match="idempotency_conflict"):
        actions.execute_action(credential="owner-session", request_key="selected-1", action="index",
                               payload={**payload, "source_id": str(uuid4())})


def test_interrupted_non_idempotent_action_is_not_replayed(disposable_database, monkeypatch):
    from runpy import run_path
    from uuid import uuid4
    from sqlalchemy import text
    from app.database import SessionLocal
    import app.knowledge.api as knowledge_api
    run_path("scripts/migrate_agent_actions.py")["apply"]()
    monkeypatch.setattr(actions, "owner_credential_matches", lambda value: value in {"owner-session", "other-owner"})
    calls=[]
    monkeypatch.setattr(knowledge_api, "run_index", lambda *args, **kwargs: calls.append(1))
    payload={"max_tasks":2}
    identity=actions.owner_hash("owner-session")
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO agent_action_requests
          (id,owner_hash,request_key,action,payload_digest,status,result)
          VALUES (:id,:owner,:key,'index',:digest,'running','{}'::jsonb)"""),
          {"id":uuid4(),"owner":identity,"key":"interrupted-index",
           "digest":actions._fingerprint("index",payload)})
        db.commit()
    result=actions.execute_action(credential="owner-session",request_key="interrupted-index",action="index",payload=payload)
    assert result["status"] == "failed"
    assert result["error_code"] == "interrupted_reconcile_required"
    assert calls == []
    with pytest.raises(actions.ActionError, match="task_not_found"):
        actions.execute_action(credential="other-owner",request_key="interrupted-index",action="status",payload={})


def test_interrupted_after_job_creation_reconciles_exact_job(disposable_database, monkeypatch):
    from runpy import run_path
    from uuid import uuid4
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.database import engine
    run_path("scripts/migrate_agent_actions.py")["apply"]()
    run_path("scripts/migrate_forecast_v2.py")["apply_schema"](engine)
    monkeypatch.setattr(actions, "owner_credential_matches", lambda value: value in {"owner-session","other-owner"})
    payload={"symbol":"AMD"}
    identity=actions.owner_hash("owner-session")
    job_id=uuid4()
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO agent_action_requests
          (id,owner_hash,request_key,action,payload_digest,status,result)
          VALUES (:id,:owner,:key,'forecast',:digest,'running','{}'::jsonb)"""),
          {"id":uuid4(),"owner":identity,"key":"crash-job",
           "digest":actions._fingerprint("forecast",payload)})
        db.execute(text("""INSERT INTO forecast_jobs_v2
          (id,symbol,kind,source_refs,status,current_stage,attempts,idempotency_key,request_fingerprint,lease_epoch)
          VALUES (:id,'AMD','new','[]'::jsonb,'queued','queued','[]'::jsonb,:key,'fixture',0)"""),
          {"id":job_id,"key":actions._underlying_key(identity,"crash-job")})
        db.commit()
    first=actions.execute_action(credential="owner-session",request_key="crash-job",action="forecast",payload=payload)
    second=actions.execute_action(credential="owner-session",request_key="crash-job",action="forecast",payload=payload)
    assert first["status"] == second["status"] == "running"
    assert first["job_id"] == second["job_id"] == str(job_id)
    by_job=actions.execute_action(credential="owner-session",request_key=str(job_id),action="status",payload={})
    assert by_job["job_id"] == str(job_id)
    with pytest.raises(actions.ActionError, match="task_not_found"):
        actions.execute_action(credential="other-owner",request_key=str(job_id),action="status",payload={})


def test_index_status_uses_remaining_and_failures():
    assert actions._index_status({"indexed":0,"failed":0},3) == "running"
    assert actions._index_status({"indexed":2,"failed":0},3) == "partially_succeeded"
    assert actions._index_status({"indexed":2,"failed":1},1) == "partially_succeeded"
    assert actions._index_status({"indexed":0,"failed":1},1) == "failed"
    assert actions._index_status({"indexed":2,"failed":0},0) == "succeeded"


def test_running_ledger_reports_running_only_while_side_effect_lock_held(disposable_database, monkeypatch):
    from runpy import run_path
    from uuid import uuid4
    from sqlalchemy import text
    from app.database import SessionLocal, engine
    run_path("scripts/migrate_agent_actions.py")["apply"]()
    monkeypatch.setattr(actions,"owner_credential_matches",lambda value: value=="owner-session")
    identity=actions.owner_hash("owner-session")
    key="active-index-fixture"
    with SessionLocal() as db:
        db.execute(text("""INSERT INTO agent_action_requests
          (id,owner_hash,request_key,action,payload_digest,status,result)
          VALUES (:id,:owner,:key,'index',:digest,'running','{}'::jsonb)"""),
          {"id":uuid4(),"owner":identity,"key":key,"digest":actions._fingerprint("index",{})})
        db.commit()
    with engine.connect() as held:
        held.execute(text("SELECT pg_advisory_lock(hashtext(:lock))"),{"lock":identity+":"+key})
        held.commit()
        active=actions.execute_action(credential="owner-session",request_key=key,action="status",payload={})
        assert active["status"]=="running"
        held.execute(text("SELECT pg_advisory_unlock(hashtext(:lock))"),{"lock":identity+":"+key})
        held.commit()
    interrupted=actions.execute_action(credential="owner-session",request_key=key,action="status",payload={})
    assert interrupted["status"]=="failed"
    assert interrupted["error_code"]=="interrupted_reconcile_required"
