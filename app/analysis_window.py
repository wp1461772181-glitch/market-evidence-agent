"""Choose which part of a filing an analysis may read.

A 10-K runs to a few hundred thousand characters; the model sees a fixed
character budget.  Taking that budget from the front of the document spends it
on the cover page, the table of contents and the forward-looking-statements
disclaimer, so the reader that was supposed to summarise the accounts never sees
an account.  This module spends the same budget on the sections that answer
"how is the company doing": management's discussion, the financial statements
and the risk factors, and reports exactly which character ranges it used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .sec_structure import Section, sections

# The budget is unchanged; only where it is spent changed.
MAX_ANALYSIS_CHARACTERS = 24_000
_STATEMENT_ANCHOR = re.compile(
    r'(?m)^\s*(?:CONDENSED\s+)?CONSOLIDATED\s+(?:BALANCE\s+SHEETS?|STATEMENTS?\s+OF\s+'
    r'(?:OPERATIONS|INCOME|CASH\s+FLOWS?|COMPREHENSIVE\s+(?:INCOME|LOSS)))', re.I)
_TOTAL_ASSETS = re.compile(r'(?m)^\s*Total\s+assets\b', re.I)
_ANCHOR_SEARCH_LIMIT = 60_000
# The balance sheet header repeats in the Item 8 index and in the notes, but only
# the statement itself totals its assets, so that line marks where to start.
_BALANCE_SHEET_LOOKBACK = 3_000
_MDA_TITLE = re.compile(r'(?i)management.{0,40}discussion|discussion.{0,40}analysis')
_FINANCIAL_TITLE = re.compile(r'(?i)financial statements')
_RISK_TITLE = re.compile(r'(?i)risk factors')
_BUSINESS_TITLE = re.compile(r'(?i)^item\s+1\.?\s+business', re.I)

# How the budget is split.  Leftover quota from a short or missing section is
# carried forward, so a filing without Item 7 still spends the whole budget.
_TARGETS: dict[str, tuple[tuple[str, int], ...]] = {
    '10-K': (('mda', 9_000), ('financials', 9_000), ('risk', 3_000), ('business', 3_000)),
    '10-Q': (('mda', 9_000), ('financials', 9_000), ('risk', 3_000), ('other', 3_000)),
    '20-F': (('mda', 9_000), ('financials', 9_000), ('risk', 3_000), ('business', 3_000)),
}
_GENERIC_TARGETS: tuple[tuple[str, int], ...] = (
    ('financials', 9_000), ('mda', 9_000), ('risk', 3_000), ('other', 3_000))


@dataclass(frozen=True)
class WindowSegment:
    title: str
    start: int
    end: int

    @property
    def characters(self) -> int:
        return max(0, self.end - self.start)


@dataclass(frozen=True)
class AnalysisWindow:
    text: str
    segments: tuple[WindowSegment, ...]
    skipped: tuple[str, ...]
    missing: tuple[str, ...]
    characters: int
    coverage: float
    complete: bool
    mode: str = 'whole'

    def locator(self) -> dict:
        return {
            'kind': 'extracted_text_sections',
            'start': self.segments[0].start if self.segments else 0,
            'end': self.segments[-1].end if self.segments else 0,
            'segments': [{'title': segment.title, 'start': segment.start, 'end': segment.end}
                         for segment in self.segments],
            'sections': [segment.title for segment in self.segments],
        }


def _kind(section: Section, form: str) -> str:
    code = section.code
    if form == '10-Q':
        return {'2': 'mda', '1': 'financials', '1a': 'risk'}.get(code, 'other')
    if code == '7': return 'mda'
    if code == '8': return 'financials'
    if code == '1a': return 'risk'
    if code == '1': return 'business'
    if code: return 'other'
    title = section.title
    if _MDA_TITLE.search(title): return 'mda'
    if _FINANCIAL_TITLE.search(title): return 'financials'
    if _RISK_TITLE.search(title): return 'risk'
    if _BUSINESS_TITLE.search(title): return 'business'
    return 'other'


def _statement_start(text: str, section: Section, *, want_statements: bool) -> int:
    """The balance sheet is what a reader wants first, not the auditor's report."""
    if not want_statements: return section.start
    limit = min(section.end, section.start + _ANCHOR_SEARCH_LIMIT)
    total = _TOTAL_ASSETS.search(text, section.start, limit)
    if total is not None:
        return max(section.start, total.start() - _BALANCE_SHEET_LOOKBACK)
    match = _STATEMENT_ANCHOR.search(text, section.start, limit)
    return match.start() if match else section.start


def build_analysis_window(text: str, *, form: str = '', budget: int = MAX_ANALYSIS_CHARACTERS,
                          own_title: str = '') -> AnalysisWindow:
    """Spend ``budget`` characters on the sections that carry the numbers."""
    if not text: return AnalysisWindow('', (), (), (), 0, 0.0, True, 'whole')
    if len(text) <= budget:
        return AnalysisWindow(text, (WindowSegment('whole document', 0, len(text)),), (), (), len(text), 1.0, True, 'whole')
    found = sections(text, own_title=own_title)
    plan = _TARGETS.get(form.upper(), _GENERIC_TARGETS)
    if not found:
        return AnalysisWindow(text[:budget], (WindowSegment('document start', 0, budget),),
                              (), tuple(kind for kind, _ in plan), budget, budget / len(text), False, 'prefix')
    used: set[int] = set(); segments: list[WindowSegment] = []; remaining = budget; found_kinds: set[str] = set()
    for want, quota in plan:
        if remaining <= 0: break
        share = min(quota, remaining)
        # A table of contents repeats every Item heading within a few hundred
        # characters of the next one; the body section that carries the numbers is
        # always the longest span for that Item.
        candidates = [item for item in found if item.ordinal not in used and _kind(item, form) == want]
        if not candidates: continue
        for section in [max(candidates, key=lambda item: (item.characters, -item.start))]:
            start = _statement_start(text, section, want_statements=want == 'financials')
            end = min(section.end, start + share)
            if end - start < 200: continue
            segments.append(WindowSegment(section.title, start, end)); used.add(section.ordinal)
            found_kinds.add(want); remaining -= end - start
    if remaining > 200:
        for section in found:
            if remaining <= 200: break
            if section.ordinal in used: continue
            start = _statement_start(text, section, want_statements=False)
            end = min(section.end, start + remaining)
            if end - start < 200: continue
            segments.append(WindowSegment(section.title, start, end)); used.add(section.ordinal)
            found_kinds.add(want); remaining -= end - start
    segments.sort(key=lambda segment: segment.start)
    if not segments:
        return AnalysisWindow(text[:budget], (WindowSegment('document start', 0, budget),),
                              tuple(section.title for section in found[:12]),
                              tuple(kind for kind, _ in plan), budget, budget / len(text), False, 'prefix')
    body = '\n\n'.join(text[segment.start:segment.end].strip('\n') for segment in segments)
    characters = sum(segment.characters for segment in segments)
    skipped = tuple(section.title for section in found if section.ordinal not in used)
    missing = tuple(kind for kind, _ in plan if kind not in found_kinds)
    return AnalysisWindow(body, tuple(segments), skipped, missing, characters, characters / len(text), False, 'sections')
