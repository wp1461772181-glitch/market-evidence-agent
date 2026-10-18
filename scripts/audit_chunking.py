"""Measure what the chunker actually covers, and how trustworthy its section labels are.

The full-text rebuild made whole filings available, but the chunker still decides
what text reaches the index and what each piece is called.  Both are heuristics, so
before trusting them for analysis we measure them on the real corpus:

  coverage   the union of every chunk's [start, end) must reach the last character
             of the document; a gap is text that no chunk carries
  labels     a section path should name an Item/Part; anything else is a guess
  anchors    balance-sheet and income-statement phrases must fall inside a chunk

Nothing is written: chunks are built in memory and thrown away.

    .venv-rag/bin/python scripts/audit_chunking.py             # whole corpus
    .venv-rag/bin/python scripts/audit_chunking.py --symbol=META --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from sqlalchemy import text

sys.path.insert(0, ".")

from app.database import engine  # noqa: E402
from app.knowledge.chunking import build_chunks, coverage_gaps  # noqa: E402
from app.knowledge.runtime import get_tokenizer  # noqa: E402
from app.knowledge.sources import adapt_sec  # noqa: E402

ANCHORS = {
    "10-K": ("CONSOLIDATED BALANCE SHEET", "CONSOLIDATED STATEMENTS OF OPERATIONS", "Total assets", "Net income"),
    "10-Q": ("CONDENSED CONSOLIDATED BALANCE SHEET", "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS", "Total assets", "Net income"),
    "8-K": ("Revenues", "Net income", "Cash and cash equivalents"),
    "DEF 14A": ("Director", "Compensation"),
}


def _intervals(chunks) -> list[tuple[int, int]]:
    spans = sorted((c.start_offset, c.end_offset) for c in chunks if c.end_offset > c.start_offset)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _gaps(spans: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        gaps.append((cursor, length))
    return gaps


def _unlabeled(section: str) -> bool:
    """A chunk that never fell under any recognisable section heading."""
    return not (section or "").strip()


def _suspect_label(section: str) -> bool:
    """A label that is present but does not read like a section title.

    Item/Part headings are trusted.  Any other label must at least look like a
    title: no sentences, no numbers, and short.  This is deliberately a second,
    independent opinion -- it must not reuse the chunker's own rules.
    """
    head = (section or "").strip().split(" > ")[0]
    if not head:
        return False
    if head.lower().startswith(("item ", "part ")):
        return False
    return (len(head) > 80 or any(ch.isdigit() for ch in head)
            or head.count(" ") > 13 or head.rstrip().endswith("."))


def audit_one(row, tokenizer, verbose: bool = False) -> dict | None:
    document = adapt_sec(row)
    if document is None:
        return None
    chunks = build_chunks(document, tokenizer)
    length = len(document.text)
    spans = _intervals(chunks)
    # Whitespace between lines is layout, not content: only a gap that hides real
    # characters counts as text the index never received.
    gaps = coverage_gaps(document.text, chunks)
    covered = sum(end - start for start, end in spans)

    missing_anchors = []
    for phrase in ANCHORS.get(str(row["form"]), ()):
        position = document.text.find(phrase)
        while position >= 0:
            if not any(start <= position < end for start, end in spans):
                missing_anchors.append(phrase)
                break
            position = document.text.find(phrase, position + 1)

    labels = [chunk.section_path for chunk in chunks]
    suspects = sum(1 for label in labels if _suspect_label(label))
    unlabeled = sum(1 for label in labels if _unlabeled(label))
    result = {
        "symbol": row["symbol"],
        "form": row["form"],
        "accession": str(row["id"])[:8],
        "length": length,
        "chunks": len(chunks),
        "coverage_pct": round(100 * covered / length, 2) if length else 100.0,
        "gap_chars": sum(end - start for start, end in gaps),
        "longest_gap": max((end - start for start, end in gaps), default=0),
        "suspect_labels": suspects,
        "unlabeled": unlabeled,
        "label_pct": round(100 * suspects / len(chunks), 2) if chunks else 0.0,
        "missing_anchors": missing_anchors,
    }
    if verbose:
        print(f"  {result['symbol']:6s} {result['form']:7s} {result['accession']} "
              f"coverage {result['coverage_pct']:6.2f}%  gaps {result['gap_chars']:6d}  "
              f"labels {result['label_pct']:5.2f}%  anchors missing {missing_anchors}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", default=None, help="write per-filing results here")
    args = parser.parse_args()

    query = """select id, symbol, form, content_text, content_text_sha256, content_status,
                      content_excerpt, content_excerpt_sha256, content_observed_at, observed_at,
                      filed_at, content_source_url, source_url, content_document_name,
                      primary_document, content_kind, content_truncated, related_attachment_status,
                      content_observed_at as content_observed_at2
                 from sec_filing_inventory
                where content_status = 'fetched' and content_text is not null"""
    params: dict = {}
    if args.symbol:
        query += " and symbol = :symbol"
        params["symbol"] = args.symbol
    query += " order by symbol, filed_at"
    if args.limit:
        query += f" limit {int(args.limit)}"

    tokenizer = get_tokenizer()
    results, skipped = [], 0
    with engine.connect() as conn:
        for row in conn.execute(text(query), params).mappings():
            item = audit_one(row, tokenizer, verbose=args.verbose)
            if item is None:
                skipped += 1
                continue
            results.append(item)

    by_form: dict[str, list[dict]] = defaultdict(list)
    for item in results:
        by_form[item["form"]].append(item)

    print(f"\nfilings audited {len(results)}  (skipped {skipped})\n")
    print(f"{'form':8s}{'docs':>6s}{'chunks':>8s}{'coverage':>10s}{'gap chars':>11s}"
          f"{'suspect':>10s}{'unlabeled':>11s}{'bad anchors':>13s}")
    for form, items in sorted(by_form.items()):
        worst = min(item["coverage_pct"] for item in items)
        print(f"{form:8s}{len(items):>6d}{sum(i['chunks'] for i in items):>8d}"
              f"{worst:>9.2f}%{sum(i['gap_chars'] for i in items):>11d}"
              f"{sum(i['suspect_labels'] for i in items):>10d}"
              f"{sum(i['unlabeled'] for i in items):>11d}"
              f"{sum(len(i['missing_anchors']) for i in items):>13d}")

    broken = [item for item in results if item["gap_chars"] or item["missing_anchors"]]
    print(f"\nfilings with lost text or a lost anchor: {len(broken)}")
    for item in sorted(broken, key=lambda i: -i["gap_chars"])[:8]:
        print(f"  {item['symbol']:6s} {item['form']:7s} {item['accession']} "
              f"gaps {item['gap_chars']:6d} (longest {item['longest_gap']:5d}) "
              f"missing {item['missing_anchors']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=1)
        print(f"\nper-filing results -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
