"""Copied conversational referents and bounded diagnostics, never write authority."""
import copy,hashlib,json,re,unicodedata
from pydantic import BaseModel,ConfigDict,Field

def normalized(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(text))).strip()

class Referent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mention:str=Field(min_length=1,max_length=100)
    text:str=Field(default='',max_length=400)
    item_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{20}$')
    source:str=Field(pattern=r'^(assistant_previous|assistant_message:[a-f0-9]{20}|user_history|result_set:[A-Za-z0-9_-]{1,64})$')

def _is_assistant_text_message(m):
    if m.get('role') != 'assistant' or not m.get('content') or m.get('status') in {'failed', 'cancelled'}:
        return False
    tc = m.get('tool_calls')
    if tc and isinstance(tc, dict) and tc.get('calls'):
        return False
    return True

def assistant_message_sources(messages):
    sources={}
    for position,m in enumerate(messages):
        if not _is_assistant_text_message(m):continue
        content=str(m['content'])
        identity=hashlib.sha256(json.dumps([position,content],ensure_ascii=False).encode()).hexdigest()[:20]
        sources['assistant_message:'+identity]=content
    return sources

def assistant_item_catalogue(messages):
    catalogue=[]
    for source,content in assistant_message_sources(messages).items():
        items=[]
        for line in content.splitlines():
            line_str=line.strip()
            if not line_str:continue
            match=re.match(r'^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:(?:\d{1,3}[.、)）]|[（(]\d{1,3}[)）])|[-*•·])\s*(.+)',line_str)
            if match:
                text=match.group(1).strip().strip('*').strip()[:400]
            else:
                match_title=re.match(r'^\s*(?:注[：:])?\s*(?:(?:\*\*(.+?)\*\*)|(?:《(.+?)》)|(?:[“"](.+?)[”"])|(?:#{1,6}\s*(.+)))',line_str)
                if match_title:
                    text=next((g for g in match_title.groups() if g),None)
                    if text: text=text.strip()[:400]
                    else: continue
                else: continue
            identity=hashlib.sha256(json.dumps([source,len(items),text],ensure_ascii=False).encode()).hexdigest()[:20]
            items.append({'position':len(items)+1,'item_id':identity,'text':text})
        if items:catalogue.append({'source':source,'items':items})
    return catalogue

def validate_referents(referents,original,messages,sets):
    previous=next((str(m.get('content') or '') for m in reversed(messages) if _is_assistant_text_message(m)), '')
    users='\n'.join(str(m.get('content','')) for m in messages if m.get('role')=='user')
    earlier=assistant_message_sources(messages)
    catalogue=assistant_item_catalogue(messages)
    verified=[]
    for ref in referents:
        actual_source=next(reversed(earlier),'') if ref.source=='assistant_previous' else ref.source
        listing=next((entry['items'] for entry in catalogue if entry['source']==actual_source),[])
        if listing:
            from .semantic import ordinal_positions
            wanted=ordinal_positions(original,len(listing))
        else:wanted=None
        if ref.item_id:
            selected=next((item for entry in catalogue if entry['source']==ref.source for item in entry['items'] if item['item_id']==ref.item_id),None)
            if not selected:raise ValueError('reference_item_unbound')
            if wanted is not None and selected['position'] not in wanted:raise ValueError('reference_position_mismatch')
            verified.append({**ref.model_dump(),'text':selected['text']});continue
        if ref.source=='assistant_previous':source=previous
        elif ref.source.startswith('assistant_message:'):source=earlier.get(ref.source,'')
        elif ref.source=='user_history':source=users
        else:
            saved=next((s for s in sets if s['id']==ref.source.split(':',1)[1]),None)
            source=json.dumps(saved['rows'],ensure_ascii=False,default=str) if saved else ''
        target_text=ref.text or ref.mention or ''
        source_norm=normalized(source)
        target_norm=normalized(target_text)
        mention_norm=normalized(ref.mention) if ref.mention else ''
        exact_found=(target_norm in source_norm) or (mention_norm and mention_norm in source_norm)
        tokens=[t for t in re.findall(r'[\u4e00-\u9fa5]{2,}|[A-Za-z0-9]{3,}',target_norm) if t.casefold() not in {'http','https','www','com'}]
        if not source or not target_text or (not exact_found and (not tokens or not any(t in source_norm for t in tokens))):
            raise ValueError('reference_text_unbound')
        ref_dict=ref.model_dump()
        if not ref_dict.get('text'):
            ref_dict['text']=target_text if exact_found else (ref.mention if mention_norm and mention_norm in source_norm else target_text)
        if wanted is not None:
            candidates=[item for item in listing if (target_norm in normalized(item['text'])) or any(t in normalized(item['text']) for t in tokens)]
            if not candidates or not any(item['position'] in wanted for item in candidates):raise ValueError('reference_position_mismatch')
        verified.append(ref_dict)
    return verified

def bounded_stage(value,key):
    """Keep each stage valid structured JSON <=400 chars, redact request credentials."""
    result=copy.deepcopy(value)
    def redact(item):
        if isinstance(item,str):return item.replace(key,'[redacted]') if key else item
        if isinstance(item,list):return [redact(v) for v in item]
        if isinstance(item,dict):return {k:redact(v) for k,v in item.items()}
        return item
    result=redact(result)
    def size():return len(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    for _ in range(50):
        if size()<=400:break
        strings=[];lists=[]
        def visit(item):
            if isinstance(item,dict):
                for k,v in item.items():
                    if isinstance(v,str) and len(v)>12:strings.append((item,k,v))
                    elif isinstance(v,list) and v:lists.append(v)
                    if isinstance(v,(dict,list)):visit(v)
            elif isinstance(item,list):
                for v in item:
                    if isinstance(v,(dict,list)):visit(v)
        visit(result)
        if strings:
            parent,name,text=max(strings,key=lambda v:len(v[2]))
            target=max(6,len(text)//2)
            if target>=len(text):target=max(0,len(text)-1)
            parent[name]=text[:target]+'…'
        elif lists:max(lists,key=len).pop()
        else:break
    return result
