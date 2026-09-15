from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.event_extraction import DocumentInput, create_event_extraction_table, extract_document
from app.event_provider import ProviderResult
from app.research_workflow import _research_event_context, create_research_run_table, run_research
import app.research_workflow as workflow


_TEXT = "Revenue increased 6% to $100 billion. Operating expenses rose by 4%."


@dataclass
class FakeExtractionProvider:
    response: str

    def extract(self, **_: object) -> ProviderResult:
        return ProviderResult(content=self.response, response_model="fake", usage=None)


@dataclass
class FakeResearchProvider:
    responses: list[str]
    calls: int = 0

    def extract(self, *, system_prompt: str, document_payload: str, model: str) -> ProviderResult:
        assert "exact contiguous quote" in system_prompt
        assert "at most 160 characters" in system_prompt
        assert '"source_id":"example-q1"' in document_payload
        self.calls += 1
        return ProviderResult(content=self.responses.pop(0), response_model="fake", usage={"total_tokens": 1})


def _document() -> DocumentInput:
    return DocumentInput.model_validate(
        {
            "document_id": "example-q1",
            "company": "Example Corp",
            "ticker": "EXM",
            "source_url": "https://investor.example.com/releases/q1",
            "source_domain": "investor.example.com",
            "published_date": "2026-01-30",
            "title": "Example results",
            "text": _TEXT,
            "sha256": hashlib.sha256(_TEXT.encode()).hexdigest(),
        }
    )


def _seed_cached_source(tmp_path: Path, monkeypatch) -> Path:
    documents = tmp_path / "documents"
    documents.mkdir(parents=True)
    document = _document()
    (documents / "example-q1.json").write_text(json.dumps(document.model_dump(mode="json")), encoding="utf-8")
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"documents": [document.model_dump(mode="json", exclude={"text"})]}), encoding="utf-8")
    monkeypatch.setattr(workflow, "DEFAULT_SOURCE_MANIFEST", manifest)
    create_event_extraction_table()
    response = json.dumps(
        {"events": [{"event_type": "earnings_release", "event_date": "2026-01-30", "impact_direction": "positive", "summary": "Revenue increased.", "evidence_quote": "Revenue increased 6% to $100 billion."}]}
    )
    with SessionLocal() as db:
        extract_document(document, db=db, provider_factory=lambda: FakeExtractionProvider(response))
    return documents


def _claim(claim: str, quote: str) -> str:
    return json.dumps({"claims": [{"claim": claim, "source_id": "example-q1", "evidence_quote": quote}]})


def _run(tmp_path: Path, monkeypatch, responses: list[str], *, as_of_time: datetime | None = None):
    documents = _seed_cached_source(tmp_path, monkeypatch)
    provider = FakeResearchProvider(responses)
    create_research_run_table()
    with SessionLocal() as db:
        run = run_research(
            symbol="EXM",
            as_of_time=as_of_time or datetime(2026, 2, 1, tzinfo=UTC),
            document_ids=["example-q1"],
            document_directory=documents,
            db=db,
            provider_factory=lambda: provider,
        )
    return run, provider


def test_runs_fixed_nodes_and_only_reports_validated_support_and_counter_evidence(tmp_path, monkeypatch, client):
    run, provider = _run(
        tmp_path,
        monkeypatch,
        [
            _claim("Revenue growth supports the business case.", "Revenue increased 6% to $100 billion."),
            _claim("Higher operating expenses qualify the business case.", "Operating expenses rose by 4%."),
        ],
    )

    assert run.status == "succeeded"
    assert provider.calls == 2
    assert [entry["stage"] for entry in run.node_trace] == [
        "source_check", "source_check", "supporting", "supporting", "counter", "counter", "review", "review", "complete"
    ]
    assert run.report is not None
    assert run.report["time_scope"]["data_mode"] == "historical_research"
    assert run.report["supporting_evidence"][0]["evidence_quote"] in _TEXT
    assert run.report["counter_evidence"][0]["evidence_quote"] in _TEXT
    assert "model proposed" in run.report["conclusion"].lower()
    assert "forecast" in run.report["conclusion"].lower()
    response = client.get(f"/research-runs/{run.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


def test_model_failure_is_persisted_without_a_report(tmp_path, monkeypatch):
    run, provider = _run(tmp_path, monkeypatch, ["not json"])

    assert provider.calls == 1
    assert run.status == "failed"
    assert run.current_stage == "supporting"
    assert run.report is None
    assert "claim JSON contract" in run.error


def test_provider_exception_is_persisted_without_a_report(tmp_path, monkeypatch):
    documents = _seed_cached_source(tmp_path, monkeypatch)

    class FailingProvider:
        def extract(self, **_: object) -> ProviderResult:
            raise RuntimeError("provider internals must not be persisted")

    create_research_run_table()
    with SessionLocal() as db:
        run = run_research(
            symbol="EXM",
            as_of_time=datetime(2026, 2, 1, tzinfo=UTC),
            document_ids=["example-q1"],
            document_directory=documents,
            db=db,
            provider_factory=lambda: FailingProvider(),
        )

    assert run.status == "failed"
    assert run.current_stage == "supporting"
    assert run.report is None
    assert run.error == "research workflow failed (RuntimeError)"


def test_unavailable_source_fails_before_a_provider_is_created(tmp_path, monkeypatch):
    run, provider = _run(
        tmp_path,
        monkeypatch,
        [],
        as_of_time=datetime(2026, 1, 30, 23, 59, tzinfo=UTC),
    )

    assert provider.calls == 0
    assert run.status == "failed"
    assert run.current_stage == "source_check"
    assert run.report is None
    assert "unavailable" in run.error


def test_invalid_citation_fails_and_never_creates_a_report(tmp_path, monkeypatch):
    run, _ = _run(tmp_path, monkeypatch, [_claim("Bad claim.", "not in the source")])

    assert run.status == "failed"
    assert run.report is None
    assert "exact substring" in run.error


def test_source_file_must_match_the_selected_manifest_identity(tmp_path, monkeypatch):
    documents = _seed_cached_source(tmp_path, monkeypatch)
    path = documents / "example-q1.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["company"] = "Replaced Corp"
    path.write_text(json.dumps(changed), encoding="utf-8")
    provider = FakeResearchProvider([])
    create_research_run_table()
    with SessionLocal() as db:
        run = run_research(
            symbol="EXM",
            as_of_time=datetime(2026, 2, 1, tzinfo=UTC),
            document_ids=["example-q1"],
            document_directory=documents,
            db=db,
            provider_factory=lambda: provider,
        )

    assert run.status == "failed"
    assert run.report is None
    assert provider.calls == 0
    assert "manifest identity" in run.error


def test_research_context_does_not_expose_excluded_week6_events():
    context = _research_event_context(
        {
            "events": [{"event_type": "other", "event_date": None, "summary": "Kept", "evidence_quote": "quote", "source_url": "https://example.com", "company": "Example", "impact_direction": "positive"}],
            "excluded_events": [{"event_type": "other", "event_date": None, "summary": "Excluded", "evidence_quote": "old quote", "source_url": "https://example.com", "company": "Example"}],
        }
    )

    assert context["events"][0]["summary"] == "Kept"
    assert "excluded_events" not in context
    assert "impact_direction" not in context["events"][0]


def test_cli_writes_persisted_failure_and_exits_nonzero(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    failed_run = SimpleNamespace(
        id="run-id",
        status="failed",
        current_stage="supporting",
        error="safe failure",
        source_snapshot=[],
        report=None,
    )
    monkeypatch.setattr(workflow, "configured_deepseek_model", lambda _: "deepseek-flash")
    monkeypatch.setattr(workflow, "create_research_run_table", lambda: None)
    monkeypatch.setattr(workflow, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(workflow, "run_research", lambda **_: failed_run)

    with pytest.raises(SystemExit) as exc:
        workflow.main(
            [
                "--symbol",
                "EXM",
                "--as-of-time",
                "2026-02-01T00:00:00Z",
                "--document-id",
                "example-q1",
                "--output",
                str(output),
            ]
        )

    assert exc.value.code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "failed"


def test_empty_counter_evidence_is_a_gap_but_empty_both_sides_is_not_a_report(tmp_path, monkeypatch):
    run, provider = _run(
        tmp_path,
        monkeypatch,
        [_claim("Revenue growth supports the business case.", "Revenue increased 6% to $100 billion."), json.dumps({"claims": []})],
    )
    assert run.status == "succeeded"
    assert provider.calls == 2
    assert run.report is not None
    assert run.report["counter_evidence"] == []
    assert "No grounded counter claim" in run.report["information_gaps"][0]

    empty, _ = _run(tmp_path / "empty", monkeypatch, [json.dumps({"claims": []}), json.dumps({"claims": []})])
    assert empty.status == "failed"
    assert empty.current_stage == "review"
    assert empty.report is None
    assert "no grounded" in empty.error
