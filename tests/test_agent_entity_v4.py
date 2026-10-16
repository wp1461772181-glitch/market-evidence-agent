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

@pytest.mark.parametrize('company,symbol,mention,question,expected',[
 ('Advanced Micro Devices, Inc.','AMD','AMD','抓取AMD的SEC原文','AMD'),
 ('Micron Technology',None,'美光','查美光最近的新闻','MU'),
])
def test_literal_ticker_or_unique_official_issuer_does_not_force_company_card(monkeypatch,company,symbol,mention,question,expected):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'AMD','title':'ADVANCED MICRO DEVICES INC'},{'ticker':'MU','title':'MICRON TECHNOLOGY INC'}])
    async def execute(*_):return {'ok':True,'data':[]}
    context=AgentRuntimeContext('custom','test','dummy',classifier=lambda _:{'category':'A','company':company,'symbol':symbol,'company_mention':mention},executor=execute,decider=lambda *_:{'content':'没有可核验资料。'})
    async def emit(_):pass
    result=asyncio.run(run_direct_turn(question,context,emit=emit))
    assert not result['pending_request'] and result['previous_scope']['symbol']==expected

def test_ambiguous_official_issuer_does_not_guess_ticker(monkeypatch):
    from app.agent_workflow import entities
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'GOOG','title':'ALPHABET INC'},{'ticker':'GOOGL','title':'ALPHABET INC'}])
    assert entities.resolve_ticker('Alphabet') is None
