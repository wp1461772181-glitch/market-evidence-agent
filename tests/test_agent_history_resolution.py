import asyncio
from app.agent_workflow.direct import Turn
from app.agent_workflow.state import AgentRuntimeContext

CATALOGUE=(
    {'turn_id':'latest','answers_back':1,'opening':'市盈率是估值指标。','sources':[]},
    {'turn_id':'first','answers_back':2,'opening':'苹果营收为 100 美元 [1]。','sources':[{'ordinal':1,'available':True}]},
)
async def emit(_):pass

def make_turn(resolver):
    messages=[{'role':'user','content':'苹果营收？'},{'role':'assistant','content':CATALOGUE[1]['opening']},
              {'role':'user','content':'市盈率？'},{'role':'assistant','content':CATALOGUE[0]['opening']},
              {'role':'user','content':'引用 [1] 的原文？'}]
    context=AgentRuntimeContext('p','m','k',conversation_id='c',resolver=resolver,history_citation_catalogue=CATALOGUE,
        classifier=lambda _:{'category':'D'},decider=lambda *_:{'content':'请补充需要查看的回答。'})
    return Turn('引用 [1] 的原文？',context,emit,messages,{})

def test_model_selected_clarification_answer_is_rendered_from_the_directory():
    turn=make_turn(lambda _:{'relationship':'clarify','question':'引用 [1] 是哪次？',
                            'clarification_answers':[2],'history_citations':[]})
    result=asyncio.run(turn.run())
    assert result['status']=='waiting_for_input'
    assert result['pending_request']['choices'][0]['label']==CATALOGUE[1]['opening']
    assert len(result['pending_request']['choices'])>=2
    assert '[1]' not in result['pending_request']['question']
    assert turn.bound_history_citations==[]


def test_clarification_never_substitutes_a_different_answer_for_an_invalid_id():
    turn=make_turn(lambda _:{'relationship':'clarify','clarification_answers':[50]})
    asyncio.run(turn.reference())
    assert turn.pending is None
    assert turn.bound_history_citations==[]
    assert turn.audit['resolver_retries']==1
    assert 'clarification_answer_not_in_catalogue' in turn.audit['stages']['resolver']['validator']['rejected_reason']


def test_clarification_answer_offsets_are_not_accepted_as_source_bindings():
    turn=make_turn(lambda _:{'relationship':'clarify','clarification_answers':[2],
                            'history_citations':[{'answers_back':2,'ordinal':1}]})
    asyncio.run(turn.reference())
    assert turn.pending is None
    assert turn.bound_history_citations==[]
    assert 'clarification_cannot_bind_sources' in turn.audit['stages']['resolver']['validator']['rejected_reason']
