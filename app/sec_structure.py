"""Section recognition for plain-text SEC filings.

Two consumers share this knowledge: the knowledge chunker, which must keep a
chunk inside one section, and the analysis window, which must spend its
character budget on the sections that answer "how is this company doing".
Both used to guess independently, so a fix in one never reached the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# Plain-text SEC filings carry no markdown headings.  Real filings write
# "Item 8.Financial Statements" with no space after the number, and a table of
# contents puts "Item 8." alone with the title on the next line, so the
# separator is optional and the bare form is handled by the caller.
ITEM_HEADING = re.compile(r'^(?:item|part)\s+(?:\d{1,2}[a-c]?|[ivx]{1,4})\.?\s*\S.{0,110}$', re.I)
BARE_ITEM = re.compile(r'^(item|part)\s+(\d{1,2}[a-c]?|[ivx]{1,4})\.?$', re.I)
NOISE_LINE = re.compile(r'(?i)^(?:table of contents|contents)$')
ALLCAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,.'&/()\-:]{6,110}$")
# Every capitalised line that is not an Item looks like a title, so the
# registrant's name, the Commission's banner and an officer's job title used to
# become section names for hundreds of chunks.  These never make a heading.
UNTRUSTED_TITLES = frozenset({'securities and exchange commission', 'united states', 'table of contents', 'contents',
                              'washington d c 20549', 'washington, d.c. 20549'})
ACRONYM = re.compile(r"^[A-Z0-9&.'-]{1,5}$")
# A bare company name on its own line is a running header, not a section title.
ENTITY_NAME = re.compile(r'(?i)\b(inc|corp|corporation|company|co|llc|l\.l\.c|plc|ltd|limited|holdings|group|nv|n\.v|sa|s\.a|ag)\b\.?$')

_ITEM_CODE = re.compile(r'^(?:item|part)\s+([0-9]{1,2}[a-c]?)\b', re.I)
_SENTENCE_END = re.compile(r'[.;:,]$')


def plausible_title(text: str, *, previous_blank: bool, next_nonblank: bool, own_title: str) -> bool:
    """A capitalised line is a heading only when the document itself says so: it
    stands alone between blank lines, reads like a title rather than a sentence,
    and is not the registrant or the Commission talking about themselves."""
    if not previous_blank or not next_nonblank: return False
    if any(c.isdigit() for c in text): return False
    if sum(c.isalpha() for c in text) < 6: return False
    lowered = ' '.join(text.lower().split())
    if lowered in UNTRUSTED_TITLES: return False
    if own_title:
        own = ' '.join(own_title.lower().split()).replace('.', '')
        if own and lowered.replace('.', '') in own: return False
    words = [word for word in text.split() if word]
    if not 2 <= len(words) <= 10: return False
    # An all-caps line made mostly of short capitalised tokens is a byline, a job
    # title or a ticker banner ("EVP & CFO", "LDICC NCGC EC"), not a section.
    if sum(1 for word in words if ACRONYM.match(word)) / len(words) >= 0.6: return False
    # The registrant naming itself is page furniture, not a section.
    if len(words) <= 4 and ENTITY_NAME.search(text): return False
    return True


def heading(line: str, *, previous_blank: bool = True, next_nonblank: bool = True,
            own_title: str = '') -> tuple[int, str] | None:
    text = ' '.join(line.split())
    if not text or len(text) > 120: return None
    if text.startswith('#'):
        level = len(text) - len(text.lstrip('#')); title = text[level:].strip()
        return (level, title) if title else None
    if ITEM_HEADING.match(text) and not NOISE_LINE.match(text):
        # Filings separate "Item" and its number with a thin space or a tab, and
        # a label must not carry that layout character into retrieval paths.
        label = re.sub(r'^((?:item|part)\s+\S+?\.)\s*', lambda m: m.group(1) + ' ', text, count=1, flags=re.I)
        return (2, label if not text.isupper() else label.title())
    if ALLCAPS_HEADING.match(text) and plausible_title(text, previous_blank=previous_blank,
                                                       next_nonblank=next_nonblank, own_title=own_title):
        return (3, text.title())
    return None


@dataclass(frozen=True)
class Section:
    """A heading and every character up to the next heading of the same level."""

    title: str
    start: int
    end: int
    level: int
    ordinal: int

    @property
    def code(self) -> str:
        match = _ITEM_CODE.match(self.title)
        return match.group(1).lower() if match else ''

    @property
    def characters(self) -> int:
        return max(0, self.end - self.start)


def _title_after(lines: list[tuple[int, str]], index: int) -> str:
    """The title printed under a bare "Item 8." line, if the next lines hold one."""
    for offset in range(index, min(index + 5, len(lines))):
        candidate = ' '.join(lines[offset][1].split())
        if not candidate: continue
        if len(candidate) > 110 or sum(c.isalpha() for c in candidate) < 6: return ''
        if _SENTENCE_END.search(candidate) or candidate.endswith('.'): return ''
        words = candidate.split()
        if len(words) < 2 and sum(c.isalpha() for c in candidate) < 5: return ''
        if ' '.join(candidate.lower().split()) in UNTRUSTED_TITLES: return ''
        return candidate
    return ''


def sections(text: str, *, own_title: str = '') -> list[Section]:
    """Every heading in ``text`` with the character range it governs."""
    if not text: return []
    lines: list[tuple[int, str]] = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        lines.append((offset, raw.rstrip('\n').rstrip('\r'))); offset += len(raw)
    found: list[tuple[int, int, str]] = []
    for index, (start, line) in enumerate(lines):
        stripped = line.strip()
        if not stripped: continue
        previous_blank = index == 0 or not lines[index - 1][1].strip()
        next_nonblank = index + 1 < len(lines)
        result = heading(stripped, previous_blank=previous_blank, next_nonblank=next_nonblank, own_title=own_title)
        if result is None:
            bare = BARE_ITEM.match(' '.join(stripped.split()))
            if bare is not None:
                title = _title_after(lines, index + 1)
                if title: result = (2, f'Item {bare.group(2)}. {title}' if bare.group(1).lower() == 'item'
                                    else f'Part {bare.group(2)}. {title}')
        if result is not None:
            found.append((start, result[0], result[1]))
    out: list[Section] = []
    for position, (start, level, title) in enumerate(found):
        end = len(text)
        for later_start, later_level, _ in found[position + 1:]:
            if later_level <= level:
                end = later_start; break
        out.append(Section(title=title, start=start, end=end, level=level, ordinal=position))
    return out
