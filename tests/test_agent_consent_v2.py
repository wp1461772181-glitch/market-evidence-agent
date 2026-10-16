import uuid
from datetime import UTC,datetime,timedelta
import pytest
from app.database import Base,engine,SessionLocal
from app.agent_workflow.models import AgentConsent
from app.agent_workflow import consent

@pytest.fixture
def owner(monkeypatch,disposable_database):
    Base.metadata.create_all(engine,tables=[AgentConsent.__table__])
    monkeypatch.setattr(consent,'owner_credential_matches',lambda c:c=='owner')
    return 'owner'

def test_consent_freezes_args_and_requires_bound_capability(owner):
    ops=[{'name':'index','arguments':{'max_tasks':1}}]
    card=consent.propose(owner,'conversation',ops,{'title':'index'})
    assert card['choices'][0]['value'].startswith('confirm:')
    token=card['choices'][0]['value'].split(':',1)[1];calls=[]
    with pytest.raises(consent.ConsentError):consent.execute(owner,'other',token,ops,lambda *_:calls.append(1))
    with pytest.raises(consent.ConsentError):consent.execute(owner,'conversation',token,[{'name':'index','arguments':{'max_tasks':2}}],lambda *_:calls.append(1))
    assert not calls
    first=consent.execute(owner,'conversation',token,ops,lambda *_:calls.append(1) or {'ok':True})
    again=consent.execute(owner,'conversation',token,ops,lambda *_:calls.append(1))
    assert first==again and calls==[1]

def test_guest_and_expired_and_tokenless_rejected(owner):
    with pytest.raises(consent.ConsentError):consent.propose('guest','c',[],{})
    card=consent.propose(owner,'c',[{'name':'index','arguments':{}}],{})
    token=card['choices'][0]['value'].split(':',1)[1]
    with SessionLocal() as db:
        row=db.get(AgentConsent,uuid.UUID(card['id']));row.expires_at=datetime.now(UTC)-timedelta(seconds=1);db.commit()
    for value in ('',token):
        with pytest.raises(consent.ConsentError):consent.execute(owner,'c',value,[{'name':'index','arguments':{}}],lambda *_:pytest.fail('must not execute'))

def test_concurrent_confirmations_have_one_business_effect(owner):
    from concurrent.futures import ThreadPoolExecutor
    import threading,time
    ops=[{'name':'index','arguments':{'max_tasks':1}}]
    card=consent.propose(owner,'concurrent',ops,{})
    token=card['choices'][0]['value'].split(':',1)[1];calls=[]
    def handler(*_):calls.append(1);time.sleep(.03);return {'ok':True,'effect':1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:consent.execute(owner,'concurrent',token,ops,handler),range(2)))
    assert calls==[1] and results[0]==results[1]

def test_crash_marked_execution_is_not_automatically_replayed(owner):
    ops=[{'name':'index','arguments':{}}];card=consent.propose(owner,'crash',ops,{})
    with SessionLocal() as db:
        row=db.get(AgentConsent,uuid.UUID(card['id']));row.status='executing';db.commit()
    with pytest.raises(consent.ConsentError,match='consumed'):
        consent.execute(owner,'crash',card['choices'][0]['value'].split(':',1)[1],ops,lambda *_:pytest.fail('duplicate'))
