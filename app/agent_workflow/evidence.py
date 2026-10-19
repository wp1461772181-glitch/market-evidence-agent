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


def cited_passages(answer):
    # A title in 《》 is one name even when it asks a question ("……哪一只更值得买入？"): its punctuation
    # does not end the sentence that cites it.
    protected=re.sub(r'《[^》\n]{0,300}》',lambda m:m.group(0).translate(_TITLE_PUNCTUATION),answer)
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


def gate_row(question: str, row: dict, *, symbol: str | None=None, company: str | None=None,
             metrics: list[str] | None=None, period: str | None=None, days_back: int | None=None,
             official_only: bool=False, now: datetime | None=None, entity_aliases: list[str] | None=None,
             soft: bool=False) -> tuple[bool,str]:
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
        if key=='price' and 'close' in row:
            import math
            try:
                if not isinstance(row['close'],bool) and math.isfinite(float(row['close'])) and float(row['close'])>=0:continue
            except (TypeError,ValueError):pass
            return _gate_outcome(False,'requested_metric_missing:price',soft)
        if key=='date' and any(row.get(field) for field in ('filed_at','published_at','trading_date','decision_at')):continue
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
    if not period_match(requested,body+' '+str(row.get('report_period') or '')+' '+str(row.get('filed_at') or row.get('trading_date') or row.get('decision_at') or '')):
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
        refs=re.findall(r'\[(\d+)\]',passage)
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
        refs=re.findall(r'\[(\d+)\]',passage)
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
              'missing_citation' if require_citations and has_factual_content and not re.search(r'\[\d+\]',stripped) and not uncertainty and not ((is_leadin or is_heading) and not _CLAIM_AMOUNT.search(stripped)) and not (stripped.endswith(('：',':')) and not _CLAIM_AMOUNT.search(stripped)) else
              'unsupported_numeric_claim' if check_claims and unsupported_numbers(stripped,citations) else
              'unsupported_claim' if check_claims and unsupported_claims(stripped,citations) else None)
        if code:issues.append(code)
        else:accepted.append(stripped)
    return accepted,issues


_ATOMIC_LINE = re.compile(r'^[|>#]|^[-*+]\s|^\d+[.)]\s')
_TABLE_RULE = re.compile(r'^\|?[\s:|-]+\|?$')
_BULLET = re.compile(r'^(?:[-*+]|\d+[.)])\s+')


def _table_citations(lines):
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
        markers=list(dict.fromkeys(re.findall(r'\[\d+\]',' '.join([before,*lines[start:index],after]))))
        for row in range(start,index):
            if markers and not re.search(r'\[\d+\]',lines[row]):inherited[row]=' '+''.join(markers)
    return inherited


def _reviewable_lines(answer):
    """Each line to check, with the text it is checked as; table headers and rules are not claims."""
    lines=answer.splitlines();inherited=_table_citations(lines);out=[]
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
    for line,stripped,checked in _reviewable_lines(answer):
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
    for _,stripped,checked in _reviewable_lines(answer):
        if checked is None:continue
        bullet=_BULLET.match(stripped)
        _,issues=reviewed_passages(checked[bullet.end():] if bullet else checked,citations,require_citations=require_citations,check_claims=check_claims)
        if issues:out.append((stripped,issues[0]))
    return out
