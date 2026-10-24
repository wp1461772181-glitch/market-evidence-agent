"""A quotation is one citation unit; its source still has to support every claim."""
import pytest
from app.agent_workflow.evidence import reviewed_answer

CITATIONS=[{'ordinal':1,'source_type':'official_filing','quote':'营收为 100 美元。利润为 20 美元。'}]

@pytest.mark.parametrize('answer',[
    '> 营收为 100 美元。利润为 20 美元。 [1]',
    '> 营收为 100 美元。\n> 利润为 20 美元。 [1]',
])
def test_a_block_quote_keeps_its_trailing_source(answer):
    text,issues=reviewed_answer(answer,CITATIONS,require_citations=True)
    assert not issues
    assert text==answer


def test_a_long_quotation_keeps_its_trailing_source():
    quote='营收为 100 美元。'+'正文说明。'*120+'利润为 20 美元。'
    answer='原文为：“'+quote+'” [1]'
    text,issues=reviewed_answer(answer,[{**CITATIONS[0],'quote':quote}],require_citations=True)
    assert not issues
    assert text==answer

@pytest.mark.parametrize('answer,reason',[
    ('> 营收为 999 美元 [1]。','unsupported_numeric_claim'),
    ('> CEO 已辞职 [1]。','unsupported_claim'),
    ('> 营收为 100 美元 [99]。','invalid_citation'),
])
def test_quoting_does_not_authorize_an_unsupported_claim(answer,reason):
    text,issues=reviewed_answer(answer,CITATIONS,require_citations=True)
    assert reason in issues
    assert not text


def test_a_quote_source_never_cites_following_prose_or_another_paragraph():
    text,issues=reviewed_answer('> 营收为 100 美元 [1]。\n\n利润为 20 美元。',CITATIONS,require_citations=True)
    assert issues==['missing_citation']
    assert '利润' not in text


def test_an_earlier_quote_marker_does_not_cite_a_later_unmarked_quote():
    text,issues=reviewed_answer('> 营收为 100 美元 [1]。\n\n> 利润为 20 美元。',CITATIONS,require_citations=True)
    assert issues==['missing_citation']
    assert '利润' not in text


def test_nested_quotation_marks_share_the_outer_trailing_source():
    answer='原文为：“原文记为“营收为 100 美元。”以及利润为 20 美元。” [1]'
    text,issues=reviewed_answer(answer,CITATIONS,require_citations=True)
    assert not issues and text==answer


def test_an_unclosed_quote_does_not_inherit_a_later_sentence_source():
    text,issues=reviewed_answer('“营收为 100 美元。利润为 20 美元 [1]。',CITATIONS,require_citations=True)
    assert 'missing_citation' in issues


def test_an_english_quotation_uses_its_trailing_source():
    quote='Revenue was 100 USD. Profit was 20 USD.'
    answer='The source says: "'+quote+'" [1]'
    text,issues=reviewed_answer(answer,[{**CITATIONS[0],'quote':quote}],require_citations=True)
    assert not issues and text==answer


def test_an_inherited_quote_source_still_rejects_a_fabricated_number():
    text,issues=reviewed_answer('> 营收为 999 美元。\n> 利润为 20 美元。 [1]',CITATIONS,require_citations=True)
    assert issues==['unsupported_numeric_claim']
    assert '999' not in text and '20' in text
