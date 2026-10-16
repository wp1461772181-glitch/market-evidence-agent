import asyncio
import pytest
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.state import AgentRuntimeContext

@pytest.mark.parametrize('company,symbol,mention,accepted',[
 ('Intel','INTC','Intel',True),('Intel','MSFT','Intel',False),
 ('Micron','MU','美光',True),('Palantir','PLTR','凭空公司',False),
])
def test_verified_ticker_pair_and_literal_mention_bind_scope(monkeypatch,company,symbol,mention,accepted):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'INTC','title':'INTEL CORP'},{'ticker':'MU','title':'MICRON TECHNOLOGY INC'}])
    async def execute(*_):return {'ok':True,'data':[]}
    context=AgentRuntimeContext('custom','test','dummy',intent_recognizer=lambda _:{'category':'A','days_back':None,'symbol':symbol,'company':company,'company_mention':mention},executor=execute,decider=lambda *_:{'content':'没有可核验资料。'})
    async def emit(_):pass
    result=asyncio.run(run_direct_turn('查Intel和美光的营收',context,emit=emit))
    assert bool(result['pending_request'])!=accepted

def test_shared_first_word_does_not_verify_a_different_issuer(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'BNS','title':'BANK OF NOVA SCOTIA'},{'ticker':'BAC','title':'BANK OF AMERICA CORP'}])
    assert not entities.verified_pair('Bank of America','BNS')
    assert entities.verified_pair('Bank of America Corporation','BAC')
