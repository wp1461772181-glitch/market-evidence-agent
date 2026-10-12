"""Deterministic selection of saved filing metadata, independent of index time."""
from __future__ import annotations

from datetime import date
from typing import Any


def choose_filing(rows: list[dict[str, Any]], *, accession_number: str | None = None,
                  form: str | None = None, year: int | None = None,
                  financial_only: bool = False) -> dict[str, Any] | None:
    eligible = [row for row in rows if
                (not accession_number or row.get("accession_number") == accession_number)
                and (not form or row.get("form") == form)
                and (not financial_only or row.get("form") in {"10-Q", "10-K"})
                and (not year or str(row.get("filed_at", ""))[:4] == str(year))]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (str(row.get("filed_at") or date.min),
                                          str(row.get("accession_number") or "")))
