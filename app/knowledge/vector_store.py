from __future__ import annotations
import json
class MilvusIndex:
    def __init__(self, uri: str, generation: str, *, timeout: float=5.0):
        from pymilvus import MilvusClient
        self.client=MilvusClient(uri=uri,timeout=timeout)
        self.generation=generation
        # BM25 requires an analyzer and sparse field; preserve the original
        # dense collection and use a separately versioned schema.
        self.collection_name='knowledge_hybrid_v1_'+generation
    def ensure_collection(self,generation: str|None=None):
        from pymilvus import DataType, Function, FunctionType
        if generation is not None and generation!=self.generation: raise ValueError('generation mismatch')
        if self.client.has_collection(self.collection_name):
            self._validate_existing_schema()
            self.client.load_collection(self.collection_name)
            return
        schema=self.client.create_schema(auto_id=False,enable_dynamic_field=False)
        schema.add_field('id',DataType.VARCHAR,is_primary=True,max_length=36)
        schema.add_field('source_hash',DataType.VARCHAR,max_length=64)
        schema.add_field('content_hash',DataType.VARCHAR,max_length=64)
        schema.add_field('generation',DataType.VARCHAR,max_length=64)
        schema.add_field('scope',DataType.VARCHAR,max_length=32)
        schema.add_field('visibility',DataType.VARCHAR,max_length=16)
        schema.add_field('owner_hash',DataType.VARCHAR,max_length=64)
        schema.add_field('symbol',DataType.VARCHAR,max_length=16)
        schema.add_field('source_type',DataType.VARCHAR,max_length=32)
        schema.add_field('available_at',DataType.INT64)
        schema.add_field('text',DataType.VARCHAR,max_length=65535,enable_analyzer=True,analyzer_params={'tokenizer':'jieba','filter':['cnalphanumonly']})
        schema.add_field('embedding',DataType.FLOAT_VECTOR,dim=1024)
        schema.add_field('sparse',DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(Function(name='text_bm25',function_type=FunctionType.BM25,input_field_names=['text'],output_field_names=['sparse']))
        self.client.create_collection(self.collection_name,schema=schema,consistency_level='Strong')
        indexes=self.client.prepare_index_params()
        indexes.add_index('embedding',index_type='FLAT',metric_type='COSINE')
        indexes.add_index('sparse',index_type='SPARSE_INVERTED_INDEX',metric_type='BM25',params={'inverted_index_algo':'DAAT_MAXSCORE'})
        self.client.create_index(self.collection_name,indexes)
        self.client.load_collection(self.collection_name)
    def _validate_existing_schema(self):
        from pymilvus import DataType
        description=self.client.describe_collection(self.collection_name)
        fields={field.get('name'):field for field in description.get('fields',[])}
        required={'id','source_hash','content_hash','generation','scope','visibility','owner_hash','symbol','source_type','available_at','text','embedding','sparse'}
        missing=required-fields.keys()
        if missing:raise RuntimeError('existing hybrid collection is missing required fields: '+', '.join(sorted(missing)))
        if description.get('auto_id') is not False or description.get('enable_dynamic_field') is not False or not fields['id'].get('is_primary'):
            raise RuntimeError('existing hybrid collection must use a non-dynamic schema with explicit VARCHAR primary IDs')
        expected_types={'id':DataType.VARCHAR,'source_hash':DataType.VARCHAR,'content_hash':DataType.VARCHAR,'generation':DataType.VARCHAR,
          'scope':DataType.VARCHAR,'visibility':DataType.VARCHAR,'owner_hash':DataType.VARCHAR,'symbol':DataType.VARCHAR,'source_type':DataType.VARCHAR,
          'available_at':DataType.INT64,'text':DataType.VARCHAR,'embedding':DataType.FLOAT_VECTOR,'sparse':DataType.SPARSE_FLOAT_VECTOR}
        for name,expected in expected_types.items():
            if fields[name].get('type')!=expected:raise RuntimeError(f'existing hybrid collection field {name} has an incompatible type')
        embedding=fields['embedding'];params=embedding.get('params',{})
        if params.get('dim')!=1024:raise RuntimeError('existing hybrid collection embedding dimension must be 1024')
        text_field=fields['text'];text_params=text_field.get('params',{})
        analyzer=text_params.get('analyzer_params')
        if isinstance(analyzer,str):
            try:analyzer=json.loads(analyzer)
            except ValueError:analyzer=None
        if str(text_params.get('enable_analyzer')).lower()!='true' or not isinstance(analyzer,dict) or analyzer.get('tokenizer')!='jieba' or analyzer.get('filter')!=['cnalphanumonly']:
            raise RuntimeError('existing hybrid collection text analyzer is missing; recreate only under a new collection version')
        functions=description.get('functions',[])
        if not any(str(fn.get('type')).split('.')[-1] in ('BM25','1') and fn.get('input_field_names')==['text'] and fn.get('output_field_names')==['sparse'] for fn in functions):
            raise RuntimeError('existing hybrid collection BM25 function is missing or incompatible')
        listed=set(self.client.list_indexes(self.collection_name))
        params=self.client.prepare_index_params()
        missing_indexes=[]
        for field,index_type,metric in (('embedding','FLAT','COSINE'),('sparse','SPARSE_INVERTED_INDEX','BM25')):
            if field not in listed:missing_indexes.append((field,index_type,metric))
            else:
                existing=self.client.describe_index(self.collection_name,field)
                if existing.get('index_type')!=index_type or existing.get('metric_type')!=metric:
                    raise RuntimeError(f'existing hybrid collection {field} index must use {index_type}/{metric}')
        if missing_indexes:
            for field,index_type,metric in missing_indexes:params.add_index(field,index_type=index_type,metric_type=metric)
            self.client.create_index(self.collection_name,params)
    def upsert(self, rows: list[dict]):
        for row in rows:
            if row.get('generation')!=self.generation: raise ValueError('generation mismatch')
            if not row.get('text'): raise ValueError('hybrid text field is required')
        return self.client.upsert(self.collection_name,rows)
    def get(self,ids: list[str]) -> dict[str,dict]:
        if not ids:return {}
        rows=self.client.get(self.collection_name,ids=ids,output_fields=['source_hash','content_hash','generation','scope','visibility','owner_hash','symbol','source_type'],consistency_level='Strong')
        return {str(row['id']):dict(row) for row in rows}
    def search_dense(self,vector: list[float],filter_expr: str,limit: int):
        return self.client.search(self.collection_name,data=[vector],anns_field='embedding',
            search_params={'metric_type':'COSINE'},filter=filter_expr,limit=limit,
            output_fields=['source_hash','content_hash','generation','scope','visibility','owner_hash','symbol','source_type'],
            consistency_level='Strong')[0]
    def search_bm25(self,query: str,filter_expr: str,limit: int):
        return self.client.search(self.collection_name,data=[query],anns_field='sparse',
            search_params={'metric_type':'BM25'},filter=filter_expr,limit=limit,
            output_fields=['source_hash','content_hash','generation','scope','visibility','owner_hash','symbol','source_type'],
            consistency_level='Strong')[0]
    def search_hybrid(self,vector: list[float],query: str,filter_expr: str,limit: int):
        from pymilvus import AnnSearchRequest, RRFRanker
        fields=['source_hash','content_hash','generation','scope','visibility','owner_hash','symbol','source_type']
        requests=[AnnSearchRequest([vector],anns_field='embedding',param={'metric_type':'COSINE'},limit=50,filter=filter_expr),
                  AnnSearchRequest([query],anns_field='sparse',param={'metric_type':'BM25'},limit=50,filter=filter_expr)]
        return self.client.hybrid_search(self.collection_name,reqs=requests,ranker=RRFRanker(k=60),limit=min(50,limit),
            output_fields=fields,consistency_level='Strong')[0]
    def search(self,vector: list[float],filter_expr: str,limit: int):
        return self.search_dense(vector,filter_expr,limit)
    def health(self) -> dict:
        return {'server_version':self.client.get_server_version(),'collection':self.collection_name,
          'loaded':self.client.get_load_state(self.collection_name)}
    def close(self): self.client.close()
