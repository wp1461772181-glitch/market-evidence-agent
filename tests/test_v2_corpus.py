import importlib.util
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from app.sec_filings import DiscoveredSecFiling, SecDiscoveryCoverage, SecFilingContent, SecRelatedExhibit


_SCRIPT = Path(__file__).parents[1] / "scripts" / "build_v2_corpus.py"
_SPEC = importlib.util.spec_from_file_location("build_v2_corpus", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
corpus = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(corpus)


class FixtureProvider:
    def __init__(self, *, accepted_at="2025-05-01T20:00:00Z", form="10-Q"):
        self.filing = DiscoveredSecFiling(
            cik="0000320193",
            accession_number="0000320193-25-000001",
            form=form,
            filed_at=date(2025, 5, 1),
            accepted_at=accepted_at,
            primary_document="q.htm",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/q.htm",
        )
        self.body_fetches = 0
        self.exhibit_fetches = 0

    def discover_between(self, symbol, **kwargs):
        return SecDiscoveryCoverage(
            filings=(self.filing,) if symbol == "AAPL" else (),
            complete=True, pages_read=0, next_page=None,
        )

    def fetch_primary_document(self, filing):
        self.body_fetches += 1
        assert filing.source_url == self.filing.source_url
        body = "Official revenue rose."
        return SecFilingContent(
            excerpt=body,
            excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),
            truncated=False,
        )

    def fetch_exhibit_99_1(self, filing):
        self.exhibit_fetches += 1
        assert filing.form == "8-K"
        body = "Quarterly results exhibit."
        return SecRelatedExhibit(
            document_name="ex99-1.htm",
            source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/ex99-1.htm",
            content=SecFilingContent(
                excerpt=body,
                excerpt_sha256=hashlib.sha256(body.encode()).hexdigest(),
                truncated=False,
            ),
        )


def test_plan_is_read_only_and_build_resumes_without_redownloading(tmp_path):
    output = tmp_path / "corpus"
    provider = FixtureProvider()
    plan = corpus.plan_corpus(provider=provider, symbols=("AAPL",))
    assert plan["candidate_counts"] == {"AAPL": 1}
    assert provider.body_fetches == 0
    assert not output.exists()

    first = corpus.build_corpus(provider=provider, output_dir=output, resume=False, max_new_documents=1, symbols=("AAPL",))
    assert first["created"] == 1
    manifest = json.loads((output / corpus.MANIFEST_NAME).read_text())
    assert manifest["mode"] == "historical_research"
    assert manifest["price_snapshot_files"] == []
    assert len(manifest["source_snapshot_files"]) == 1
    snapshot = json.loads((output / manifest["source_snapshot_files"][0]["filename"]).read_text())
    assert snapshot["published_at"] == "2025-05-01T20:00:00+00:00"
    assert snapshot["observed_at"] != snapshot["published_at"]
    assert snapshot["text"] == "Official revenue rose."

    again = corpus.build_corpus(provider=provider, output_dir=output, resume=True, max_new_documents=1, symbols=("AAPL",))
    assert again["created"] == 0
    assert provider.body_fetches == 1


def test_ambiguous_legacy_acceptance_time_is_not_backdated(tmp_path):
    output = tmp_path / "corpus"
    provider = FixtureProvider(accepted_at="20250501200000")
    result = corpus.build_corpus(provider=provider, output_dir=output, resume=False, max_new_documents=1, symbols=("AAPL",))
    assert result["created"] == 0
    assert result["skipped_ambiguous_time"] == 1
    assert provider.body_fetches == 0


def test_resume_rejects_modified_snapshot(tmp_path):
    output = tmp_path / "corpus"
    provider = FixtureProvider()
    corpus.build_corpus(provider=provider, output_dir=output, resume=False, max_new_documents=1, symbols=("AAPL",))
    manifest = json.loads((output / corpus.MANIFEST_NAME).read_text())
    source = output / manifest["source_snapshot_files"][0]["filename"]
    source.write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        corpus.build_corpus(provider=provider, output_dir=output, resume=True, max_new_documents=1, symbols=("AAPL",))


def test_8k_corpus_snapshot_uses_related_attachment_with_exact_provenance(tmp_path):
    output = tmp_path / "corpus"
    provider = FixtureProvider(form="8-K")

    result = corpus.build_corpus(
        provider=provider, output_dir=output, resume=False, max_new_documents=1, symbols=("AAPL",)
    )
    manifest = json.loads((output / corpus.MANIFEST_NAME).read_text())
    snapshot = json.loads((output / manifest["source_snapshot_files"][0]["filename"]).read_text())

    assert result["created"] == 1
    assert provider.exhibit_fetches == 1 and provider.body_fetches == 0
    assert snapshot["source_url"].endswith("/ex99-1.htm")
    assert snapshot["filing_source_url"].endswith("/q.htm")
    assert snapshot["document_kind"] == "exhibit_99_1"
    assert snapshot["related_attachment_status"] == "fetched"
    assert snapshot["text_sha256"] == hashlib.sha256(b"Quarterly results exhibit.").hexdigest()
    assert snapshot["citation_locator"] == {
        "kind": "extracted_text_char_range", "start": 0, "end": len("Quarterly results exhibit.")
    }
