import importlib.util
import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("pymilvus") is None, reason="optional dependency: install requirements-rag.lock")

from unittest.mock import MagicMock, patch


def test_hybrid_index_uses_versioned_schema_and_both_prefilters():
    client = MagicMock()
    client.has_collection.return_value = False
    schema = MagicMock()
    client.create_schema.return_value = schema
    indexes = MagicMock()
    client.prepare_index_params.return_value = indexes
    with patch('pymilvus.MilvusClient', return_value=client):
        from app.knowledge.vector_store import MilvusIndex
        index = MilvusIndex('http://127.0.0.1:19530', 'a' * 64)
        index.ensure_collection()
        name = client.create_collection.call_args.args[0]
        assert name == 'knowledge_hybrid_v1_' + 'a' * 64
        fields = [call.args[0] for call in schema.add_field.call_args_list]
        assert 'text' in fields and 'sparse' in fields and 'embedding' in fields
        client.upsert.reset_mock()
        index.upsert([{'generation':'a'*64,'text':'revenue','embedding':[0.0]*1024}])
        assert client.upsert.call_args.args[0] == name
        filt = 'scope == "public" and symbol == "AAPL"'
        index.search_hybrid([0.0]*1024, '營收 FY2025', filt, 50)
        requests = client.hybrid_search.call_args.kwargs['reqs']
        assert len(requests) == 2 and all(request.filter == filt for request in requests)
        index.search_bm25('AAPL FY2025', filt, 50)
        assert client.search.call_args.kwargs['filter'] == filt


def test_query_expansion_keeps_stock_year_and_numbers():
    from app.knowledge.query_understanding import retrieval_query
    q='AAPL 2025 revenue was USD 123 million?'
    expanded=retrieval_query(q)
    assert q in expanded
    assert 'AAPL' in expanded and '2025' in expanded and '123' in expanded
    assert expanded.count('revenue') == 1
    assert '经营现金流' in retrieval_query('operating cash flow 2025 AAPL')
    assert 'revenue' in retrieval_query('营收 FY2025 AAPL')

def test_existing_hybrid_collection_schema_is_validated_and_missing_index_is_completed():
    from pymilvus import DataType
    client=MagicMock();client.has_collection.return_value=True
    types={'id':DataType.VARCHAR,'source_hash':DataType.VARCHAR,'content_hash':DataType.VARCHAR,'generation':DataType.VARCHAR,'scope':DataType.VARCHAR,'visibility':DataType.VARCHAR,'owner_hash':DataType.VARCHAR,'symbol':DataType.VARCHAR,'source_type':DataType.VARCHAR,'available_at':DataType.INT64,'text':DataType.VARCHAR,'embedding':DataType.FLOAT_VECTOR,'sparse':DataType.SPARSE_FLOAT_VECTOR}
    fields=[{'name':name,'type':types[name],'is_primary':name=='id','params':({'dim':1024} if name=='embedding' else {'enable_analyzer':'true','analyzer_params':'{"tokenizer":"jieba","filter":["cnalphanumonly"]}'} if name=='text' else {})} for name in types]
    client.describe_collection.return_value={'auto_id':False,'enable_dynamic_field':False,'fields':fields,'functions':[{'type':'BM25','input_field_names':['text'],'output_field_names':['sparse']}]}
    client.list_indexes.return_value=['embedding']
    client.describe_index.return_value={'index_type':'FLAT','metric_type':'COSINE'}
    indexes=MagicMock();client.prepare_index_params.return_value=indexes
    with patch('pymilvus.MilvusClient',return_value=client):
        from app.knowledge.vector_store import MilvusIndex
        index=MilvusIndex('http://127.0.0.1:19530','a'*64);index.ensure_collection()
    indexes.add_index.assert_called_once_with('sparse',index_type='SPARSE_INVERTED_INDEX',metric_type='BM25')
    client.create_index.assert_called_once()

def test_existing_malformed_hybrid_collection_fails_with_actionable_error():
    import pytest
    client=MagicMock();client.has_collection.return_value=True
    client.describe_collection.return_value={'fields':[{'name':'id','params':{}}],'functions':[]}
    with patch('pymilvus.MilvusClient',return_value=client):
        from app.knowledge.vector_store import MilvusIndex
        index=MilvusIndex('http://127.0.0.1:19530','a'*64)
        with pytest.raises(RuntimeError,match='missing required fields'):index.ensure_collection()
def test_retained_evidence_alternates_relevance_ranks_between_prompt_edges():
    from app.knowledge.retrieval import _head_tail_order
    assert _head_tail_order([1,2,3,4,5]) == [1,3,5,4,2]
    assert _head_tail_order([1,2,3,4]) == [1,3,4,2]

def test_tool_budget_keeps_strongest_relevance_rows_before_edge_arrangement():
    from app.knowledge.retrieval import _fit_tool_budget, _head_tail_order
    rows=[{'id':str(rank),'excerpt':f'[{rank}] '+('x'*700)} for rank in range(1,6)]
    selected=_fit_tool_budget({'ok':True,'data':rows,'source':'fixture','as_of':None},budget=1700)
    assert [row['id'] for row in selected['data']]==['1','2']
    assert [row['id'] for row in _head_tail_order(selected['data'])]==['1','2']
