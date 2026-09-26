# Jev through OpenRouter

`app/jev_provider.py` implements the Decisions API as an independent adapter. It sends a validated research brief as `state` to `https://openrouter.ai/api/alpha/decisions` and asks one `choice` question named `direction`. The criteria are exactly `bullish`, `neutral`, and `bearish`. This adapter does not use Chat Completions and is not connected to the production forecast default.

The request and response shape was checked against [OpenRouter's Jev Decisions example](https://openrouter.ai/blog/insights/what-is-jev/), [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice), and [TypeSafe state](https://docs.typesafe.ai/concepts/state). The documented request has top-level `model`, `state`, and `questions`; each choice question supplies `type`, `instructions`, and a `criteria` map. A response carries `answers.<question_id>.choice`, a probability for each option, and optional confidence, with actual model, usage, and request ID at the top level. The project's request shape matches those fields. TypeSafe permits structured JSON as state, which is appropriate for the ResearchBrief object. No response schema discrepancy was found in the documentation checked on 2026-09-26.

## Configuration and probe

Copy the empty OpenRouter entries from `.env.example` into the ignored project `.env`, then place the key there. `create_jev_provider_from_env()` loads only this repository's `.env` (without replacing process environment values) and reads `OPENROUTER_API_KEY`; it never searches personal key files. The pinned default is `typesafe/jev-1.13`. Other model IDs are rejected so a failed Jev call cannot silently become a different model's result.

Run a local configuration check without making a provider call:

```sh
.venv/bin/python scripts/probe_jev.py
```

Make the explicitly live connectivity probe only when authorized:

```sh
.venv/bin/python scripts/probe_jev.py --live
```

The live probe makes exactly one application-level `evaluate()` call using a synthetic, evidence-free demonstration brief. It does not read a database or write files. A call can make up to three HTTP attempts total: the initial attempt plus at most two retries, with short bounded backoff. Only 429, 5xx, and transport failures retry. 401/402/403, request-format failures, and malformed responses do not. Probe output reports application-call and actual HTTP-attempt counts, plus safe metadata; it never prints the key or an upstream error body.

## Validation and limits

The adapter rejects missing/extra probability classes, booleans, non-finite/out-of-range values, a distribution not summing to one within `1e-6`, and a choice that is not one of the maximum-probability classes. It does not normalize malformed output or invent a result after an HTTP or validation error. HTTP failures expose only a safe code and status. The result retains requested and actual model, request ID, usage, latency, question version, and SHA-256 of the canonical input brief.

Jev's confidence describes probability concentration; it is not a measure of correctness for this stock-return task. The three outcomes encode target close return above +2%, between -2% and +2% inclusive, or below -2% versus the original anchor close. The result is experimental and uncalibrated for the market task. This provider is an interface and a limited connectivity probe, not evidence of forecast performance or production integration.

## Live connectivity probe (2026-09-26)

One authorized probe ran at approximately 09:02:10 UTC (17:02:10 MYT). It made **1 application call and 1 HTTP attempt**, succeeded with requested model `typesafe/jev-1.13` and actual model `typesafe/jev-1.13-20260917`, and returned request ID `gen-dec-1790413330-1xY9EGs3gpoTCUWinFtA`. Usage was 612 input tokens, 41 output tokens, cost `$0.000025704`; elapsed time was 734 ms. The returned distribution was bullish 0.0, neutral 1.0, bearish 0.0 (choice `neutral`, confidence 1.0).

This was a connectivity-only call using the script's synthetic `DEMO` brief with no market data, materials, or database access. It does not validate any stock forecast, model calibration, or production workflow.
