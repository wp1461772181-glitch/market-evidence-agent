from app.knowledge.sources import adapt_uploaded, adapt_sec

def test_unowned_legacy_upload_is_not_indexed_without_allowlist():
    row=dict(id='u1',symbol='DEMO',title='x',content_text='source text',content_sha256='a'*64,
      source_url='https://example.test',published_at=None,observed_at=None)
    assert adapt_uploaded(row,owner_hash=None,allowlist=set()) is None
    doc=adapt_uploaded(row,owner_hash=None,allowlist={'u1'})
    assert doc.scope=='public' and doc.visibility=='public'

def test_sec_without_body_is_not_indexed_and_excerpt_uses_actual_content_url():
    base=dict(id='s1',symbol='DEMO',primary_document='10-K',source_url='https://example.test/main',
      content_excerpt=None,content_excerpt_sha256=None,content_observed_at=None,observed_at=None,
      filed_at=None,content_truncated=False,content_source_url='https://example.test/exhibit',
      content_document_name='ex99.htm',content_kind='exhibit')
    assert adapt_sec(base) is None
    import hashlib
    excerpt='Revenue USD 123 million FY2025'
    base.update(content_excerpt=excerpt,content_excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest())
    assert adapt_sec(base).source_url=='https://example.test/exhibit'


def test_upload_snapshot_hash_covers_parsed_body_title_url_and_scope():
    row=dict(id='u1',symbol='DEMO',title='A',content_text='body A',content_sha256='x',source_url='https://a.test',published_at=None,observed_at=None)
    original=adapt_uploaded(row,owner_hash=None,allowlist={'u1'})
    for change in ({'content_text':'body B'},{'title':'B'},{'source_url':'https://b.test'}):
        assert adapt_uploaded({**row,**change},owner_hash=None,allowlist={'u1'}).source_hash!=original.source_hash
    assert adapt_uploaded(row,owner_hash='a'*64,allowlist=set()).source_hash!=original.source_hash

def test_sec_rejects_excerpt_when_stored_hash_does_not_match_actual_text():
    row=dict(id='s2',symbol='DEMO',primary_document='10-K',source_url='https://example.test/main',
      content_excerpt='actual text',content_excerpt_sha256='0'*64,content_status='fetched',review_status='accepted',
      content_source_url='https://example.test/excerpt',filed_at=None,observed_at=None,content_observed_at=None,content_truncated=True)
    assert adapt_sec(row) is None

def test_sec_fetched_pending_review_is_indexable_but_tampering_is_not():
    import hashlib
    from datetime import UTC, datetime, timedelta
    excerpt='Item 2. Management reports revenue increased 18 percent during FY2025.'
    now=datetime.now(UTC)
    row=dict(id='s3',symbol='AMD',primary_document='10-Q',source_url='https://www.sec.gov/Archives/edgar/data/example',
      content_excerpt=excerpt,content_excerpt_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),content_status='fetched',
      review_status='pending_review',reviewed_at=None,content_source_url=None,filed_at=(now-timedelta(days=1)).date(),
      observed_at=now-timedelta(minutes=1),content_observed_at=now,content_truncated=True)
    assert adapt_sec(row) is not None
    assert adapt_sec({**row,'content_excerpt':'tampered'}) is None
    assert adapt_sec({**row,'content_excerpt_sha256':None}) is None
    accepted=adapt_sec({**row,'review_status':'accepted','reviewed_at':now-timedelta(minutes=2)})
    assert accepted.source_hash==adapt_sec(row).source_hash
