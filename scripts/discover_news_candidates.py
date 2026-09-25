#!/usr/bin/env python3
"""Print public stock-news metadata candidates for manual review.

The command has no database, forecast, article-body, or model side effect.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.news_discovery import SUPPORTED_SYMBOLS, discover_news_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default=",".join(SUPPORTED_SYMBOLS),
        help="Comma-separated supported stock symbols (default: all five)",
    )
    args = parser.parse_args()
    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    try:
        report = discover_news_candidates(symbols=symbols)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
