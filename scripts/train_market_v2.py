#!/usr/bin/env python3
"""Plan or create one immutable V2 market-only baseline artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.training_data_v2 import STOCK_SYMBOLS, load_v2_market_training_data
from app.training_v2 import (
    DEFAULT_ARTIFACT_ROOT,
    MarketTrainingError,
    build_market_training_plan,
    train_market_baseline,
    write_market_artifact,
)


DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "v2" / "dataset-manifest.json"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--plan", action="store_true", help="Read and validate data only (the default)")
    action.add_argument("--apply", action="store_true", help="Fit once and append a new immutable artifact")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Frozen V2 dataset manifest")
    parser.add_argument("--symbols", default=",".join(STOCK_SYMBOLS), help="Comma-separated stock symbols")
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT, help="Local V2 artifact root")
    parser.add_argument("--run-id", help="Required with --apply; creates market-<run-id>")
    args = parser.parse_args(argv)

    if args.run_id and not args.apply:
        parser.error("--run-id is only valid with --apply")
    if args.apply and not args.run_id:
        parser.error("--apply requires --run-id")
    symbols = tuple(part.strip().upper() for part in args.symbols.split(",") if part.strip())
    try:
        dataset = load_v2_market_training_data(args.manifest, symbols=symbols)
        plan = build_market_training_plan(dataset)
    except (MarketTrainingError, ValueError, OSError) as exc:
        print(json.dumps({"mode": "apply" if args.apply else "plan", "data_state": "blocked", "model_state": "not_trained", "error": str(exc)}, sort_keys=True))
        return 2

    payload: dict[str, object] = {
        "mode": "apply" if args.apply else "plan",
        "data_state": "ready",
        "model_state": "not_trained",
        "manifest": str(args.manifest),
        "row_counts": plan.row_counts,
        "class_counts": plan.class_counts,
        "feature_columns": list(plan.feature_columns),
        "joint_probabilities": None,
    }
    if not args.apply:
        print(json.dumps(payload, sort_keys=True))
        return 0

    try:
        result = train_market_baseline(dataset, manifest_path=args.manifest)
        output_dir = write_market_artifact(result, artifact_root=args.artifact_root, run_id=args.run_id)
    except (MarketTrainingError, ValueError, OSError) as exc:
        payload.update({"data_state": "ready", "model_state": "blocked", "error": str(exc)})
        print(json.dumps(payload, sort_keys=True))
        return 2
    payload.update({"model_state": "trained_research_only", "artifact": str(output_dir), "test_metrics": result.test_metrics})
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
