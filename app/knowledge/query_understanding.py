from __future__ import annotations
import re

_ALIASES=(('营收','revenue'),('营业收入','revenue'),('经营现金流','operating cash flow'),('经营活动现金流','operating cash flow'),('毛利率','gross margin'))
def retrieval_query(query: str) -> str:
    """Append bounded bilingual aliases without editing the user's question."""
    additions=[]
    folded=query.casefold()
    for zh,en in _ALIASES:
        if zh in query and en not in folded:additions.append(en)
        elif en in folded and zh not in query:additions.append(zh)
    return query if not additions else query+' '+' '.join(additions)

def expanded_queries(query: str) -> list[str]:
    """Expand only a bounded two-topic financial question; never alter ingestion."""
    groups = (("营收", "现金流"), ("revenue", "cash flow"),
              ("利润", "营收"), ("profit", "revenue"))
    lower = query.lower()
    for first, second in groups:
        if first in lower and second in lower:
            left = query.replace(second, "").strip(" 和及,，")
            right = query.replace(first, "").strip(" 和及,，")
            return list(dict.fromkeys((query, left, right)))[:3]
    return [query]
