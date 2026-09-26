"""Bounded SEC source snapshot collection for V2 historical research.

This prepares source inputs only. It does not train a model, call an LLM, or
pretend today's backfill was observed at the historical publication time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable

from app.models import SecFilingInventory
from app.sec_filings import SecEdgarProvider, SecFilingsError


SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "v2"
MANIFEST_NAME = "dataset-manifest.json"
MAX_PILOT_DOCUMENTS = 30
PARTITION_ORDER = ("train", "calibration", "test")
TRAIN_END = date(2023, 12, 31)
CALIBRATION_END = date(2024, 12, 31)


def plan_corpus(
    *,
    provider: SecEdgarProvider,
    symbols: tuple[str, ...] = SYMBOLS,
    start_date: date = date(2021, 1, 1),
    end_date: date = date(2026, 8, 31),
    max_pages: int = 3,
    max_new_documents: int = MAX_PILOT_DOCUMENTS,
    excluded_source_ids: Iterable[str] = (),
) -> dict:
    """Discover source candidates without downloading bodies or writing files."""
    if not 1 <= max_new_documents <= MAX_PILOT_DOCUMENTS:
        raise ValueError(f"max_new_documents must be between 1 and {MAX_PILOT_DOCUMENTS}")
    candidates: dict[str, list] = {}
    incomplete: dict[str, bool] = {}
    for symbol in symbols:
        coverage = provider.discover_between(
            symbol, start_date=start_date, end_date=end_date, max_pages=max_pages
        )
        candidates[symbol] = list(coverage.filings)
        incomplete[symbol] = not coverage.complete
    excluded = set(excluded_source_ids)
    selected, selection_audit = _stratified_round_robin(
        candidates,
        start_date=start_date,
        end_date=end_date,
        excluded_source_ids=excluded,
    )
    return {
        "symbols": list(symbols),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "candidate_counts": {symbol: len(items) for symbol, items in candidates.items()},
        "candidate_counts_after_resume": {
            symbol: sum(item.accession_number not in excluded for item in items)
            for symbol, items in candidates.items()
        },
        "discovery_incomplete": incomplete,
        "selection_preview": _selection_preview(
            selected, limit=max_new_documents, start_date=start_date, end_date=end_date
        ),
        "selection_audit": selection_audit,
        "candidates": candidates,
    }


def build_corpus(
    *,
    provider: SecEdgarProvider,
    output_dir: Path,
    resume: bool,
    max_new_documents: int,
    symbols: tuple[str, ...] = SYMBOLS,
    start_date: date = date(2021, 1, 1),
    end_date: date = date(2026, 8, 31),
    max_pages: int = 3,
) -> dict:
    """Save at most 30 new official snapshots, resuming verified local files."""
    if not 1 <= max_new_documents <= MAX_PILOT_DOCUMENTS:
        raise ValueError(f"max_new_documents must be between 1 and {MAX_PILOT_DOCUMENTS}")
    manifest_path = output_dir / MANIFEST_NAME
    if output_dir.exists() and not resume:
        raise ValueError("output directory exists; use --resume or a new directory")
    if resume and output_dir.exists() and not manifest_path.exists():
        raise ValueError("resume requires an existing dataset-manifest.json")
    if not output_dir.exists():
        output_dir.mkdir(parents=True)

    manifest = _read_manifest(manifest_path) if manifest_path.exists() else _new_manifest(symbols, start_date, end_date)
    if manifest["symbols"] != list(symbols) or manifest["training_range"] != {
        "start": start_date.isoformat(), "end": end_date.isoformat()
    }:
        raise ValueError("resume settings do not match the saved corpus manifest")
    _validate_existing_files(output_dir, manifest)
    remaining_pilot_capacity = MAX_PILOT_DOCUMENTS - len(manifest["source_snapshot_files"])
    if max_new_documents > remaining_pilot_capacity:
        raise ValueError(
            f"requested documents exceed remaining pilot capacity: {remaining_pilot_capacity}"
        )
    saved_ids = {entry["source_id"] for entry in manifest["source_snapshot_files"]}
    discovery = plan_corpus(
        provider=provider, symbols=symbols, start_date=start_date, end_date=end_date,
        max_pages=max_pages, max_new_documents=max_new_documents, excluded_source_ids=saved_ids,
    )
    manifest["discovery_incomplete"] = discovery["discovery_incomplete"]
    pending, _ = _stratified_round_robin(
        discovery["candidates"],
        start_date=start_date,
        end_date=end_date,
        excluded_source_ids=saved_ids,
    )
    created = 0
    skipped_ambiguous_time = 0
    errors: list[dict[str, str]] = []
    for symbol, filing in pending:
        if created >= max_new_documents:
            break
        if filing.accession_number in saved_ids:
            continue
        published_at = _published_at(filing.accepted_at)
        if published_at is None:
            skipped_ambiguous_time += 1
            continue
        inventory = SecFilingInventory(
            symbol=symbol,
            cik=filing.cik,
            accession_number=filing.accession_number,
            form=filing.form,
            filed_at=filing.filed_at,
            accepted_at=filing.accepted_at,
            primary_document=filing.primary_document,
            source_url=filing.source_url,
        )
        try:
            content = None
            content_url = filing.source_url
            document_name = filing.primary_document
            attachment_status = "not_applicable"
            exhibit_error = None
            if filing.form == "8-K":
                try:
                    exhibit = provider.fetch_exhibit_99_1(inventory)
                except SecFilingsError as exc:
                    exhibit = None
                    exhibit_error = str(exc)
                    attachment_status = "unavailable"
                    coverage_incomplete = True
                if exhibit is not None:
                    content = exhibit.content
                    content_url = exhibit.source_url
                    document_name = exhibit.document_name
                    attachment_status = "fetched"
                elif attachment_status != "unavailable":
                    attachment_status = "not_found"
            if content is None:
                content = provider.fetch_primary_document(inventory)
            coverage_incomplete = content.truncated or attachment_status == "unavailable"
        except SecFilingsError as exc:
            errors.append({"source_id": filing.accession_number, "reason": str(exc)})
            continue
        if hashlib.sha256(content.excerpt.encode("utf-8")).hexdigest() != content.excerpt_sha256:
            errors.append({"source_id": filing.accession_number, "reason": "source text hash mismatch"})
            continue
        observed_at = datetime.now(UTC)
        snapshot = {
            "schema_version": "v2-source-snapshot-v1",
            "mode": "historical_research",
            "source_type": "official_filing",
            "source_id": filing.accession_number,
            "symbol": symbol,
            "form": filing.form,
            "document_name": document_name,
            "source_url": content_url,
            "filing_source_url": filing.source_url,
            "document_kind": "exhibit_99_1" if attachment_status == "fetched" else "primary_document",
            "related_attachment_status": attachment_status,
            "related_attachment_error": exhibit_error,
            "publication_time_basis": "sec_accession_acceptance_time",
            "published_at": published_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "text": content.excerpt,
            "text_sha256": content.excerpt_sha256,
            "citation_locator": {"kind": "extracted_text_char_range", "start": 0, "end": len(content.excerpt)},
            "coverage_incomplete": coverage_incomplete,
            "exhibit_error": exhibit_error,
        }
        filename = f"{symbol}-{filing.accession_number.replace('-', '')}.json"
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        (output_dir / filename).write_bytes(payload)
        manifest["source_snapshot_files"].append({
            "source_id": filing.accession_number,
            "filename": filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "published_at": published_at.isoformat(),
            "observed_at": observed_at.isoformat(),
            "coverage_incomplete": coverage_incomplete,
        })
        _write_manifest(manifest_path, manifest)
        saved_ids.add(filing.accession_number)
        created += 1
    return {
        "created": created,
        "saved_total": len(manifest["source_snapshot_files"]),
        "candidate_counts": discovery["candidate_counts"],
        "candidate_counts_after_resume": discovery["candidate_counts_after_resume"],
        "discovery_incomplete": discovery["discovery_incomplete"],
        "selection_preview": discovery["selection_preview"],
        "selection_audit": discovery["selection_audit"],
        "skipped_ambiguous_time": skipped_ambiguous_time,
        "errors": errors,
        "price_coverage": "pending_P3",
    }


def _new_manifest(symbols: tuple[str, ...], start_date: date, end_date: date) -> dict:
    return {
        "schema_version": "v2-corpus-manifest-v1",
        "mode": "historical_research",
        "symbols": list(symbols),
        "training_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "source_snapshot_files": [],
        "price_snapshot_files": [],
        "target_spec_version": "absolute-close-v1",
        "price_basis": "provider_quote_close_v1",
        "extraction_schema_version": "pending_P2",
        "partitions": {
            "train": ["2021-01-01", "2023-12-31"],
            "calibration": ["2024-01-01", "2024-12-31"],
            "test": ["2025-01-01", "2026-08-31"],
        },
        "discovery_incomplete": {},
    }


def _stratified_round_robin(
    candidates: dict[str, list],
    *,
    start_date: date,
    end_date: date,
    excluded_source_ids: set[str],
) -> tuple[list[tuple[str, object]], dict[str, int]]:
    """Select bounded candidates across time partitions, then symbols.

    The SEC response order is not a sampling policy.  This selector keeps the
    fixed manifest range and cycles train, calibration, test; within each
    partition it cycles symbols.  Ambiguous acceptance times are retained at
    the end so the existing explicit skip accounting still reports them, while
    valid dated material is never silently backdated.
    """
    buckets: dict[str, dict[str, list]] = {
        partition: {symbol: [] for symbol in candidates} for partition in PARTITION_ORDER
    }
    ambiguous: list[tuple[str, object]] = []
    audit = {"excluded_existing": 0, "duplicate_accession": 0, "ambiguous_time": 0, "out_of_range": 0}
    seen_accessions: set[str] = set()
    for symbol, items in candidates.items():
        for filing in items:
            accession = filing.accession_number
            if accession in excluded_source_ids:
                audit["excluded_existing"] += 1
                continue
            if accession in seen_accessions:
                audit["duplicate_accession"] += 1
                continue
            seen_accessions.add(accession)
            published_at = _published_at(filing.accepted_at)
            if published_at is None:
                ambiguous.append((symbol, filing))
                audit["ambiguous_time"] += 1
                continue
            partition = _partition_for_date(published_at.date(), start_date=start_date, end_date=end_date)
            if partition is None:
                audit["out_of_range"] += 1
                continue
            buckets[partition][symbol].append(filing)

    for partition in PARTITION_ORDER:
        for symbol in candidates:
            buckets[partition][symbol].sort(key=lambda item: item.accepted_at, reverse=True)
    ambiguous.sort(key=lambda item: (item[0], item[1].filed_at), reverse=True)

    selected: list[tuple[str, object]] = []
    while any(buckets[partition][symbol] for partition in PARTITION_ORDER for symbol in candidates):
        for partition in PARTITION_ORDER:
            for symbol in candidates:
                items = buckets[partition][symbol]
                if items:
                    selected.append((symbol, items.pop(0)))
    return [*selected, *ambiguous], audit


def _partition_for_date(value: date, *, start_date: date, end_date: date) -> str | None:
    if not start_date <= value <= end_date:
        return None
    if value <= TRAIN_END:
        return "train"
    if value <= CALIBRATION_END:
        return "calibration"
    return "test"


def _selection_preview(
    pending: list[tuple[str, object]], *, limit: int, start_date: date, end_date: date
) -> list[dict[str, str]]:
    """Expose only planned stock/date/form metadata, never filing text or IDs."""
    preview: list[dict[str, str]] = []
    for symbol, filing in pending:
        if len(preview) >= limit:
            break
        published_at = _published_at(filing.accepted_at)
        if published_at is None:
            continue
        partition = _partition_for_date(published_at.date(), start_date=start_date, end_date=end_date)
        if partition is None:
            continue
        preview.append(
            {
                "partition": partition,
                "symbol": symbol,
                "published_date": published_at.date().isoformat(),
                "form": filing.form,
            }
        )
    return preview


def _published_at(raw: str | None) -> datetime | None:
    if not raw or (len(raw) == 14 and raw.isdigit()):
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(UTC)


def _read_manifest(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "v2-corpus-manifest-v1":
        raise ValueError("existing corpus manifest has an unsupported schema")
    return value


def _validate_existing_files(output_dir: Path, manifest: dict) -> None:
    for entry in manifest["source_snapshot_files"]:
        filename = entry["filename"]
        if Path(filename).name != filename:
            raise ValueError("manifest contains an unsafe filename")
        actual = hashlib.sha256((output_dir / filename).read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(f"saved source snapshot hash mismatch: {filename}")


def _write_manifest(path: Path, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect bounded V2 historical SEC source snapshots")
    parser.add_argument("--plan", action="store_true", help="discover gaps without downloading bodies or writing files")
    parser.add_argument("--max-new-documents", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-pages", type=int, default=3)
    args = parser.parse_args()
    provider = SecEdgarProvider()
    if args.plan:
        excluded_source_ids: set[str] = set()
        manifest_path = args.output_dir / MANIFEST_NAME
        if manifest_path.exists():
            manifest = _read_manifest(manifest_path)
            if manifest["symbols"] != list(SYMBOLS) or manifest["training_range"] != {
                "start": date(2021, 1, 1).isoformat(), "end": date(2026, 8, 31).isoformat()
            }:
                raise ValueError("existing corpus manifest is incompatible with the fixed V2 plan")
            _validate_existing_files(args.output_dir, manifest)
            excluded_source_ids = {entry["source_id"] for entry in manifest["source_snapshot_files"]}
        result = plan_corpus(
            provider=provider,
            max_pages=args.max_pages,
            max_new_documents=args.max_new_documents,
            excluded_source_ids=excluded_source_ids,
        )
        result.pop("candidates")
    else:
        result = build_corpus(
            provider=provider,
            output_dir=args.output_dir,
            resume=args.resume,
            max_new_documents=args.max_new_documents,
            max_pages=args.max_pages,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
