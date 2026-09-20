# Does refitting each month help?

The original experiment fits once through December 2022 and then predicts each
month in 2023 and 2024 using the actual earlier observations for lag features.
I wanted to test what happens if the model also learns from those new
observations as they arrive.

`src.backtest` does that comparison. It starts with the same 60 usable training
months, predicts January 2023, observes January's actual volume, refits, and
predicts February. It continues through December 2024. Every forecast is one
month ahead. The first 12 observations supply lag history and do not count as
usable training rows.

## Recorded result

This is the included synthetic shipment series. Errors are in moves per month;
lower is better. Values are rounded here, with full precision in
[the JSON report](backtest-example.json).

| Target months | Months | Monthly refit MAE | Seasonal baseline MAE | Model better / worse |
|---|---:|---:|---:|---:|
| All | 24 | 245.1 | 326.8 | 17 / 7 |
| 2023 | 12 | 253.7 | 246.9 | 5 / 7 |
| 2024 | 12 | 236.6 | 406.8 | 12 / 0 |
| Peak, May to August | 8 | 480.4 | 602.8 | 6 / 2 |
| Other months | 16 | 127.5 | 188.9 | 11 / 5 |

Refitting reduces average error by about 25% against the seasonal baseline in
this experiment. It loses to that baseline in 2023. It also does worse overall
than the original fixed model's roughly 226 MAE on the same target months.
More frequent training is not automatically an improvement, so this evaluation
does not change the serving policy or replace its artifact.

## Reproduce it

After installing `requirements-dev.txt`, run from the repository root:

```sh
python -m src.backtest --output reports/backtest.json
```

`make backtest` runs the same command. No trained artifact is required. The
command reads the existing CSV and produces a report; it does not regenerate
data, save a model, or reject an experiment because the baseline wins. Omit
`--output` to print JSON to stdout.

To explore another starting window or a compatible dataset:

```sh
python -m src.backtest --data data/shipments.csv --initial-train-months 36 --output reports/earlier-start.json
```

That changes the evaluated months as well as the amount of initial training
data. Do not compare its overall MAE directly with the default run as if both
covered the same targets. The command requires at least 12 usable initial
training months and one subsequent evaluation month.

## What the report records

Each prediction includes its target month, last observed training month, number
of usable training rows, actual volume, both forecasts, and both absolute
errors. Summaries pool those errors across all months, each calendar year, and
the May to August peak period. A negative `mae_delta_model_minus_naive` means the
model did better. `mae_improvement_fraction` is null when baseline MAE is zero,
because a relative improvement has no denominator in that case. MAPE values
are fractions, so 0.035 means about 3.5%.

The command also records the exact input CSV's SHA256, hashes of the three
implementation files, Python version, and numerical package versions. Source
hashes normalize line endings to LF; the CSV hash deliberately identifies the
exact bytes read. A checkout that changes the CSV's line endings changes its
byte hash even when the observations are identical. Compare predictions with
a numerical tolerance across supported environments, not a promise of identical
floating point bits.

Input must contain `date` and `volume` columns, one observation per consecutive
month starting on the first, and finite positive volumes. Zero is rejected
because this estimator takes the logarithm of its target. The input is sorted
by date without changing the caller's frame. Gaps, duplicate months, invalid
volumes, and insufficient history fail before any evaluation report is written.

## Checks and limits

[The tests](../tests/test_backtest.py) change current and future observations
and confirm that earlier forecasts stay unchanged. They also compare the first
fit with the existing training model, independently reconcile grouped errors,
check input validation, and verify that the command never saves a model artifact.

This is one series with overlapping training histories. Its 24 predictions are
not 24 independent experiments. The report does not estimate a confidence
interval, model selection significance, or future demand range. It does not
test multi month forecasts, tune parameters, or establish performance on
operational data. Both policies have now been examined on this test period;
choosing between them and claiming a fresh result would require new data.
