"""One typed, permission-bearing catalogue shared by A and D."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Literal,Annotated
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .agent_tools import SymbolArgs

class Args(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Empty(Args):pass
class CalculationInput(Args):
    name:str=Field(pattern=r'^[a-z]$')
    value:str=Field(pattern=r'^-?\d{1,24}(?:\.\d{1,12})?$')
    source_ordinal:int=Field(ge=1,le=1000)
class CalculateArgs(Args):
    expression:str=Field(min_length=1,max_length=200)
    inputs:list[CalculationInput]=Field(min_length=1,max_length=8)
    decimals:int=Field(default=2,ge=0,le=8)
class KnowledgeArgs(Args):
    query:str=Field(min_length=1,max_length=400)
    symbol:str|None=Field(default=None,pattern=r'^[A-Za-z]{1,5}(?:[.-][A-Za-z]{1,2})?$')
    company:str|None=Field(default=None,max_length=160)
    financial_only:bool=False
    latest:bool=False
    form:Literal['10-K','10-Q','8-K']|None=None
    source_id:str|None=Field(default=None,pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    limit:int=Field(default=5,ge=1,le=10)
class QueueArgs(SymbolArgs):pass
class StatusArgs(Args):
    kind:Literal['jobs','monitor','evaluations','replays','knowledge']
    symbol:str|None=Field(default=None,pattern=r'^[A-Za-z]{1,5}(?:[.-][A-Za-z]{1,2})?$')
    job_id:str|None=Field(default=None,pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    limit:int=Field(default=5,ge=1,le=20)
class FilingWriteArgs(SymbolArgs):
    accession_number:str|None=Field(default=None,pattern=r'^\d{10}-\d{2}-\d{6}$')
    form:Literal['10-K','10-Q','8-K']|None=None
class SourceWriteArgs(SymbolArgs):
    source_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    source_type:Literal['official_filing','uploaded_media','ai_analysis']='official_filing'
class IndexArgs(Args):
    source_id:str|None=Field(default=None,pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    max_tasks:int=Field(default=20,ge=1,le=200)
class SourceRef(Args):
    source_type:Literal['official_filing','uploaded_media']
    source_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
class ForecastArgs(SymbolArgs):
    source_refs:list[SourceRef]=Field(default_factory=list,max_length=10)
class WebIngestArgs(Args):
    url:str=Field(min_length=8,max_length=2048)
    symbol:str|None=Field(default=None,pattern=r'^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$')
    company:str|None=Field(default=None,max_length=160)
    # Evidence ordinals, never free-form model dates. The server looks up the dates.
    newer_than:int|None=Field(default=None,ge=1,le=50)
class InitializeArgs(SymbolArgs):
    days:int=Field(default=366,ge=1,le=3660)
class ScanArgs(SymbolArgs):pass
class PageArgs(Args):
    page:Literal['overview','predictions','materials','sec','revisions','evaluation','agent','knowledge']
class StockArgs(SymbolArgs):pass
class CardArgs(Args):
    question:str=Field(min_length=1,max_length=400)
    options:list[str]=Field(default_factory=list,max_length=8)
class ExistingArgs(Args):
    result_set_id:str=Field(min_length=1,max_length=64)
    positions:list[int]=Field(min_length=1,max_length=8)
    action:Literal['summarize','import','analyze','index']='summarize'
class ReviewArgs(Args):
    source_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    decision:Literal['accepted','rejected']
    note:str=Field(default='',max_length=500)

class WorkspaceArgs(Args):
    kind:Literal['forecast','timeline','roots','learning','analysis_history','original','knowledge_chunks','knowledge_candidates','importable']
    symbol:str|None=None
    record_id:str|None=Field(default=None,pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    source_type:Literal['official_filing','uploaded_media','faq','upload','sec','analysis']='official_filing'
class ReplayArgs(SymbolArgs):
    decision_date:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
class RevisionArgs(Args):
    version_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    source_refs:list[SourceRef]=Field(min_length=1,max_length=10)
class DocumentArgs(Args):
    document_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
class TranslateArgs(Args):
    kind:Literal['analysis','forecast']
    record_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
class CandidateArgs(Args):
    candidate_id:str=Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
    approved:bool
    reviewer:str=Field(default='owner via agent',min_length=1,max_length=120)
class ImportArgs(Args):
    source_type:Literal['faq','upload','sec','analysis']
    source_ids:list[Annotated[str,Field(pattern=r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')]]=Field(min_length=1,max_length=8)

@dataclass(frozen=True)
class ToolPolicy:
    name:str
    description:str
    args_schema:type[BaseModel]
    level:Literal['read','reversible','costly','card','client']
    owner:bool=True
    guest:bool=True
    confirmation:bool=False
    errors:tuple[str,...]=('invalid_arguments','scope_not_grounded','tool_unavailable')
    actionable_error:str='Correct the typed arguments, choose a returned source, or explain the missing evidence.'

EXTRA=(
 ('calculate',CalculateArgs,'read','Calculate derived figures from cited source values. Only a..z, + - * / parentheses and constants 0,1,100. Inputs must occur in the chosen evidence. Formula and parents are recorded; derived results are not official originals.'),
 ('read_workspace',WorkspaceArgs,'read','Read a saved forecast/version timeline, analysis history/original, learning status, knowledge chunks/candidates or importable catalogue. Private records remain owner-scoped.'),
 ('start_historical_replay',ReplayArgs,'costly','Propose a retrospective replay for a specific past decision date; this is not a forward prediction. Confirmation required.'),
 ('revise_forecast',RevisionArgs,'costly','Propose a manual revision of an exact saved forecast version with exact saved source references. Confirmation required.'),
 ('retry_document_index',DocumentArgs,'costly','Propose retrying embeddings for an exact failed knowledge document. Confirmation required.'),
 ('translate_content',TranslateArgs,'costly','Propose paid English translation of one saved analysis or forecast brief. Confirmation required.'),
 ('review_knowledge_candidate',CandidateArgs,'costly','Propose approving or rejecting one saved knowledge candidate with the named reviewer. Confirmation required.'),
 ('import_sources',ImportArgs,'costly','Propose importing exact FAQ/upload/SEC/analysis IDs from a returned catalogue. Confirmation required.'),
 ('search_knowledge',KnowledgeArgs,'read','Search saved knowledge and filings before public research. Empty results do not stop web research.'),
 ('query_system_status',StatusArgs,'read','Read existing jobs, monitor, evaluation, replay or indexing status. Never starts work.'),
 ('query_research_queue',Empty,'read','Read the owner queue, or ask the guest browser to display its local queue.'),
 ('add_to_research_queue',QueueArgs,'reversible','Add a requested symbol: owner database requires server confirmation; guest browser-local queue needs no server write.'),
 ('remove_from_research_queue',QueueArgs,'reversible','Remove a requested symbol, keeping materials and history. Owner database requires server confirmation; guest browser-local only.'),
 ('select_stock',StockArgs,'client','Select the current stock in the browser without fetching new data.'),
 ('open_page',PageArgs,'client','Open a named existing workspace page without changing data.'),
 ('ask_user',CardArgs,'card','Ask for a missing entity or let the user choose between options. Use when the reference is ambiguous.'),
 ('use_result_set',ExistingArgs,'card','Select exact one-based positions from a returned result set; summarize or propose a confirmed import/analyze/index.'),
 ('fetch_sec',FilingWriteArgs,'costly','Propose fetching a saved SEC filing original. Server confirmation required; optional accession or latest form.'),
 ('refresh_sec',FilingWriteArgs,'costly','Propose refreshing the original SEC document. Confirmation required.'),
 ('analyze',SourceWriteArgs,'costly','Propose paid AI analysis of an exact saved source. Confirmation required.'),
 ('import',SourceWriteArgs,'costly','Propose importing one exact saved source into the knowledge library. Confirmation required.'),
 ('index',IndexArgs,'costly','Propose building pending knowledge embeddings for an exact source or a bounded batch. Confirmation required.'),
 ('forecast',ForecastArgs,'costly','Propose a new research forecast, using only user-requested saved source IDs. Confirmation required.'),
 ('ingest_web_source',WebIngestArgs,'costly','Propose saving a returned public URL as web_news. Only if the USER requested saving, including conditional saving. newer_than refers to the saved-evidence ordinal; server compares publication dates. Confirmation required.'),
 ('initialize_prices',InitializeArgs,'costly','Propose fetching historical daily prices for a symbol and explicit days. Confirmation required.'),
 ('scan_official',ScanArgs,'costly','Propose scanning official SEC filings for one symbol; may enqueue eligible existing automatic research. Confirmation required.'),
 ('review_filing',ReviewArgs,'reversible','Record the owner requested accepted/rejected relevance review for a saved SEC filing. Never delete data.'),
)

# Every base registry entry is read-only; write names come from catalogue levels.
WriteTool=Literal[tuple(name for name,_,level,_ in EXTRA if level in {'reversible','costly'})]
class RequestActionArgs(Args):
    tool:WriteTool
    arguments:dict=Field(default_factory=dict)
    reason:str=Field(min_length=1,max_length=400)

def write_tool_names():return {n for n,p in definitions().items() if p.level in {'reversible','costly'}}

def definitions():
    from .agent_tools import TOOL_REGISTRY
    result={name:ToolPolicy(name,tool.description,tool.args_schema,'read') for name,tool in TOOL_REGISTRY.items()}
    for name,schema,level,description in EXTRA:
        result[name]=ToolPolicy(name,description,schema,level,True,level!='costly' and name!='review_filing',level in {'reversible','costly'})
    result['request_action']=ToolPolicy('request_action','Request owner approval for an agent-suggested operation that is NOT a user request. Arguments must match the target tool schema. No fetching or business writes before approval; at most one suggestion per turn.',RequestActionArgs,'card',True,False)
    return result

def schemas(*,owner:bool,granted=()):
    return [{'type':'function','function':{'name':p.name,'description':p.description,'parameters':p.args_schema.model_json_schema()}}
        for p in definitions().values() if p.owner and(owner or p.guest) and (p.level not in {'reversible','costly'} or p.name in granted)]

def validate(name,args):
    policy=definitions().get(name)
    if policy is None:raise ValueError('unknown_tool')
    return policy.args_schema.model_validate(args).model_dump(mode='json')

# Lazy consumers use definitions(); avoid duplicating a mutable registry at import time.
def assert_catalog_complete(registry):
    declared=definitions()
    if not set(registry)<=set(declared):raise RuntimeError('tool_catalog_mismatch')

def registered_tools(*,owner,registry):
    return [t for n,t in registry.items() if n in definitions() and (owner or definitions()[n].guest)]

POLICIES=definitions()
