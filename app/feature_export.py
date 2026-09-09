"""Export leakage-safe market features from one reproducible snapshot."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from typing import TextIO

from .features import build_features_from_snapshot, metadata


DEFAULT_SYMBOLS = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-time", required=True, help="ISO-8601 timezone-aware timestamp")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--source", default="yahoo-finance-chart")
    parser.add_argument("--mode", choices=("historical_research", "observed"), default="historical_research")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--output", default="-", help="Output path, or - for stdout")
    args = parser.parse_args()

    try:
        as_of_time = datetime.fromisoformat(args.as_of_time.replace("Z", "+00:00"))
    except ValueError as exc:
        parser.error(f"--as-of-time must be ISO-8601: {exc}")
    if as_of_time.tzinfo is None or as_of_time.utcoffset() is None:
        parser.error("--as-of-time must include a timezone")

    report = build_features_from_snapshot(
        args.symbols,
        as_of_time=as_of_time,
        source=args.source,
        mode=args.mode,
    )
    export_metadata = metadata(as_of_time=as_of_time, source=args.source, mode=args.mode)
    export_metadata.update(
        {
            "symbols": sorted({*(symbol.strip().upper() for symbol in args.symbols), "SPY"}),
            "row_count": len(report.rows),
            "skipped_by_reason": report.skipped_by_reason,
        }
    )

    output: TextIO
    close_output = False
    if args.output == "-":
        output = sys.stdout
    else:
        output = open(args.output, "w", encoding="utf-8", newline="")
        close_output = True
    try:
        if args.format == "json":
            json.dump(
                {
                    "metadata": export_metadata,
                    "rows": [row.to_dict() | {"trading_date": row.trading_date.isoformat()} for row in report.rows],
                    "skips": [
                        {"symbol": skip.symbol, "trading_date": skip.trading_date.isoformat(), "reason": skip.reason}
                        for skip in report.skips
                    ],
                },
                output,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
        else:
            output.write("# metadata=" + json.dumps(export_metadata, sort_keys=True) + "\n")
            writer = csv.DictWriter(output, fieldnames=tuple(report.rows[0].to_dict()) if report.rows else ())
            if report.rows:
                writer.writeheader()
                for row in report.rows:
                    payload = row.to_dict()
                    payload["trading_date"] = row.trading_date.isoformat()
                    writer.writerow(payload)
    finally:
        if close_output:
            output.close()


if __name__ == "__main__":
    main()
