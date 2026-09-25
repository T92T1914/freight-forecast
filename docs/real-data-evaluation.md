# Testing the existing model on real freight activity

The existing model beats a seasonal baseline on the synthetic shipment series.
On this separate public-data test, it also beats that baseline, but loses to the
simpler prediction that next month's index will equal this month's. That is the
result worth keeping. A seasonal baseline alone would have made the model look
more useful than this comparison supports.

This is the Bureau of Transportation Statistics' Freight Transportation Services
Index, a national measure of domestic for-hire freight activity. Its units are
index points, with the 2000 average equal to 100. It is seasonally adjusted. It
does not measure household-goods moves, a particular carrier's shipments, or
local staffing requirements. The [BTS methodology](https://www.bts.gov/learn-about-bts-and-our-work/statistical-methods-and-policies/technical-note-tsi-documentation)
explains the included modes and weighting.

## What was fixed before evaluation

The [protocol](real-data-protocol.json) records the periods, estimator, selection
rule and data-access disclosure. The periods were declared before inspecting CSV
values or model errors. Later source research incidentally displayed some
2023-2025 actual index levels. The test values were not completely blinded, but
no test-period forecast results informed model or policy selection.

| Block | Months | Use |
|---|---|---|
| Initial history | 2000-01 through 2009-12 | First 12 months provide lags. The remaining 108 fit the first model. |
| Development | 2010-01 through 2019-12 | Compare fixed fitting with monthly refitting on 120 matching targets. |
| Final test | 2020-01 through 2025-12 | Evaluate the selected policy and both baselines on 72 targets. |
| Later snapshot rows | 2026-01 through 2026-07 | Retained in the source file, excluded from this evaluation. |

Both policies use the existing `StandardScaler` and `Ridge(alpha=1.0)` estimator
with a log target and the existing lag, trend and month features. Nothing was
tuned on this dataset. Lower development MAE selects the policy. An exact tie
selects fixed fitting. Monthly refitting won that comparison, at 1.168 index
points against 1.273 for fixed fitting. Both lost to last observation at 0.966.

The selected policy was then fitted through December 2019 and evaluated on the
final period. It can learn each preceding month's observation before the next
prediction. The fixed comparator freezes its fitted coefficients within a block
but still updates lag features from preceding observations. Both are one-step
comparisons in observation order.

## Final-period result

Lower errors are better. MAE and RMSE are in index points. MAPE below is a
percentage, while the [saved report](real-data-example.json) stores a fraction.

| Method | MAE | RMSE | MAPE |
|---|---:|---:|---:|
| Selected monthly-refit Ridge | 1.778 | 2.476 | 1.328% |
| Last observed month | 1.260 | 1.893 | 0.941% |
| Same month last year | 2.783 | 4.082 | 2.081% |

The model loses to last observation in every final-test calendar year on MAE.
It also loses to the seasonal baseline in 2024 and 2025. The report retains
every prediction, paired absolute errors, all three metrics and yearly results.
There is no May-August peak grouping because that label belongs to the synthetic
shipment example, not this seasonally adjusted index.

This does not justify replacing the serving model or making a new operational
forecast claim. It demonstrates why the baseline needs to fit the target.
The final period is now inspected. Further selection against these same results
would be development work, not another untouched test.

## Source and timing limits

The committed [CSV](../data/public/bts-freight-tsi-2026-09-24.csv) is an unchanged
response from the official API, selecting only `obs_date` and `tsi_freight` and
sorting by observation date. All 319 date/value pairs were reconciled with the
full official export. The [manifest](../data/public/bts-freight-tsi-2026-09-24.json)
records the source URL, exact byte hash, retrieval time, dataset update time,
coverage, field, units and source-document hashes. The importer rejects changed
bytes, wrong target metadata, incomplete calendars and nonpositive values.

The official [catalogue](https://catalog.data.gov/dataset/transportation-services-index-and-seasonally-adjusted-transportation-data)
labels the dataset public and links its government-work license. The API metadata
identifies `USGOV_WORKS`. This repository attributes BTS and includes only its
aggregate index, not separately obtained proprietary modal inputs or agency logos.

This is a **latest-vintage retrospective evaluation**. The snapshot was retrieved
in September 2026. BTS [revises historical values](https://www.bts.gov/browse-statistical-products-and-data/transportation-economic-trends/revision-policy-tsi)
when inputs or methods change, and adding later observations can revise past
seasonal adjustments. Prefix-only model code cannot undo information already
present in those revised values.

Observation dates also differ from release dates. The
[2026 release schedule](https://www.bts.gov/newsroom/transportation-services-index-release-schedule)
places July's release in September, for example. This experiment does not replay
those publication lags or recover historical as-published vintages. It therefore
does not establish a forecast that could have been issued at each historical
month end. The series is one overlapping history, not 72 independent trials.
No statistical significance, prediction interval or general superiority is claimed.

## Reproduce and inspect

Use the existing development dependencies and run from the repository root:

```sh
python -m src.real_backtest --development-only --output reports/real-development.json
python -m src.real_backtest --output reports/real-test.json
python -m pytest -q tests/test_real_backtest.py
```

No network connection or trained serving artifact is needed. The first command
does not score the final period. The second follows the recorded development
selection and evaluates only its chosen policy on the final period. Neither
command publishes or replaces a model, and neither fails merely because a
baseline wins. The report records dataset, protocol and implementation hashes
plus Python and numerical-library versions. Compare floating-point results with
a tolerance across platforms.

Regression checks perturb current, future and excluded observations. They verify
selection independence, the fixed tie rule, correct fitting cutoffs, source
identity, both independent baselines, reconciled metrics and no model publication.

The saved numerical run used the evaluator preserved in
[revision 45ae578](https://github.com/T92T1914/freight-forecast/blob/45ae5781e91d3a61250aa881e62c2e4eca98a9f4/src/real_backtest.py).
A subsequent validation correction rejects non-object protocols and contradictory
warmup or metric declarations. It also derives the fit description from the
implemented behavior. That correction did not tune the model or rescore the final
period. The report preserves the evaluated source hash and records the later
validation-source hash separately. Its original protocol hash identifies the
CRLF bytes read in that Windows run. New reports hash LF-normalized protocol text,
so Git line-ending conversion cannot change its identity across platforms.
