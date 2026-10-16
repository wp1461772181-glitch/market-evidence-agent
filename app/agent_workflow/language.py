"""Output language formatting; does not rewrite stored source snapshots."""
import re
from functools import lru_cache

@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC
    return OpenCC('t2s')

def simplified(text):
    return _converter().convert(text) if re.search(r'[\u3400-\u9fff]',text) else text

def format_answer(text,question,citations):
    if not re.search(r'[\u3400-\u9fff]',question):return text
    protected={}
    literals=[str(c.get('title') or '') for c in citations]
    literals+=re.findall(r'https?://[^\s<>）)]+',text)
    for literal in sorted(set(literals),key=len,reverse=True):
        if not literal or literal not in text:continue
        marker='MEAPROTECTED'+str(len(protected))+'END'
        protected[marker]=literal;text=text.replace(literal,marker)
    text=simplified(text)
    for marker,literal in protected.items():text=text.replace(marker,literal)
    return text
