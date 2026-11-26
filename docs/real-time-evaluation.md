# Testing the model on only what was known at the time

The [earlier BTS study](real-data-evaluation.md) used one latest vintage of the
Freight Transportation Services Index. Its README admits that revised values may
carry information a forecaster did not have. This report replays the same model
on the values as they stood at each origin and scores it two ways. The model
still loses to last observation. That stays on record.

The [protocol](real-time-protocol.json) was merged before any vintage was fetched
or any error on it was computed. The estimator, its features and the build gate
are the existing ones. Nothing was tuned.

## What was replayed

The series is `TSIFRGHT` from ALFRED, the vintage archive of the St. Louis Fed.
The [retained table](../data/public/tsifrght-alfred-vintages.json) holds all 136
vintages from October 8, 2014 to November 1, 2026. It was fetched once by hand
with [a script](../tools/fetch_alfred_vintages.py), with no account and no key, and
its hash is in the manifest. The [code](../src/real_time_backtest.py) and the
[saved report](real-time-example.json) are in the repository.

For each target month from January 2020 to December 2025, the origin is the first
vintage that holds the month before the target. The forecaster sees every month
through that one, as published on that vintage, and nothing from a later vintage. A
[test](../tests/test_vintages.py) covers all 72 origins and fails if a later value
or a value from the target month is planted. Three forecasters are scored:
the existing `StandardScaler` and `Ridge(alpha=1.0)` model refit at every origin,
last observation, and the same month last year.

Two outcomes score each forecast. First release is the value of the target month
on the first vintage that holds it. Latest values are the value on the November 1,
2026 vintage. Mean absolute revision from first release to latest value was 1.49
index points, with a largest revision of 4.60. That is the same size as the errors
being compared.

## Result

Lower errors are better. MAE and RMSE are in index points. MAPE is a percentage.

Scored against the first release:

| Method | MAE | RMSE | MAPE |
|---|---:|---:|---:|
| Monthly refit Ridge | 1.878 | 2.653 | 1.394% |
| Last observed month | 1.551 | 2.302 | 1.147% |
| Same month last year | 3.138 | 4.548 | 2.329% |

Scored against the latest values:

| Method | MAE | RMSE | MAPE |
|---|---:|---:|---:|
| Monthly refit Ridge | 2.494 | 3.438 | 1.858% |
| Last observed month | 1.850 | 2.706 | 1.376% |
| Same month last year | 2.828 | 4.205 | 2.122% |

For comparison, the earlier study on one latest vintage gave 1.778, 1.260 and
2.783 for MAE in the same order. Last observation wins in both real time
scorings, as it did there. The model beats the same month last year on both
outcomes overall, and the margin is smaller against the latest values. The
yearly MAE below shows where the ranking changes.

| Year | Ridge, first release | Last observation, first release | Same month last year, first release |
|---|---:|---:|---:|
| 2020 | 2.975 | 2.267 | 6.325 |
| 2021 | 2.346 | 1.758 | 4.408 |
| 2022 | 1.907 | 1.575 | 4.092 |
| 2023 | 1.033 | 1.067 | 1.967 |
| 2024 | 1.552 | 1.617 | 0.925 |
| 2025 | 1.453 | 1.025 | 1.108 |

Against first releases the model has the lowest error of the three in 2023 only
by a narrow margin, and the same month last year has it in 2024. Against the
latest values the model is behind last observation in every year, and the same
month last year beats it in 2021, 2022 and 2024, as the saved report shows. The
report retains every prediction, both outcomes and every paired absolute error.

## Limits

1. ALFRED vintage dates are the dates FRED took the data in. They trail the BTS
   release by days or weeks, so this replay is slightly kinder to the forecaster
   than the real wait. Some vintages arrived late, and the origin for December
   2025 is dated February 2, 2026.
2. ALFRED vintages for this series begin in October 2014, so the development
   years of the earlier study cannot be replayed. The monthly refit policy was
   chosen on that earlier study, which the protocol discloses.
3. One observation step is not a publication date simulation. Each target month
   is already underway when its origin vintage appears.
4. 72 dependent months on one national index are not a general result. No winner
   was chosen after the outcomes. Which of these gaps can be told apart from noise
   was tested later, under its own protocol, in the
   [comparison report](forecast-comparison.md).
5. The model does not change, so nothing here is a claim about a better model.
