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


def cited_passages(answer):
    # Both “fact[1]。” and “fact。[1]” bind the marker to the same sentence.
    normalized=re.sub(r'([。！？.!?])(\s*(?:\[\d+\]\s*)+)',lambda m:m[2].strip()+m[1],answer)
    return re.split(r'[。！？\n]|(?<=[.!?])\s+',normalized)


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
    if any(y not in body for y in years):return False
    requested_pairs=period_pairs(request)
    if requested_pairs and not requested_pairs<=period_pairs(body):return False
    quarters=set(re.findall(r'Q([1-4])',request))
    if not quarters<=set(re.findall(r'Q([1-4])',body)):return False
    fiscal=set(re.findall(r'FY\s*((?:19|20)\d{2})',request))
    if fiscal and not fiscal<=set(re.findall(r'FY\s*((?:19|20)\d{2})',body)):return False
    return True


def gate_row(question: str, row: dict, *, symbol: str | None=None, company: str | None=None,
             metrics: list[str] | None=None, period: str | None=None, days_back: int | None=None,
             official_only: bool=False, now: datetime | None=None) -> tuple[bool,str]:
    kind=row.get('source_type','')
    web=kind in {'web','web_news'} or 'url' in row
    if row.get('fetch_error') or row.get('body_hash_valid') is False:return False,'invalid_source_body'
    if official_only and kind not in {'official_filing','saved_filing'}:return False,'official_source_required'
    body=' '.join(str(row.get(k) or '') for k in ('excerpt','content','summary','text','answer','research_brief_summary'))
    title=str(row.get('title') or '')
    if not body.strip() and not any(k in row for k in ('close','probabilities','status','form')):
        metric=next((key for key,terms in METRIC_GROUPS.items() if any(contains(t,question) for t in terms)),None)
        return False,('requested_metric_missing:'+metric if metric else 'zero_evidence')
    if symbol and row.get('symbol') and row['symbol'].upper()!=symbol.upper():return False,'requested_entity_missing'
    # Saved rows are verified by their query's symbol filter. Public pages must name the
    # entity in body text; a forged headline by itself is never sufficient.
    if web and (company or symbol) and not any(contains(x,body) for x in (company,symbol) if x):
        return False,'requested_entity_missing'
    if not web and symbol and not row.get('symbol') and not row.get('_verified_symbol') and not any(contains(x,body+' '+title) for x in (company,symbol) if x):
        return False,'requested_entity_missing'
    needed=metrics if metrics is not None else [key for key,terms in METRIC_GROUPS.items() if any(contains(t,question) for t in terms)]
    for key in needed:
        if key=='price' and 'close' in row:
            import math
            try:
                if not isinstance(row['close'],bool) and math.isfinite(float(row['close'])) and float(row['close'])>=0:continue
            except (TypeError,ValueError):pass
            return False,'requested_metric_missing:price'
        if key=='date' and any(row.get(field) for field in ('filed_at','published_at','trading_date','decision_at')):continue
        if key=='forecast' and row.get('probabilities'):continue
        terms=METRIC_GROUPS.get(key,(key,))
        passages=re.split(r'[。！？\n]|(?<=[.!?])\s+',body)
        relevant=[part for part in passages if any(contains(t,part) for t in terms) and (not web or not(company or symbol) or any(contains(x,part) for x in (company,symbol) if x))]
        if not relevant:return False,'requested_metric_missing:'+key
        if not any(period_match(period or question,part) for part in relevant):return False,'requested_period_missing'
    requested=period or question
    if not period_match(requested,body+' '+str(row.get('report_period') or '')+' '+str(row.get('filed_at') or row.get('trading_date') or row.get('decision_at') or '')):
        return False,'requested_period_missing'
    if days_back is not None and not published_within(row.get('published_at') or row.get('filed_at'),days_back,now or datetime.now(UTC)):
        return False,'requested_time_window_missing'
    query_without_url=re.sub(r'https://\S+',' ',question)
    from ..agent_web_research import _GENERIC_LATIN
    noise=set(_GENERIC_LATIN)|{'read','explain','summarize','summarise','verified','publication','dates','cite','citation','citations','briefly'}
    words=[t.casefold() for t in re.findall(r'[A-Za-z]{3,}',query_without_url) if t.casefold() not in noise]
    if not needed and not(company or symbol) and words and not any(contains(t,body) for t in words):return False,'weak_question_coverage'
    return True,'sufficient_evidence'


def deterministic_confidence_gate(question: str,result: dict,citations=None,verified_symbols=None,**requirements):
    if not isinstance(result,dict) or not result.get('ok'):return False,(result or {}).get('error_code') or 'retrieval_unavailable'
    rows=result.get('data')
    if not isinstance(rows,list) or not rows:return False,'zero_evidence'
    if not any(isinstance(r,dict) and (r.get('id') or r.get('source_id') or r.get('url') or r.get('source_url') or r.get('question') or 'close' in r) for r in rows):return False,'missing_source_coverage'
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


def citation_errors(answer: str,citations: list[dict]) -> list[int]:
    valid={int(c['ordinal']) for c in citations if type(c.get('ordinal')) is int}
    return [int(n) for n in re.findall(r'\[(\d+)\]',answer) if int(n) not in valid]


def number_rows(name: str,rows: list[dict],citations: list[dict],scope: dict) -> list[dict]:
    """Give accepted rows a citation without changing provenance or raw saved snapshots."""
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
                if not old:citations.append({**original_citation,'quote':original_citation.get('quote') or str(row.get('excerpt') or row.get('answer') or '')[:5000],'ordinal':number})
            for field in ('excerpt','answer','summary'):
                if isinstance(row.get(field),str):row[field]=re.sub(r'\[(\d+)\]',lambda m:'['+str(mapping.get(int(m[1]),int(m[1])))+']',row[field])
            row['citation']='['+str(next(iter(mapping.values())))+']'
            row['_citations']=[{**c,'ordinal':mapping[c['ordinal']]} for c in rich]
            accepted.append(row)
            continue
        kind=row.get('source_type') or ('web' if name=='web_research' else 'official_filing' if name=='query_sec_filings' else 'saved_price' if name=='query_stock_prices' else 'saved_forecast' if name=='query_latest_forecast' else 'ai_analysis' if 'analysis' in name else 'research_faq' if 'faq' in name else 'saved_material')
        body=str(row.get('excerpt') or row.get('answer') or row.get('summary') or json.dumps(row,ensure_ascii=False,default=str))
        key=hashlib.sha256(json.dumps([kind,row.get('id'),row.get('source_id'),row.get('url'),body],sort_keys=True,default=str).encode()).hexdigest()
        old=next((c for c in citations if c.get('evidence_key')==key),None)
        if old: ordinal=old['ordinal']
        else:
            ordinal=len(citations)+1
            citations.append({'ordinal':ordinal,'evidence_key':key,'chunk_id':row.get('chunk_id'),'document_id':row.get('document_id'),
                'source_id':str(row.get('source_id') or row.get('id') or ''),'question':row.get('question'),'source_type':kind,'tool_arguments':row.get('tool_arguments'),
                'title':str(row.get('title') or row.get('accession_number') or scope.get('company') or name)[:300],
                'source_url':row.get('source_url') or row.get('content_source_url') or row.get('url'),
                'published_at':row.get('published_at') or row.get('filed_at') or row.get('trading_date'),
                'observed_at':row.get('fetched_at') or row.get('observed_at'),'quote':body[:5000],
                'trading_date':row.get('trading_date'),'source':row.get('source'),'fetched_at':row.get('fetched_at'),
                'symbol':row.get('symbol') or scope.get('symbol'),'analysis_id':row.get('analysis_id'),'derived':kind=='ai_analysis','truncated':bool(row.get('truncated'))})
        if kind=='saved_price':citations[ordinal-1]['quote']=str(row['close'])
        row['citation']=f'[{ordinal}]';row['source_type']=kind
        accepted.append(row)
    return accepted


def unsupported_numbers(answer,citations):
    """Check cited quantitative claims without inferring facts or entity aliases.

    Unit-normalized amounts may be translated (10 billion / 100亿) and probabilities
    may be shown as percentages. This is a guard, not a semantic answer scorer.
    """
    from .semantic import figure_values
    from .language import simplified
    answer=simplified(answer)
    indexed={str(c['ordinal']):c for c in citations}
    for passage in cited_passages(answer):
        refs=re.findall(r'\[(\d+)\]',passage)
        body=' '.join(str(indexed[r].get('quote',''))+' '+str(indexed[r].get('published_at','')) for r in refs if r in indexed)
        allowed=figure_values(body)
        checked=re.sub(r'\[\d+\]','',passage)
        amounts=re.findall(r'\d+(?:\.\d+)?\s*(?:千万|百万|亿|万|千|trillion|billion|million|thousand|bn|mn)?\s*(?:美元|元|dollars?|USD|%|％)',checked,re.I)
        amounts+=re.findall(r'\$\s*\d+(?:\.\d+)?\s*(?:billion|million|bn|mn)?',checked,re.I)
        claim=figure_values(' '.join(amounts))
        if any(v not in allowed and not(v/100 in allowed and re.search(r'%|％|百分',passage)) for v in claim):return True
    return False

# Claim validation only. These concepts never route a request or choose a tool.
CLAIM_FEATURES=(
 ('CEO','chief executive','首席执行官','执行长'),
 ('CFO','chief financial','首席财务官','财务主管'),
 ('resign','resigned','resignation','stepped down','辞职','辞任','离任','卸任','退任'),
 ('appoint','appointed','appointment','named','任命','出任','上任'),
 ('bankruptcy','bankrupt','破产'),
 ('acquisition','acquire','acquired','收购'),
)

def event_polarities(text,terms):
    """Local explicit negation guard; intentionally not a full natural-language parser."""
    from .language import simplified
    text=simplified(text);states=set()
    for term in terms:
        pattern=re.escape(term)
        if term.isascii():pattern=r'(?<![A-Za-z])'+pattern+r'(?![A-Za-z])'
        for match in re.finditer(pattern,text,re.I):
            prefix=re.split(r'[。！？;；.!?\n,，]',text[:match.start()])[-1][-80:]
            negative=bool(re.search(r"(?:\b(?:not|never|no)|n.t)\s+(?:[A-Za-z]+\s+){0,2}$|(?:没有|并非|从未|未曾|未|不)[^，。；;.!?\n]{0,10}$",prefix,re.I))
            states.add(negative)
    return states

def unsupported_claims(answer,citations):
    """Reject source-kind and explicit claim-feature mismatches, not prove full entailment."""
    from .language import simplified
    answer=simplified(answer)
    indexed={str(c['ordinal']):c for c in citations}
    for passage in cited_passages(answer):
        refs=re.findall(r'\[(\d+)\]',passage)
        sources=[indexed[r] for r in refs if r in indexed]
        if re.search(r'没有(?:可核验)?(?:资料|证据)|无法(?:核实|确认)|no (?:verified )?evidence',passage,re.I):continue
        if not sources:
            if any(event_polarities(passage,terms) for terms in CLAIM_FEATURES[2:]):return True
            continue
        evidence=simplified(' '.join(str(c.get('quote','')) for c in sources))
        for terms in CLAIM_FEATURES:
            if any(contains(t,passage) for t in terms) and not any(contains(t,evidence) for t in terms):return True
        for terms in CLAIM_FEATURES[2:]:
            claim_polarity=event_polarities(passage,terms)
            if claim_polarity and not claim_polarity<=event_polarities(evidence,terms):return True
        if all(c.get('source_type')=='saved_price' for c in sources):
            price_terms=('股价','收盘','开盘','最高','最低','成交量','行情','价格','交易日','交易时间','抓取','来源','close','open','high','low','volume','price','date','saved','source')
            table_values='|' in passage and bool(re.search(r'\d',re.sub(r'\[\d+\]','',passage)))
            if not table_values and not any(contains(t,passage) for t in price_terms):return True
    return False
