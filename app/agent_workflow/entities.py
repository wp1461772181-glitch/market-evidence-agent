"""Verify an interpreted issuer/ticker pair using official data, never an alias table."""
import re,time
from .references import normalized

_cache=None
_cached_at=0.0

def ticker_records():
    global _cache,_cached_at
    if _cache is not None and time.monotonic()-_cached_at<3600:return _cache
    from ..sec_filings import SecEdgarProvider,SEC_TICKERS_URL
    value=SecEdgarProvider()._get_json(SEC_TICKERS_URL)
    rows=[r for r in value.values() if isinstance(r,dict) and isinstance(r.get('title'),str) and isinstance(r.get('ticker'),str)]
    if not rows:raise ValueError('entity_mapping_unavailable')
    _cache=rows;_cached_at=time.monotonic()
    return rows

def issuer_words(value):
    legal={'inc','incorporated','corp','corporation','ltd','limited','plc','sa'}
    words=re.findall(r'[a-z0-9]+',normalized(value).casefold())
    while words and words[-1] in legal:words.pop()
    return words

def resolve_ticker(company):
    """Fill an omitted code only for one unambiguous official issuer match."""
    words=issuer_words(company)
    if not words or len(words[0])<3:return None
    try:
        rows=ticker_records()
        exact={r['ticker'] for r in rows if issuer_words(r['title'])==words}
        if exact:return next(iter(exact)) if len(exact)==1 else None
        symbols={r['ticker'] for r in rows if issuer_words(r['title'])[:len(words)]==words}
        return next(iter(symbols)) if len(symbols)==1 else None
    except Exception:return None

def verified_pair(company,symbol):
    if not company or not symbol:return False
    words=issuer_words(company)
    if not words or len(words[0])<3:return False
    try:
        return any(r['ticker'].casefold()==symbol.casefold() and issuer_words(r['title'])[:len(words)]==words for r in ticker_records())
    except Exception:return False
