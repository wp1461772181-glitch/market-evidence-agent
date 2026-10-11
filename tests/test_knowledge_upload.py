from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.database import engine,SessionLocal
import app.main as main
from app.knowledge.models import KnowledgeUploadOwner
from scripts.migrate_knowledge import migrate

def test_only_new_upload_captures_session_owner(disposable_database,monkeypatch):
    monkeypatch.setattr(main,"PUBLIC_API_MODE",False)
    monkeypatch.setattr(main,"APP_API_ACCESS_KEY","test-api-access-key-1234567890-test-key")
    main.create_tables()
    migrate(engine)
    payload={'title':'Synthetic DEMO note','source_url':'https://example.test/demo','published_at':'2026-01-01T00:00:00Z',
      'credibility_stars':'3','credibility_reason':'test fixture','impact_severity':'low'}
    with TestClient(main.app) as client:
        first=client.post('/uploaded-evidence/DEMO',headers={'X-Chat-Session':'a'*64,'X-App-Access-Key':'test-api-access-key-1234567890-test-key'},
          data=payload,files={'file':('demo.txt',b'Synthetic FY2025 revenue was USD 123 million.','text/plain')})
        assert first.status_code==201
        upload_id=first.json()['id']
        duplicate=client.post('/uploaded-evidence/DEMO',headers={'X-Chat-Session':'b'*64,'X-App-Access-Key':'test-api-access-key-1234567890-test-key'},
          data=payload,files={'file':('demo.txt',b'Synthetic FY2025 revenue was USD 123 million.','text/plain')})
        assert duplicate.status_code==200 and duplicate.json()['id']==upload_id
    with SessionLocal() as s:
        row=s.query(KnowledgeUploadOwner).filter_by(upload_id=upload_id).one()
        assert row.owner_hash==__import__('hashlib').sha256(('a'*64).encode()).hexdigest()
        assert s.query(KnowledgeUploadOwner).filter_by(owner_hash=__import__('hashlib').sha256(('b'*64).encode()).hexdigest()).count()==0
