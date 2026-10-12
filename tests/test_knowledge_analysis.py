from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from app.knowledge.analysis_sources import load_analysis_document


class _Result:
    def __init__(self, value): self.value=value
    def mappings(self): return self
    def first(self): return self.value
    def scalar(self): return self.value


class _Session:
    def __init__(self,row,latest=None): self.row=row; self.latest=latest or row["id"]
    def execute(self, statement, params):
        if "ORDER BY created_at DESC" in str(statement): return _Result(self.latest)
        return _Result(self.row)


def _fixture():
    aid,eid,sid=uuid4(),uuid4(),uuid4(); now=datetime.now(UTC)-timedelta(minutes=1)
    body="Revenue grew 12% in FY2025."
    payload={"schema_version":"material-analysis-v1","prompt_version":"material-analysis-prompt-v1","summary":"Revenue improved.",
      "facts":[{"id":"f1","statement":"Revenue grew.","citations":[{"quote":"Revenue grew 12%","start_char":0,"end_char":16}]}],
      "supporting":[{"id":"s1","statement":"Trend is positive.","rationale":"Growth continues.","fact_ids":["f1"],"citations":[{"quote":"Revenue grew 12%","start_char":0,"end_char":16}]}],
      "counter":[{"id":"c1","statement":"Uncertainty remains.","rationale":"One period only.","fact_ids":["f1"],"citations":[{"quote":"in FY2025.","start_char":17,"end_char":27}]}],
      "key_numbers":[{"name":"Revenue growth","value_text":"12%","period":"FY2025","citations":[{"quote":"12% in FY2025","start_char":13,"end_char":26}]}],
      "uncertainties":[{"id":"u1","statement":"Future trend unknown.","reason":"No later period.","citations":[]}]
    }
    ah="Revenue grew 12% in FY2025."
    digest=sha256(body.encode()).hexdigest()
    manifest={"source_type":"official_filing","source_id":str(sid),"content_sha256":digest,"evidence_version_id":str(eid),"analysis_text_sha256":sha256(ah.encode()).hexdigest()}
    requested_model="model-x"; schema="material-analysis-v1"; prompt="material-analysis-prompt-v1"
    def canonical(value): return sha256(__import__('json').dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    model_fingerprint=canonical({"model":requested_model,"schema":schema,"prompt":prompt})
    input_fingerprint=canonical({"source_type":"official_filing","source_id":str(sid),"text_sha256":digest,"schema":schema,"prompt":prompt,"model_fingerprint":model_fingerprint})
    row={"id":aid,"source_type":"official_filing","source_id":sid,"version_no":2,"created_at":now,"actual_model":requested_model,"requested_model":requested_model,"schema_version":schema,"prompt_version":prompt,"payload":payload,"source_manifest":manifest,"input_fingerprint":input_fingerprint,"evidence_version_id":eid,"evidence_source_type":"official_filing","evidence_source_id":sid,"symbol":"AMD","published_at":now-timedelta(days=2),"observed_at":now-timedelta(days=1),"content_sha256":digest,"source_snapshot":{"analysis_text":ah,"source_id":str(sid),"content_sha256":digest,"source_type":"official_filing","symbol":"AMD"},"accession_number":"0000000000-25-000001","form":"10-Q","filed_at":now.date()-timedelta(days=1),"sec_url":"https://www.sec.gov/Archives/edgar/data/1/a.htm","content_source_url":None,"content_document_name":"AMD Quarterly Results","sec_text":body,"content_excerpt_sha256":digest,"content_observed_at":now-timedelta(hours=1),"sec_observed":now-timedelta(hours=1),"content_status":"fetched","content_truncated":False}
    return row


def test_analysis_adapter_preserves_all_groups_provenance_and_exact_quotes():
    row=_fixture(); doc=load_analysis_document(_Session(row),row["id"])
    assert doc is not None
    assert doc.source_type=="ai_analysis" and doc.source_table=="material_analysis_versions"
    assert doc.source_id==f"official_filing:{row['source_id']}" and doc.source_version==str(row["id"])
    assert "已保存 AI 分析" in doc.title and "AI 推论" in doc.text and "AI 提取" in doc.text
    assert "FY2025" in doc.text and "原文引文：Revenue grew 12%" in doc.text
    assert str(row["id"]) in doc.text and str(row["source_id"]) in doc.text and "https://www.sec.gov" in doc.text
    assert doc.available_at>=row["created_at"]


def test_analysis_adapter_rejects_invalid_quote_and_future_analysis():
    row=_fixture(); row["payload"]["facts"][0]["citations"][0]["quote"]="different"
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_analysis_adapter_rejects_tampered_manifest_frozen_snapshot_and_coordinates():
    row=_fixture();row["source_manifest"]["analysis_text_sha256"]="f"*64
    assert load_analysis_document(_Session(row),row["id"]) is None
    row=_fixture();row["source_snapshot"]["source_id"]=str(uuid4())
    assert load_analysis_document(_Session(row),row["id"]) is None
    row=_fixture();row["payload"]["facts"][0]["citations"][0]["end_char"]=15
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_analysis_adapter_recomputes_input_fingerprint():
    row=_fixture(); row["input_fingerprint"]="a"*64
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_analysis_adapter_rejects_future_source_content_observation():
    row=_fixture(); row["content_observed_at"]=datetime.now(UTC)+timedelta(days=1)
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_analysis_citation_detail_uses_document_provenance_not_missing_category_field():
    from types import SimpleNamespace
    from app.knowledge.api import _analysis_citation_metadata
    doc=SimpleNamespace(source_version=str(uuid4()),source_text="模型：model-x；分析版本：v2",source_id="official_filing:"+str(uuid4()),source_type="ai_analysis")
    assert _analysis_citation_metadata(doc)=={"derived":True,"analysis_version":doc.source_version,"version_no":2,"model":"model-x","classification":"saved_ai_analysis_not_official_source_text","original_source_type":"official_filing"}


def test_analysis_citation_exposes_original_quotes_separately_from_derived_excerpt():
    from types import SimpleNamespace
    from app.knowledge.api import _analysis_original_quotes
    doc=SimpleNamespace(source_text="Derived statement.\n> 原文引文：Revenue grew 8%.\nOther text\n> 原文引文：Net income was $2B.")
    assert _analysis_original_quotes(doc)==["Revenue grew 8%.","Net income was $2B."]


def test_analysis_source_url_rejects_credentials_and_invalid_ports_and_redacts_sensitive_query():
    from app.agent_tools import _safe_source_url
    assert _safe_source_url("https://example.test:invalid/report") is None
    assert _safe_source_url("https://example.test:0/report") is None
    assert _safe_source_url("https://example.test/report?token=private&view=full")=="https://example.test/report?view=full"
    row=_fixture(); row["created_at"]=datetime.now(UTC)+timedelta(days=1)
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_analysis_adapter_rejects_userinfo_before_shared_url_sanitizer():
    row=_fixture(); row["content_source_url"]="https://user:pass@example.test/report"
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_analysis_retrieval_uses_analysis_prefilter_and_emits_derived_citation(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import app.knowledge.retrieval as retrieval
    from app.knowledge.runtime import ResearchContext
    generation='g'*64; chunk_id='chunk-1'; doc=SimpleNamespace(id='doc-1',source_id='official_filing:source-1',source_version='analysis-version',source_table='material_analysis_versions',source_hash='s'*64,source_type='ai_analysis',symbol='AMD',scope='public',visibility='public',owner_hash=None,title='已保存 AI 分析 · AMD · Quarterly · v2',source_url='https://www.sec.gov/example',published_at=None,observed_at=datetime.now(UTC),content_observed_at=datetime.now(UTC),available_at=datetime.now(UTC),truncated=False)
    chunk=SimpleNamespace(id=chunk_id,answer='## 摘要（AI 分析）\nRevenue improved.\n> 原文引文：Revenue grew 12%',category='ai_analysis',generation=generation,content_hash='c'*64,section_path='AI 分析 / 摘要')
    seen={}
    class Runtime:
        threshold=0.0
        settings=SimpleNamespace(strategy='dense',generation=generation)
        async def encode(self,query): return [0.0]
        async def search(self,vector,filter_expr,limit): seen['filter']=filter_expr;return [{'id':chunk_id,'distance':1.0}]
    context=ResearchContext('a'*64,citations=[])
    monkeypatch.setattr(retrieval,'get_runtime',lambda:Runtime())
    monkeypatch.setattr(retrieval,'_eligible_ann_ids',lambda *args: seen.update({'eligibility_args':args}) or {chunk_id:(chunk,doc)})
    monkeypatch.setattr(retrieval,'current_research_context',lambda:context)
    result=asyncio.run(retrieval.retrieve_analysis('AMD','revenue growth'))
    assert result['ok'] and result['data'][0]['derived'] is True
    assert result['data'][0]['material_type']=='ai_analysis'
    assert 'source_type == "ai_analysis"' in seen['filter']
    assert seen['eligibility_args'][-1] is True
    assert context.citations[0]['source_type']=='ai_analysis' and context.citations[0]['source_url']==doc.source_url
    assert context.citations[0]['analysis_version']==doc.source_version and context.citations[0]['derived'] is True


def test_analysis_source_hash_fails_closed_when_current_analysis_is_invalid(monkeypatch):
    from types import SimpleNamespace
    import app.knowledge.retrieval as retrieval
    doc=SimpleNamespace(source_table='material_analysis_versions',source_version=str(uuid4()),source_id='official_filing:source',source_type='ai_analysis',symbol='AMD',scope='public',visibility='public',owner_hash=None,source_hash='a'*64)
    monkeypatch.setattr('app.knowledge.analysis_sources.load_analysis_document',lambda *args,**kwargs:None)
    assert retrieval._source_hash(object(),doc) is None


def test_old_analysis_version_cannot_be_replayed_and_unmapped_upload_stays_private():
    row=_fixture()
    assert load_analysis_document(_Session(row,latest=uuid4()),row["id"]) is None
    row=_fixture(); row["source_type"]="uploaded_media"; row["evidence_source_type"]="uploaded_media"
    assert load_analysis_document(_Session(row),row["id"]) is None


def test_source_association_tampering_fails_closed():
    row=_fixture(); row["evidence_source_id"]=uuid4()
    assert load_analysis_document(_Session(row),row["id"]) is None
