from __future__ import annotations
import os
from functools import lru_cache

@lru_cache(maxsize=1)
def _load(path: str, revision: str):
    from FlagEmbedding import FlagReranker
    # A single lazy CPU instance keeps the production process bounded.
    os.environ.setdefault('OMP_NUM_THREADS','2')
    return FlagReranker(path,use_fp16=False,devices=['cpu'],cache_dir=None)

def rerank(query: str, passages: list[str], model_path: str, revision: str) -> list[tuple[int,float]]:
    if not passages:return []
    model=_load(model_path,revision)
    scores=model.compute_score([[query,text] for text in passages],batch_size=4)
    if not isinstance(scores,list):scores=[scores]
    return sorted(enumerate(float(x) for x in scores),key=lambda item:item[1],reverse=True)

def unload():
    _load.cache_clear()
    import gc;gc.collect()
