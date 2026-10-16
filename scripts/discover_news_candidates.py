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

from app.news_discovery import SUPPORTED_SYMBOLS, DiscoveryScope, GdeltDocSource, TechCrunchRssSource, discover_news_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default=",".join(SUPPORTED_SYMBOLS),
        help="Comma-separated validated stock symbols (default: seven sample tickers)",
    )
    parser.add_argument('--company-query',help='Literal company search query; requires exactly one symbol')
    parser.add_argument('--match-term',action='append',default=[],help='Literal title match term; repeat as needed')
    parser.add_argument('--exclude-term',action='append',default=[],help='Literal title exclusion; repeat as needed')
    parser.add_argument('--rss-tag',help='Explicit TechCrunch tag, e.g. a company slug')
    args = parser.parse_args()
    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    try:
        scopes={}
        if args.company_query or args.match_term or args.exclude_term or args.rss_tag:
            if len(symbols)!=1 or not args.company_query or not args.match_term:
                parser.error('Company scope requires one symbol, --company-query and at least one --match-term')
            scopes={symbols[0]:DiscoveryScope(args.company_query,tuple(args.match_term),tuple(args.exclude_term),args.rss_tag)}
        report = discover_news_candidates(symbols=symbols,sources=(TechCrunchRssSource(scopes=scopes),GdeltDocSource(scopes=scopes)))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
