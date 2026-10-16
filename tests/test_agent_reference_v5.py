import asyncio
import json
import pytest
from app.agent_workflow import references, entities
from app.agent_workflow.direct import run_direct_turn
from app.agent_workflow.state import AgentRuntimeContext

HISTORY=[{'role':'user','content':'列几个公司'}, {'role':'assistant','content':'1. 苹果（Apple，AAPL）\n2. 微软（Microsoft，MSFT）'}]

def test_server_item_identity_restores_original_label_and_rejects_foreign_source():
    catalogue=references.assistant_item_catalogue(HISTORY)
    source=catalogue[0]['source'];item=catalogue[0]['items'][1]
    ref=references.Referent(mention='第二个',source=source,item_id=item['item_id'])
    verified=references.validate_referents([ref],'查第二个',HISTORY,[])
    assert verified[0]['text']=='微软（Microsoft，MSFT）'
    with pytest.raises(ValueError):
        references.validate_referents([references.Referent(mention='第二个',source='assistant_message:'+'a'*20,item_id=item['item_id'])],'查第二个',HISTORY,[])

def test_reference_item_does_not_trust_retyped_model_text():
    catalogue=references.assistant_item_catalogue(HISTORY);item=catalogue[0]['items'][1]
    ref=references.Referent(mention='第二个',text='Apple',source=catalogue[0]['source'],item_id=item['item_id'])
    assert references.validate_referents([ref],'第二个',HISTORY,[])[0]['text']=='微软（Microsoft，MSFT）'

def test_valid_item_id_cannot_select_the_wrong_requested_position():
    catalogue=references.assistant_item_catalogue(HISTORY);item=catalogue[0]['items'][0]
    ref=references.Referent(mention='第二个',source=catalogue[0]['source'],item_id=item['item_id'])
    with pytest.raises(ValueError,match='position'):
        references.validate_referents([ref],'解释第二个',HISTORY,[])

def test_verified_bilingual_reference_can_switch_company(monkeypatch):
    monkeypatch.setattr(entities,'ticker_records',lambda:[{'ticker':'AAPL','title':'Apple Inc'},{'ticker':'MSFT','title':'Microsoft Corp'}])
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'followup','canonical_question':'查微软（Microsoft，MSFT）的新闻','referents':[{'mention':'第二个','text':'微软（Microsoft，MSFT）','source':'assistant_previous'}]},
        classifier=lambda _:{'category':'A','symbol':'MSFT','company':'Microsoft Corporation','company_mention':'微软（Microsoft，MSFT）'},
        decider=lambda *_:{'content':'没有可核验资料。'},executor=lambda *_:{'ok':True,'data':[]})
    result=asyncio.run(run_direct_turn('查第二个的新闻',ctx,emit=emit,messages=HISTORY,state={'previous_scope':{'symbol':'AAPL','company':'Apple'}}))
    assert not result['pending_request'] and result['previous_scope']['symbol']=='MSFT'

def test_relative_date_year_is_grounded_in_server_time():
    from datetime import datetime, UTC
    year=datetime.now(UTC).year
    async def emit(_):pass
    ctx=AgentRuntimeContext('custom','test','dummy',resolver=lambda _:{'relationship':'followup','canonical_question':f'查询{year}年最近半个月的新闻'},
        classifier=lambda _:{'category':'A'},executor=lambda *_:{'ok':True,'data':[]},decider=lambda *_:{'content':'没有可核验资料。'})
    result=asyncio.run(run_direct_turn('最近半个月呢',ctx,emit=emit,messages=[{'role':'user','content':'查新闻'}]))
    assert not result['pending_request']
