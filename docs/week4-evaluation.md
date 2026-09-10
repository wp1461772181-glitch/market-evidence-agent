# Week 4: fixed offline baseline evaluation

This is a reproducible baseline, not a trading claim or an API model. It uses
the checked local feature export at `2026-09-04T21:00:00Z` in
`historical_research` mode. The input had 4,416 feature rows for AAPL, AMZN,
GOOGL, MSFT, NVDA, and SPY. SPY is excluded from supervised rows; 3,580 labelled
stock rows remain and 100 rows are excluded because their 20-session outcome was
not mature at the cutoff.

## Fixed definitions

- Features: 5- and 20-day momentum, 20-day volatility and volume ratio,
  20-day drawdown, and 20-day return relative to SPY.
- Target at date `t`: stock return from `t` through its 20th following XNYS
  session minus SPY's return over the same dates.
- Class threshold: `max(1e-8, 0.5 * volatility_20d(t) * sqrt(20 / 252))`;
  bearish is below the negative threshold, neutral includes equality, and
  bullish is above the positive threshold.
- Folds: three expanding date-ordered folds. Each has 84 test sessions (420
  stock rows) and a preceding 63-session calibration block (215 rows after the
  label-maturity purge). Training rows per fold are 1,905, 2,325, and 2,745.
  A training or calibration row is retained only when its `label_available_at`
  is strictly before the next role's earliest decision time.
- Models: `Pipeline(StandardScaler, LogisticRegression(C=1, max_iter=1000,
  random_state=42))`; the same fitted pipeline sigmoid-calibrated with
  `CalibratedClassifierCV(FrozenEstimator(...))`; and fixed class-prior,
  majority-class, and relative-momentum baselines. No test-fold tuning or model
  selection occurred.

The held-out date blocks are 2025-08-07–2025-12-04,
2025-12-05–2026-04-08, and 2026-04-09–2026-08-07.

## Pooled out-of-sample results

All rows below pool the three test blocks (1,260 rows per model). Lower Brier
score and log loss are better. The complete machine-readable fold reports,
confusion matrices, and calibration bins are in
[week4-metrics.json](week4-metrics.json).

| Model | Accuracy | Balanced accuracy | Macro F1 | Brier | Log loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raw logistic | 0.4516 | 0.3530 | 0.2655 | 0.6252 | 1.0465 |
| Sigmoid-calibrated logistic | 0.4508 | 0.3584 | 0.2974 | 0.6920 | 1.2237 |
| Class-prior baseline | 0.4460 | 0.3333 | 0.2056 | 0.6501 | 1.0756 |
| Majority-class baseline | 0.4460 | 0.3333 | 0.2056 | 1.1079 | 19.9670 |
| Relative-momentum baseline | 0.3222 | 0.2945 | 0.2939 | 1.3556 | 24.4296 |

Raw logistic slightly exceeds the class-prior baseline in accuracy and has
better probability scores. Calibration improves balanced accuracy and macro F1
here, but worsens Brier score and log loss relative to the raw pipeline. This
run does not justify adopting calibration or changing the model after seeing
these test blocks.

## Reproduce and inspect

```bash
.venv/bin/python -m app.training \
  --features exports/week3-features-2026-09-04.json \
  --output-dir artifacts/week4-2026-09-10
```

That directory is intentionally ignored by Git. It contains
`training_dataset.csv`, `oos_predictions.csv`, `report.json`,
`last_fold_calibrated_model.joblib`, and `manifest.json`. The manifest records
feature order, class order, library versions, input provenance, and the model
reload check. The saved joblib is only the last-fold calibrated model, so it is
not a full-data production model and the `/forecasts` API remains `mock-v1`.

## Limits carried forward

The historical research snapshot treats the initial backfill as available at
session close. It is reproducible and prevents later revisions from changing an
earlier example, but it does not prove that the provider supplied each bar at
that time. The 20-session forward labels overlap, so the 1,260 OOS rows are not
independent evidence of a trading strategy or profitability.
