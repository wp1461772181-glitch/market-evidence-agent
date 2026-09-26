#!/usr/bin/env python3
"""Check Jev configuration, or make one explicitly requested live probe."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.jev_provider import (
    DEFAULT_JEV_MODEL,
    JevProviderError,
    JevDecisionProvider,
    configured_jev_model,
    create_jev_provider_from_env,
)


_SYNTHETIC_BRIEF = {
    "schema_version": "research-brief-v1",
    "symbol": "DEMO",
    "decision_at": "illustrative-only",
    "target_contract": {
        "description": "Synthetic probe example, not a real security or forecast.",
        "anchor_close": 100.0,
        "target_date": "illustrative future date",
    },
    "market_summary": {"description": "No market data supplied; this is only a connectivity probe."},
    "material_refs": [],
    "new_facts": [],
    "supporting": [],
    "counter": [],
    "background": [],
    "conflicts": [],
    "unknowns": ["Synthetic probe contains no evidence or actual market data."],
    "input_quality": {"status": "insufficient", "reasons": ["Connectivity-only synthetic input."]},
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="make one live Jev application call")
    args = parser.parse_args(argv)

    try:
        model = configured_jev_model()
    except JevProviderError as exc:
        print(json.dumps({"status": "error", "safe_code": exc.code}, ensure_ascii=False))
        return 2

    if not args.live:
        JevDecisionProvider._load_project_env()
        print(json.dumps({
            "status": "configured" if os.getenv("OPENROUTER_API_KEY") else "missing_key",
            "live": False,
            "endpoint": "https://openrouter.ai/api/alpha/decisions",
            "requested_model": model,
            "application_calls": 0,
            "http_attempts": 0,
        }, ensure_ascii=False))
        return 0 if model == DEFAULT_JEV_MODEL else 2

    provider = None
    try:
        provider = create_jev_provider_from_env()
        result = provider.evaluate(_SYNTHETIC_BRIEF)
    except JevProviderError as exc:
        print(json.dumps({
            "status": "error",
            "safe_code": exc.code,
            "status_code": exc.status_code,
            "retryable": exc.retryable,
            "application_calls": 1,
            "http_attempts": provider.last_http_attempts if provider else 0,
        }, ensure_ascii=False))
        return 1

    print(json.dumps({
        "status": "success",
        "requested_model": result.requested_model,
        "actual_model": result.actual_model,
        "request_id": result.request_id,
        "usage": result.usage,
        "probabilities": result.probabilities,
        "choice": result.choice,
        "confidence": result.confidence,
        "latency_ms": result.latency_ms,
        "application_calls": 1,
        "http_attempts": provider.last_http_attempts,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
