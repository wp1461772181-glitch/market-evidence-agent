from app.knowledge.chunking import build_chunks, embedding_text
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
