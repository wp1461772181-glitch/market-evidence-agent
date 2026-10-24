"""A bracketed number in an exact source title is text, never a source handle."""
import pytest
from app.agent_workflow.evidence import reviewed_answer

TITLE='Revenue outlook: $100 [2026]'
CITATIONS=[{'ordinal':1,'source_type':'web','title':TITLE,'quote':TITLE+' Revenue was 100 USD.'}]

@pytest.mark.parametrize('answer',[
    '**《'+TITLE+'》** [1]',
    '- AAPL · '+TITLE+' [1]',
    '> '+TITLE+'\n> Revenue was 100 USD. [1]',
    '| Article | Amount |\n|---|---|\n| '+TITLE+' | 100 USD |\nSource [1]',
])
def test_an_exact_source_title_keeps_literal_brackets(answer):
    text,issues=reviewed_answer(answer,CITATIONS,require_citations=True)
    assert not issues and text==answer


@pytest.mark.parametrize('answer',[
    'Revenue was 100 USD. [2026]',
    '**《Revenue outlook: $100 [2027]》** [1]',
    '**《'+TITLE+'》** [99]',
])
def test_literal_title_handling_never_accepts_an_unknown_source(answer):
    text,issues=reviewed_answer(answer,CITATIONS,require_citations=True)
    assert 'invalid_citation' in issues


@pytest.mark.parametrize('wrapper',['《{}》','> {}'])
def test_a_literal_matching_a_valid_ordinal_cannot_supply_a_citation(wrapper):
    title='Revenue was 100 USD [1]'
    text,issues=reviewed_answer(wrapper.format(title),
        [{'ordinal':1,'source_type':'web','title':title,'quote':'Revenue was 100 USD.'}],require_citations=True)
    assert 'missing_citation' in issues and not text


def test_a_title_literal_does_not_authorize_a_fabricated_amount():
    text,issues=reviewed_answer('《'+TITLE+'》 Revenue was 999 USD [1]',CITATIONS,require_citations=True)
    assert 'unsupported_numeric_claim' in issues and not text
