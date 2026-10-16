"""Copied conversational referents and bounded diagnostics, never write authority."""
import copy,hashlib,json,re,unicodedata
from pydantic import BaseModel,ConfigDict,Field

def normalized(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(text))).strip()

class Referent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mention:str=Field(min_length=1,max_length=100)
    text:str=Field(min_length=1,max_length=400)
    source:str=Field(pattern=r'^(assistant_previous|assistant_message:[a-f0-9]{20}|user_history|result_set:[A-Za-z0-9_-]{1,64})$')

def assistant_message_sources(messages):
    sources={}
    for position,m in enumerate(messages):
        if m.get('role')!='assistant' or not m.get('content') or m.get('tool_calls') or m.get('status') in {'failed','cancelled'}:continue
        content=str(m['content'])
        identity=hashlib.sha256(json.dumps([position,content],ensure_ascii=False).encode()).hexdigest()[:20]
        sources['assistant_message:'+identity]=content
    return sources

def validate_referents(referents,original,messages,sets):
    previous=next((str(m.get('content') or '') for m in reversed(messages) if m.get('role')=='assistant' and m.get('content') and not m.get('tool_calls') and m.get('status') not in {'failed','cancelled'}), '')
    users='\n'.join(str(m.get('content','')) for m in messages if m.get('role')=='user')
    earlier=assistant_message_sources(messages)
    verified=[]
    for ref in referents:
        if ref.source=='assistant_previous':source=previous
        elif ref.source.startswith('assistant_message:'):source=earlier.get(ref.source,'')
        elif ref.source=='user_history':source=users
        else:
            saved=next((s for s in sets if s['id']==ref.source.split(':',1)[1]),None)
            source=json.dumps(saved['rows'],ensure_ascii=False,default=str) if saved else ''
        if not source or normalized(ref.text) not in normalized(source):raise ValueError('reference_text_unbound')
        verified.append(ref.model_dump())
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
    while size()>400:
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
            parent,name,text=max(strings,key=lambda v:len(v[2]));parent[name]=text[:max(12,len(text)//2)]+'…'
        elif lists:max(lists,key=len).pop()
        else:break
    return result
