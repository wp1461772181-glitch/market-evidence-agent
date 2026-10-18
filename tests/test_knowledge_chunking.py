from app.knowledge.chunking import coverage_gaps, build_chunks, embedding_text
from app.knowledge.sources import SourceDocument

class CharTokenizer:
    def encode(self, text, **kwargs):
        return list(text)

def source(text, **kw):
    defaults=dict(source_table='fixture',source_id='123',source_version='v1',source_hash='a'*64,
      source_type='official_filing',symbol='DEMO',scope='public',visibility='public',owner_hash=None,
      title='Annual report',source_url='https://example.test/report',published_at=None,observed_at=None,
      content_observed_at=None,truncated=False,parser='markdown')
    defaults.update(kw); return SourceDocument(text=text,**defaults)

def test_bilingual_sentence_boundaries_and_stable_identity():
    doc=source('# Financials\nRevenue was USD 123 million in FY2025.\n\n# 财务\n2025财年营业收入为1.23亿美元。')
    a=build_chunks(doc,CharTokenizer()); b=build_chunks(doc,CharTokenizer())
    assert [c.id for c in a]==[c.id for c in b]
    assert all(c.start_offset < c.end_offset for c in a)
    assert any('USD 123 million' in c.answer for c in a)
    assert any('亿美元' in c.answer for c in a)

def test_embedding_format_and_full_field_token_ceiling():
    c=build_chunks(source('# FY2025\nRevenue was USD 123 million in FY2025.'),CharTokenizer())[0]
    assert c.embedding_text==embedding_text(c.category,c.questions,c.answer)
    assert len(CharTokenizer().encode(c.embedding_text))<=768

def test_table_rows_preserve_heading_header_unit_and_period():
    text='# Financials\n| Metric | FY2025 (USD million) |\n| Revenue | 123 |\n| Expenses | 45 |'
    chunks=build_chunks(source(text),CharTokenizer())
    assert any('FY2025 (USD million)' in c.answer and '123' in c.answer and 'Metric' in c.answer for c in chunks)

def test_unsplittable_long_row_is_preserved_for_review():
    row='Revenue ' + 'x'*900
    chunks=build_chunks(source(row),CharTokenizer())
    reviewed=[chunk for chunk in chunks if chunk.needs_review]
    assert len(reviewed)==1 and reviewed[0].answer==row

def test_abbreviation_decimal_and_table_context_are_not_split_or_lost():
    text='Figures are in USD millions for FY2025. U.S. operations earned 10.5 million.\nAmounts below use USD millions for the fiscal year ended 2025.\n| Metric | Amount |\n| Revenue | 123 |'
    chunks=build_chunks(source(text),CharTokenizer())
    assert any('U.S. operations earned 10.5 million.' in c.answer for c in chunks)
    row=next(c for c in chunks if c.content_type=='table_row')
    assert 'USD millions' in row.answer and '2025' in row.answer and '123' in row.answer

def test_category_uses_parent_path_while_question_uses_current_heading():
    chunks=build_chunks(source('# Financials\n## Revenue\nFY2025 revenue was 123 USD million.'),CharTokenizer())
    assert chunks[0].category=='official_filing > Financials'
    assert chunks[0].questions=='Revenue'

def test_plain_text_item_and_title_case_headings_scope_each_chunk():
    """SEC filings arrive as plain text without markdown headings.

    Sentence windows used to run straight from an Item 7 paragraph into the Item 8
    statements, so a chunk's category said nothing about which section it covered.
    """
    text=(
        'Item 7. Management Discussion and Analysis\n'
        'Revenue grew because of demand.\n'
        'Item 8. Financial Statements and Supplementary Data\n'
        'CONSOLIDATED BALANCE SHEETS\n'
        'Total assets were 123 million.\n'
    )
    chunks=build_chunks(source(text),CharTokenizer())
    discussion=next(c for c in chunks if 'Revenue grew' in c.answer)
    balance=next(c for c in chunks if 'Total assets were' in c.answer)
    # A heading is consumed as metadata, so its characters ride into the chunk
    # that follows rather than disappearing from the index.
    assert discussion.answer.strip()=='Item 7. Management Discussion and Analysis Revenue grew because of demand.'
    assert 'Item 7' in discussion.questions
    # "CONSOLIDATED BALANCE SHEETS" sits directly under an Item heading with no
    # blank line of its own, so v6 does not promote it to a second section: the
    # balance sheet stays scoped by Item 8 and its title is carried as text.
    assert balance.section_path=='Item 8. Financial Statements and Supplementary Data'
    assert balance.questions.startswith('Item 8')
    assert 'Item 7' not in balance.category and 'Item 7' not in balance.section_path
    assert 'CONSOLIDATED BALANCE SHEETS' in balance.answer

def test_no_character_of_a_filing_is_left_out_of_the_index():
    """Every character must belong to some chunk.

    The chunker used to discard a flushed block whenever it looked like page
    furniture, and standalone statements went missing, page numbers went missing,
    and a table header could belong to nothing at all.
    """
    text=(
        'UNITED STATES\nSECURITIES AND EXCHANGE COMMISSION\nWashington, D.C. 20549\n'
        '1\n'
        'Item 7. Management Discussion and Analysis\n'
        'Revenue grew because of demand. Margins held.\n'
        '| Metric | FY2026 | FY2025 |\n| --- | --- | --- |\n| Revenue | 29,600 | 25,100 |\n'
        'CONSOLIDATED BALANCE SHEETS\n'
        'Total assets were 123 million.\n'
        '2\n'
    )
    document=source(text);chunks=build_chunks(document,CharTokenizer())
    assert coverage_gaps(text,chunks)==[]
    joined=' '.join(c.answer for c in chunks)
    for phrase in ('UNITED STATES','Washington, D.C. 20549','Item 7.','CONSOLIDATED BALANCE SHEETS','Total assets were'):
        assert phrase in joined,phrase

def test_page_furniture_never_becomes_a_section_label():
    """The registrant, the Commission and job titles are not headings."""
    text=(
        'SECURITIES AND EXCHANGE COMMISSION\n'
        'Management provided the following update.\n'
        'EVP & CFO\n'
        'The officer signed the report.\n'
        'Item 1A. Risk Factors\n'
        'Supply constraints may reduce revenue.\n'
    )
    chunks=build_chunks(source(text),CharTokenizer())
    labels=' '.join(c.section_path for c in chunks)
    assert 'Securities And Exchange Commission' not in labels
    assert 'Evp & Cfo' not in labels
    assert 'Item 1A. Risk Factors' in labels

def test_financial_line_labels_are_not_mistaken_for_headings():
    text='TOTAL ASSETS 1,234 1,100\nTotal liabilities were 45 million.\n'
    chunks=build_chunks(source(text),CharTokenizer())
    assert any('TOTAL ASSETS' in c.answer for c in chunks)
    assert all('Total Assets 1' not in c.category for c in chunks)
