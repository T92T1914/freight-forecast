# freight-forecast

[![CI](https://github.com/T92T1914/freight-forecast/actions/workflows/ci.yml/badge.svg)](https://github.com/T92T1914/freight-forecast/actions/workflows/ci.yml)
![Python 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Monthly shipment volume forecasting on a **seeded synthetic dataset**, served
as a containerized API: scikit-learn, FastAPI, Docker, Kubernetes, Prometheus
and Grafana. The data models a seasonal logistics problem; these results are
not an evaluation on operational shipment records.

**226 vs 327 MAE on 24 held-out months, 31% under the seasonal-naive baseline;
`train.py` exits non-zero if it ever loses, so a regression fails the image
build.**

[Results](#results-beat-same-month-last-year) |
[Run it](#run-it) |
[How the numbers were measured](#how-the-numbers-were-measured) |
[What this does not do](#what-this-does-not-do) |
[VERIFICATION.md](VERIFICATION.md)

![The provisioned Grafana dashboard under live traffic](docs/grafana-dashboard.png)

*The stack from `monitoring/docker-compose.yml`, twelve minutes of traffic
against the container. Two request bursts on `/predict`, p95 latency steady
around 8-9 ms, the `400` line showing out-of-range months being refused
rather than answered, and the prediction histogram sitting in the 3,000-14,000
moves/month band the model was trained on.*

I spent six years in Air Force logistics, much of it at a JPPSO moving
household goods: freight rates, storage, and the annual PCS surge. Forecasting
that demand cycle was the job, not a class exercise — every summer, roughly
40,000 moves land between May and August while winter runs at about a third of
peak, and staffing, warehouse space and carrier capacity all have to be
committed months ahead of it. This project is that problem, built properly:
a model that has to earn its place against the baseline a human planner
already uses, and then the serving, deployment and monitoring around it.

- [Quickstart](#quickstart)
- [Results: beat "same month last year"](#results-beat-same-month-last-year)
- [Run it](#run-it)
- [Architecture: how it fits together](#architecture-how-it-fits-together)
- [CI and release](#ci-and-release)
- [What this does not do](#what-this-does-not-do)
- [License](#license)

## Quickstart

```bash
make install
make train
make serve
make test
```

The plain commands behind each target are listed under [Run it](#run-it),
for machines without `make`. In a second terminal after starting the service:

```bash
curl -X POST http://127.0.0.1:8000/predict -H 'content-type: application/json' -d '{"month":"2025-01"}'
```

```json
{"month":"2025-01","predicted_volume":3297,"naive_same_month_last_year":3477,"trained_through":"2022-12-01"}
```

The checked-in artifact produces this response. The API accepts only historical
months with a full lag history and the next unobserved month. Features are built
once per startup and indexed by month; [the serving tests](tests/test_api.py)
compare every supported response with uncached construction and verify refresh
after a data change.

**Code tour:** [features and leakage guards](src/features.py) →
[chronological evaluation](src/train.py) → [API and metrics](src/serve.py) →
[tests](tests/) → [deployment evidence](VERIFICATION.md).

## Results: beat "same month last year"

[![Actual synthetic shipment volume, model predictions and a seasonal baseline over 24 test months.](docs/freight-forecast-example.png)](docs/visual-example.md)

The model averages 226 moves of error per month, compared with 327 for the seasonal baseline. This is a chronological test on synthetic data, with prior observations available for each prediction.
[Reproduce and inspect the values](docs/visual-example.md).

A planner with no model looks up last year's number for the same month. That
seasonal-naive forecast is the honest baseline, and a model that can't clear
it is not worth deploying no matter how good its architecture looks.

The first twelve months are spent filling the `lag_12` warm-up, so the model
trains on **2018-01 → 2022-12 (60 months)** and is evaluated on **2023-01 →
2024-12 (24 months), never seen during training**:

| | MAE (moves) | MAPE |
|---|---|---|
| Seasonal naive — same month last year | 327 | 4.8% |
| Ridge on log(volume) | **226** | **3.4%** |

**31% lower error than the baseline.** `src/train.py` exits non-zero if the
model ever loses to the naive predictor, so a regression fails CI rather than
shipping quietly.

### How the numbers were measured

- **MAE** is the mean absolute error in moves per month; **MAPE** is the same
  error as a fraction of the actual volume. Both come from scikit-learn's
  `mean_absolute_error` and `mean_absolute_percentage_error`, applied to the 24
  held-out months in `src/train.py`, for the model and for the naive predictor
  side by side.
- The naive predictor is literally the `lag_12` feature: the value observed
  twelve rows earlier in the same series the model is scored on.
- `python -m src.train` (or `make train`) prints the table above and refuses
  to save the artifact if the model loses;
  `tests/test_train.py::test_model_beats_seasonal_naive` asserts the same
  thing on every CI run, on a dataset rebuilt from the seed.
- What the numbers do and do not prove is spelled out under
  [What this does not do](#what-this-does-not-do): a point estimate on 24
  observations, with the error concentrated in the peak months.

Two modelling decisions worth naming:

- **The target is `log(volume)`, not volume.** Seasonality here is
  multiplicative — the summer peak scales with the overall level rather than
  adding a fixed number of moves — so the model fits in log space and
  `TransformedTargetRegressor` maps predictions back to move counts. Fitting
  raw volume asks a linear model to treat a 200-move miss in January as the
  same error as in July, which is not what a planner means by "wrong."
- **Features can only see the past.** `lag_12` (same month last year) and
  `roll_3` (previous quarter's mean) are both shifted before use, and any row
  without a full 12-month history is dropped. `build_features` also *raises*
  if the series has gaps or duplicate months, because those lags are computed
  by row position — on a series with a hole, "12 rows back" silently stops
  meaning "last year" and the model would be training on leaked information
  with no visible symptom.

## Run it

Every target in the Makefile is one plain command, listed by `make help`; the
equivalents are shown for machines without `make`.

```bash
make install     # pip install -r requirements-dev.txt
make train       # python -m src.train      regenerates data if missing, trains, evaluates
make serve       # python -m uvicorn src.serve:app --reload   -> http://127.0.0.1:8000/docs
make test        # python -m pytest -q
make check       # ruff check + ruff format --check + pytest: CI's lint and test jobs
```

```bash
curl localhost:8000/health
curl -X POST localhost:8000/predict -H 'content-type: application/json' -d '{"month":"2025-01"}'
```

The container, the cluster and the monitoring stack:

```bash
make image       # docker build -t freight-forecast .     trains inside the build
make run         # docker run --rm -p 8000:8000 freight-forecast
make k8s         # kubectl apply -f k8s/                  deployment + service, probes wired
make monitor     # docker compose -f monitoring/docker-compose.yml up --build
                 #   API :8000, Prometheus :9090, Grafana :3000
```

### Verification

`VERIFICATION.md` records what was executed and observed rather than intended:
identical predictions from the local run and the container, both replicas
reaching Ready behind the readiness probe on a k3d cluster, and Grafana's own
datasource returning `/predict` at 0.8 req/s with 70 observations in the
prediction histogram after a traffic burst.

## Architecture: how it fits together

```
  generate_data.py          features.py               train.py
  96 months, seed 2017  ->  lag_12 - roll_3      ->   Ridge on log(volume)
                            month dummies             trained on 60 months
                            (raises on a gap)                  |
                                                               v
                                                   beats the seasonal naive?
                                                        |            |
                                                    no  |            |  yes
                                                        v            v   226 vs 327 MAE
                                                     exit 1      model.joblib
                                               the build fails        |
                                                                      v
                                                             FastAPI, loaded
                                                               at startup
                                                                      |
                                        /health   /predict   /metrics <
```

`/metrics` is scraped by Prometheus and drawn by the Grafana dashboard at the
top of this page. `k8s/` runs the same image behind liveness and readiness
probes.

The gate is the part worth noticing: the model has to beat "same month last
year" or `train.py` exits non-zero — and because the Docker image trains
during its own build, a model that stops clearing the baseline fails the image
rather than shipping.

### What's in the box

```
src/
  generate_data.py   synthetic monthly volumes, fixed seed, PCS-shaped
  features.py        lags, rolling mean, month dummies + the contiguity guard
  train.py           trains, evaluates against the naive, saves the artifact
  serve.py           FastAPI app: /health, /predict, /metrics
tests/               36 tests: features, data, training, API, metrics,
                     manifests and workflows; conftest.py serves the artifact
Makefile             the common commands; CI calls the same targets
Dockerfile           trains during the build, so a broken model fails the image
.github/workflows/   ci.yml: lint, tests on 3.11-3.13, image build, every push
                     release.yml: a v* tag publishes the image to GHCR
k8s/                 deployment with liveness + readiness probes, service
monitoring/          Prometheus config, provisioned Grafana, dashboard JSON
data/shipments.csv   96 months (2017–2024), regenerable from seed 2017
VERIFICATION.md      what was actually run and observed, with output
CHANGELOG.md         what changed, by milestone
```

The data is synthetic and says so. The shape is modelled on the JPPSO cycle —
peak months carry factors summing to 7.45 against a 5,200/month base, which
puts the May–August window near 38,700 moves — with a slight year-over-year
drift and 4% noise. A fixed seed makes every number on this page reproducible.

### The API

Model loads once at startup, not per request. Input is validated by pattern
(`^\d{4}-(0[1-9]|1[0-2])$`), and a month outside the forecastable range is a
4xx with a reason rather than a confident number from an extrapolated lag.
Responses are declared as models too, so `/docs` states the contract rather
than leaving a consumer to infer it from one example.

```
GET  /health   -> {"status":"ok","model_loaded":true,"trained_through":"2022-12-01",
                   "test_mae":226.2,"test_mape":0.0335}
POST /predict  {"month":"2025-01"} -> {"month":"2025-01","predicted_volume":3297,
                                       "naive_same_month_last_year":3477,
                                       "trained_through":"2022-12-01"}
GET  /metrics  -> Prometheus exposition
GET  /docs     -> interactive OpenAPI documentation
```

`/health` reports the metrics the artifact was accepted on, so a deployed
instance can be asked what it actually is instead of what it was supposed to
be. `/predict` returns the naive number alongside its own, because a forecast
is only meaningful next to the thing it claims to beat.

### Monitoring

Three Prometheus series: request count by endpoint and status, request latency,
and a histogram of predicted volumes. The last one is the interesting one —
latency and error rate tell you the service is alive, but a model can be
perfectly healthy and quietly wrong. A prediction distribution that drifts away
from its training range is the signal that the world changed, and it is visible
before anyone files a ticket.

## CI and release

On every push, CI lints (ruff check and format), runs the tests on Python
3.11, 3.12 and 3.13, and builds the image — which retrains the model and fails
if it no longer beats the naive. Pushing a `v*` tag runs the checks again and
publishes the image to GitHub Container Registry as
`ghcr.io/t92t1914/freight-forecast:<tag>`.

## What this does not do

Stated because a project that only lists its wins is not worth much.

- **The 226 MAE is a point estimate on 24 observations.** The absolute errors have
  a standard deviation of 276, so the standard error on that mean is 56 and a 95%
  interval runs from roughly 116 to 336. It clears the naive baseline of 327
  comfortably and repeatedly, which is the claim being made — but "226" should not
  be read as three significant figures of accuracy.
- **The error is concentrated exactly where it matters.** Split the held-out months
  by season: peak (May-Aug) MAE is **418**, off-peak is **130**. In relative terms
  they are close (3.83% vs 3.11% MAPE) because peak volumes are roughly three times
  larger, but capacity is committed in absolute moves, so the forecast is least
  precise in the four months anyone actually plans around.
- **The data is synthetic**, and its seasonality was specified rather than
  discovered — the model is being asked to recover a pattern that was put there on
  purpose. That makes it a fair test of the pipeline and a weak test of the model.
  Real JPPSO data would bring regime changes (policy shifts, base realignments,
  a pandemic) that a 60-month linear fit has no way to anticipate.
- **Point predictions only.** Planning wants an interval, not a number — the useful
  question is "how bad is the plausible worst case for August" and this cannot
  answer it. Quantile regression or conformal prediction would be the next thing
  I built, ahead of any more accurate point model.
- **The monitoring watches, it does not act.** The prediction histogram will show
  drift, but nothing alerts on it and nothing retrains. There is no data
  versioning, so a rerun with different data would silently produce a different
  model under the same tag.

## License

MIT licensed. The full text is in [LICENSE](LICENSE).

## Serving performance

[Feature reuse measurement](docs/serving-performance.md): a matched local in-process check measured 4.534 ms → 1.278 ms for one request shape, with identical predictions. The report includes raw samples, reproduction instructions and limits.
