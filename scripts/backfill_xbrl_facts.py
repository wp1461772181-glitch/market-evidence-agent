"""Store each fetched filing's reported XBRL figures beside its text.

Run after fetching filing text: ``python scripts/backfill_xbrl_facts.py --apply``.
Without ``--apply`` it prints what it would store and writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import SecFilingInventory  # noqa: E402
from app.sec_filings import SecEdgarProvider, create_sec_filing_inventory_table  # noqa: E402
from app.xbrl_financials import as_payload, collect, describe  # noqa: E402

# Only filings that state their own accounts carry XBRL figures worth lifting.
FACT_FORMS = ("10-K", "10-Q", "20-F")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="", help="comma separated tickers; default every fetched filing")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many filings")
    parser.add_argument("--apply", action="store_true", help="write the figures to the inventory rows")
    parser.add_argument("--force", action="store_true", help="refetch filings that already carry figures")
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    create_sec_filing_inventory_table()
    provider = SecEdgarProvider()
    stored = skipped = 0
    with SessionLocal() as db:
        query = select(SecFilingInventory).where(
            SecFilingInventory.content_status == "fetched",
            SecFilingInventory.form.in_(FACT_FORMS),
        ).order_by(SecFilingInventory.symbol, SecFilingInventory.filed_at.desc())
        if symbols:
            query = query.where(SecFilingInventory.symbol.in_(symbols))
        rows = db.scalars(query).all()
        for index, row in enumerate(rows):
            if args.limit and stored >= args.limit:
                break
            if row.content_financials and not args.force:
                skipped += 1
                continue
            figures = collect(provider, row.cik, form=row.form, filed_at=row.filed_at)
            payload = as_payload(figures)
            print(f"[{index + 1}/{len(rows)}] {row.symbol} {row.form} {row.filed_at} "
                  f"{row.accession_number}: {len(payload)} metrics")
            preview = describe(payload)
            if preview:
                print("    " + preview.splitlines()[1].strip())
            if not payload:
                skipped += 1
                continue
            if args.apply:
                row.content_financials = payload
                db.commit()
            stored += 1
    print(f"\n{'stored' if args.apply else 'would store'} {stored}, skipped {skipped}"
          f"{'' if args.apply else ' (dry run: pass --apply to write)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
