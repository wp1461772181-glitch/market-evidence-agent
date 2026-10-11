"""Explicit real PostgreSQL, BGE-M3, Milvus and cross-encoder integration gate."""
import gc,hashlib,json,os,asyncio,time,statistics
from datetime import UTC,datetime
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import Base,engine
from app.agent_chat_models import AgentFAQ
from app.models import UploadedEvidence
from app.knowledge.embedding import DenseEmbedder
from app.knowledge.indexing import index_pending
from app.knowledge.models import KnowledgeChunk,KnowledgeDocument,KnowledgeUploadOwner
from app.knowledge.query_understanding import retrieval_query
from app.knowledge.reranking import rerank
from app.knowledge.repository import embedding_generation,ingest_documents
from app.knowledge.runtime import KnowledgeRuntime,research_context
from app.knowledge.settings import KnowledgeSettings,MODEL_ID,MODEL_REVISION,RERANKER_ID,RERANKER_REVISION
from app.knowledge.sources import adapt_faq,adapt_uploaded
from app.knowledge.vector_store import MilvusIndex
from scripts.migrate_knowledge import migrate

ROOT=Path(__file__).parent/'fixtures'/'knowledge_rag_ch04'
pytestmark=pytest.mark.skipif(os.getenv('KNOWLEDGE_CH04_REAL')!='1',reason='explicit four-strategy real integration gate')
OWNER={name:hashlib.sha256(name.encode()).hexdigest() for name in ('owner-a','owner-b')}

def test_four_real_strategies_and_write_report(disposable_database,monkeypatch):
    corpus=json.loads((ROOT/'corpus.json').read_text());queries=json.loads((ROOT/'queries.json').read_text())
    assert corpus['disclaimer'].startswith('All organizations')
    Base.metadata.create_all(bind=engine,tables=[AgentFAQ.__table__,UploadedEvidence.__table__]);migrate(engine)
    now=datetime.now(UTC);docs=[];logical_targets={}
    with Session(engine) as session,session.begin():
        for faq in corpus['faqs']:
            session.add(AgentFAQ(id=faq['id'],question=faq['question'],answer=faq['answer'],category=faq['category']))
    with Session(engine) as session,session.begin():
      for row in corpus['documents']:
        owner=OWNER[row['owner']] if row['owner'] else None
        from uuid import uuid5,NAMESPACE_URL
        upload_id=uuid5(NAMESPACE_URL,'ch04-real-eval:'+row['id']);logical_targets[row['id']]=str(upload_id);content_hash=hashlib.sha256(row['text'].encode()).hexdigest()
        upload=UploadedEvidence(id=upload_id,symbol=row['symbol'],title=row['title'],source_url='https://example.invalid/'+row['id'],published_at=now,observed_at=now,credibility_stars=3,credibility_reason='Declared synthetic evaluation fixture',impact_severity='low',filename=row['id']+'.txt',content_sha256=content_hash,raw_content=row['text'].encode(),content_text=row['text'],status='unconfirmed')
        session.add(upload);session.flush()
        if owner:session.add(KnowledgeUploadOwner(upload_id=upload_id,owner_hash=owner))
        docs.append(adapt_uploaded({'id':upload.id,'symbol':upload.symbol,'title':upload.title,'source_url':upload.source_url,'published_at':upload.published_at,'observed_at':upload.observed_at,'content_sha256':upload.content_sha256,'content_text':upload.content_text},owner_hash=owner,allowlist={str(upload_id)} if owner is None else set()))
    for faq in corpus['faqs']:
        docs.append(adapt_faq(faq))
        logical_targets[str(faq['id'])]=str(adapt_faq(faq).source_id)
    # Labels deliberately remain human-readable in the fixture. Resolve every
    # expected and forbidden label to the actual source ID before scoring.
    for query in queries['calibration']+queries['heldout']:
        for label in (query.get('expected_source'),query.get('forbidden_source')):
            if label is not None:
                assert label in logical_targets, f"unmapped fixture source label: {label} ({query['id']})"
    gen=embedding_generation(MODEL_ID,MODEL_REVISION,'ch04-eval-format-v1','ch04-eval-chunker-v1')
    embedder=DenseEmbedder('data/knowledge/models/bge-m3',cpu_threads=2)
    with Session(engine) as session,session.begin():ingest_documents(session,docs,embedder.tokenizer,gen)
    index=MilvusIndex('http://127.0.0.1:19530',gen);index.ensure_collection()
    indexed=index_pending(lambda:Session(engine),embedder,index,gen,max_tasks=100,worker_id='ch04-real-eval')
    cutoff=datetime.now(UTC)
    records={}
    with Session(engine) as session:
        for chunk,doc in session.execute(select(KnowledgeChunk,KnowledgeDocument).join(KnowledgeDocument).where(KnowledgeChunk.generation==gen,KnowledgeChunk.status=='ready')).all():
            records[str(chunk.id)]={'source_id':doc.source_id,'answer':chunk.answer,'scope':chunk.scope,'owner':chunk.owner_hash,'symbol':chunk.symbol,'source_type':chunk.source_type,'available_at':chunk.available_at}
    all_queries=queries['calibration']+queries['heldout'];vectors=embedder.encode([retrieval_query(q['query']) for q in all_queries])
    raw={}
    for q,vector in zip(all_queries,vectors):
        private=OWNER.get(q.get('owner'));access=['(scope == "public" and visibility == "public")']
        if private:access.append(f'(scope == "private" and visibility == "private" and owner_hash == "{private}")')
        source_filter='source_type == "research_faq"' if q['kind']=='faq' else 'symbol == "'+q['symbol']+'" and source_type == "uploaded_media"'
        elig=[key for key,val in records.items() if (val['scope']=='public' or private and val['owner']==private) and (q['kind']=='faq' and val['source_type']=='research_faq' or q['kind']=='material' and val['symbol']==q['symbol'] and val['source_type']=='uploaded_media') and val['available_at'] and val['available_at']<=cutoff]
        if not elig:
            if q.get('expected_source') is not None: print('expected_source_missing_from_pg_eligibility',q['id'])
            raw[q['id']]={'dense':[],'bm25':[],'hybrid':[],'latency_ms':{'dense':0.0,'bm25':0.0,'hybrid':0.0}};continue
        expr='('+ ' or '.join(access)+') and '+source_filter+' and available_at <= '+str(int(cutoff.timestamp()*1000))+' and id in ['+','.join('"'+x+'"' for x in elig)+']'
        rq=retrieval_query(q['query'])
        raw[q['id']]={'latency_ms':{}}
        for strategy,call in [('dense',lambda:index.search_dense(vector,expr,50)),('bm25',lambda:index.search_bm25(rq,expr,50)),('hybrid',lambda:index.search_hybrid(vector,rq,expr,50))]:
            started=time.perf_counter();raw[q['id']][strategy]=call();raw[q['id']]['latency_ms'][strategy]=(time.perf_counter()-started)*1000
    # Calibrate the dense cosine cutoff solely from this generation's fixed
    # calibration queries. Heldout labels/scores are not read by this selector.
    calibration_labels=[]
    for q in queries['calibration']:
        expected=logical_targets.get(q.get('expected_source'))
        for hit in raw[q['id']]['dense']:
            source=records.get(str(hit['id']),{}).get('source_id')
            score=float(hit.get('distance',-1.0))
            calibration_labels.append((score,bool(expected and source==expected)))
    candidates=sorted({score for score,_ in calibration_labels})
    if not candidates or not any(label for _,label in calibration_labels):
        raise AssertionError('calibration split did not produce dense relevance scores for this generation')
    def f1_at(threshold):
        tp=sum(score>=threshold and label for score,label in calibration_labels)
        fp=sum(score>=threshold and not label for score,label in calibration_labels)
        fn=sum(score<threshold and label for score,label in calibration_labels)
        return (2*tp/(2*tp+fp+fn)) if 2*tp+fp+fn else 0.0
    calibration_threshold=max(candidates,key=lambda threshold:(f1_at(threshold),threshold))
    calibration_meta={'split':'calibration','generation':gen,'cosine_threshold':calibration_threshold,'candidate_pairs':len(calibration_labels),'selection_f1':f1_at(calibration_threshold),'heldout_used':False}
    del vectors,embedder;gc.collect()
    try:
        import torch;torch.set_num_threads(2)
    except ImportError:pass
    rows=[];all_splits={**{q['id']:('calibration',q) for q in queries['calibration']},**{q['id']:('heldout',q) for q in queries['heldout']}}
    for qid,(split,q) in all_splits.items():
        base=raw[qid];reranked=[];rerank_ms=0.0
        if base['hybrid']:
            passages=[records.get(str(h['id']),{}).get('answer','') for h in base['hybrid']]
            started=time.perf_counter();ranked=rerank(q['query'],passages,'data/knowledge/models/bge-reranker-v2-m3',RERANKER_REVISION);rerank_ms=(time.perf_counter()-started)*1000
            reranked=[base['hybrid'][i] for i,_ in ranked[:10]]
        for strategy,hits in [('dense',base['dense']),('bm25',base['bm25']),('hybrid',base['hybrid']),('hybrid_rerank',reranked)]:
            raw_sources=[records[str(h['id'])]['source_id'] for h in hits if str(h['id']) in records]
            filtered=[h for h in hits if str(h['id']) in records and (records[str(h['id'])]['scope']=='public' or q.get('owner') and records[str(h['id'])]['owner']==OWNER[q['owner']]) and (q['kind']=='faq' and records[str(h['id'])]['source_type']=='research_faq' or q['kind']=='material' and records[str(h['id'])]['symbol']==q.get('symbol'))]
            sources=[]
            for h in filtered:
                sid=records[str(h['id'])]['source_id']
                if sid not in sources:sources.append(sid)
            expected=logical_targets.get(q.get('expected_source'));rank=(sources.index(expected)+1) if expected in sources else None
            forbidden=logical_targets.get(q.get('forbidden_source'))
            rows.append({'split':split,'query_id':qid,'query_type':q.get('query_type','baseline'),'strategy':strategy,'latency_ms':(base['latency_ms']['hybrid']+rerank_ms) if strategy=='hybrid_rerank' else base['latency_ms'][strategy],'expected_source':expected,'expected_none':bool(q.get('expected_none')),'rank':rank,'recall_at_5':bool(rank and rank<=5),'mrr':1/rank if rank else 0,'retrieval_marker_diagnostic':bool(q.get('marker') and any(q.get('marker') in records[str(h['id'])]['answer'] for h in filtered[:5])),'negative_false_positive':bool(q.get('expected_none') and sources),'forbidden_leak':bool(forbidden and forbidden in raw_sources[:5]),'returned_sources':sources[:10],'raw_ann_sources':raw_sources[:10]})
    from app.knowledge.reranking import unload as unload_reranker
    from app.knowledge import runtime as runtime_module
    from app.knowledge.retrieval import _retrieve
    from types import SimpleNamespace
    unload_reranker()
    production_embedder=DenseEmbedder('data/knowledge/models/bge-m3',cpu_threads=2)
    production_rows=[]
    for strategy in ('dense','bm25','hybrid','hybrid_rerank'):
        production_index=MilvusIndex('http://127.0.0.1:19530',gen)
        strategy_settings=SimpleNamespace(generation=gen,strategy=strategy,max_concurrency=1,queue_capacity=1,reranker_path='data/knowledge/models/bge-reranker-v2-m3',reranker_revision=RERANKER_REVISION)
        runtime=KnowledgeRuntime(strategy_settings,production_embedder,production_index,calibration_threshold if strategy=='dense' else 0.0);runtime.warm();monkeypatch.setattr(runtime_module,'_runtime',runtime)
        async def run_production_queries():
            output=[]
            for query in all_queries:
                owner=OWNER.get(query.get('owner'));started=time.perf_counter()
                with research_context(owner or '0'*64) as research:
                    result=await _retrieve(query['query'],faq=query['kind']=='faq',symbol=query.get('symbol'),limit=5)
                latency=(time.perf_counter()-started)*1000
                if query['kind']=='faq':
                    source_ids=[]
                    for citation in research.citations or []:
                        meta=records.get(citation['chunk_id'])
                        if meta and meta['source_id'] not in source_ids:source_ids.append(meta['source_id'])
                else:source_ids=list(dict.fromkeys(str(row.get('id')) for row in result.get('data') or [] if row.get('id')))
                allowed=[];leaks=[]
                for source_id in source_ids:
                    meta=next((value for value in records.values() if value['source_id']==source_id),None)
                    if meta and (meta['scope']=='public' or owner and meta['owner']==owner) and (query['kind']=='faq' or meta['symbol']==query.get('symbol')):allowed.append(source_id)
                    else:leaks.append(source_id)
                expected=logical_targets.get(query.get('expected_source'));rank=allowed.index(expected)+1 if expected in allowed else None
                rendered=json.dumps(result.get('data'),ensure_ascii=False)
                output.append({'split':'calibration' if query in queries['calibration'] else 'heldout','query_id':query['id'],'query_type':query.get('query_type','baseline'),'strategy':strategy,'latency_ms':latency,'expected_source':expected,'expected_none':bool(query.get('expected_none')),'rank':rank,'recall_at_5':bool(rank and rank<=5),'mrr':1/rank if rank else 0,'negative_false_positive':bool(query.get('expected_none') and allowed),'owner_or_stock_leaks':leaks,'retrieved_sources':allowed,'citation_count':len(research.citations or []),'retrieval_marker_diagnostic':bool(query.get('marker') and query['marker'] in rendered)})
            return output
        production_rows.extend(asyncio.run(run_production_queries()))
        runtime.close();monkeypatch.setattr(runtime_module,'_runtime',None)
    production_metrics={}
    for split in ('calibration','heldout'):
        production_metrics[split]={}
        for strategy in ('dense','bm25','hybrid','hybrid_rerank'):
            selected=[row for row in production_rows if row['split']==split and row['strategy']==strategy];positive=[row for row in selected if row['expected_source']];latencies=[row['latency_ms'] for row in selected]
            production_metrics[split][strategy]={'queries':len(selected),'positive_queries':len(positive),'negative_queries':sum(row['expected_none'] for row in selected),'negative_false_positives':sum(row['negative_false_positive'] for row in selected),'latency_mean_ms':statistics.mean(latencies) if latencies else 0,'latency_p50_ms':statistics.median(latencies) if latencies else 0,'recall_at_5':sum(row['recall_at_5'] for row in positive)/max(1,len(positive)),'mrr':sum(row['mrr'] for row in positive)/max(1,len(positive)),'owner_or_stock_leaks':sum(len(row['owner_or_stock_leaks']) for row in selected),'retrieval_marker_diagnostic':sum(row['retrieval_marker_diagnostic'] for row in positive)/max(1,len(positive))}
            production_metrics[split][strategy]['by_query_type']={kind:{'queries':sum(row['query_type']==kind for row in selected),'positives':sum(row['query_type']==kind and bool(row['expected_source']) for row in selected),'negatives':sum(row['query_type']==kind and row['expected_none'] for row in selected),'recall_at_5':sum(row['query_type']==kind and row['recall_at_5'] for row in selected if row['expected_source'])/max(1,sum(row['query_type']==kind and bool(row['expected_source']) for row in selected)),'negative_false_positives':sum(row['query_type']==kind and row['negative_false_positive'] for row in selected)} for kind in sorted({row['query_type'] for row in selected})}
    report={'fixture':corpus['version'],'synthetic_data_only':True,'logical_source_mapping':logical_targets,'source_mapping_validated':True,'dense_threshold_calibration':calibration_meta,'evaluation_kind':'component_benchmark_plus_production_retrieval_path','production_retrieval_path_tested':True,'versions':{'milvus':index.health()['server_version'],'pymilvus':'2.6.13','bge_m3_revision':MODEL_REVISION,'reranker':RERANKER_ID,'reranker_revision':RERANKER_REVISION},'indexing':indexed,'metrics':{},'rows':rows,'faithfulness':{'status':'not evaluated','method':'no generated answers or claim-level judge; retrieval_marker_diagnostic is a retrieval-only measure, not answer hallucination or faithfulness'},'production_path':{'metrics':production_metrics,'rows':production_rows,'integrity_scope_time_budget_checks':'executed by app.knowledge.retrieval._retrieve and PostgreSQL source adapters','generation':'not called here; see bounded live provider strategy acceptance'},'live_provider':'not called by this retrieval eval'}
    for split in ('calibration','heldout'):
        report['metrics'][split]={}
        for strategy in ('dense','bm25','hybrid','hybrid_rerank'):
            selected=[r for r in rows if r['split']==split and r['strategy']==strategy];relevant=[r for r in selected if r['expected_source']]
            latencies=[r['latency_ms'] for r in selected]
            report['metrics'][split][strategy]={'queries':len(selected),'positive_queries':len(relevant),'negative_queries':sum(r['expected_none'] for r in selected),'negative_false_positives':sum(r['negative_false_positive'] for r in selected),'latency_mean_ms':statistics.mean(latencies) if latencies else 0,'latency_p50_ms':statistics.median(latencies) if latencies else 0,'recall_at_5':sum(r['recall_at_5'] for r in relevant)/max(1,len(relevant)),'mrr':sum(r['mrr'] for r in relevant)/max(1,len(relevant)),'retrieval_marker_diagnostic':sum(r['retrieval_marker_diagnostic'] for r in relevant)/max(1,len(relevant)),'owner_leaks':sum(r['forbidden_leak'] for r in selected),'by_query_type':{kind:{'queries':sum(r['query_type']==kind for r in selected),'positives':sum(r['query_type']==kind and bool(r['expected_source']) for r in selected),'negatives':sum(r['query_type']==kind and r['expected_none'] for r in selected),'recall_at_5':sum(r['query_type']==kind and r['recall_at_5'] for r in selected if r['expected_source'])/max(1,sum(r['query_type']==kind and bool(r['expected_source']) for r in selected)),'negative_false_positives':sum(r['query_type']==kind and r['negative_false_positive'] for r in selected)} for kind in sorted({r['query_type'] for r in selected})}}
    out=Path('data/knowledge/ch04/real-four-strategy-report.json');out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    md=['# Chapter 4 retrieval evaluation','',f"Fixture: `{corpus['version']}` — synthetic only. Real PostgreSQL, Milvus, BGE-M3 and cross-encoder. Production section calls `app.knowledge.retrieval._retrieve`; component section calls index APIs directly.",'',f"Milvus {report['versions']['milvus']}; reranker revision `{RERANKER_REVISION}`.",f"Dense cosine threshold calibrated on calibration split only: `{calibration_threshold:.6f}` (generation `{gen}`, selection F1 {f1_at(calibration_threshold):.3f}); heldout excluded.",'','## Production retrieval path','','| Split | Strategy | queries (+/-) | Mean ms | P50 ms | Recall@5 positive | MRR positive | Negative FP | Scope leaks |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for split,strategies in production_metrics.items():
        for name,m in strategies.items():md.append(f"| {split} | {name} | {m['queries']} ({m['positive_queries']}/{m['negative_queries']}) | {m['latency_mean_ms']:.1f} | {m['latency_p50_ms']:.1f} | {m['recall_at_5']:.3f} | {m['mrr']:.3f} | {m['negative_false_positives']} | {m['owner_or_stock_leaks']} |")
    md+=['','## Direct-index component benchmark','','| Split | Strategy | queries (+/-) | Mean ms | P50 ms | Recall@5 positive | MRR positive | Negative FP | Owner leaks |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for split,strategies in report['metrics'].items():
        for name,m in strategies.items():md.append(f"| {split} | {name} | {m['queries']} ({m['positive_queries']}/{m['negative_queries']}) | {m['latency_mean_ms']:.1f} | {m['latency_p50_ms']:.1f} | {m['recall_at_5']:.3f} | {m['mrr']:.3f} | {m['negative_false_positives']} | {m['owner_leaks']} |")
    md+=['','Faithfulness was not evaluated. `retrieval_marker_diagnostic` is not a generated-answer or claim-level measure. Heldout labels were fixed in the fixture before this run; no labels or thresholds are tuned from heldout results.','', 'Per-query-type positive/negative counts and failed cases are in the JSON report.']
    Path('docs/knowledge-rag-ch34-reports/four-strategy-evaluation.md').write_text('\n'.join(md)+'\n')
    assert indexed['failed']==0
    assert all(r['forbidden_leak']==0 for r in rows)
    index.close()
