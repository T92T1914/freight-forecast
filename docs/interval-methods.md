# Intervals around the saved monthly forecasts

The next comparison asks how much of the observed error a prediction interval
captures. It reuses the saved BTS point forecasts. It does not retrain the model,
replace the synthetic serving artifact or turn the inspected 2020 to 2025 period
into an untouched test.

## Identity already in place

The current API exposes separate loaded model and history identities. Its single
atomic artifact contains training history, features, implementation hashes and
evaluation evidence. Startup rejects revised overlapping observations and
incompatible feature schemas. Existing regressions also cover model replacement
during loading and a restart after supported history changes. That contract is
documented in [model provenance](model-provenance.md). No second registry is needed
for this experiment. These identifiers are distinct from a release, container
digest and the BTS study's evaluated revision.

## Method decision before the interval comparison

I kept the point forecasts and selected three simple residual methods. The fixed
comparator retains errors from 2015 to 2019. Rolling calibration uses the latest
60 observed errors. The adaptive variant uses the same window and adjusts its
miscoverage level only after an issued interval has been scored. All three use
the same conservative order statistic and start at a nominal 90 percent target.
Each runs around the Ridge forecast, last observation and seasonal baseline.

Gibbs and Candes describe the adaptive update in section 2 and distinguish its
long run coverage frequency from coverage at a particular time. Their section
2.1 explains the stability and adaptation tradeoff and uses a step of 0.005. I
use that value as a declared comparator, without tuning it on Freight outcomes.
Their result does not establish this short retrospective experiment's coverage.
See [Adaptive Conformal Inference Under Distribution Shift](https://proceedings.neurips.cc/paper/2021/hash/0d441de75945e5acbc865406fc9a2559-Abstract.html).

Zaffran and colleagues examine learning rate sensitivity and sequential
calibration in section 2 of their ICML paper. They also discuss unbounded
intervals and the difficulty of averaging their widths. This implementation
retains those intervals explicitly. It never substitutes a future maximum
residual to make the average finite. See [Adaptive Conformal Predictions for
Time Series](https://proceedings.mlr.press/v162/zaffran22a.html).

The [protocol](interval-protocol.json) fixes the controls before new interval
results are computed. Five years of monthly residuals is a bounded initial
choice, not a selected optimum. The point policy was previously chosen on
development data that include these calibration months. The series is dependent
and uses revised historical values. Therefore these are empirical residual
intervals, without a claim of independent split conformal calibration or a
distribution free finite sample guarantee.

## What is measured

Coverage alone can reward uselessly wide intervals. The report therefore retains
width, every uncovered target and the nominal 90 percent interval score. The
[Winkler score](https://otexts.com/fpp3/distaccuracy.html) adds width to a penalty
of `2 / alpha` times the distance outside the interval. Lower is better. Scores
use the nominal alpha of 0.1 even when the adaptive internal level changes.

An unbounded interval covers any finite value. Its width and score remain
infinite, represented in JSON by null and an explicit status. Finite only means
use a separately labeled denominator. There is no clipping to positive demand
or future extrema. A radius of zero remains an honest point interval.

The evaluation is one observation step, not a release date simulation. No new
model, baseline selection, significance test or operational forecast claim is
introduced. The original result that last observation beat the model remains.

## Reproduce without refitting

From the reviewed commit containing the protocol and implementation, use a clean
tree and a new path outside the checkout:

```sh
python tools/evaluate_intervals.py --output ../interval-attempt.json
```

The runner records the evaluated revision, input and implementation hashes. It
refuses to overwrite a previous attempt. Tests use separate small fixtures to
check quantile ranks, score arithmetic, calibration boundaries, observation
ordering and the effect of future errors on already issued intervals.
