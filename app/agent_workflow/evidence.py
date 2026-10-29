"""Deterministic evidence validation; these parsers never select a route or tool."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from ..agent_web_research import METRIC_GROUPS
from ..freshness import published_within


def contains(term: str, text: str) -> bool:
    pattern=re.escape(term)
    if term.isascii(): pattern=r'(?<![A-Za-z0-9])'+pattern+r'(?![A-Za-z0-9])'
    return bool(re.search(pattern,text,re.I))


_TITLE_PUNCTUATION=str.maketrans({'？':'','！':'','。':'','?':'','!':'','.':''})
_TITLE_RESTORED=str.maketrans({v:k for k,v in {'？':'','！':'','。':'','?':'','!':'','.':''}.items()})
# A quoted passage is one unit: a full stop inside the quotation (“……毛利率为 46.2%。” [1]) must not
# split the [1] away from the sentence it supports.
_QUOTE_PAIRS={'“':'”','「':'」','『':'』','‘':'’','"':'"'}


def _protect_passage_punctuation(match):
    return match.group(0).translate(_TITLE_PUNCTUATION)


def _protect_quotations(text):
    """Protect balanced quote punctuation in one pass, including long and nested quotes."""
    pieces=[];stack=[];start=0;last=0
    for index,char in enumerate(text):
        if char=='\n':
            stack.clear()
        elif stack and char==stack[-1]:
            stack.pop()
            if not stack:
                pieces.extend((text[last:start],text[start:index+1].translate(_TITLE_PUNCTUATION)))
                last=index+1
        elif char in _QUOTE_PAIRS:
            if not stack:start=index
            stack.append(_QUOTE_PAIRS[char])
    pieces.append(text[last:])
    return ''.join(pieces)


def cited_passages(answer):
    # A title in 《》 is one name even when it asks a question ("……哪一只更值得买入？"): its punctuation
    # does not end the sentence that cites it.
    protected=_protect_quotations(answer)
    protected=re.sub(r'《[^》\n]{0,300}》',_protect_passage_punctuation,protected)
    # Markdown block quotes have the same citation scope as quoted strings. Protect only
    # their own lines: a trailing source never authorizes surrounding ordinary prose.
    protected=re.sub(r'(?m)^\s*>[^\n]*',_protect_passage_punctuation,protected)
    # Both “fact[1]。” and “fact。[1]” bind the marker to the same sentence.
    normalized=re.sub(r'([。！？.!?])(\s*(?:\[\d+\]\s*)+)',lambda m:m[2].strip()+m[1],protected)
    return [part.translate(_TITLE_RESTORED) for part in re.split(r'[。！？\n]|(?<=[.!?])\s+',normalized)]


def normalized_period(text):
    text=unicodedata.normalize('NFKC',text).upper()
    for word,number in [('FIRST','1'),('SECOND','2'),('THIRD','3'),('FOURTH','4')]:
        text=re.sub(r'\b'+word+r' QUARTER\b','Q'+number,text)
    text=re.sub(r'第?([一二三四1-4])季度',lambda m:'Q'+dict(zip('一二三四','1234')).get(m[1],m[1]),text)
    text=re.sub(r'FISCAL\s+YEAR\s*','FY',text)
    return text


def period_pairs(text):
    text=normalized_period(text)
    pairs={(year,q) for q,year in re.findall(r'Q([1-4])(?:\s+OF)?[\s年,-]{0,5}((?:19|20)\d{2})',text)}
    pairs|={(year,q) for year,q in re.findall(r'((?:19|20)\d{2})[\s年,-]{0,5}Q([1-4])',text)}
    return pairs


def period_match(request: str, body: str) -> bool:
    request=normalized_period(request);body=normalized_period(body)
    from ..freshness import requested_years
    years=requested_years(request)
    if years and any(y not in body for y in years):return False
    requested_pairs=period_pairs(request)
    if requested_pairs and not requested_pairs<=period_pairs(body):
        quarters=set(re.findall(r'Q([1-4])',request))
        if quarters and not quarters<=set(re.findall(r'Q([1-4])',body)):return False
    return True


# The gate knows three structural metrics that are not synonyms of a text term.  When the classifier
# was limited to a fixed enum these arrived spelled exactly; now it copies the user's own wording, so
# "股价" and "已保存价格" must still reach the price branch and "提交日期" the date branch.
_STRUCTURAL_METRICS={
 'price':('price','close','收盘','股价','收盘价','价格','已保存价格','quote','ohlcv'),
 'date':('date','日期','时间','发布日期','提交日期','披露日期','filed_at','published_at'),
 'forecast':('forecast','预测','概率','probability','probabilities'),
}


def canonical_metric(name):
    """The gate's own name for a metric the user wrote in their own words."""
    text=' '.join(str(name or '').split()).strip()
    if not text:return ''
    folded=text.casefold()
    for canonical,terms in METRIC_GROUPS.items():
        if folded==canonical or any(folded==t.casefold() for t in terms):return canonical
    for canonical,terms in _STRUCTURAL_METRICS.items():
        if folded==canonical or any(folded==t.casefold() for t in terms):return canonical
    return text


# Which checks may only annotate a row instead of discarding it.  The course's rule is that recall
# answers "did we find it" and the model answers "is it enough": a source that is merely about a
# different period, or that words the metric differently, is evidence the model should see and judge.
# What stays a hard stop is data that cannot be trusted (a broken body) and a source about the wrong
# company, because attributing one company's numbers to another is the one error worth refusing.
# An explicit freshness window is the user's own constraint, not a relevance judgement, so it stays a
# hard filter.  What softens is wording: whether the text says 毛利率 or gross margin, whether the
# fiscal period is phrased the way the question phrased it.
SOFT_GATE_CHECKS={'requested_metric_missing','requested_period_missing','weak_question_coverage'}


def _gate_outcome(ok: bool,reason: str,soft: bool) -> tuple[bool,str]:
    """``soft`` turns the annotation-only checks into an accepted row that carries a note.

    The reason comes back prefixed with ``soft:`` so the caller can record the reservation on the
    row and the answer model can say the source is not on point instead of the source disappearing.
    """
    if ok:return True,reason
    if soft and reason.split(':',1)[0] in SOFT_GATE_CHECKS:return True,'soft:'+reason
    return False,reason


def _has_valid_http_url(value: object) -> bool:
    text = str(value or '').strip()
    return text.startswith(('http://', 'https://')) and '://' in text and len(text.split('://', 1)[1].strip('/')) >= 3


def gate_row(question: str, row: dict, *, symbol: str | None=None, company: str | None=None,
             metrics: list[str] | None=None, period: str | None=None, days_back: int | None=None,
             official_only: bool=False, now: datetime | None=None, entity_aliases: list[str] | None=None,
             soft: bool=False) -> tuple[bool,str]:
    kind=row.get('source_type','')
    is_configured_news=kind=='configured_news' or row.get('origin')=='configured_news'
    if is_configured_news and not _has_valid_http_url(row.get('url') or row.get('source_url')):
        return False,'missing_citation_url'
    web=kind in {'web','web_news','configured_news'} or 'url' in row
    if row.get('fetch_error') or row.get('body_hash_valid') is False:return False,'invalid_source_body'
    if official_only and kind not in {'official_filing','saved_filing'}:return False,'official_source_required'
    body=' '.join(str(row.get(k) or '') for k in ('excerpt','content','summary','text','answer','reasons'))
    title=str(row.get('title') or '')
    if not body.strip() and not any(k in row for k in ('close','first_close','last_close','probabilities','status','form')):
        metric=next((key for key,terms in METRIC_GROUPS.items() if any(contains(t,question) for t in terms)),None)
        return False,('requested_metric_missing:'+metric if metric else 'zero_evidence')
    if symbol and row.get('symbol') and row['symbol'].upper()!=symbol.upper():return False,'requested_entity_missing'
    allowed_entities = [x for x in (company, symbol, *(entity_aliases or [])) if x]
    if company:
        clean = re.sub(r'\b(Inc\.?|Corporation|Corp\.?|Platforms|Company|Co\.?|Ltd\.?|LLC|Group|Holdings|PLC)\b', '', company, flags=re.I).strip(' ,.')
        if clean and clean not in allowed_entities:
            allowed_entities.append(clean)
        for part in re.split(r'[,/·\s]+', clean):
            p = part.strip()
            if len(p) >= 2 and p not in allowed_entities:
                allowed_entities.append(p)
        c_lower = company.lower()
        if 'alphabet' in c_lower:
            allowed_entities.extend(['Google', '谷歌'])
        elif 'google' in c_lower:
            allowed_entities.extend(['Alphabet'])
        elif 'meta' in c_lower:
            allowed_entities.extend(['Facebook'])
    # Saved rows are verified by their query's symbol filter. Public pages must name the
    # entity in body text; a forged headline by itself is never sufficient.
    if web and (company or symbol) and not any(contains(x,body+' '+title) for x in allowed_entities):
        return False,'requested_entity_missing'
    if not web and symbol and not row.get('symbol') and not row.get('_verified_symbol') and not any(contains(x,body+' '+title) for x in allowed_entities):
        return False,'requested_entity_missing'
    needed=metrics if metrics is not None else [key for key,terms in METRIC_GROUPS.items() if any(contains(t,question) for t in terms)]
    for key in needed:
        if key=='price' and ('close' in row or 'last_close' in row):
            import math
            price_val=row.get('close') if 'close' in row else row.get('last_close')
            try:
                if not isinstance(price_val,bool) and math.isfinite(float(price_val)) and float(price_val)>=0:continue
            except (TypeError,ValueError):pass
            return _gate_outcome(False,'requested_metric_missing:price',soft)
        if key=='date' and any(row.get(field) for field in ('filed_at','published_at','trading_date','decision_at','start_date','end_date')):continue
        if key=='forecast' and row.get('probabilities'):continue
        terms=METRIC_GROUPS.get(key,(key,))
        passages=re.split(r'[。！？\n]|(?<=[.!?])\s+',body)
        relevant=[part for part in passages if any(contains(t,part) for t in terms) and (not web or not(company or symbol) or any(contains(x,part) for x in allowed_entities))]
        if not relevant:
            for i, part in enumerate(passages):
                if any(contains(t,part) for t in terms):
                    prev = passages[i-1] if i > 0 else ''
                    nxt = passages[i+1] if i + 1 < len(passages) else ''
                    combined = prev + ' ' + part + ' ' + nxt
                    if any(contains(x, combined) for x in allowed_entities) and not re.search(r'\b(?:Samsung|Google|Microsoft|Meta|Amazon|Intel|Nvidia|Tesla)\b', part, re.I):
                        relevant.append(combined)
        if not relevant:return _gate_outcome(False,'requested_metric_missing:'+key,soft)
        if not any(period_match(period or question,part) for part in relevant):return _gate_outcome(False,'requested_period_missing',soft)
    requested=period or question
    if not period_match(requested,body+' '+str(row.get('report_period') or '')+' '+str(row.get('filed_at') or row.get('trading_date') or row.get('decision_at') or row.get('start_date') or '')+' '+str(row.get('end_date') or '')):
        return _gate_outcome(False,'requested_period_missing',soft)
    if days_back is not None and not published_within(row.get('published_at') or row.get('filed_at'),days_back,now or datetime.now(UTC)):
        return _gate_outcome(False,'requested_time_window_missing',soft)
    query_without_url=re.sub(r'https://\S+',' ',question)
    from ..agent_web_research import _GENERIC_LATIN
    noise=set(_GENERIC_LATIN)|{'read','explain','summarize','summarise','verified','publication','dates','cite','citation','citations','briefly'}
    words=[t.casefold() for t in re.findall(r'[A-Za-z]{3,}',query_without_url) if t.casefold() not in noise]
    if not needed and not(company or symbol) and words and not any(contains(t,body) for t in words):return _gate_outcome(False,'weak_question_coverage',soft)
    return True,'sufficient_evidence'


def deterministic_confidence_gate(question: str,result: dict,citations=None,verified_symbols=None,**requirements):
    if not isinstance(result,dict) or not result.get('ok'):return False,(result or {}).get('error_code') or 'retrieval_unavailable'
    rows=result.get('data')
    if not isinstance(rows,list) or not rows:return False,'zero_evidence'
    if not any(isinstance(r,dict) and (r.get('id') or r.get('set_id') or r.get('source_id') or r.get('url') or r.get('source_url') or r.get('question') or 'close' in r or 'first_close' in r) for r in rows):return False,'missing_source_coverage'
    inferred=[t for t in re.findall(r'(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])',question) if t not in {'SEC','FY','EPS','URL','HTTPS'}]
    explicit_symbol=requirements.pop('symbol',None)
    symbols=verified_symbols or ([explicit_symbol] if explicit_symbol else inferred or [None])
    for symbol in symbols:
        outcomes=[gate_row(question,{**r,**({'source_type':'web'} if result.get('source_type')=='web' or str(result.get('source','')).startswith('public web') else {})},symbol=symbol,**requirements) for r in rows if isinstance(r,dict)]
        if not any(ok for ok,_ in outcomes):
            reason=next((why for _,why in outcomes),'zero_evidence')
            if reason=='requested_time_window_missing' and any(not r.get('published_at') for r in rows):reason='publisher_date_unavailable'
            return False,reason
    if requirements.get('days_back') and any(not published_within(r.get('published_at'),requirements['days_back'],datetime.now(UTC)) for r in rows):return False,'publisher_date_unavailable'
    return True,'heuristic_coverage_pass'


def literal_titles(text,citations):
    """The text with bracketed numbers inside exact, server-known source titles made literal."""
    from .language import simplified
    titles={str(c.get('title') or '') for c in citations}
    for title in sorted(titles,key=len,reverse=True):
        if not re.search(r'\[\d+\]',title):continue
        for variant in {title,simplified(title)}:
            literal=re.sub(r'\[(\d+)\]',lambda m:'［'+m[1]+'］',variant)
            text=text.replace(variant,literal)
    return text


def citation_markers(answer,citations):
    """Read handles while preserving bracketed text in exact, server-known source titles."""
    return re.findall(r'\[(\d+)\]',literal_titles(answer,citations))


def citation_errors(answer: str,citations: list[dict]) -> list[int]:
    valid=set()
    for c in citations:
        if type(c.get('ordinal')) is not int:continue
        if c.get('source_type')=='unavailable' or c.get('unavailable'):continue
        if (c.get('source_type') in {'configured_news'} or c.get('origin')=='configured_news') and not _has_valid_http_url(c.get('url') or c.get('source_url')):
            continue
        valid.add(int(c['ordinal']))
    return [int(n) for n in citation_markers(answer,citations) if int(n) not in valid]


_CITATION_FIELD_SKIP={'excerpt','answer','summary','content','text','body','quote','raw_content','content_text',
    'source_snapshot','payload','_citations','citation','evidence_quotes','figures'}


def _citation_fields(row: dict) -> dict:
    """Scalar row fields a claim may bind to, kept beside the prose quote.

    Structured sources (price, XBRL, forecasts, saved analyses) expose values the
    answer may cite without a natural-language passage.  The citation has to carry
    those values too, otherwise a correct field claim would fail as a mismatch.
    """
    fields={}
    for key,value in row.items():
        if key in _CITATION_FIELD_SKIP or str(key).startswith('_') or value is None:
            continue
        if isinstance(value,bool) or isinstance(value,(int,float)):
            fields[str(key)]=value
        elif isinstance(value,str) and len(value)<=300:
            fields[str(key)]=value
    return fields


def _evidence_quotes(row: dict) -> list[str]:
    quotes=[]
    for quote in row.get('evidence_quotes') or []:
        if isinstance(quote,str) and quote and quote not in quotes:
            quotes.append(quote)
    return quotes


def number_rows(name: str,rows: list[dict],citations: list[dict],scope: dict | None=None) -> list[dict]:
    """Give accepted rows a citation without changing provenance or raw saved snapshots."""
    scope=scope or {}
    if name=='query_stock_prices' and len(rows)>10 and all(isinstance(r,dict) and r.get('source_type')!='saved_price_set' for r in rows):
        from ..agent_tools import build_price_set_row
        receipt,_=build_price_set_row(rows,symbol=scope.get('symbol'))
        rows=[receipt]
    accepted=[]
    for original in rows:
        row=dict(original)
        rich=row.pop('_citations',[])
        if rich:
            mapping={}
            for original_citation in rich:
                old=next((c for c in citations if c.get('chunk_id')==original_citation.get('chunk_id') and c.get('source_hash')==original_citation.get('source_hash')),None)
                number=old['ordinal'] if old else len(citations)+1
                mapping[original_citation['ordinal']]=number
                if not old:
                    citations.append({**original_citation,'quote':original_citation.get('quote') or str(row.get('excerpt') or row.get('answer') or '')[:5000],
                        'ordinal':number,'fields':_citation_fields(row),'evidence_quotes':_evidence_quotes(row)})
                else:
                    old.setdefault('fields',_citation_fields(row))
                    old.setdefault('evidence_quotes',_evidence_quotes(row))
            for field in ('excerpt','answer','summary'):
                if isinstance(row.get(field),str):row[field]=re.sub(r'\[(\d+)\]',lambda m:'['+str(mapping.get(int(m[1]),int(m[1])))+']',row[field])
            row['citation']='['+str(next(iter(mapping.values())))+']'
            row['_citations']=[{**c,'ordinal':mapping[c['ordinal']]} for c in rich]
            accepted.append(row)
            continue
        kind=row.get('source_type') or ('configured_news' if name=='search_configured_news' else 'web' if name=='web_research' else 'official_filing' if name=='query_sec_filings' else 'saved_price' if name=='query_stock_prices' else 'saved_forecast' if name=='query_latest_forecast' else 'ai_analysis' if 'analysis' in name else 'research_faq' if 'faq' in name else 'saved_material')
        if kind=='configured_news' and not _has_valid_http_url(row.get('url') or row.get('source_url')):
            continue
        if kind=='saved_price_set':
            from ..agent_tools import PRICE_SET_FIELDS, get_cached_price_set
            set_id=str(row.get('set_id') or '')
            meta=get_cached_price_set(set_id) or {}
            sym=str(row.get('symbol') or meta.get('symbol') or scope.get('symbol') or '')
            row_ids=list(row.get('row_ids') or meta.get('row_ids') or [])
            parent_digest=str(row.get('parent_digest') or meta.get('parent_digest') or '')
            n_count=int(row.get('n') or meta.get('n') or len(row_ids))
            start_date=str(row.get('start_date') or meta.get('start_date') or '')
            end_date=str(row.get('end_date') or meta.get('end_date') or '')
            first_close=row.get('first_close') if row.get('first_close') is not None else meta.get('first_close')
            last_close=row.get('last_close') if row.get('last_close') is not None else meta.get('last_close')
            fields_avail=list(row.get('fields_available') or meta.get('fields_available') or PRICE_SET_FIELDS)
            source_name=row.get('source') or meta.get('source') or 'market_prices'
            fetched_at=row.get('fetched_at') or meta.get('fetched_at')
            key=hashlib.sha256(json.dumps(['saved_price_set',sym,set_id,parent_digest],sort_keys=True,default=str).encode()).hexdigest()
            old=next((c for c in citations if c.get('evidence_key')==key),None)
            if old:
                ordinal=old['ordinal']
            else:
                ordinal=len(citations)+1
                figures_str=f'{start_date} close {first_close} {end_date} close {last_close}'
                receipt_fields={'symbol':sym,'set_id':set_id,'n':n_count,'start_date':start_date,'end_date':end_date,
                    'first_close':first_close,'last_close':last_close}
                citations.append({
                    'ordinal':ordinal,
                    'evidence_key':key,
                    'source_type':'saved_price_set',
                    'origin':'saved',
                    'symbol':sym,
                    'set_id':set_id,
                    'row_ids':row_ids,
                    'n':n_count,
                    'start_date':start_date,
                    'end_date':end_date,
                    'first_close':first_close,
                    'last_close':last_close,
                    'fields_available':fields_avail,
                    'parent_digest':parent_digest,
                    'source':source_name,
                    'fetched_at':fetched_at,
                    'published_at':end_date,
                    'observed_at':fetched_at,
                    'title':f'{sym} 价格序列 ({start_date}..{end_date})'.strip(),
                    'quote':figures_str,
                    'figures':figures_str,
                    'fields':receipt_fields,
                })
            accepted.append({
                'citation':f'[{ordinal}]',
                'set_id':set_id,
                'symbol':sym,
                'n':n_count,
                'start_date':start_date,
                'end_date':end_date,
                'first_close':first_close,
                'last_close':last_close,
                'fields_available':fields_avail,
            })
            continue
        body=str(row.get('excerpt') or row.get('answer') or row.get('summary') or json.dumps(row,ensure_ascii=False,default=str))
        key=hashlib.sha256(json.dumps([kind,row.get('id'),row.get('source_id'),row.get('url'),body],sort_keys=True,default=str).encode()).hexdigest()
        old=next((c for c in citations if c.get('evidence_key')==key),None)
        if old: ordinal=old['ordinal']
        else:
            ordinal=len(citations)+1
            resolved_url=row.get('source_url') or row.get('content_source_url') or row.get('url')
            origin=row.get('origin') or ('configured_news' if kind=='configured_news' else 'web' if kind in {'web','web_news'} else 'saved')
            citations.append({'ordinal':ordinal,'evidence_key':key,'chunk_id':row.get('chunk_id'),'document_id':row.get('document_id'),
                'source_id':str(row.get('source_id') or row.get('id') or ''),'question':row.get('question'),'source_type':kind,'origin':origin,'tool_arguments':row.get('tool_arguments'),
                'title':str(row.get('title') or row.get('accession_number') or scope.get('company') or name)[:300],
                'source_name':row.get('source_name') or row.get('source'),
                'url':resolved_url,'source_url':resolved_url,
                'published_at':row.get('published_at') or row.get('filed_at') or row.get('trading_date'),
                'observed_at':row.get('fetched_at') or row.get('observed_at'),'quote':body[:5000],
                'trading_date':row.get('trading_date'),'source':row.get('source'),'fetched_at':row.get('fetched_at'),
                'symbol':row.get('symbol') or scope.get('symbol'),'analysis_id':row.get('analysis_id'),'derived':kind=='ai_analysis','truncated':bool(row.get('truncated')),
                'fields':_citation_fields(row),'evidence_quotes':_evidence_quotes(row)})
        if kind=='saved_price':
            citations[ordinal-1]['quote']=str(row['close'])
            # The quote stays the close (the restore check compares it); the row's other prices are
            # kept beside it so an answer that states the open, high, low or volume can be checked.
            citations[ordinal-1]['figures']=' '.join(f'{k} {row[k]}' for k in ('trading_date','open','high','low','close','volume') if row.get(k) is not None)
        row['citation']=f'[{ordinal}]';row['source_type']=kind
        accepted.append(row)
    return accepted


def unsupported_numbers(answer,citations):
    """Check cited quantitative claims without inferring facts or entity aliases.

    Unit-normalized amounts may be translated (10 billion / 100亿) and probabilities
    may be shown as percentages. This is a guard, not a semantic answer scorer.
    """
    return bool(unsupported_number_values(answer,citations))


_DASHES=re.compile('[‐‑‒–—−]')
# Never figures: a form or a section reference states no amount whatever the evidence says.
_ALWAYS_IDENTIFIERS=re.compile(r'\b(?:10-K|10-Q|8-K|20-F|6-K|S-1|DEF\s*14A)\b|\b(?:Note|Item|Part|CIK)\s*\d+[A-Z]?\b',re.I)
# Identifiers and names that carry digits: accepted only where the turn's evidence shows them.
_GROUNDED_IDENTIFIERS=re.compile(
    r'\b\d{10}-\d{2}-\d{6}\b|\b\d{18}\b|[\w.-]+\.(?:htm|html|pdf|xml|txt)\b'
    r'|(?:Exhibit|EX-|附件|附录)\s*\d+(?:\.\d+)?'
    r'|\b[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)?\s\d{1,4}(?![,.]?\d)(?!\s*(?:%|％|万亿|千万|百万|亿|万|千|million|billion|thousand|trillion|bn|mn|美元|元|dollars?|USD|倍))',re.I)
# A link written without its scheme ("sec.gov/Archives/edgar/data/789019/…") names a source, not an amount.
_BARE_LINK=re.compile(r'(?<![\w@/.-])(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,6}/[^\s|()\[\]（）<>，。；、]*',re.I)
_SCALE=((re.compile(r'\(?\s*in\s+millions|百万美元|单位[:：]\s*百万',re.I),1e6),
        (re.compile(r'\(?\s*in\s+thousands|千美元|单位[:：]\s*千',re.I),1e3),
        (re.compile(r'\(?\s*in\s+billions|十亿美元',re.I),1e9))
_CLAIM_NUMBER=re.compile(r'(\$\s*)?(\d+(?:\.\d+)?)\s*(万亿|千万|百万|亿|万|千|trillion|billion|million|thousand|bn|mn|b|k|m)?(?![A-Za-z])(\s*(?:%|％))?(\s*(?:美元|元|dollars?|USD))?',re.I)


def identifier_tokens(text):
    """Digit-bearing names and identifiers in a passage: forms, sections, accessions, file names, product names."""
    text=_DASHES.sub('-',text)
    return [m.group(0) for m in _ALWAYS_IDENTIFIERS.finditer(text)]+[m.group(0) for m in _GROUNDED_IDENTIFIERS.finditer(text)]


def _without_identifiers(passage,grounds):
    passage=_DASHES.sub('-',passage)
    passage=_ALWAYS_IDENTIFIERS.sub(' ',passage)
    def keep(match):
        token=' '.join(match.group(0).split()).casefold()
        return ' ' if token in grounds else match.group(0)
    return _GROUNDED_IDENTIFIERS.sub(keep,passage)


def _significant(number):
    digits=number.replace('.','').lstrip('0')
    if '.' not in number:digits=digits.rstrip('0')
    return max(len(digits),1)


def _round_to(value,digits):
    import math
    if not value:return 0.0
    return round(value,-int(math.floor(math.log10(abs(value))))+digits-1)


def _claim_figures(text):
    """Each stated amount with its precision; the same exclusions as ``figure_values``."""
    from .semantic import ACCESSION,SOURCE_ID,TASK_ID,PERIOD_TEXT,_MAGNITUDE
    text=unicodedata.normalize('NFKC',text).replace('萬','万').replace('億','亿')
    text=re.sub(r'(?<=\d)T(?=\d{2}:)',' ',text)
    text=PERIOD_TEXT.sub(' ',ACCESSION.sub(' ',SOURCE_ID.sub(' ',TASK_ID.sub(' ',text))))
    text=re.sub(r'(?<=\d),(?=\d{3}(?!\d))','',text)
    found=[]
    for dollar,number,unit,percent,currency in _CLAIM_NUMBER.findall(text):
        value=round(float(number)*_MAGNITUDE.get(unit.lower(),1.0),4)
        found.append((value,_significant(number),bool(unit or percent),not(dollar or unit or percent or currency)))
    return found


def unsupported_number_values(answer,citations,*,question=''):
    """The candidate numbers no cited source supports, so a repair can name them.

    Same rules as ``unsupported_numbers``; the list only tells the rewriting model what to
    drop or re-source, it never decides the answer.  A figure is supported when its cited source
    states it: exactly, in the unit the source's table is in ("(In millions)" 32,000 is 320 亿),
    or rounded to the precision the answer itself writes.  Digits inside identifiers and names
    are not figures when the turn's evidence or the question shows that identifier.
    """
    from datetime import datetime, timedelta
    from .semantic import figure_values
    from .language import simplified
    answer=simplified(answer)
    # Chinese dates are often typeset with spaces ("2025 年 12 月 31 日"), and a CJK neighbour is a word
    # character, so the date is bounded by digits rather than by \b.
    date_pat = re.compile(
        r'(?<!\d)(?:\d{4}\s*[-/年]\s*)?\d{1,2}\s*[-/月]\s*\d{1,2}(?:\s*日)?(?!\d)|(?<!\d)\d{4}\s*年\s*\d{1,2}\s*月|'
        r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b|'
        r'\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b',
        re.I
    )
    indexed={str(c['ordinal']):c for c in citations}
    evidence=' '.join(str(c.get(k) or '') for c in citations for k in ('quote','title','source_url'))+' '+str(question or '')
    grounds=' '.join(_DASHES.sub('-',evidence).split()).casefold()
    rejected=[]
    for passage in cited_passages(answer):
        refs=citation_markers(passage,citations)
        cited=[indexed[r] for r in refs if r in indexed]
        body=' '.join(str(c.get('quote',''))+' '+str(c.get('figures',''))+' '+str(c.get('published_at',''))+' '+str(c.get('source_url',''))+' '+str(c.get('title','')) for c in cited)
        allowed=figure_values(body)
        for citation in cited:
            quote=str(citation.get('quote',''))
            for marker,scale in _SCALE:
                if marker.search(quote):allowed|={round(v*scale,4) for v in figure_values(quote) if v>=1}
            if citation.get('source_type')=='calculation':
                # A result computed on values bound per million is itself in millions.
                allowed|={round(v*1e6,4) for v in figure_values(quote) if abs(v)>=1}
            pub = citation.get('published_at') or citation.get('filed_at') or citation.get('trading_date')
            if pub and isinstance(pub, str):
                try:
                    dt = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                    allowed.add(float(dt.day))
                    allowed.add(float((dt + timedelta(hours=8)).day))
                    allowed.add(float((dt - timedelta(hours=8)).day))
                    allowed.add(float(dt.month))
                    allowed.add(float((dt + timedelta(hours=8)).month))
                except (ValueError, TypeError):
                    pass
        # A full reported value may be written bare in a table headed in millions, 亿 or billions; a
        # number that carries its own unit ("54.2 美元") is held to that unit.
        headed={round(v/scale,4) for v in allowed if abs(v)>=1e6 for scale in (1e6,1e8,1e9)}
        checked=re.sub(r'\[\d+\](?:\([^)]*\))?','',passage)
        checked=re.sub(r'https?://\S+','',checked)
        # Its digits are not figures when the turn's evidence shows that link; an unknown link keeps them.
        checked=_BARE_LINK.sub(lambda m:' ' if m.group(0).casefold().rstrip('/.,;:') in grounds else m.group(0),checked)
        checked=_without_identifiers(checked,grounds)
        # Bare financial amounts are also claims. Strip only citation identities,
        # list labels and grounded date/period syntax, never arbitrary numbers.
        bare=re.sub(r'^\s*\d+[.、)]\s*','',checked)
        bare=date_pat.sub('',bare)
        bare=re.sub(r'\d+(?:\.\d+)?\s*(?:天|日|个月|年|周|条|篇|份|项|页|行|个|次|点|位|名|步|轮|倍|小时|分|秒|字符|字|days?|hours?|items?|times?|points?|characters?|chars?)', ' ', bare)
        number=r'\d+(?:,\d{3})*(?:\.\d+)?'
        magnitude=r'(?:万亿|千万|百万|亿|万|千|trillion|billion|million|thousand|bn|mn|b|k|m)?'
        # Amounts with a currency or percent are read before list labels are stripped: "0.5万亿美元"
        # must not become "5万亿美元".
        amounts=re.findall(number+r'\s*'+magnitude+r'\s*(?:美元|元|dollars?|USD|%|％)',checked,re.I)
        amounts+=re.findall(r'\$\s*'+number+r'\s*'+magnitude,checked,re.I)
        percent=bool(re.search(r'%|％|百分',passage))
        for value,digits,scaled,unitless in _claim_figures(' '.join(amounts))+_claim_figures(bare):
            # Exclude list numbering and calendar years to avoid false positive claim rejections
            if (1.0<=value<=10.0 and value==int(value)) or (1990<=value<=2035 and value==int(value)):continue
            support=allowed|headed if unitless else allowed
            if value in support or (percent and value/100 in allowed):continue
            if (digits>=3 or (digits>=2 and scaled)) and any(_round_to(a,digits)==_round_to(value,digits) for a in support if a):continue
            rejected.append(value)
    return sorted(set(rejected))

# Claim validation only. These concepts never route a request or choose a tool.
RESIGNATION_TERMS=('resign','resigned','resignation','stepped down','stepping down','辞职','辞任','离职','退任')
DEPARTURE_TERMS=('departure','departed','departing','left','leave','retired','retires','离任','卸任','退任','离职','走人')
APPOINTMENT_TERMS=('appoint','appointed','appointment','named','promoted','elected','tapped','hired','join','joined','joins','任命','出任','就任','委任','担任')
ROLE_START_TERMS=('takes charge','took charge','taking charge','takes office','took office','上任','接任','接棒')
CLAIM_FEATURES=(
 ('CEO','chief executive','首席执行官','执行长'),
 ('CFO','chief financial','首席财务官','财务主管'),
 RESIGNATION_TERMS,
 APPOINTMENT_TERMS,
 ('bankruptcy','bankrupt','chapter 11','insolvent','insolvency','liquidation','破产','清算'),
 ('acquisition','acquire','acquired','收购','buy','bought','purchased','purchase','merger','merged','deal','transaction','investment','invested','takeover','stake','买入','入股','全资','并入','买下'),
 DEPARTURE_TERMS,
 ROLE_START_TERMS,
)

def supporting_event_terms(terms):
    # Resignation entails departure; a bare departure does not prove resignation.
    if terms==DEPARTURE_TERMS:return terms+RESIGNATION_TERMS
    return terms

def event_polarities(text,terms):
    """Local explicit negation guard; intentionally not a full natural-language parser."""
    from .language import simplified
    text=simplified(text);states=set()
    for term in terms:
        pattern=re.escape(term)
        if term.isascii():pattern=r'(?<![A-Za-z])'+pattern+r'(?![A-Za-z])'
        for match in re.finditer(pattern,text,re.I):
            prefix=re.split(r'[。！？;；.!?\n,，]',text[:match.start()])[-1][-80:]
            negative=bool(re.search(r"(?:\b(?:not|never|no)|n.t)\s+(?:[A-Za-z]+\s+){0,2}$|(?:没有|并非|从未|未曾|并未|未有|不曾|不再|并不|未见|未|不)(?![来断论仅管])[^，。；;.!?\n]{0,10}$",prefix,re.I))
            states.add(negative)
    return states

def unsupported_claims(answer,citations):
    """Reject source-kind and explicit claim-feature mismatches, not prove full entailment."""
    from .language import simplified
    answer=simplified(answer)
    indexed={str(c['ordinal']):c for c in citations}
    for passage in cited_passages(answer):
        refs=citation_markers(passage,citations)
        sources=[indexed[r] for r in refs if r in indexed]
        if re.search(r'没有(?:可核验)?(?:资料|证据)|无法(?:核实|确认)|no (?:verified )?evidence',passage,re.I):continue
        if not sources:
            if any(event_polarities(passage,terms) for terms in CLAIM_FEATURES[2:]):return True
            continue
        evidence=simplified(' '.join(str(c.get('quote','')) for c in sources))
        for terms in CLAIM_FEATURES:
            if any(contains(t,passage) for t in terms) and not any(contains(t,evidence) for t in supporting_event_terms(terms)):return True
        for terms in CLAIM_FEATURES[2:]:
            claim_polarity=event_polarities(passage,terms)
            if claim_polarity and not claim_polarity<=event_polarities(evidence,supporting_event_terms(terms)):return True
    return False


# A claim is only factual when it states an amount, a rate or a named event. A bare digit, a year,
# a count of results and a form label ("10-K") are none of them, so they must not each demand a
# citation: that rule used to reject most ordinary sentences and turn good turns into partial ones.
_CLAIM_AMOUNT = re.compile(
    r'\d[\d,.]*\s*(?:万亿|千万|百万|亿|万|千|trillion|billion|million|thousand|bn|mn|b\b|k\b)'
    r'|\d[\d,.]*\s*(?:%|％|美元|元|dollars?|USD)', re.I)


# A passage that reports what was NOT found states no fact, so it must not be asked for a
# citation: "本次检索没有取得可核验资料" and "两次均未返回可引用的营收来源" are honest reports of an
# absence.  The old two-form pattern recognised almost none of them, so those sentences were deleted
# and an otherwise good turn ended as partial.
_ABSENCE = re.compile(
    r'没有[^。！？；\n]{0,12}?(?:可核验|可靠|可用)?[^。！？；\n]{0,8}?(?:资料|证据|来源|数据|记录|结果|内容)'
    r'|(?:未|没能|均未|都未|全部未)(?:能)?(?:取得|找到|检索到|返回|核实|确认|读取)'
    r'|无法(?:核实|确认|取得|读取|验证)'
    r'|查无|暂无'
    r'|no (?:verified |matching )?(?:evidence|data|sources?|records?|results?)', re.I)


def reports_absence(text):
    """Whether this passage reports that nothing was found, rather than asserting a fact."""
    return bool(_ABSENCE.search(text))


def factual_claim(text):
    """Whether this passage states a fact that needs a source.

    A stated amount or rate needs one; so does a named event (an acquisition, an appointment).
    Merely naming a metric does not: "本次没有取得可核验的营收资料" mentions revenue while stating
    that nothing was found, and demanding a citation for it used to delete the honest sentence and
    end an otherwise good turn as partial.
    """
    if _CLAIM_AMOUNT.search(text):return True
    if any(any(contains(k, text) for k in group) for group in CLAIM_FEATURES):return True
    return any(any(contains(t, text) for t in terms) for terms in METRIC_GROUPS.values())


def reviewed_passages(answer,citations,*,require_citations=False,check_claims=True):
    """Diagnostics apply to affected passages, never erase other verified content."""
    accepted=[];issues=[]
    for passage in cited_passages(answer):
        stripped = passage.strip()
        if not stripped:continue
        uncertainty=reports_absence(stripped)
        is_leadin = bool(re.match(r'^(?:以下|根据|综上|总结|如上|经查询|查询结果|核验结果|提示|注意|说明|确认后|请核对|已为您|请注意)[^。！？\n]{0,35}[：:\n]?', stripped)) or (len(stripped) < 35 and stripped.endswith(('：', ':')))
        # A heading names what follows ("## 二、营收对比", "**单季营收（FY2026 Q3）**"); one that states no
        # amount asserts nothing to cite.
        is_heading = bool(re.match(r'^#{1,6}\s',stripped) or re.fullmatch(r'\*\*[^*]+\*\*\s*[：:]?',stripped))
        has_factual_content = factual_claim(stripped)
        code=('invalid_citation' if citation_errors(stripped,citations) else
              'missing_citation' if require_citations and has_factual_content and not citation_markers(stripped,citations) and not uncertainty and not ((is_leadin or is_heading) and not _CLAIM_AMOUNT.search(stripped)) and not (stripped.endswith(('：',':')) and not _CLAIM_AMOUNT.search(stripped)) else
              'unsupported_numeric_claim' if check_claims and unsupported_numbers(stripped,citations) else
              'unsupported_claim' if check_claims and unsupported_claims(stripped,citations) else None)
        if code:issues.append(code)
        else:accepted.append(stripped)
    return accepted,issues


_ATOMIC_LINE = re.compile(r'^[|>#]|^[-*+]\s|^\d+[.)]\s')
_TABLE_RULE = re.compile(r'^\|?[\s:|-]+\|?$')
_BULLET = re.compile(r'^(?:[-*+]|\d+[.)])\s+')


def _table_citations(lines,citations):
    """For each table row that carries no [n] of its own, the markers its table states elsewhere.

    A table is cited as a unit: the caption above it, a note below it, a source column or another
    row carries the [n].  The row's figures are still checked against exactly those sources.
    """
    inherited={};index=0
    while index<len(lines):
        if not lines[index].strip().startswith('|'):index+=1;continue
        start=index
        while index<len(lines) and lines[index].strip().startswith('|'):index+=1
        before=next((lines[i] for i in range(start-1,-1,-1) if lines[i].strip()),'')
        after=next((lines[i] for i in range(index,len(lines)) if lines[i].strip()),'')
        markers=list(dict.fromkeys('['+n+']' for n in citation_markers(' '.join([before,*lines[start:index],after]),citations)))
        for row in range(start,index):
            if markers and not citation_markers(lines[row],citations):inherited[row]=' '+''.join(markers)
    return inherited


def _reviewable_lines(answer,citations):
    """Each line to check, with the text it is checked as; table headers and rules are not claims."""
    lines=answer.splitlines();inherited=_table_citations(lines,citations);out=[]
    index=0
    while index<len(lines):
        if not lines[index].lstrip().startswith('>'):index+=1;continue
        start=index
        while index<len(lines) and lines[index].lstrip().startswith('>') and lines[index].lstrip(' >').strip():index+=1
        if index==start:index+=1;continue
        # A citation at the end of one quoted paragraph covers that paragraph. An internal
        # marker, another paragraph, or a source outside the quote has no such scope.
        trailing=re.search(r'((?:\[\d+\]\s*)+)[。.!！?？]*\s*$',literal_titles(lines[index-1],citations))
        if trailing:
            for row in range(start,index):
                if not citation_markers(lines[row],citations):inherited[row]=' '+trailing[1]
    for index,line in enumerate(lines):
        stripped=line.strip()
        if not stripped or _TABLE_RULE.match(stripped):out.append((line,stripped,None));continue
        following=lines[index+1].strip() if index+1<len(lines) else ''
        if stripped.startswith('|') and '-' in following and _TABLE_RULE.match(following):
            # A header names columns ("| 期间 | 全年营业收入 |"); it states nothing to cite.
            out.append((line,stripped,None));continue
        out.append((line,stripped,stripped+inherited.get(index,'')))
    return out


def reviewed_answer(answer,citations,*,require_citations=False,check_claims=True):
    """Drop only what cannot be supported, line by line, so the answer keeps its formatting.

    ``reviewed_passages`` reads sentences; the answer is written in lines.  A rejected passage used
    to be handled by splitting the whole answer and rejoining the survivors with '。', which turned a
    Markdown table's separator row into '| --- | --- |。' and broke the table.  Here the line is the
    unit: a table row, list item, heading or quote is dropped whole (its syntax cannot survive half a
    sentence), and a prose line keeps the sentences that passed.
    """
    kept=[];issues=[]
    for line,stripped,checked in _reviewable_lines(answer,citations):
        if checked is None:
            kept.append(line);continue
        # A list marker is not a sentence ("1." would otherwise split off as one).
        bullet=_BULLET.match(stripped)
        accepted,line_issues=reviewed_passages(checked[bullet.end():] if bullet else checked,citations,require_citations=require_citations,check_claims=check_claims)
        if not line_issues:
            kept.append(line);continue
        issues.extend(line_issues)
        if bullet and accepted:
            # A list item survives losing a sentence: keep its marker and the sentences that passed.
            kept.append(bullet.group(0)+'。'.join(accepted));continue
        if _ATOMIC_LINE.match(stripped):continue
        if accepted:kept.append('。'.join(accepted))
    text='\n'.join(kept).strip()
    return text,issues


def rejected_lines(answer,citations,*,require_citations=False,check_claims=True):
    """The lines ``reviewed_answer`` drops or trims, each with its first reason, for the repair."""
    out=[]
    for _,stripped,checked in _reviewable_lines(answer,citations):
        if checked is None:continue
        bullet=_BULLET.match(stripped)
        _,issues=reviewed_passages(checked[bullet.end():] if bullet else checked,citations,require_citations=require_citations,check_claims=check_claims)
        if issues:out.append((stripped,issues[0]))
    return out


# Derived rates are not free rewrites of a raw cell. Keyword list stays small and fixture-driven.
_DERIVED_CLAIM = re.compile(
    r"growth|margin|yoy|year-over-year|同比|增长率|毛利率|净利率|差额|增速",
    re.I,
)


class ClaimGateResult:
    """Accepted claims plus drop issues. Iterates as ``(accepted, issues)``."""

    def __init__(self, accepted, issues, audit):
        self.accepted = accepted
        self.issues = issues
        self.audit = audit

    def __iter__(self):
        yield self.accepted
        yield self.issues


def reviewed_claims(claims, citations, source_bodies=None, *, min_chars=24):
    """Drop claims whose quote or field evidence does not bind to the cited source.

    Numbers are checked on the resolved source span (and header walk-up), never on
    the model-copied quote. Quote miss and unverified binds are drops, not pending.
    ``reviewed_answer`` is intentionally not called from here.
    """
    from dataclasses import replace

    from .claim_numeric import field_evidence_matches, header_context, numbers_supported
    from .claim_units import ClaimParseError, ClaimUnit, FieldEvidence, QuoteEvidence, parse_claims
    from .quote_bind import bind_quote

    units = _claim_units(claims, parse_claims, ClaimUnit, ClaimParseError)
    indexed = {}
    for citation in citations or []:
        if isinstance(citation, dict) and citation.get("ordinal") is not None:
            try:
                indexed[int(citation["ordinal"])] = citation
            except (TypeError, ValueError):
                continue
    bodies = source_bodies or {}
    accepted = []
    issues = []
    audit = {
        "claim_count": len(units),
        "accepted_count": 0,
        "quote_match_kinds": [],
        "resolved_span_hashes": [],
        "drop_reasons": [],
        "source_body_fallback": [],
    }
    for claim in units:
        citation = indexed.get(claim.citation_ordinal)
        if citation is None:
            _drop(issues, audit, claim, "bad_ordinal")
            continue
        derived = bool(_DERIVED_CLAIM.search(claim.statement)) and not _calculation_citation(citation)
        if isinstance(claim.evidence, FieldEvidence):
            if not field_evidence_matches(claim.evidence, citation):
                _drop(issues, audit, claim, "field_mismatch")
                continue
            material = _citation_material(citation)
            unsupported = numbers_supported(claim.statement, material)
            if unsupported:
                _drop(issues, audit, claim, "derived_requires_calculate" if derived else "unsupported_numeric", values=unsupported)
                continue
            accepted.append(claim)
            continue
        if not isinstance(claim.evidence, QuoteEvidence):
            _drop(issues, audit, claim, "field_mismatch")
            continue
        source, fallback = _source_body(bodies, claim.citation_ordinal, citation)
        if fallback:
            audit["source_body_fallback"].append(claim.citation_ordinal)
        bound = bind_quote(claim.evidence.evidence_quote, source, min_chars=min_chars)
        if not bound.ok:
            _drop(issues, audit, claim, bound.reason or "quote_not_found")
            continue
        context = header_context(source, bound.start_char)
        unsupported = numbers_supported(claim.statement, bound.resolved_quote, header_context=context)
        if unsupported:
            _drop(issues, audit, claim, "derived_requires_calculate" if derived else "unsupported_numeric", values=unsupported)
            continue
        audit["quote_match_kinds"].append(bound.match_kind)
        audit["resolved_span_hashes"].append(hashlib.sha256(bound.resolved_quote.encode()).hexdigest())
        accepted.append(replace(claim, evidence=replace(claim.evidence, evidence_quote=bound.resolved_quote)))
    audit["accepted_count"] = len(accepted)
    audit["drop_reasons"] = [item["reason"] for item in issues]
    return ClaimGateResult(accepted, issues, audit)


def _claim_units(claims, parse_claims, claim_type, error_type):
    if not claims:
        return []
    if isinstance(claims, dict):
        return parse_claims(claims)
    first = claims[0]
    if isinstance(first, claim_type):
        return list(claims)
    try:
        return parse_claims(claims)
    except error_type:
        raise


def _drop(issues, audit, claim, reason, **extra):
    issues.append({
        "id": claim.id,
        "reason": reason,
        "citation_ordinal": claim.citation_ordinal,
        "statement": claim.statement,
        **extra,
    })
    audit["drop_reasons"].append(reason)


def _calculation_citation(citation):
    return citation.get("source_type") in {"calculation", "calculate", "formula_calculation"} or isinstance(citation.get("calculation"), dict)


def _source_body(bodies, ordinal, citation):
    text = bodies.get(ordinal) if isinstance(bodies, dict) else None
    if isinstance(text, str) and text.strip():
        return text, False
    parts = [str(citation.get("quote") or "")]
    parts.extend(str(quote) for quote in citation.get("evidence_quotes") or [] if isinstance(quote, str) and quote)
    return "\n".join(part for part in parts if part), True


def _citation_material(citation):
    parts = [str(citation.get(key) or "") for key in ("quote", "figures", "title",
             "published_at", "observed_at", "filed_at", "analysis_id", "accession_number", "form")]
    fields = citation.get("fields")
    if isinstance(fields, dict):
        parts.append(json.dumps(fields, ensure_ascii=False, default=str))
    parts.extend(str(quote) for quote in citation.get("evidence_quotes") or [] if isinstance(quote, str) and quote)
    calculation = citation.get("calculation")
    if isinstance(calculation, dict):
        parts.append(json.dumps(calculation, ensure_ascii=False, default=str))
    return " ".join(parts)
