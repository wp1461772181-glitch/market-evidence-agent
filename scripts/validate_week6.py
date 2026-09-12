"""Run the bounded Week 6 extraction check against the saved source set.

The command deliberately verifies only things that can be checked locally:
the document/manifest contract, Pydantic output, source grounding, and the
database cache.  A person must still review the generated summaries and
qualitative impact directions before treating them as useful analysis.

It makes at most one DeepSeek request for each uncached saved document.  It
then repeats the whole batch with a factory that raises if used, proving that
the second pass came from PostgreSQL rather than sending another model call.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.event_extraction import (
    DocumentInput,
    EventExtractionError,
    ExtractionResult,
    create_event_extraction_table,
    extract_document,
    load_document,
)
from app.event_provider import DEFAULT_DEEPSEEK_MODEL, DeepSeekEventProvider, EventProviderError
from sqlalchemy.exc import SQLAlchemyError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMENT_DIRECTORY = REPOSITORY_ROOT / "data" / "week6-documents"
DEFAULT_MANIFEST_PATH = REPOSITORY_ROOT / "docs" / "week6-sources.json"


class ValidationError(ValueError):
    """A local source, extraction, or cache check failed."""


def load_source_set(
    *, document_directory: Path, manifest_path: Path
) -> list[tuple[DocumentInput, dict[str, Any]]]:
    """Load exactly the manifest's local documents and validate their identity."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot read source manifest: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"source manifest is not valid JSON: {manifest_path}") from exc

    documents = manifest.get("documents")
    document_count = manifest.get("document_count")
    if not isinstance(documents, list) or not documents:
        raise ValidationError("source manifest must contain a non-empty documents list")
    if document_count != len(documents):
        raise ValidationError("source manifest document_count does not match documents")

    expected_paths: set[Path] = set()
    source_set: list[tuple[DocumentInput, dict[str, Any]]] = []
    seen_ids: set[str] = set()
    for entry in documents:
        if not isinstance(entry, dict):
            raise ValidationError("source manifest documents must be objects")
        document_id = entry.get("document_id")
        gold = entry.get("gold_annotation")
        if not isinstance(document_id, str) or not document_id:
            raise ValidationError("source manifest document_id must be a non-empty string")
        if document_id in seen_ids:
            raise ValidationError(f"source manifest repeats document_id: {document_id}")
        if not isinstance(gold, dict):
            raise ValidationError(f"source manifest has no gold_annotation for {document_id}")

        path = document_directory / f"{document_id}.json"
        expected_paths.add(path)
        document = load_document(path)
        for field in ("document_id", "company", "ticker", "source_url", "source_domain", "published_date", "title", "sha256"):
            expected = entry.get(field)
            actual = getattr(document, field)
            actual_value = actual.isoformat() if hasattr(actual, "isoformat") else actual
            if expected != actual_value:
                raise ValidationError(f"manifest {field} does not match local document for {document_id}")
        if not isinstance(entry.get("text_characters"), int) or entry["text_characters"] != len(document.text):
            raise ValidationError(f"manifest text_characters does not match local document for {document_id}")

        _validate_gold_annotation(document, gold)
        seen_ids.add(document_id)
        source_set.append((document, gold))

    actual_paths = set(document_directory.glob("*.json"))
    if actual_paths != expected_paths:
        unexpected = sorted(str(path.relative_to(REPOSITORY_ROOT)) for path in actual_paths - expected_paths)
        missing = sorted(str(path.relative_to(REPOSITORY_ROOT)) for path in expected_paths - actual_paths)
        raise ValidationError(f"local source set does not exactly match manifest (unexpected={unexpected}, missing={missing})")
    return source_set


def _validate_gold_annotation(document: DocumentInput, gold: dict[str, Any]) -> None:
    if gold.get("event_type") != "earnings_release":
        raise ValidationError(f"{document.document_id}: expected earnings_release gold annotation")
    if gold.get("event_date") != document.published_date.isoformat():
        raise ValidationError(f"{document.document_id}: gold event_date must equal published_date")
    quote = gold.get("evidence_quote")
    if not isinstance(quote, str) or not quote or quote not in document.text:
        raise ValidationError(f"{document.document_id}: gold evidence_quote is not in its local document")
    if not isinstance(gold.get("expected_summary"), str) or not gold["expected_summary"].strip():
        raise ValidationError(f"{document.document_id}: gold expected_summary must be non-empty")


def validate_extraction(
    *, document: DocumentInput, gold: dict[str, Any], result: ExtractionResult
) -> dict[str, Any]:
    """Prove every extracted event remains grounded and find the required gold match."""
    events = result.batch.model_dump(mode="json")["events"]
    gold_matches = []
    for event in events:
        if event["company"] != document.company:
            raise ValidationError(f"{document.document_id}: event company differs from the source document")
        if event["source_url"] != document.source_url:
            raise ValidationError(f"{document.document_id}: event source_url differs from the source document")
        if event["evidence_quote"] not in document.text:
            raise ValidationError(f"{document.document_id}: event evidence_quote is not in the source document")
        if (
            event["event_type"] == gold["event_type"]
            and event["event_date"] == gold["event_date"]
            and event["company"] == document.company
            and event["source_url"] == document.source_url
            and event["evidence_quote"] in document.text
        ):
            gold_matches.append(event)
    if not gold_matches:
        raise ValidationError(
            f"{document.document_id}: no earnings_release matched its company, source URL, and gold event date"
        )
    return {
        "document_id": document.document_id,
        "source_url": document.source_url,
        "cache_key": result.cache_key,
        "cache_hit": result.cache_hit,
        "response_model": result.response_model,
        "usage": result.usage,
        "events": events,
        "matching_earnings_events": gold_matches,
    }


def run_pass(
    *,
    source_set: list[tuple[DocumentInput, dict[str, Any]]],
    provider_factory: Callable[[], DeepSeekEventProvider],
    model: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with SessionLocal() as db:
        for document, gold in source_set:
            result = extract_document(document, db=db, provider_factory=provider_factory, model=model)
            rows.append(validate_extraction(document=document, gold=gold, result=result))
    return rows


def run_validation(
    *,
    source_set: list[tuple[DocumentInput, dict[str, Any]]],
    model: str,
    provider_client: DeepSeekEventProvider,
) -> dict[str, Any]:
    """Run one provider-backed pass and one independently cached-only pass."""
    provider_factory_calls = 0

    def counting_factory() -> DeepSeekEventProvider:
        nonlocal provider_factory_calls
        provider_factory_calls += 1
        return provider_client

    first = run_pass(source_set=source_set, provider_factory=counting_factory, model=model)

    def cache_only_factory() -> DeepSeekEventProvider:
        raise ValidationError("second pass attempted to construct a provider instead of using the cache")

    second = run_pass(source_set=source_set, provider_factory=cache_only_factory, model=model)
    if not all(row["cache_hit"] for row in second):
        raise ValidationError("second pass contains a cache miss")
    for first_row, second_row in zip(first, second, strict=True):
        if first_row["cache_key"] != second_row["cache_key"]:
            raise ValidationError(f"cache key changed between validation passes for {first_row['document_id']}")
        if first_row["events"] != second_row["events"]:
            raise ValidationError(f"cached events changed between validation passes for {first_row['document_id']}")

    return {
        "validation_version": "week6-batch-validation-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "provider": "deepseek",
        "requested_model": model,
        "document_count": len(source_set),
        "first_pass": {
            "provider_factory_calls": provider_factory_calls,
            "cache_hits": sum(row["cache_hit"] for row in first),
            "cache_misses": sum(not row["cache_hit"] for row in first),
            "documents": first,
        },
        "second_pass": {
            "provider_factory_calls": 0,
            "cache_hits": sum(row["cache_hit"] for row in second),
            "cache_misses": 0,
            "documents": second,
        },
        "human_review_required": (
            "Review every generated summary and impact_direction against its source URL. "
            "These structural checks prove grounding and cache behavior only; they do not measure factual completeness, "
            "summary quality, or predictive value."
        ),
    }


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="A new ignored artifact directory")
    parser.add_argument("--documents-dir", type=Path, default=DEFAULT_DOCUMENT_DIRECTORY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--model", default=DEFAULT_DEEPSEEK_MODEL, help="DeepSeek model name")
    args = parser.parse_args(argv)

    try:
        if args.output_dir.exists():
            raise ValidationError(f"output directory already exists: {args.output_dir}")
        source_set = load_source_set(document_directory=args.documents_dir, manifest_path=args.manifest)
        # Constructing the adapter validates the local configuration but does
        # not send a request.  A missing key therefore fails before the batch
        # can make any paid provider call.
        provider_client = DeepSeekEventProvider(model=args.model)
        create_event_extraction_table()
        report = run_validation(source_set=source_set, model=args.model, provider_client=provider_client)
    except (EventExtractionError, EventProviderError, OSError, SQLAlchemyError, ValidationError, ValueError) as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True)
    output_path = args.output_dir / "report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output_path),
        "document_count": report["document_count"],
        "first_pass": {key: report["first_pass"][key] for key in ("provider_factory_calls", "cache_hits", "cache_misses")},
        "second_pass": {key: report["second_pass"][key] for key in ("provider_factory_calls", "cache_hits", "cache_misses")},
        "human_review_required": report["human_review_required"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
