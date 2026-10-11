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

def protected_terms(query: str) -> tuple[str,...]:
    return tuple(re.findall(r'(?<![\w])[A-Z]{1,5}(?:\.[A-Z])?|\b(?:19|20)\d{2}\b|\b\d+(?:\.\d+)?%?\b',query))
