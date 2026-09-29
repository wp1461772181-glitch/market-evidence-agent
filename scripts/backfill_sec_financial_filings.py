"""Backfill Magnificent Seven SEC filings into the source library.

Discovery is read-only by default. Pass ``--apply`` to save filing metadata
and fetch bounded text excerpts through the existing SEC inventory path. Each
row retains its SEC filing/acceptance time and the actual current observation
time, so a backfill cannot masquerade as historically observed evidence.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import SecFilingInventory
from app.sec_filings import (
    SEC_SOURCE,
    MAX_PRIMARY_DOCUMENT_BYTES,
    SecEdgarProvider,
    SecFilingsError,
    canonical_sec_filing_url,
    fetch_inventory_content,
)


SUPPORTED_FORMS = frozenset({"10-K", "10-Q", "8-K"})
FINANCIAL_FORMS = frozenset({"10-K", "10-Q"})
ALL_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
MAX_HISTORY_PAGES = 100
MAX_HISTORY_FILINGS = 5_000


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _history(
    provider: SecEdgarProvider,
    start: date,
    end: date,
    *,
    symbols: tuple[str, ...],
    forms: frozenset[str],
) -> dict[str, tuple]:
    result: dict[str, tuple] = {}
    for symbol in symbols:
        coverage = provider.discover_between(
            symbol,
            start_date=start,
            end_date=end,
            max_pages=MAX_HISTORY_PAGES,
            max_filings=MAX_HISTORY_FILINGS,
        )
        if not coverage.complete:
            raise SecFilingsError(
                f"SEC history scan for {symbol} is incomplete; next page: {coverage.next_page or 'filing cap reached'}"
            )
        filings = tuple(filing for filing in coverage.filings if filing.form in forms)
        result[symbol] = filings
    return result


def _save_metadata(db: Session, history: dict[str, tuple], observed_at: datetime) -> dict[str, int]:
    created: dict[str, int] = {}
    for symbol, filings in history.items():
        accessions = [filing.accession_number for filing in filings]
        existing = {
            row.accession_number
            for row in db.query(SecFilingInventory)
            .filter(SecFilingInventory.symbol == symbol)
            .filter(SecFilingInventory.accession_number.in_(accessions))
            .all()
        } if accessions else set()
        new_count = 0
        for filing in filings:
            if filing.accession_number in existing:
                continue
            db.add(
                SecFilingInventory(
                    symbol=symbol,
                    cik=filing.cik,
                    accession_number=filing.accession_number,
                    form=filing.form,
                    filed_at=filing.filed_at,
                    accepted_at=filing.accepted_at,
                    primary_document=filing.primary_document,
                    source_url=canonical_sec_filing_url(
                        cik=filing.cik,
                        accession_number=filing.accession_number,
                        primary_document=filing.primary_document,
                    ),
                    source=SEC_SOURCE,
                    review_status="pending_review",
                    human_review_note=None,
                    reviewed_at=None,
                    content_status="not_fetched",
                    observed_at=observed_at,
                    content_observed_at=None,
                    content_excerpt=None,
                    content_excerpt_sha256=None,
                    content_truncated=False,
                    content_error=None,
                    content_source_url=None,
                    content_document_name=None,
                    content_kind=None,
                    related_attachment_status="not_checked" if filing.form == "8-K" else "not_applicable",
                    related_attachment_error=None,
                )
            )
            new_count += 1
        created[symbol] = new_count
    db.commit()
    return created


def _fetch_excerpts(
    db: Session,
    provider: SecEdgarProvider,
    *,
    start: date,
    end: date,
    observed_at: datetime,
    symbols: tuple[str, ...],
    forms: frozenset[str],
) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    for symbol in symbols:
        rows = (
            db.query(SecFilingInventory)
            .filter(
                SecFilingInventory.symbol == symbol,
                SecFilingInventory.form.in_(forms),
                SecFilingInventory.filed_at >= start,
                SecFilingInventory.filed_at <= end,
            )
            .order_by(SecFilingInventory.filed_at, SecFilingInventory.accession_number)
            .all()
        )
        fetched = sum(row.content_status == "fetched" for row in rows)
        unavailable = 0
        pending = [row for row in rows if row.content_status != "fetched"]
        for index, row in enumerate(pending, start=1):
            saved, _ = fetch_inventory_content(
                symbol=symbol,
                accession_number=row.accession_number,
                db=db,
                provider=provider,
                observed_at=observed_at,
                content_observed_at_factory=lambda: datetime.now(UTC),
            )
            if saved.content_status == "fetched":
                fetched += 1
            else:
                unavailable += 1
            if index % 10 == 0 or index == len(pending):
                print(f"{symbol}: 原文摘要 {index}/{len(pending)}，成功 {fetched}，暂不可用 {unavailable}", flush=True)
        results[symbol] = {"total": len(rows), "fetched": fetched, "unavailable": unavailable}
    return results


def main() -> int:
    default_end = datetime.now(UTC).date()
    try:
        default_start = default_end.replace(year=default_end.year - 10)
    except ValueError:
        default_start = default_end.replace(year=default_end.year - 10, day=28)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=_parse_date, default=default_start)
    parser.add_argument("--end-date", type=_parse_date, default=default_end)
    parser.add_argument("--symbols", nargs="+", choices=ALL_SYMBOLS, default=ALL_SYMBOLS)
    parser.add_argument("--include-8k", action="store_true", help="also include 8-K current reports")
    parser.add_argument("--apply", action="store_true", help="save filings and fetch text excerpts; without this flag only report counts")
    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")

    forms = FINANCIAL_FORMS | ({"8-K"} if args.include_8k else set())
    provider = SecEdgarProvider(max_primary_document_bytes=MAX_PRIMARY_DOCUMENT_BYTES)
    symbols = tuple(dict.fromkeys(args.symbols))
    history = _history(
        provider,
        args.start_date,
        args.end_date,
        symbols=symbols,
        forms=frozenset(forms),
    )
    print(f"SEC {'/'.join(sorted(forms))} filings from {args.start_date} through {args.end_date} (UTC)")
    for symbol in symbols:
        print(f"{symbol}: {len(history[symbol])} filings")
    if not args.apply:
        print("只读预览；使用 --apply 才会写入资料库并抓取材料摘要。")
        return 0

    observed_at = datetime.now(UTC)
    with SessionLocal() as db:
        created = _save_metadata(db, history, observed_at)
        print("新增资料库记录：" + ", ".join(f"{symbol} {created[symbol]}" for symbol in created), flush=True)
        fetched = _fetch_excerpts(
            db,
            provider,
            start=args.start_date,
            end=args.end_date,
            observed_at=observed_at,
            symbols=symbols,
            forms=frozenset(forms),
        )
    print("拉取结果（已保存的摘要 / 资料库总数 / 本次暂不可用）：")
    for symbol, counts in fetched.items():
        print(f"{symbol}: {counts['fetched']} / {counts['total']} / {counts['unavailable']}")
    print("每条材料都保留 SEC 原文链接；较长文件仍可直接从链接打开。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
