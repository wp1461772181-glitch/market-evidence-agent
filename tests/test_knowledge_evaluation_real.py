"""Real PostgreSQL + local BGE-M3 + Milvus acceptance; enable explicitly with KNOWLEDGE_REAL_INTEGRATION=1."""
import asyncio,hashlib,json,os,math
from datetime import UTC,datetime,date,timedelta
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from sqlalchemy.orm import Session
from app.database import Base,engine,SessionLocal
from app.agent_chat_models import AgentFAQ
from app.models import UploadedEvidence,SecFilingInventory
from app.knowledge.sources import adapt_faq,adapt_uploaded,adapt_sec
from app.knowledge.chunking import CHUNKER_VERSION,FORMAT_VERSION
from app.knowledge.repository import embedding_generation,ingest_documents,reconcile
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.vector_store import MilvusIndex
from app.knowledge.indexing import index_pending,InjectedIndexCrash
from app.knowledge.models import KnowledgeDocument,KnowledgeChunk,KnowledgeIndexTask,KnowledgeUploadOwner
from app.knowledge.runtime import KnowledgeRuntime,research_context
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.retrieval import retrieve_faq,retrieve_materials
from scripts.migrate_knowledge import migrate

pytestmark=pytest.mark.skipif(os.getenv('KNOWLEDGE_REAL_INTEGRATION')!='1',reason='explicit real Milvus/BGE integration gate')
ROOT=Path(__file__).parent/'fixtures'/'knowledge_rag'
OWNER_A=hashlib.sha256(b'fixture-owner-A').hexdigest()
OWNER_B=hashlib.sha256(b'fixture-owner-B').hexdigest()
FIXTURE_TIME=datetime(2026,4,1,tzinfo=UTC)

def _load():
    return json.loads((ROOT/'corpus.json').read_text()),json.loads((ROOT/'queries.json').read_text())

def _prepare(disposable_database):
    corpus,_=_load()
    Base.metadata.create_all(bind=engine,tables=[AgentFAQ.__table__,UploadedEvidence.__table__,SecFilingInventory.__table__])
    migrate(engine)
    with engine.begin() as connection:
        names=set(__import__('sqlalchemy').inspect(connection).get_table_names())
        tables=[name for name in ('knowledge_index_tasks','knowledge_chunks','knowledge_documents','knowledge_candidates','knowledge_extraction_runs','knowledge_upload_owners','agent_faq','uploaded_evidence','sec_filing_inventory') if name in names]
        if tables:connection.exec_driver_sql('TRUNCATE TABLE '+', '.join('"'+name+'"' for name in tables)+' CASCADE')
    docs=[]
    with Session(engine) as s,s.begin():
        for row in corpus['faq']:
            faq=AgentFAQ(id=row['id'],question=row['question'],answer=row['answer'],category=row['category']);s.add(faq);s.flush()
            docs.append(adapt_faq({'id':faq.id,'question':faq.question,'answer':faq.answer,'category':faq.category}))
        for row in corpus['uploads']:
            raw=row['text'].encode(); published=datetime.fromisoformat(row['published_at'].replace('Z','+00:00'))
            upload=UploadedEvidence(id=UUID(row['id']),symbol=row['symbol'],title=row['title'],source_url=row['source_url'],
              published_at=published,observed_at=FIXTURE_TIME,credibility_stars=3,credibility_reason='synthetic fixture',
              impact_severity='low',filename='fixture.md',content_sha256=hashlib.sha256(raw).hexdigest(),raw_content=raw,
              content_text=row['text'],status='unconfirmed')
            s.add(upload);s.flush()
            owner=OWNER_A if row.get('owner')=='A' else OWNER_B if row.get('owner')=='B' else None
            if owner:s.add(KnowledgeUploadOwner(upload_id=upload.id,owner_hash=owner))
            doc=adapt_uploaded({'id':upload.id,'symbol':upload.symbol,'title':upload.title,'source_url':upload.source_url,
              'published_at':upload.published_at,'observed_at':upload.observed_at,'content_sha256':upload.content_sha256,'content_text':upload.content_text},
              owner_hash=owner,allowlist={str(upload.id)} if owner is None else set())
            docs.append(doc)
        row=corpus['sec'][0]; excerpt=row['text']; sec=SecFilingInventory(id=UUID(row['id']),symbol=row['symbol'],cik='0000000001',
          accession_number='0000000001-26-000001',form=row['form'],filed_at=date.fromisoformat(row['filed_at']),
          accepted_at=None,primary_document=row['primary_document'],source_url=row['source_url'],source='synthetic',
          review_status='accepted',reviewed_at=FIXTURE_TIME,observed_at=FIXTURE_TIME,content_status='fetched',
          content_observed_at=FIXTURE_TIME,content_excerpt=excerpt,content_excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
          content_truncated=True,content_source_url=row['content_source_url'],content_document_name=row['content_document_name'],
          content_kind=row['content_kind'])
        s.add(sec);s.flush();docs.append(adapt_sec({c.key:getattr(sec,c.key) for c in sec.__table__.columns}))
        generation=embedding_generation('BAAI/bge-m3','5617a9f61b028005a4858fdac845db406aefb181',FORMAT_VERSION,CHUNKER_VERSION)
        counts=ingest_documents(s,docs,DenseEmbedder('data/knowledge/models/bge-m3').tokenizer,generation)
        assert counts['documents']==7 and counts['chunks']>=10
        chunks=s.query(KnowledgeChunk).filter_by(generation=generation).all()
        assert all(len(DenseEmbedder('data/knowledge/models/bge-m3').tokenizer.encode(chunk.embedding_text,add_special_tokens=True))<=768 for chunk in chunks if not chunk.needs_review)
    return generation

def test_real_rag_recovery_permissions_and_heldout(disposable_database,tmp_path):
    async def run():
        corpus,query_sets=_load(); generation=_prepare(disposable_database)
        embedder=DenseEmbedder('data/knowledge/models/bge-m3',cpu_threads=4)
        index=MilvusIndex('http://127.0.0.1:19530',generation);index.ensure_collection(generation)
        with pytest.raises(InjectedIndexCrash):
            index_pending(SessionLocal,embedder,index,generation,crash_at='after_pending')
        with SessionLocal.begin() as s:
            task=s.query(KnowledgeIndexTask).filter_by(status='leased').one();task.lease_until=datetime.now(UTC)-timedelta(seconds=1)
        complete=index_pending(SessionLocal,embedder,index,generation)
        assert complete['failed']==0
        with SessionLocal.begin() as s:
            chunk=s.query(KnowledgeChunk).filter_by(status='ready').order_by(KnowledgeChunk.id).first()
            chunk.status='pending';s.query(KnowledgeDocument).filter_by(id=chunk.document_id).update({'status':'pending'})
            task=s.query(KnowledgeIndexTask).filter_by(chunk_id=chunk.id).one();task.status='pending';task.lease_epoch=0
        with pytest.raises(InjectedIndexCrash):
            index_pending(SessionLocal,embedder,index,generation,crash_at='after_upsert')
        with SessionLocal.begin() as s:
            task=s.query(KnowledgeIndexTask).filter_by(status='leased').one();task.lease_until=datetime.now(UTC)-timedelta(seconds=1)
        resumed=index_pending(SessionLocal,embedder,index,generation)
        with SessionLocal() as s:
            assert resumed['failed']==0
            assert s.query(KnowledgeIndexTask).filter(KnowledgeIndexTask.status!='complete').count()==0
            assert s.query(KnowledgeChunk).filter(KnowledgeChunk.status=='ready').count()>=10
            report=reconcile(s,index,generation)
            assert report['ok'],report
            chunk_ids=[str(x) for x, in s.query(KnowledgeChunk.id).filter_by(generation=generation).all() if s.get(KnowledgeChunk,x).status=='ready']
        assert len(index.get(chunk_ids))==len(chunk_ids)
        # An upsert retry of the same primary key leaves exactly one row in Milvus.
        assert len(index.get([str(chunk.id)]))==1

        calibration=query_sets['calibration']; heldout=query_sets['heldout']
        supplement=json.loads((ROOT/'queries-supplement.json').read_text())
        supplement_errata=json.loads((ROOT/'queries-supplement-adjudications.json').read_text())
        supplement_adjudicated={row['id']:row for row in supplement_errata['adjudications']}
        from app.knowledge.retrieval import _current_rows
        from app.knowledge.runtime import ResearchContext
        adjudication=json.loads((ROOT/'adjudications.json').read_text())
        adjudicated={row['id']:row['adjudicated_label'] for row in adjudication['adjudications']}
        def filter_for(item):
            timestamp=int(datetime.now(UTC).timestamp()*1000)
            if item['kind']=='faq':return f'source_type == "research_faq" and scope == "public" and visibility == "public" and available_at <= {timestamp}'
            owner=OWNER_A if item.get('owner')=='A' else OWNER_B if item.get('owner')=='B' else None
            access='(scope == "public" and visibility == "public")'+(f' or (scope == "private" and visibility == "private" and owner_hash == "{owner}")' if owner else '')
            return f'source_type in ["uploaded_media", "official_filing"] and symbol == "{item["symbol"]}" and ({access}) and available_at <= {timestamp}'
        def authorized_hits(item,hits):
            owner=OWNER_A if item.get('owner')=='A' else OWNER_B if item.get('owner')=='B' else None
            context=ResearchContext(owner,datetime.now(UTC)) if owner else None
            rows=_current_rows([str(hit['id']) for hit in hits],generation,context,item.get('symbol'),item['kind']=='faq')
            allowed={str(chunk.id) for chunk,_doc in rows}
            return [hit for hit in hits if str(hit['id']) in allowed]
        with SessionLocal() as s:
            source_chunks={}
            for chunk,doc in s.query(KnowledgeChunk,KnowledgeDocument).join(KnowledgeDocument).filter(KnowledgeChunk.status=='ready').all():
                source_chunks.setdefault(doc.source_id,[]).append(str(chunk.id))
        scores=[]
        for item in calibration+supplement['calibration']:
            vector=embedder.encode([item['query']])[0]
            hits=authorized_hits(item,index.search(vector,filter_for(item),20))
            label_info=adjudicated.get(item['id'],{})
            label_info=supplement_adjudicated.get(item['id'],label_info)
            if label_info.get('forbidden_source'):continue
            expected_source=label_info.get('expected_source',item.get('expected_source'))
            if item['kind']=='faq' and not expected_source and not item.get('expected_none',False):
                expected_source=str(next(row['id'] for row in corpus['faq'] if row['question']==item.get('expected_question')))
            expected_none=item.get('expected_none',False) and not expected_source and not label_info.get('forbidden_source')
            if expected_none:
                score=max((float(h['distance']) for h in hits),default=-1.0); label=False
            else:
                ids=set(source_chunks.get(expected_source,[]))
                score=max((float(h['distance']) for h in hits if str(h['id']) in ids),default=-1.0);label=True
            scores.append((score,label))
        candidates=sorted({-1.0,1.0,*[s for s,_ in scores],*[math.nextafter(s,math.inf) for s,_ in scores if s<1.0]})
        def f1(threshold):
            tp=sum(label and score>=threshold for score,label in scores)
            fp=sum(not label and score>=threshold for score,label in scores)
            fn=sum(label and score<threshold for score,label in scores)
            return 2*tp/max(1,2*tp+fp+fn)
        def recall_precision(threshold):
            tp=sum(label and score>=threshold for score,label in scores)
            fp=sum(not label and score>=threshold for score,label in scores)
            fn=sum(label and score<threshold for score,label in scores)
            return tp/max(1,tp+fn),tp/max(1,tp+fp)
        # Select on calibration F1 with a minimum precision guard; maximize
        # threshold on exact ties so a broad near-minus-one cutoff is not
        # preferred when it adds false positives without improving F1.
        eligible=[t for t in candidates if recall_precision(t)[1]>=0.85]
        threshold=max(eligible,key=lambda t:(f1(t),recall_precision(t)[1],t))
        calibration_hash=hashlib.sha256(json.dumps({'queries':calibration,'adjudications':adjudication,'supplement_calibration':supplement['calibration'],'supplement_adjudications':supplement_errata},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        calibration_path=Path('data/knowledge/calibration/threshold.json');calibration_path.parent.mkdir(parents=True,exist_ok=True)
        calibration_path.write_text(json.dumps({'generation':generation,'cosine_threshold':threshold,'calibration_sha256':calibration_hash,'queries':len(calibration)+len(supplement['calibration']),'selection_f1':f1(threshold),'source_fingerprint':'original+supplement+errata'},indent=2)+'\n')
        settings=KnowledgeSettings.from_env()
        settings=KnowledgeSettings(True,settings.model_path,settings.revision,generation,settings.milvus_uri,4,1,1,calibration_path)
        runtime=KnowledgeRuntime(settings,embedder,index,threshold);runtime.warm()
        import app.knowledge.runtime as runtime_module
        runtime_module._runtime=runtime
        async def retrieve(item):
            if item['kind']=='faq':return await retrieve_faq(item['query'])
            owner=OWNER_A if item.get('owner')=='A' else OWNER_B if item.get('owner')=='B' else None
            if owner:
                with research_context(owner):return await retrieve_materials(item['symbol'],item['query'],5)
            return await retrieve_materials(item['symbol'],item['query'],5)
        outcomes=[]
        try:
            for split,items in [('calibration',calibration),('heldout',heldout)]:
                for item in items:
                    result=await retrieve(item)
                    raw_query_vector=embedder.encode([item['query']])[0]
                    raw_hits=authorized_hits(item,index.search(raw_query_vector,filter_for(item),20))
                    hit_sources=[{'id':str(hit['id']),'source_id':next((source for source,chunk_ids in source_chunks.items() if str(hit['id']) in chunk_ids),None),'score':float(hit['distance'])} for hit in raw_hits[:5]]
                    if item.get('forbidden_source'):ok=not any(row['id']==item['forbidden_source'] for row in result['data'])
                    elif item.get('expected_none'):ok=result['ok'] and result['data']==[]
                    elif item['kind']=='faq':ok=any(row['question']==item['expected_question'] for row in result['data'])
                    else:ok=any(row['id']==item['expected_source'] and item.get('expected_text','') in row['excerpt'] for row in result['data'])
                    outcomes.append({'split':split,'id':item['id'],'ok':ok,'result_count':len(result['data'] or []),'bytes':len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()),'results':result.get('data'),'top_hits':hit_sources})
            supplement_outcomes=[]
            for split,items in [('calibration',supplement['calibration']),('heldout',supplement['heldout'])]:
                for item in items:
                    result=await retrieve(item)
                    correction=supplement_adjudicated.get(item['id'],{})
                    if correction.get('forbidden_source'):ok=not any(row.get('id')==correction['forbidden_source'] for row in (result.get('data') or []))
                    elif correction.get('expected_source'):
                        ok=any(row.get('id')==correction['expected_source'] for row in (result.get('data') or []))
                    elif item.get('expected_none'):ok=result['ok'] and result['data']==[]
                    elif item['kind']=='faq':ok=any(row['question']==item['expected_question'] for row in result['data'])
                    else:ok=any(row['id']==item['expected_source'] and item['expected_text'] in row['excerpt'] for row in result['data'])
                    supplement_outcomes.append({'split':split,'id':item['id'],'ok':ok,'answerability':'missing_fact_refusal_required' if correction.get('corrected_label')=='relevant_source_missing_fact' else None,'result_count':len(result['data'] or []),
                      'bytes':len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()),'results':result.get('data')})
            sse_demo=None
            if os.getenv('KNOWLEDGE_DEMO_SSE')=='1':
                import app.agent_chat_stream as chat
                from fastapi.testclient import TestClient
                from app.main import app
                mp=pytest.MonkeyPatch(); provider_calls={'decision':0,'answer':0}
                async def decide(*_):
                    provider_calls['decision']+=1
                    call_id=f"rag-demo-call-{provider_calls['decision']}"
                    return ({},{'tool_calls':[{'id':call_id,'type':'function','function':{'name':'query_research_faq','arguments':json.dumps({'keyword':'研究材料可信度星级是什么？'})}}]})
                async def answer(*args):
                    provider_calls['answer']+=1
                    messages=args[3]
                    tool_message=next(message for message in messages if message.get('role')=='tool')
                    parsed=json.loads(tool_message['content'])
                    assert parsed['ok'] and parsed['data']
                    yield 'Synthetic demonstration answer based on the retrieved reviewed FAQ.'
                mp.setattr(chat,'decision_request',decide);mp.setattr(chat,'stream_answer',answer)
                session_id='demo-chat-session-'+uuid4().hex
                with TestClient(app) as client:
                    payload={'provider':'deepseek','model':'mock-demo','api_key':'mock-key-12345678','message':'What does the evidence rating mean?'}
                    first=client.post('/agent-chat/stream',headers={'X-Chat-Session':session_id},json=payload)
                    wire=first.text
                    first_events=[json.loads(line[6:]) for line in wire.splitlines() if line.startswith('data: ')]
                    conversation=next(x['conversation_id'] for x in first_events if 'conversation_id' in x)
                    tool_event=next(x for x in first_events if 'result' in x and x.get('call_id','').startswith('rag-demo-call'))
                    assert first.status_code==200 and 'event: tool_result' in wire and 'event: done' in wire
                    assert tool_event['result']['ok'] and tool_event['result']['data']
                    payload['conversation_id']=conversation
                    second=client.post('/agent-chat/stream',headers={'X-Chat-Session':session_id},json=payload)
                    assert second.status_code==200 and 'event: done' in second.text
                    history_response=client.get(f'/agent-chat/conversations/{conversation}/messages',headers={'X-Chat-Session':session_id})
                    denied=client.get(f'/agent-chat/conversations/{conversation}/messages',headers={'X-Chat-Session':'different-session-'+uuid4().hex})
                    assert history_response.status_code==200 and denied.status_code==404
                    messages=history_response.json()['messages']
                    assert len([m for m in messages if m['role']=='user'])==2
                    assert len([m for m in messages if m['role']=='assistant'])==2
                    assert all(m.get('tool_trace') and m['tool_trace'][0]['status']=='complete' for m in messages if m['role']=='assistant')
                # TestClient shutdown closes application-owned resources; restore
                # the explicit integration runtime for the remaining private FAQ checks.
                runtime=KnowledgeRuntime(settings,embedder,MilvusIndex('http://127.0.0.1:19530',generation),threshold)
                runtime.warm();runtime_module._runtime=runtime;index=runtime.index
                assert provider_calls=={'decision':2,'answer':2}
                sse_demo={'provider_decision_and_answer_mocked':'yes','chat_api_session_messages_and_history':'real',
                  'embedding_index_retrieval_and_tools':'real','conversation_id':conversation,
                  'source_ids':[row.get('question') for row in tool_event['result']['data']],
                  'events':[name for name in ('conversation','tool_start','tool_result','delta','done') if f'event: {name}' in wire],
                  'followup_turns':2,'tool_calls':2,'provider_request_counts':provider_calls,'owner_mismatch_status':denied.status_code,
                  'history_message_count':len(messages),'history_tool_traces':True}
                Path('data/knowledge/logs/knowledge-chat-sse-demo.json').write_text(json.dumps(sse_demo,indent=2)+'\n')
                mp.undo()
            held=[x for x in outcomes if x['split']=='heldout']
            held_by_id={x['id']:x for x in held}
            with SessionLocal() as owner_session:
                owners={str(doc.source_id):doc.owner_hash for doc in owner_session.query(KnowledgeDocument).all()}
            queries_by_id={q['id']:q for q in calibration+heldout}
            leaked=sum(1 for x in outcomes for row in (x.get('results') or []) if owners.get(str(row.get('id'))) and owners[str(row['id'])] != (OWNER_A if queries_by_id[x['id']].get('owner')=='A' else OWNER_B if queries_by_id[x['id']].get('owner')=='B' else None))
            original_positive=[q for q in heldout if not (q.get('expected_none') or q.get('forbidden_source'))]
            original_found=sum(bool(held_by_id[q['id']]['ok']) for q in original_positive)
            adjudicated_positive=[q for q in heldout if (adjudicated.get(q['id'],{}).get('expected_source') or
              (not q.get('expected_none') and not q.get('forbidden_source')))]
            adjudicated_found=0; raw_ann_found=0
            for q in adjudicated_positive:
                rule=adjudicated.get(q['id'],{})
                source=rule.get('expected_source',q.get('expected_source'))
                text=rule.get('expected_text',q.get('expected_text',''))
                delivered=held_by_id[q['id']]
                if any(row.get('id')==source and text in row.get('excerpt','') or q['kind']=='faq' and row.get('question')==q.get('expected_question') for row in (delivered.get('results') or [])):
                    adjudicated_found+=1
                if any(hit.get('source_id')==source for hit in delivered.get('top_hits',[])):
                    raw_ann_found+=1
            adjudicated_recall=adjudicated_found/max(1,len(adjudicated_positive))
            raw_ann_recall=raw_ann_found/max(1,len(adjudicated_positive))
            strict_negative_failures=[q['id'] for q in heldout if q.get('expected_none') and not adjudicated.get(q['id']) and held_by_id[q['id']]['result_count']>0]
            privacy_queries=[q for q in heldout if adjudicated.get(q['id'],{}).get('privacy_only')]
            privacy_failures=[q['id'] for q in privacy_queries if any(owners.get(str(row.get('id')))==OWNER_B for row in (held_by_id[q['id']].get('results') or []))]
            excluded=[q['id'] for q in heldout if adjudicated.get(q['id'],{}).get('exclude_from_accuracy')]
            result={'generation':generation,'calibration_threshold':threshold,'calibration_f1':f1(threshold),'calibration_recall_precision':recall_precision(threshold),
              'original_labels':{'positive_total':len(original_positive),'positive_found':original_found,'recall_at_5':original_found/max(1,len(original_positive)),'strict_failures':[x['id'] for x in held if not x['ok']]},
              'adjudicated_labels':{'eligible_positive_ids':[q['id'] for q in adjudicated_positive],'positive_total':len(adjudicated_positive),'delivered_relevant_found':adjudicated_found,'delivered_recall_at_5':adjudicated_recall,'raw_ann_relevant_found':raw_ann_found,'raw_ann_recall_at_5':raw_ann_recall,'strict_negative_failures':strict_negative_failures,'privacy_failures':privacy_failures,'excluded_ids':excluded},
              'heldout_total':len(heldout),'cross_owner_leakage':leaked,'max_result_json_bytes':max(x['bytes'] for x in outcomes+supplement_outcomes),
              'quality_targets':{'heldout_recall_target':0.85,'heldout_recall_met':adjudicated_recall>=0.85,
                'cross_owner_leakage_target':0,'strict_negative_failures':len(strict_negative_failures)},
              'calibration_scores':[{'score':score,'positive':label} for score,label in scores],'queries':outcomes,'adjudication_manifest':adjudication,
              'supplement':{'manifest':supplement,'queries':supplement_outcomes,
                'calibration_positive_total':sum(not q.get('expected_none',False) for q in supplement['calibration']),
                'calibration_negative_total':sum(bool(q.get('expected_none')) for q in supplement['calibration']),
                'heldout_positive_total':sum(not q.get('expected_none',False) for q in supplement['heldout']),
                'heldout_positive_recall_at_5':sum(x['ok'] for x in supplement_outcomes if x['split']=='heldout' and not next(q for q in supplement['heldout'] if q['id']==x['id']).get('expected_none'))/max(1,sum(not q.get('expected_none',False) for q in supplement['heldout'])),
                'heldout_unrelated_rejections':sum(x['ok'] for x in supplement_outcomes if x['split']=='heldout' and next(q for q in supplement['heldout'] if q['id']==x['id']).get('expected_none')),
                'heldout_unrelated_rejection_failures':sum(not x['ok'] for x in supplement_outcomes if x['split']=='heldout' and next(q for q in supplement['heldout'] if q['id']==x['id']).get('expected_none'))}}
            Path('data/knowledge/logs').mkdir(parents=True,exist_ok=True)
            Path('data/knowledge/logs/rag-evaluation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
            assert max(x['bytes'] for x in outcomes+supplement_outcomes)<=8000
            assert leaked==0
            assert not privacy_failures
            # Private reviewed product FAQs are visible to their owner only.
            from app.knowledge.models import KnowledgeCandidate
            from app.knowledge.extraction import review_candidate
            private_question='How does owner A archive a research note?'
            private_answer='Owner A can archive a note from its saved material record.'
            candidate=KnowledgeCandidate(dedupe_key=hashlib.sha256(b'private-faq-demo').hexdigest(),scope='private',owner_hash=OWNER_A,
              conversation_id='synthetic-conversation',turn_id='synthetic-turn',evidence_ref='synthetic-conversation:synthetic-turn',
              evidence_hash=hashlib.sha256(b'evidence').hexdigest(),question=private_question,answer=private_answer,
              category='private usage FAQ',candidate_kind='product_faq',evidence_quote=private_answer,status='pending_review')
            with SessionLocal.begin() as session:
                session.add(candidate);session.flush();candidate_id=candidate.id
                review_candidate(session,candidate_id,'approve','synthetic-reviewer','private',embedder.tokenizer,generation)
            index_pending(SessionLocal,embedder,index,generation)
            with research_context(OWNER_A):owner_faq=await retrieve_faq('How can I archive my saved note?')
            with research_context(OWNER_B):other_faq=await retrieve_faq('How can I archive my saved note?')
            assert any(row['question']==private_question for row in owner_faq['data'])
            assert not any(row['question']==private_question for row in other_faq['data'])
        finally:
            runtime_module._runtime=None
            runtime.close()

    asyncio.run(run())
