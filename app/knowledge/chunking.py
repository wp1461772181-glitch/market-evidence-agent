from __future__ import annotations
import hashlib, re, uuid
from typing import Protocol
from .sources import ChunkDraft, SourceDocument
CHUNKER_VERSION='markdown-sentence-v6'
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
# Plain-text SEC filings carry no markdown headings, so sentence windows used to
# drift across unrelated sections (an Item 7 paragraph could share a chunk with
# Item 8 tables).  The recognisers themselves live in app/sec_structure.py so the
# knowledge index and the analysis window can never disagree about where a
# section starts; the private aliases keep this module's call sites unchanged.
from ..sec_structure import (ALLCAPS_HEADING as _ALLCAPS_HEADING, ACRONYM as _ACRONYM,
    BARE_ITEM as _BARE_ITEM, ENTITY_NAME as _ENTITY_NAME, ITEM_HEADING as _ITEM_HEADING,
    NOISE_LINE as _NOISE_LINE, UNTRUSTED_TITLES as _UNTRUSTED_TITLES,
    heading as _heading, plausible_title as _plausible_title)
_TABLE_RULE=re.compile(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*')


def coverage_gaps(text: str, chunks: list[ChunkDraft]) -> list[tuple[int,int]]:
    """Regions of ``text`` that no chunk carries; whitespace-only gaps do not count.

    The chunker used to drop a flushed block whenever it looked like page
    furniture, so a filing could lose whole paragraphs without anything noticing.
    Every character (bar layout whitespace) must belong to some chunk, and this is
    how the tests and ``scripts/audit_chunking.py`` check it.
    """
    spans=sorted((chunk.start_offset,chunk.end_offset) for chunk in chunks if chunk.end_offset>chunk.start_offset)
    gaps=[]; cursor=0
    for start,end in spans:
        if start>cursor and text[cursor:start].strip(): gaps.append((cursor,start))
        cursor=max(cursor,end)
    if cursor<len(text) and text[cursor:].strip(): gaps.append((cursor,len(text)))
    return gaps


def build_chunks(document: SourceDocument, tokenizer: Tokenizer, *, target_tokens: int=512, overlap_tokens: int=64, max_tokens: int=768) -> list[ChunkDraft]:
    if target_tokens<1 or overlap_tokens<0 or max_tokens<target_tokens: raise ValueError('invalid chunk token limits')
    lines=document.text.splitlines(keepends=True); positions=[]; offset=0
    for line in lines: positions.append((offset,offset+len(line),line.rstrip('\r\n'))); offset+=len(line)
    chunks=[]; headings=[]; active=[]; category_base=document.category or document.source_type
    # Text consumed as a heading, a table header or a running header but not yet
    # carried by any chunk.  Prepending it to the next chunk keeps every character
    # of the filing inside the index, so a missed heading can only mislabel a
    # region -- it can never drop the balance sheet out of retrieval.
    pending=[]; own_title=document.title or ''
    def metadata():
        question=document.questions or (headings[-1][1] if headings else document.title)
        category=' > '.join(([category_base] if category_base else [])+[x[1] for x in headings[:-1]]) or category_base
        return category,question
    def _raw_add(answer,start,end,content_type='text',needs_review=False,question_override=None):
        category,question=metadata(); question=question_override or question; emb=embedding_text(category,question,answer)
        review=needs_review or _token_count(tokenizer,emb)>max_tokens
        ordinal=len(chunks); identity=f'{document.source_table}|{document.source_id}|{document.source_version}|{CHUNKER_VERSION}|{FORMAT_VERSION}|{ordinal}'
        chunks.append(ChunkDraft(str(uuid.uuid5(_NAMESPACE,identity)),ordinal,category,question,answer,emb,hashlib.sha256(answer.encode()).hexdigest(),start,end,review,content_type,' > '.join(x[1] for x in headings)))
    def _emit_pending():
        if not pending: return
        items=pending[:]; pending.clear()
        text=' '.join(item[2].strip() for item in items if item[2].strip())
        if text: _raw_add(text,items[0][0],max(item[1] for item in items))
    def add(answer,start,end,content_type='text',needs_review=False,question_override=None):
        if pending:
            # Only a run that ends exactly where this chunk starts may become its
            # prefix; otherwise the span would claim characters the answer does not
            # contain and the offsets would no longer describe the filing.
            if pending[-1][1]<=start and not document.text[pending[-1][1]:start].strip():
                text=' '.join(item[2].strip() for item in pending if item[2].strip())
                first=pending[0][0]; pending.clear()
                if text:
                    answer=f'{text} {answer.strip()}' if answer.strip() else text
                    start=first
            else:
                _emit_pending()
        _raw_add(answer,start,end,content_type,needs_review,question_override)
    def _noise(answer: str) -> bool:
        """Page numbers and running headers carry no meaning worth embedding."""
        letters=sum(c.isalpha() or '\u4e00'<=c<='\u9fff' for c in answer)
        if letters<8: return True
        return bool(_NOISE_LINE.match(answer.strip()))
    def flush():
        nonlocal active
        if active:
            answer=' '.join(x[2] for x in active)
            if _noise(answer):
                # Keep the text instead of dropping it: it rides along as the
                # prefix of the next real chunk and only stands alone when a
                # whole page of furniture collects (a cover page, page numbers).
                pending.extend(active)
                if sum(len(item[2]) for item in pending)>200: _emit_pending()
            else:
                add(answer,active[0][0],active[-1][1])
            active=[]
    def _blank(index: int) -> bool:
        return not (0<=index<len(positions) and positions[index][2].strip())
    def _text_follows(index: int) -> bool:
        return any(not _blank(ahead) for ahead in range(index+1,min(len(positions),index+4)))
    i=0; preceding_context=''
    while i<len(positions):
        start,end,line=positions[i]
        if not line.strip(): i+=1; continue
        heading=_heading(line,previous_blank=_blank(i-1),next_nonblank=_text_follows(i),own_title=own_title)
        if heading is None and _BARE_ITEM.match(line.strip()):
            # A table of contents keeps the number alone and the title on the
            # next non-empty line; joining them restores the real heading.
            for ahead in range(i+1,min(len(positions),i+6)):
                title=positions[ahead][2].strip()
                if not title: continue
                if len(title)<=100 and not title.isdigit() and not _NOISE_LINE.match(title):
                    heading=(2,f'{line.strip()} {title}')
                    pending.append((positions[ahead][0],positions[ahead][1],title))
                break
        if heading is not None:
            flush(); level,title=heading
            pending.append((start,end,line.strip()))
            headings=[x for x in headings if x[0]<level]+[(level,title)]; i+=1; continue
        if line.lstrip().startswith('|'):
            preceding_context=' '.join(item[2] for item in active)
            flush(); table=[]
            while i<len(positions) and positions[i][2].lstrip().startswith('|'): table.append(positions[i]); i+=1
            rows=[x for x in table if not _TABLE_RULE.fullmatch(x[2])]
            header=rows[0][2] if len(rows)>1 else ''
            for row in table:
                row_start,row_end,text=row
                if _TABLE_RULE.fullmatch(text):
                    # The rule line has no content worth embedding, but its
                    # characters still belong to the table block, not to a gap.
                    pending.append((row_start,row_end,'')); continue
                if header and row is rows[0]:
                    # The header line belongs to the first row's chunk, so nothing
                    # disappears between the header and the data beneath it.
                    pending.append((row_start,row_end,text.strip())); continue
                cells=[cell.strip() for cell in text.strip().strip('|').split('|')]
                metric=cells[0] if cells else ''
                _,base_question=metadata()
                row_question=f'{base_question} — {metric}' if metric else base_question
                context=(preceding_context+'\n') if preceding_context else ''
                if header and text!=header:
                    add(f'{context}{header}\n{text}',row_start,row_end,'table_row',question_override=row_question)
                else:
                    add(context+text,row_start,row_end,'table_row',question_override=row_question)
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
    flush(); _emit_pending()
    return chunks
