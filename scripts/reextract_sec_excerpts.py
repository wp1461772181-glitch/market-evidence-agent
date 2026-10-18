"""Re-extract stored SEC filing excerpts with the current extractor.

``sec_filing_inventory`` freezes the excerpt produced by whatever extractor ran
at fetch time, so an extractor fix does not reach filings that are already
cached. Pass ``--apply`` to re-download and re-extract them through the normal
``fetch_inventory_content`` path (same provenance, hashes, size limits, and SEC
rate limiting). Discovery is untouched: only ``content_*`` columns change.
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select

from app.database import SessionLocal
from app.models import SecFilingInventory
from app.sec_filings import SecEdgarProvider, SecFilingsError, fetch_inventory_content

# Mirrors app/evidence_context.py:MAX_ANALYSIS_CHARACTERS: the analysis window is what
# decided whether a filing was usable, so pollution is judged there.
ANALYSIS_WINDOW = 24_000

# Inline-XBRL labels are the signature of the pre-2026-10 extractor, which let
# hidden ``ix:header``/``ix:hidden`` text outrank the filing's real prose.
POLLUTION_MARKERS = ("us-gaap:", "iso4217:", "xbrli:", "srt:", "ix:")


def _polluted(excerpt: str | None) -> bool:
    if not excerpt:
        return False
    head = excerpt[:ANALYSIS_WINDOW]
    return any(marker in head for marker in POLLUTION_MARKERS)


def _preview(excerpt: str | None) -> str:
    if not excerpt:
        return "<empty>"
    return " / ".join(excerpt[:ANALYSIS_WINDOW].splitlines()[:2])[:80]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", help="limit to one or more symbols")
    parser.add_argument("--accession", action="append", help="limit to one or more accession numbers")
    parser.add_argument(
        "--only-polluted",
        action="store_true",
        help="re-extract only excerpts that still carry hidden-XBRL labels",
    )
    parser.add_argument("--limit", type=int, default=None, help="stop after this many filings")
    parser.add_argument("--apply", action="store_true", help="write results (default is a dry run)")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        query = select(SecFilingInventory).where(SecFilingInventory.content_status == "fetched")
        if args.symbol:
            query = query.where(SecFilingInventory.symbol.in_([s.upper() for s in args.symbol]))
        if args.accession:
            query = query.where(SecFilingInventory.accession_number.in_(args.accession))
        rows = list(db.scalars(query.order_by(SecFilingInventory.symbol, SecFilingInventory.filed_at)))
        if args.only_polluted:
            rows = [row for row in rows if _polluted(row.content_excerpt)]
        if args.limit:
            rows = rows[: args.limit]

        print(f"{len(rows)} filing(s) selected; apply={args.apply}")
        provider = SecEdgarProvider() if args.apply and rows else None
        outcomes: Counter[str] = Counter()
        for index, row in enumerate(rows, start=1):
            before = (row.content_status, len(row.content_excerpt or ""), row.related_attachment_status)
            if not args.apply:
                print(f"[{index}/{len(rows)}] {row.symbol} {row.form} {row.accession_number} {_preview(row.content_excerpt)}")
                outcomes["planned"] += 1
                continue
            try:
                filing, _ = fetch_inventory_content(
                    symbol=row.symbol,
                    accession_number=row.accession_number,
                    db=db,
                    provider=provider,
                    force_refresh=True,
                )
                db.commit()
                status = "refetched" if filing.content_status == "fetched" else filing.content_status
                outcomes[status] += 1
                print(
                    f"[{index}/{len(rows)}] {row.symbol} {row.form} {row.accession_number} "
                    f"{before[1]}->{len(filing.content_excerpt or '')} chars "
                    f"truncated={filing.content_truncated} "
                    f"attachment={before[2]}->{filing.related_attachment_status} "
                    f"| {_preview(filing.content_excerpt)}"
                )
            except SecFilingsError as exc:
                db.rollback()
                outcomes[f"error:{exc.code}"] += 1
                print(f"[{index}/{len(rows)}] {row.symbol} {row.accession_number} failed: {exc.code}")
        print("outcomes:", dict(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
