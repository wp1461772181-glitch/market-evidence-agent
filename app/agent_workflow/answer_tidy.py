"""Formatting cleanup after unsupported lines were dropped from an answer.

The source check removes a line whole (a table row, a list item, a sentence), so what stays can
be a table with only its header, a heading over nothing, or a lead-in ("公开报道披露：") whose
content was removed.  This only removes those frames; it never removes a line that states content.
"""
from __future__ import annotations
import re

_RULE=re.compile(r'^\|?[\s:|-]*-[\s:|-]*\|?$')
_NUMBERED=re.compile(r'^[一二三四五六七八九十]+、')


def _is_heading(line):
    """A line that frames content without stating any: a Markdown heading, a short bold label."""
    s=line.strip()
    if not s:return False
    if s.startswith('#'):return True
    if re.search(r'\[\d+\]',s):return False
    if _NUMBERED.match(s) and len(s)<=40:return True
    label=re.fullmatch(r'\*\*([^*]{1,30})\*\*([^*。！？.!?]{0,30})',s)
    return bool(label) and not re.search(r'[。！？.!?]',label.group(1))


def _is_lead_in(line):
    s=re.sub(r'\[\d+\]','',line.strip())
    return bool(s) and not s.startswith('|') and len(s)<=80 and s.endswith(('：',':'))


def _drop_header_only_tables(lines):
    out=[];i=0
    while i<len(lines):
        if lines[i].strip().startswith('|'):
            j=i
            while j<len(lines) and lines[j].strip().startswith('|'):j+=1
            block=lines[i:j]
            rules=[k for k,row in enumerate(block) if _RULE.match(row.strip())]
            if not(rules and rules[0]==len(block)-1):out.extend(block)
            i=j;continue
        out.append(lines[i]);i+=1
    return out


def _next_content(lines,index):
    for line in lines[index+1:]:
        if line.strip():return line
    return None


def tidy_answer(text: str) -> str:
    lines=_drop_header_only_tables(text.split('\n'))
    lines=[re.sub(r'^(\s*)\*\*\s*',r'\1',line) if line.count('**')%2 else line for line in lines]
    lines=[line for line in lines if line.strip()!='**']
    changed=True
    while changed:
        changed=False
        for index,line in enumerate(lines):
            if not line.strip():continue
            following=_next_content(lines,index)
            if (_is_heading(line) or _is_lead_in(line)) and (following is None or _is_heading(following)):
                del lines[index];changed=True;break
    cleaned='\n'.join(lines)
    cleaned=re.sub(r'\n{3,}','\n\n',cleaned)
    return cleaned.strip()
