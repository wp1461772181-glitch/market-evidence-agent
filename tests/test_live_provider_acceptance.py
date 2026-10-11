import asyncio, json, os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.database import engine, SessionLocal
from app.main import app
from app.agent_chat_models import AgentMessage
from app.knowledge.models import LowConfidenceQuestion
from app.event_provider import configured_deepseek_model
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending
from app.knowledge.vector_store import MilvusIndex
from app.knowledge.runtime import KnowledgeRuntime
from app.knowledge.settings import KnowledgeSettings
import app.knowledge.runtime as runtime_module
import app.agent_chat_stream as chat
from tests.test_knowledge_evaluation_real import _prepare
OUT=Path('/tmp/market-rag-accept-20261011')
STRATEGY=KnowledgeSettings.from_env().strategy
REPORT_PATH=OUT/f'live-rag-acceptance-{STRATEGY}.json'
pytestmark=pytest.mark.skipif(os.getenv('LIVE_PROVIDER_ACCEPTANCE')!='1' and os.getenv('KNOWLEDGE_LIVE_PROVIDER_ACCEPTANCE')!='1',reason='explicit gate: this test may make external provider requests')
def frames(wire):
    result=[]
    for block in wire.split('\n\n'):
        event=None;data=None
        for line in block.splitlines():
            if line.startswith('event: '):event=line[7:]
            elif line.startswith('data: '):data=json.loads(line[6:])
        if event and data is not None:result.append((event,data))
    return result
def test_real_deepseek_rag_sql_sse(disposable_database,monkeypatch):
    configured_deepseek_model('deepseek-chat')
    key=os.environ.get('DEEPSEEK_API_KEY')
    if not key:pytest.skip('existing DeepSeek config is unavailable')
    generation=_prepare(disposable_database)
    settings=KnowledgeSettings.from_env()
    artifact=json.loads(settings.threshold_path.read_text())
    assert artifact['generation']==generation
    embedder=DenseEmbedder(settings.model_path,cpu_threads=4)
    index=MilvusIndex(settings.milvus_uri,generation);index.ensure_collection(generation)
    indexed=index_pending(SessionLocal,embedder,index,generation)
    assert indexed['failed']==0
    runtime=KnowledgeRuntime(settings,embedder,index,float(artifact['cosine_threshold']));runtime.warm()
    monkeypatch.setattr(runtime_module,'_runtime',runtime)
    actual_decide=chat.decision_request;actual_answer=chat.stream_answer;counts={'decision':0,'answer':0}
    async def observed_decide(*a,**kw):
        counts['decision']+=1
        return await actual_decide(*a,**kw)
    async def observed_answer(*a,**kw):
        counts['answer']+=1
        async for token in actual_answer(*a,**kw):yield token
    monkeypatch.setattr(chat,'decision_request',observed_decide);monkeypatch.setattr(chat,'stream_answer',observed_answer)
    credential='primary-live-acceptance-'+uuid4().hex
    cases=[
      {'name':'faq_paraphrase','query':'在这个系统里，可信度三星就是上涨概率60%吗？请查询系统的研究说明后回答。','tool':'query_research_faq'},
      {'name':'financial_fact','query':'请在已保存的研究材料中检索 DEMO 公司 FY2025 的营收数值和单位，注明报告期和原始来源。','tool':'query_materials'},
      {'name':'missing_symbol','query':'请在已保存的研究材料中检索 DEMB 公司 FY2025 的营收。若无材料明确说没有，不要猜。','tool':'query_materials'},
      {'name':'truncated_missing_fact','query':'请在已保存的研究材料中检索 DEMO 公司 FY2025 的 EBITDA 具体金额。若保存片段没有该金额，明确说明不足。','tool':'query_materials'},
    ]
    report={'provider':'deepseek','requested_model':'deepseek-chat','strategy':settings.strategy,'acceptance_subset':os.getenv('LIVE_GENERATION_SUBSET')=='1','real_embedding':True,'real_milvus':True,'real_postgresql':True,'mocked_components':[], 'cases':[]}
    if os.getenv('LIVE_GENERATION_SUBSET')=='1':cases=cases[:2]
    try:
      with TestClient(app) as client:
        for item in cases:
          counts.update(decision=0,answer=0)
          response=client.post('/agent-chat/stream',headers={'X-Chat-Session':credential},json={'provider':'deepseek','model':'deepseek-chat','api_key':key,'message':item['query']})
          events=frames(response.text)
          errors=[data for event,data in events if event=='error']
          tools=[data for event,data in events if event=='tool_result']
          answer=''.join(data['text'] for event,data in events if event=='delta')
          conv=next((data for event,data in events if event=='conversation'),{})
          cid=conv.get('conversation_id');turn_id=conv.get('turn_id')
          saved=client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':credential}) if cid else None
          history=saved.json().get('messages',[]) if saved is not None and saved.status_code==200 else []
          citation_frames=[data.get('items',[]) for event,data in events if event=='citations']
          citations=citation_frames[-1] if citation_frames else []
          result={'name':item['name'],'http_status':response.status_code,'errors':errors,'tools':tools,'answer':answer,'request_counts':dict(counts),'history_roles':[m['role'] for m in history],'citations':citations}
          report['cases'].append(result)
          REPORT_PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2))
          assert response.status_code==200 and not errors,result
          assert any(event=='done' and data['status']=='complete' for event,data in events),result
          assert len(tools)==1 and tools[0]['name']==item['tool'],result
          has_evidence=bool(tools and tools[0]['result'].get('ok') and tools[0]['result'].get('data'))
          assert counts['decision']==(2 if has_evidence else 1) and counts['answer'] in (0,1),result
          assert history and history[-1]['content']==answer,result
          assert any(m.get('tool_trace') for m in history if m['role']=='assistant'),result
          assert client.get(f'/agent-chat/conversations/{cid}/messages',headers={'X-Chat-Session':'different-owner-'+'x'*40}).status_code==404
          with Session(engine) as session:
            rows=session.query(AgentMessage).filter_by(conversation_id=cid).all()
            assert len(rows)==4
            tool_rows=[row for row in rows if row.role=='tool']
            requests=[row for row in rows if row.role=='assistant' and isinstance(row.tool_calls,dict) and row.tool_calls.get('calls')]
            assert len(tool_rows)==len(requests)==1
            assert tool_rows[0].tool_call_id==requests[0].tool_calls['calls'][0]['id']
            low_rows=session.query(LowConfidenceQuestion).filter_by(turn_id=turn_id).count()
          if item['name']=='faq_paraphrase':assert '60%' not in answer or any(x in answer for x in ('不是','不等于','不能','不代表')),result
          if item['name']=='financial_fact':assert all(x in answer for x in ('123','2025')) and any(x in answer for x in ('百万','million')) and 'https://example.test/synthetic-demo-fy2025' in answer,result
          if item['name']=='missing_symbol':assert tools[0]['result']['data']==[] and any(x in answer for x in ('没有','未','不足','无法')),result
          if item['name']=='truncated_missing_fact':assert any(x in answer for x in ('没有','未','不足','不包含','无法')),result
          if item['name'] in {'missing_symbol','truncated_missing_fact'}:assert low_rows==1,result
          saved_assistant=next((message for message in reversed(history) if message['role']=='assistant'),None)
          stored_citations=(saved_assistant or {}).get('citations',[])
          assert stored_citations==citations,result
          if tools[0]['result'].get('data'):
            tool_evidence=json.dumps(tools[0]['result']['data'],ensure_ascii=False)
            assert citations and all(f"[{citation.get('ordinal')}]" in tool_evidence for citation in citations),result
          if item['name']=='faq_paraphrase':checks={'rating_not_upside_probability':any(x in answer for x in ('不是','不等于','不能','不代表'))}
          elif item['name']=='financial_fact':checks={'revenue':all(x in answer for x in ('123','2025')),'unit':any(x in answer for x in ('百万','million')),'source_url':'https://example.test/synthetic-demo-fy2025' in answer,'citation_association':bool(citations)}
          else:checks={'missing_fact_refused':any(x in answer for x in ('没有','未','不足','不包含','无法')),'low_confidence_persisted':low_rows==1 if item['name'] in {'missing_symbol','truncated_missing_fact'} else True}
          result['low_confidence_rows']=low_rows;result['claim_checks']=checks
          report.setdefault('generation_faithfulness',{'method':'live generated answers checked against declared synthetic ground truth and stored citations; no LLM judge','claims':[]})
          report['generation_faithfulness']['claims'].append({'case':item['name'],'checks':checks})
          OUT.joinpath('live-rag-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
      runtime.close();runtime_module._runtime=None
      report['completed_cases']=len(report['cases'])
      REPORT_PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2))
