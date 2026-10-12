import asyncio

from app.knowledge.query_understanding import expanded_queries
from app.agent_tools import _expanded_retrieval


def test_only_compound_financial_topics_expand_and_results_deduplicate():
    assert expanded_queries("AAPL 最新财报") == ["AAPL 最新财报"]
    queries = expanded_queries("AAPL 2025 营收和现金流")
    assert len(queries) == 3 and "营收" in queries[1] and "现金流" in queries[2]
    called=[]
    async def retrieve(symbol, query, limit):
        called.append(query)
        return {"ok":True,"data":[{"id":"source-1","excerpt":"[1] source text"}],"source":"knowledge","as_of":None}
    result=asyncio.run(_expanded_retrieval(retrieve,"AAPL","AAPL 2025 营收和现金流",5,None))
    assert len(called)==3 and len(result["data"])==1
