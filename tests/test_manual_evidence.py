from __future__ import annotations

from io import BytesIO
from hashlib import sha256

from pypdf import PdfWriter
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine
from app.manual_evidence import create_uploaded_evidence_table
from app.models import UploadedEvidence


def _clear_uploaded_evidence() -> None:
    with SessionLocal() as db:
        db.query(UploadedEvidence).delete(synchronize_session=False)
        db.commit()


def _upload(client, *, symbol: str = "AAPL", filename: str = "report.md", content: bytes = b"# Report\nProduct issue reported by a named publication.", **overrides):
    data = {
        "title": "Product issue report",
        "source_url": "https://news.example.com/report",
        "published_at": "2026-09-02T08:30:00Z",
        "credibility_stars": "4",
        "credibility_reason": "Named reporter and corroborating customer reports.",
        "impact_severity": "high",
    }
    data.update(overrides)
    return client.post(
        f"/uploaded-evidence/{symbol}",
        data=data,
        files={"file": (filename, content, "text/markdown")},
    )


def test_uploads_manual_media_evidence_as_unconfirmed_without_forecast_side_effect(client):
    _clear_uploaded_evidence()
    content = b"# Product concern\nA named publication reports a material product failure."

    response = _upload(client, content=content)

    assert response.status_code == 201
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["title"] == "Product issue report"
    assert body["status"] == "unconfirmed"
    assert body["credibility_stars"] == 4
    assert body["impact_severity"] == "high"
    assert body["content_sha256"] == sha256(content).hexdigest()
    assert "product failure" in body["content_preview"]
    assert "probability" not in body
    assert body["published_at"].endswith("Z")
    assert body["observed_at"].endswith("Z")

    with SessionLocal() as db:
        row = db.get(UploadedEvidence, body["id"])
        assert row is not None
        assert row.raw_content == content
        assert row.status == "unconfirmed"


def test_listing_is_symbol_scoped_newest_first_and_duplicate_upload_is_idempotent(client):
    _clear_uploaded_evidence()
    older = _upload(
        client,
        filename="older.txt",
        content=b"Older source text",
        title="Older report",
        published_at="2026-09-01T08:30:00+00:00",
    )
    newer = _upload(
        client,
        filename="newer.md",
        content=b"Newer source text",
        title="Newer report",
        published_at="2026-09-02T08:30:00+00:00",
    )
    duplicate = _upload(
        client,
        filename="different-name.md",
        content=b"Newer source text",
        title="Changed title must not create a second record",
    )
    msft = _upload(client, symbol="MSFT", content=b"Same source text but explicit other ticker")

    assert older.status_code == newer.status_code == msft.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == newer.json()["id"]

    listed = client.get("/uploaded-evidence/aapl")
    assert listed.status_code == 200
    body = listed.json()
    assert body["symbol"] == "AAPL"
    assert [item["title"] for item in body["items"]] == ["Newer report", "Older report"]
    assert all(item["status"] == "unconfirmed" for item in body["items"])


def test_upload_rejects_untrusted_metadata_and_unsupported_or_oversize_files(client):
    _clear_uploaded_evidence()
    invalid_url = _upload(client, source_url="http://news.example.com/report")
    naive_time = _upload(client, content=b"different", published_at="2026-09-02T08:30:00")
    invalid_stars = _upload(client, content=b"other", credibility_stars="6")
    invalid_severity = _upload(client, content=b"severity", impact_severity="critical")
    unsupported = _upload(client, content=b"binary", filename="report.docx")
    oversize = _upload(client, content=b"x" * 5_000_001)

    assert invalid_url.status_code == 422
    assert "HTTPS" in invalid_url.json()["detail"]
    assert naive_time.status_code == 422
    assert "timezone" in naive_time.json()["detail"]
    assert invalid_stars.status_code == 422
    assert "between 1 and 5" in invalid_stars.json()["detail"]
    assert invalid_severity.status_code == 422
    assert "impact_severity" in invalid_severity.json()["detail"]
    assert unsupported.status_code == 422
    assert "TXT, Markdown, or PDF" in unsupported.json()["detail"]
    assert oversize.status_code == 422
    assert "exceeds" in oversize.json()["detail"]


def test_upload_accepts_pdf_and_marks_an_image_only_document_as_unconfirmed(client):
    _clear_uploaded_evidence()
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)

    response = _upload(client, filename="media-clip.pdf", content=stream.getvalue())

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "media-clip.pdf"
    assert body["status"] == "unconfirmed"
    assert body["content_preview"] == "[PDF uploaded; no extractable text preview is available.]"


def test_upload_table_migration_adds_impact_severity_to_an_existing_table_twice():
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE uploaded_evidence DROP COLUMN IF EXISTS impact_severity"))

    create_uploaded_evidence_table()
    create_uploaded_evidence_table()

    columns = {column["name"] for column in inspect(engine).get_columns("uploaded_evidence")}
    assert "impact_severity" in columns
