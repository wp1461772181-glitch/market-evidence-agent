from __future__ import annotations
import hashlib, re, uuid
from typing import Protocol
from .sources import ChunkDraft, SourceDocument
CHUNKER_VERSION='markdown-sentence-v3'
FORMAT_VERSION='category-questions-answer-v1'
_NAMESPACE=uuid.UUID('d26df052-bc20-4d4d-8b9c-3156b7e9e31f')
class Tokenizer(Protocol):
    def encode(self, text: str, **kwargs) -> list[int]: ...
def embedding_text(category: str, questions: str, answer: str) -> str:
    def clean(s: str) -> str: return s.replace('\\','\\\\').replace('\r','\\r').replace('\n','\\n')
    return f'category: {clean(category)}\nquestions: {clean(questions)}\nanswer: {clean(answer)}'
def _token_count(tokenizer: Tokenizer, text: str) -> int:
    return len(tokenizer.encode(text, add_special_tokens=True))
def _sentences(text: str) -> list[tuple[int,int,str]]:
    # Protect common abbreviations and decimal points while preserving character offsets.
    protected=text
    for pattern in (r'\b(?:U\.S|U\.K|e\.g|i\.e|Inc|Corp|Ltd|Co|No|Fig|Mr|Mrs|Ms|Dr|Prof|vs)\.',r'(?<=\d)\.(?=\d)'):
        protected=re.sub(pattern,lambda match:match.group(0)[:-1]+'\ue000',protected,flags=re.I)
    out=[]; start=0
    for match in re.finditer(r'(?<=[。！？!?；;])\s*|(?<=[.!?])\s+(?=[A-Z0-9“"(\[])|\n+',protected):
        end=match.start(); part=protected[start:end].replace('\ue000','.')
        if part.strip():
            left=start+len(part)-len(part.lstrip()); right=start+len(part.rstrip()); out.append((left,right,text[left:right]))
        start=match.end()
    part=protected[start:].replace('\ue000','.')
    if part.strip():
        left=start+len(part)-len(part.lstrip()); right=start+len(part.rstrip()); out.append((left,right,text[left:right]))
    return out
def build_chunks(document: SourceDocument, tokenizer: Tokenizer, *, target_tokens: int=512, overlap_tokens: int=64, max_tokens: int=768) -> list[ChunkDraft]:
    if target_tokens<1 or overlap_tokens<0 or max_tokens<target_tokens: raise ValueError('invalid chunk token limits')
    lines=document.text.splitlines(keepends=True); positions=[]; offset=0
    for line in lines: positions.append((offset,offset+len(line),line.rstrip('\r\n'))); offset+=len(line)
    chunks=[]; headings=[]; active=[]; category_base=document.category or document.source_type
    def metadata():
        question=document.questions or (headings[-1][1] if headings else document.title)
        category=' > '.join(([category_base] if category_base else [])+[x[1] for x in headings[:-1]]) or category_base
        return category,question
    def add(answer,start,end,content_type='text',needs_review=False,question_override=None):
        category,question=metadata(); question=question_override or question; emb=embedding_text(category,question,answer)
        review=needs_review or _token_count(tokenizer,emb)>max_tokens
        ordinal=len(chunks); identity=f'{document.source_table}|{document.source_id}|{document.source_version}|{CHUNKER_VERSION}|{FORMAT_VERSION}|{ordinal}'
        chunks.append(ChunkDraft(str(uuid.uuid5(_NAMESPACE,identity)),ordinal,category,question,answer,emb,hashlib.sha256(answer.encode()).hexdigest(),start,end,review,content_type,' > '.join(x[1] for x in headings)))
    def flush():
        nonlocal active
        if active:
            add(' '.join(x[2] for x in active),active[0][0],active[-1][1])
            active=[]
    i=0; preceding_context=''
    while i<len(positions):
        start,end,line=positions[i]
        if not line.strip(): i+=1; continue
        if line.startswith('#'):
            flush(); level=len(line)-len(line.lstrip('#')); title=line[level:].strip()
            headings=[x for x in headings if x[0]<level]+[(level,title)]; i+=1; continue
        if line.lstrip().startswith('|'):
            preceding_context=' '.join(item[2] for item in active)
            flush(); table=[]
            while i<len(positions) and positions[i][2].lstrip().startswith('|'): table.append(positions[i]); i+=1
            rows=[x for x in table if not re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*',x[2])]
            header=rows[0][2] if len(rows)>1 else ''
            for row_start,row_end,row in rows[1:] if header else rows:
                cells=[cell.strip() for cell in row.strip().strip('|').split('|')]
                metric=cells[0] if cells else ''
                _,base_question=metadata()
                row_question=f'{base_question} — {metric}' if metric else base_question
                context=(preceding_context+'\n') if preceding_context else ''
                add(f'{context}{header}\n{row}' if header and row!=header else context+row,row_start,row_end,'table_row',question_override=row_question)
            continue
        preceding_context=''
        for s,e,text in _sentences(line):
            category,question=metadata(); candidate=active+[(start+s,start+e,text)]
            if active and _token_count(tokenizer,embedding_text(category,question,' '.join(x[2] for x in candidate)))>target_tokens:
                previous=active; flush(); overlap=[]; total=0
                for item in reversed(previous):
                    n=_token_count(tokenizer,item[2])
                    if total+n>overlap_tokens: break
                    overlap.insert(0,item); total+=n
                active=overlap
            active.append((start+s,start+e,text))
            category,question=metadata()
            if _token_count(tokenizer,embedding_text(category,question,' '.join(x[2] for x in active)))>max_tokens:
                previous=active
                if previous:
                    # Remove the just-added indivisible unit from the active buffer;
                    # the oversized source sentence is retained once as review-only.
                    active=previous[:-1]
                    flush()
                add(text,start+s,start+e,needs_review=True)
        i+=1
    flush()
    return chunks
