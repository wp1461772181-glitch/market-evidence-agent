from __future__ import annotations
import os
from functools import lru_cache

@lru_cache(maxsize=1)
def _load(path: str, revision: str):
    from FlagEmbedding import FlagReranker
    # A single lazy CPU instance keeps the production process bounded. Two threads measured
    # 0.5 s per pair; four are what a ten-core machine can give without starving the API.
    os.environ.setdefault('OMP_NUM_THREADS','4')
    return FlagReranker(path,use_fp16=False,devices=['cpu'],cache_dir=None)

# A reranker reads the head of a passage: scoring 1,600 characters cost 0.5 s a pair and changed
# the order of the top ten no more than scoring the first 1,000 did.
RERANK_CHARACTERS = 1000


def rerank(query: str, passages: list[str], model_path: str, revision: str) -> list[tuple[int,float]]:
    if not passages:return []
    model=_load(model_path,revision)
    trimmed=[text[:RERANK_CHARACTERS] for text in passages]
    scores=model.compute_score([[query,text] for text in trimmed],batch_size=8)
    if not isinstance(scores,list):scores=[scores]
    return sorted(enumerate(float(x) for x in scores),key=lambda item:item[1],reverse=True)

def unload():
    _load.cache_clear()
    import gc;gc.collect()
