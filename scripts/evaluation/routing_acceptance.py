"""Run the 50 checked-in cases with real model decisions and synthetic external I/O.

Each phase runs against its pinned source tree in a fresh test-only PostgreSQL DB.
Only the forced controller/error probes are excluded from model accuracy/latency.
No production business operation or real web fetch is allowed.
"""
import argparse,asyncio,fcntl,json,os,statistics,time,uuid,hashlib,subprocess
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def database():
    from sqlalchemy import create_engine,text
    from sqlalchemy.engine import make_url
    url=make_url(os.getenv('TEST_DATABASE_URL','postgresql+psycopg://market_evidence:market_evidence_dev@localhost:55432/test_market_evidence'))
    if not url.database or not url.database.startswith('test_'):raise RuntimeError('test_database_required')
    name='test_routing_eval_'+uuid.uuid4().hex;admin=create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:conn.execute(text('CREATE DATABASE "'+name+'"'))
    os.environ['DATABASE_URL']=url.set(database=name).render_as_string(hide_password=False)
    os.environ['RAG_ENABLED']='false';os.environ['PERSONAL_CHAT_LOGIN_PATH']='/tmp/routing-eval-no-login-config.json'
    try:yield
    finally:
        from app.database import engine
        engine.dispose()
        with admin.connect() as conn:conn.execute(text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()

class EvaluationBudgetExceeded(BaseException):pass

def controlled_response(message):
    # A synthetic controller probe makes no provider request. Missing usage
    # would charge the fallback prompt estimate and test the wrong budget.
    return {'usage':{'total_tokens':0}},message

def charge(identity,amount,path,limit=.5):
    with path.open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX);handle.seek(0)
        value=json.loads(handle.read() or '{"prior_runs_upper_usd":0.0,"requests":{}}')
        value['requests'][identity]=amount
        if value['prior_runs_upper_usd']+sum(value['requests'].values())>limit:raise EvaluationBudgetExceeded('evaluation_budget_exhausted')
        handle.seek(0);handle.truncate();json.dump(value,handle);handle.flush()

async def evaluate(args):
    from dotenv import dotenv_values
    key_name=args.key_name or ('DEEPSEEK_API_KEY' if args.provider=='deepseek' else 'OPENROUTER_API_KEY')
    key=dotenv_values('/Users/wupeng/monash-ai/projects/market-evidence-agent/.env').get(key_name)
    if not key:raise RuntimeError('evaluation_key_unavailable')
    import app.main
    from app.database import Base,engine
    Base.metadata.create_all(engine)
    import app.agent_chat_identity as identity
    identity.owner_credential_matches=lambda c:c=='routing-eval-owner'
    import app.agent_workflow.graph as graph
    import app.agent_workflow.actions as actions
    actions.owner_credential_matches=identity.owner_credential_matches
    from app.agent_workflow.state import AgentRuntimeContext
    from app import agent_chat_provider as provider
    from routing_fixtures import Fixture
    provider.REASONING_EFFORT=args.effort
    if args.phase=='baseline' and args.provider=='deepseek':
        # Benchmark shim: identical native thinking settings in both source trees.
        original_options=provider.reasoning_options
        provider.reasoning_options=lambda name: {'thinking':{'type':'enabled'},'reasoning_effort':args.effort} if name=='deepseek' else original_options(name)
    if args.phase=='after':
        from app.agent_workflow import direct,consent,tools
        for module in (direct,consent,tools):module.owner_credential_matches=identity.owner_credential_matches
        # Defence in depth: never execute a production business adapter in evaluation.
        def no_business(operation,key,context):
            active.writes.append(operation);raise AssertionError('business_write_forbidden_in_evaluation')
        tools.execute_costly=no_business
    original_decision=provider.decision_request;original_stream=provider.stream_answer
    current=[];ledger=[];active=None
    budget=Path(args.ledger or '/tmp/market-routing-evidence-20261015/evaluation-'+args.provider+'-cost-ledger.json')
    reserve=.03 if args.provider=='deepseek' else .007
    def bill(identity,value):charge(identity,value,budget,args.budget_usd)
    def usage_cost(usage):
        if isinstance(usage.get('cost'),(int,float)):return float(usage['cost'])
        if args.provider=='deepseek':
            hit=usage.get('prompt_cache_hit_tokens',0);miss=usage.get('prompt_cache_miss_tokens',max(0,usage.get('prompt_tokens',64000)-hit))
            return (hit*.006+miss*.3+usage.get('completion_tokens',8192)*1.2)/1_000_000
        return usage.get('prompt_tokens',32000)*.00000002376+usage.get('completion_tokens',8192)*.00000072
    async def decision(*pos,**kwargs):
        selected=active.forced_decision(pos[4])
        if selected is not None:
            response,message=controlled_response(selected)
            active.native_tape.append({'controlled':True,'message':message,'usage':response['usage']});return response,message
        request_id=uuid.uuid4().hex;bill(request_id,reserve);current.append(1)
        response,message=await original_decision(*pos,**kwargs)
        stage_names={tool['function']['name'] for tool in pos[4]}
        if stage_names & {'resolve_reference','classify_request'}:active.model_stages.append({'schemas':sorted(stage_names),'content':message.get('content'),'calls':message.get('tool_calls')})
        usage=response.get('usage',{});upper=usage_cost(usage)
        active.native_tape.append({'controlled':False,'response_model':response.get('model'),'message':{k:v for k,v in message.items() if k in {'role','content','tool_calls'}},'usage':usage})
        bill(request_id,upper);ledger.append({'case':active.case['id'],'cost_upper_usd':upper,'usage':{k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens','cost'}}})
        return response,message
    async def stream(*pos,**kwargs):
        request_id=uuid.uuid4().hex;bill(request_id,reserve);current.append(1)
        arguments=list(pos)
        collector=arguments[4] if len(arguments)>4 else kwargs.get('reasoning_collector')
        if not isinstance(collector,dict):
            collector={}
            if len(arguments)>4:arguments[4]=collector
            else:kwargs['reasoning_collector']=collector
        async for part in original_stream(*arguments,**kwargs):yield part
        usage=collector.get('_usage',{});cost=usage.get('cost')
        upper=usage_cost(usage) if usage else reserve
        ledger.append({'case':active.case['id'],'cost_upper_usd':upper,'estimated_upper_bound':not isinstance(cost,(int,float)),'usage':{k:v for k,v in usage.items() if k in {'prompt_tokens','completion_tokens','total_tokens','cost'}}});bill(request_id,upper)
    provider.decision_request=decision;provider.stream_answer=stream
    if args.phase=='after':direct.decision_request=decision
    cases=[json.loads(line) for line in Path(args.cases).read_text().splitlines() if line.strip()]
    source_files=sorted(path for folder in ('app','frontend/src','scripts/evaluation') for path in Path(folder).rglob('*') if path.is_file() and path.suffix in {'.py','.ts','.tsx','.mjs','.jsonl'})
    manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    source_digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    evaluator_digest=hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('routing_fixtures.py').read_bytes()).hexdigest()
    revision=subprocess.run(['git','rev-parse','HEAD'],text=True,capture_output=True)
    base_sha=revision.stdout.strip() if revision.returncode==0 else '81f62be32fbcf9dbbd329e34673aed705567da4b'
    results=[]
    if args.ids:cases=[c for c in cases if c['id'] in args.ids.split(',')]
    cases=cases[:args.limit]
    for case in cases[:args.limit]:
        active=Fixture(case,args.phase);active.seed();before=len(current);started=time.monotonic()
        if args.phase=='baseline':
            import app.agent_tool_executor as executor
            executor.execute_tool=active.execute;graph.execute_tool=active.execute
            if hasattr(graph,'owner_credential_matches'):graph.owner_credential_matches=identity.owner_credential_matches
        context=AgentRuntimeContext(args.provider,args.model,key,owner_hash=active.owner,conversation_id=active.conversation,identity_credential=active.credential,executor=active.execute if args.phase=='after' else None)
        try:
            state=await asyncio.wait_for(graph.build_workflow().ainvoke(active.state(),{'configurable':{'thread_id':str(uuid.uuid4())}},context=context),135)
            if args.phase=='baseline':
                audit=state.setdefault('audit',{});raw=audit.get('intent')
                audit['legacy_intent']=raw
                audit['route']={'web_research':'A','saved_research':'A','compound_research':'D','general':'B','out_of_scope':'C','unsupported_action':'C','system_action':'D'}.get(raw,raw)
            result=active.grade(state)
        except EvaluationBudgetExceeded:
            print('Evaluation budget reached; remaining cases were not run.',flush=True);break
        except Exception as exc:
            code=getattr(exc,'detail',type(exc).__name__)
            if not isinstance(code,str) or len(code)>100:code=type(exc).__name__
            result={'passed':False,'errors':[code],'reason':code,'tools':active.calls}
        result.update(native_tape=active.native_tape,model_stages=active.model_stages,id=case['id'],controlled_probe=active.scenario in {'limit','no_progress','repair'},model_calls=len(current)-before,elapsed_ms=round((time.monotonic()-started)*1000))
        results.append(result)
        value={'evaluator_digest':evaluator_digest,'source_digest':source_digest,'source_manifest':manifest,'base_sha':base_sha,'case_digest':hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),'phase':args.phase,'provider':args.provider,'model':args.model,'reasoning_effort':args.effort,'budget_usd':args.budget_usd,'environment':'real provider; synthetic public evidence and disposable test DB; no production writes','results':results,'ledger':ledger,'cost_upper_usd':sum(c['cost_upper_usd'] for c in ledger)}
        Path(args.output).write_text(json.dumps(value,ensure_ascii=False,indent=2))
        print(case['id'],result['passed'],result.get('errors'),result['model_calls'],flush=True)
    if not results:raise RuntimeError('evaluation_not_run')
    quality=[r for r in results if not r['controlled_probe']]
    single=[r for r in quality if r['id'] in {'R01','R03','R04','R13','R15','R19','R20','R45','R46','R48','R49','R50'}]
    value['summary']={'cases':len(results),'passed':sum(r['passed'] for r in results),'model_quality_cases':len(quality),'model_quality_passed':sum(r['passed'] for r in quality),'p50_ms':statistics.median(r['elapsed_ms'] for r in quality),'single_source_median_model_calls':statistics.median(r['model_calls'] for r in single) if single else None,'successful_p50_ms':statistics.median(r['elapsed_ms'] for r in quality if r['passed']) if any(r['passed'] for r in quality) else None}
    Path(args.output).write_text(json.dumps(value,ensure_ascii=False,indent=2));print(value['summary'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--provider',choices=['deepseek','openrouter'],default='deepseek');parser.add_argument('--model',default='deepseek-flash');parser.add_argument('--key-name');parser.add_argument('--effort',choices=['low','high'],default='low');parser.add_argument('--ledger');parser.add_argument('--budget-usd',type=float,default=.5);parser.add_argument('--phase',choices=['after','baseline'],required=True);parser.add_argument('--cases',default=str(Path(__file__).with_name('cases')/'agent-routing-v3.jsonl'));parser.add_argument('--output',required=True);parser.add_argument('--limit',type=int,default=50);parser.add_argument('--ids');args=parser.parse_args()
    with database():asyncio.run(evaluate(args))
if __name__=='__main__':main()
