# Changelog

What changed and when, newest first. [Project notes](docs/project-notes.md)
explain how the pieces fit together. Dates below are commit dates. No version has been tagged yet. The
first `v*` tag is what publishes the image (`.github/workflows/release.yml`).

## [Unreleased]

### 2026-11-27, the HTTPX test pin

* The development pin of HTTPX moves from 0.28.1 to 0.29.0. The release notes
  were read. They raise the minimum Python to 3.10, below the 3.11 this project
  needs, and expose `FunctionAuth` in the public API. Only the tests use it. No
  forecast, model or published error changes.

### 2026-11-27, the README links the comparison and the walkthrough

* The results section of the README now links `docs/forecast-comparison.md` and
  `docs/planner-walkthrough.md`. It adds no number, and the opening is unchanged.

### 2026-11-27, when the origin vintages are dated

* Limit 3 of `docs/real-time-evaluation.md` said each target month was already
  underway when its origin vintage appeared. All 72 saved origins use a vintage
  dated after their target month ended. The report and
  `docs/planner-walkthrough.md` now say that, and a test counts it from the saved
  predictions. The declared protocol and the saved report keep their original
  sentence. No forecast, model or published error changes.

### 2026-11-27, the PyYAML test pin

* The development pin of PyYAML moves from 6.0.3 to 6.0.4. The release notes
  were read. They add Python 3.15 support, drop 3.8 and 3.9, fix exponential
  expansion of merge keys and move the wheels to libyaml 0.2.6. Only the tests
  use it. No forecast, model or published error changes.

### 2026-11-26, how far the ranking holds

* `docs/planner-walkthrough.md` now says how far the comparison tests support
  each ranking. On the freight index the lead of the latest month over the model
  is told apart from noise against latest values. Against first releases it is
  unsettled on the miss as it is and not told apart on the squared miss.
* `docs/real-time-evaluation.md` adds a limit. On 8 of 72 origins the vintage
  already holds the target month. The value is dropped before forecasting. No
  forecast, model or published error changes.

### 2026-11-25, a walkthrough for the planner

* `docs/planner-walkthrough.md` explains what the service returns, how to read a
  forecast and a range, why the latest value is the better input for the national
  index today and what the ledger proves. It opens with one page for a reader who
  is not an engineer. It quotes results already in the repository and adds none.

### 2026-11-25, a dry run for the release workflow

* `release.yml` can now be started by hand. That run checks and builds the image
  in a job with a read only token and pushes and attests nothing. The publication
  job still starts only from a pushed version tag, and its condition now also
  requires a push event. No tag or release exists.

### 2026-11-25, which forecast differences can be told apart

* `docs/forecast-comparison.md` tests every pair of forecasters in the two real
  time reports under the protocol declared first. For the Ridge model against
  last observation 5 of 32 readings are told apart, 7 are unsettled and 20 are
  not told apart. The two earlier reports now link it and no longer say there
  are no significance tests. No forecast, model or published error changes.
* `arch` 9.0.0, `scipy` 1.17.1, `statsmodels` 0.15.0 and `dieboldmariano` 1.1.0
  are new dev pins. None enters the serving image.

### 2026-11-25, the comparison test protocol

* `docs/forecast-comparison-protocol.json` fixes how I will test whether the
  differences in the two real time reports can be told apart from noise. It
  names a Diebold Mariano test with the Harvey, Leybourne and Newbold
  correction, a fixed smoothing check, a Holm adjustment across the seven
  component series, a model confidence set, the rule for reading them and why
  the Clark and West test does not apply. It was declared before any of those
  statistics was computed. No result, model or published number changes.

### 2026-11-21, three pins move

* `fastapi` moves from 0.163.1 to 0.165.0, `pydantic` from 2.14.3 to 2.14.4 and
  `ruff` from 0.17.5 to 0.17.6. I did not read the release notes for these
  three. The checks on the card and the image build are the evidence. Nothing
  in `src`, `tools` or the tests changed.

### 2026-11-16, the README points to the first record

* The README ledger section now says I issue one real record each month and
  links the first one, for December 2026. It also says the public reading view
  does not show it and that nothing is scored until the February 2027 release.

### 2026-11-16, the first monthly ledger record

* `data/ledger/2026-12-outlook/` holds one real issuance under the declared
  three step outlook. The snapshot runs through September 2026, the target is
  December 2026, and the prediction is the last observation, 135.8 index points,
  against 137.0 for the same month last year. It was issued at 17:39 UTC today,
  before the target month began and before any outcome exists.
* The folder keeps the captured bytes, their manifest and availability receipt,
  the ledger database file and its JSON export. The availability receipt shows
  that these bytes were available by capture, not when BTS first published
  them. Nothing is scored. The December value is not due until the February 2027
  release.
* This is the first record since I lost the two local ones of October 14, and
  it is the first of the one a month I decided to issue.

### 2026-11-16, README results for the real time tests

* The README results section states the real time replay and the component
  series results in the words of their reports. The opening is untouched.

### 2026-11-16, the real time test on seven component series

* `docs/real-time-components.md` runs the same protocol on the seven component
  series of the index and reports the scaled error against last observation and
  the same month last year, with the mean and median across series. The model
  beats last observation on one series of seven and the same month last year on
  all seven, against first releases.
* Values are committed for air freight, waterway tonnage, pipeline petroleum and
  natural gas. Truck tonnage and the two rail series keep only a manifest with
  its hash, because their sources are private bodies. A test recomputes the
  saved result for the four committed series.

### 2026-11-16, the BTS study on real time vintages

* `docs/real-time-evaluation.md` replays the existing model, last observation
  and the same month last year on only what each origin could have seen, for
  2020 to 2025, and scores them against the first release and against the latest
  values. Last observation wins on both, as it did in the earlier study.
  Nothing was tuned and the estimator is unchanged.
* `src/real_time_backtest.py` computes it and `docs/real-time-example.json`
  retains every prediction. A test recomputes the saved report.

### 2026-11-16, the October 14 records are lost

* The two local records of October 14, 2026, a September nowcast and a November
  outlook, were lost before either one was observed. Neither was ever scored,
  and no replacement is issued, since a record written now would carry an issue
  time after the outcome. The entries of October 14 and November 15 below
  describe them as they stood on those days. The ledger guide now says the same.

### 2026-11-16, retained vintages of the freight index

* `data/public/tsifrght-alfred-vintages.csv` holds every ALFRED vintage of
  `TSIFRGHT` from October 8, 2014 to November 1, 2026, with its manifest and
  hashes. `tools/fetch_alfred_vintages.py` fetched it once by hand with no
  account and no key. ALFRED vintage dates trail the BTS release.
* `src/vintages.py` returns the history an origin could have seen, and a test
  fails if any of the 72 origins reads a later vintage or a month from its
  target on. No error on this data has been computed yet.

### 2026-11-16, real time protocol

* `docs/real-time-protocol.json` declares the real time replay of the BTS study
  before any vintage is fetched or any error on it is computed. It fixes the
  series, the origins, the three forecasters, the two outcome policies, the
  metrics and the rule that nothing is tuned.

### 2026-11-15, ledger guide and README order

* The ledger guide now says what the changelog already said about October 14,
  2026. Two local records exist, a September nowcast and a November outlook,
  both unobserved and unscored.
* The README sections that sat after License now sit above it. No claim,
  number or limit changed, and the opening is untouched.

### 2026-11-15, newer fastapi patch

* The requirements move fastapi from 0.163.0 to 0.163.1. I could not read the
  0.163.1 release notes, so this change rests on the checks and not on the notes.
  Lint, format and the tests give the same result as before, and the image build
  in the pull request checks is the gate for the container.

### 2026-11-15, newer fastapi release

* The requirements move fastapi from 0.162.7 to 0.163.0. I could not read the
  0.163.0 release notes, so this change rests on the checks and not on the notes.
  Lint, format and the tests give the same result as before, and the image build
  in the pull request checks is the gate for the container.

### 2026-11-15, newer pandas

* The requirements move pandas from 3.0.3 to 3.1.1. I read the documented 3.1.0
  changes through context7. Index values become read only, `float_precision` in
  `read_csv` is deprecated, and `.values` on timezone aware series is deprecated.
  The code here uses none of them, which I checked with a search of `src`,
  `tools` and `tests`. I did not read the full notes.
* The test count and the one warning are the same as before. The image build in
  the pull request checks retrains the model and gates it. I did not change the
  forecasting code or the gate.

### 2026-11-15, newer scikit-learn patch release

* The requirements move scikit-learn from 1.9.0 to 1.9.1, a patch release. I
  could not read the 1.9.1 release notes, so this change rests on the checks and
  not on the notes. Lint, format and the tests give the same result as before.
* The model is a fitted scikit-learn estimator, so the image build in the pull
  request checks is the check that matters. It retrains the model and gates it.
  I did not change the forecasting code or the gate.

### 2026-11-15, newer model file library

* The training and serving requirements move joblib from 1.5.3 to 1.6.0. The
  1.6.0 change I could read lets `dump()` take any path object, and it says
  nothing about the file format. I did not read the full notes. This project saves and loads the model through byte
  streams, so it does not use that path.
* The tests pass with the new version. The image build in the pull request
  checks retrains the model and gates it, so it is the check that matters for
  the saved model.

### 2026-11-15, newer web framework and validation library

* The serving requirements move fastapi from 0.139.2 to 0.162.7 and pydantic
  from 2.13.4 to 2.14.3. I read the documented breaking changes through context7.
  They remove the `openapi_prefix` and `**extra` arguments, deprecate
  `root_path` and drop Python 3.9. This service uses none of those, and it
  builds the app with a title and a lifespan only. Starlette resolves to 1.10.0, the same as before.
* The model, the forecasts and the response shapes are unchanged. Lint, format
  and the tests give the same result as before, and the image build in the pull
  request checks is the gate for the container.

### 2026-11-15, newer metrics library

* The serving requirements move prometheus-client from 0.24.1 to 0.26.0. I read
  the documented changes that touch this service. Metric label names must now be
  unique, and the metrics here already use distinct label names. The metrics page
  is still mounted at `/metrics`, which 0.26.0 supports. I did not read a full
  release changelog, because none was available to me.
* The metric names, the model and the forecasts are unchanged. Lint, format and
  the tests give the same result as before, and the image build in the pull
  request checks is the gate for the container.

### 2026-11-14, newer server

* The serving requirements move uvicorn from 0.51.0 to 0.54.0. The releases in
  between add experimental HTTP/2 and an experimental HTTP parser that stay off
  unless asked for, so the container command, the model and the forecasts are unchanged.
* The lint, format and test checks give the same result as before. The image
  build in the pull request checks is the gate for the container.

### 2026-11-14, newer test and lint tools

* The development requirements move pytest from 9.1.1 to 9.2.0 and ruff from
  0.15.5 to 0.17.5. Both stay out of the serving image, so the model, the
  forecasts and the pinned runtime packages are unchanged.
* The lint and format checks pass with no source change, and the full test set
  gives the same result as before.

### 2026-11-14, a range on the observed interval coverage

* The interval report adds a 95 percent Wilson range beside each observed
  coverage in the overall and yearly tables, and the interval notes explain how to read it.
  The retained results, the protocol and the forecasts are unchanged, and the
  range is computed when the report is rendered.
* The months are dependent, so the range is a rough guide to how little 72
  months can settle. It is not a coverage guarantee.

### 2026-10-27, Obscur readers by default

* The README, project notes and public page lead with the retained Obscur
  figure and explain the past only inputs, the chronological evaluation and
  the forecast comparison beside it. Explicit appearance choices still win.
* No training or forecast study was rerun by this or the other documentation
  changes below.

### 2026-10-23, translation support

* Literal code, commands and machine identifiers carry `translate="no"` in
  the maintained generators and templates, so cooperating translation tools
  leave them alone. Explanations and accessible labels stay translatable.
* The README and project page link to a translation reading guide. Browser
  translation stays under the reader's control, and the project has no
  reviewed translations.

### 2026-10-20, introduction and evidence wording

* The results heading is balanced and keeps its old anchor, and the dashboard
  is identified as a retained container example.
* The statement that no real forecast has been issued is scoped to the public
  ledger example, in the container origin guide, the ledger guide and the
  project page. A local ledger can hold real issuances.

### 2026-10-18, project overview

* The overview leads with the Obscur figure, stacked on narrow screens, with
  direct links to Clair, and points to the BTS comparison that favors last
  observation.
* The ledger guide documents the offline `inspect-metadata` command and how
  a frozen `observation.json` payload reaches `review-observation`. The
  monitoring text now calls the prediction histogram an output distribution
  and not proof of drift.

### 2026-10-16, release metadata inspection

* `inspect-metadata --capture CAPTURE --output NEW_DIRECTORY` writes a
  separate, unverified report of the JSON-LD blocks in a saved release
  capture, with inert script text, source locations, file hashes, closure
  state and omitted counts. It validates the capture before reading it and
  rechecks the same bytes before publishing the report.
* Canonical release facts keep their earlier meaning. Unknown capture
  schemas and changed or damaged captures are refused.

### 2026-10-15, release evidence before outcome admission

* The official Freight bulletin shows a calendar date, while outcome
  admission needs an exact publication time and a qualified first release
  value. A bounded one shot capture of the monthly BTS Freight newsroom page
  retains the received bytes and the failure responses.
* Candidate preparation binds publication, first release and unit
  declarations to retained proof bytes. Missing evidence produces a review
  envelope that admits nothing, and no timestamp is manufactured.

### 2026-10-14, official sources, a future outlook and bounded access

* The BTS capture command retains the exact official CSV and metadata
  response bytes and validates the same CSV buffer before writing a manifest
  and availability receipt. Exact first publication stays unknown, with
  `published_at: null` and `publication_verified: false`. The ledger admits
  that joined input basis explicitly, and an availability receipt cannot
  establish when an outcome was published.
* `docs/future-outlook.md` declares a three month, last observation outlook
  from a published BTS vintage before any record exists under it. The
  committed command created two local records that day, a September nowcast
  and a November outlook. Both are unobserved and unscored, and the public
  fixture still holds no real issued forecast.
* The local image runs as UID and GID 10001 with one worker and one numerical
  thread. The local container command and the monitoring Compose file bind to
  loopback and limit the API to half a CPU, 512 MiB and 64 processes, with a
  read only root filesystem, all capabilities dropped and privilege escalation
  disabled. A new qualification consumer checks the actual process
  privileges, cgroup limits, API and metrics responses, invalid artifacts, a
  bounded workload, a request finished during graceful shutdown and a healthy
  restart (`docs/local-service-qualification.md`).
* The backtest command checks file identity before fitting and before
  delivery, so an output named through a hard link cannot replace its input.
  It stages a complete JSON report beside the destination before replacing the
  old one. Interval calculations reject non finite endpoints, widths and
  residuals before they reach calibration state.

### 2026-10-02, ledger integrity and release attestation

* Version tag releases add signed build origin and SPDX attestations for the
  exact published manifest, followed by a consumer verifier that checks the
  digest, repository, workflow, tag, source commit and signed inventory. The
  producer and the real cryptographic consumer path have not run, because no
  tag has been cut (`docs/container-origin.md`).
* Request handling requires the same single finite, nonnegative numeric
  forecast that startup validates. Boolean, string and malformed shapes
  return the structured 500 response before any prediction metric changes.
* The selected backtest year is saved in its own query parameter and travels
  with sharing links. Long revision links and enlarged text wrap on phones.
* Restored ledgers validate observation to issuance linkage, correction
  chains and observation order before scoring or export.
* Ledger exports refuse the SQLite database, its sidecars and file aliases as
  destinations, and stage a complete adjacent file before replacing the old
  output. The limits exclude concurrent alias changes and power loss.
* A real outcome must declare a publication instant strictly later than the
  forecast's original issuance. This checks supplied timestamps and does not
  authenticate publication.

### 2026-10-01, forecast vintage

* The BTS adapter records the hash that the loader verified against the
  parsed bytes, instead of rereading the path. A replaced or deleted file can
  no longer pair one version's observations with another version's identity.

### 2026-09-30, prospective forecast ledger

* A local append only SQLite ledger records issuance, observation vintage and
  correction events. Exact retries keep their identity and clock, and
  conflicting retries, incompatible artifacts and unsupported targets fail
  visibly. An unfavorable outcome is retained regardless of serving
  promotion.
* The public reading page separates three synthetic workflow fixtures from a
  retained July vintage BTS replay. It contains zero real issued forecasts.
  Local clocks and hashes are not independent timestamping.

### 2026-09-28, sharing cards

* A compact Obscur sharing card and Open Graph metadata on the public page.
  The site build preserves the recorded bytes of the wide SVGs on Windows.

### 2026-09-27, figures, intervals and accessibility

* The retained freight figure has Inter Clair and Obscur renditions in
  stacked and wide layouts, selected by container width on the public page and
  by picture sources in the README. The model's 226.16 mean error against
  326.83 for the seasonal baseline is unchanged.
* A separate retrospective interval experiment wraps the saved BTS point
  forecasts. Fixed Ridge intervals covered 59 of 72 months against a 90
  percent target, and rolling and adaptive Ridge each covered 66 with wider
  ranges. Last observation had the lower mean interval score under all three
  methods. This reuses inspected outcomes, so it is not a new untouched test
  (`docs/interval-methods.md`).
* A hidden stacked figure link no longer sits in the keyboard sequence at wide
  sizes.

### 2026-09-25, real freight activity and themes

* A locked evaluation on the BTS Freight Transportation Services Index uses
  2010 to 2019 for development and 2020 to 2025 for a separate final block.
  The selected Ridge policy scored 1.778 index points of mean absolute error
  on the 72 final months, against 1.260 for last observation and 2.783 for
  the seasonal baseline. Last observation won every final year. The data is
  the revised latest vintage, so it carries no historical release lags
  (`docs/real-data-evaluation.md`).
* The saved results interface and an offline HTML report offer Auto, Clair
  and Obscur, with explicit SVG downloads. Fonts are looked up locally and
  never bundled, so readers without Inter get a system fallback. The Pages
  workflow tests the generated site before publishing it.

### 2026-09-23, artifact safety and traceability

* Training serializes to a temporary file beside the artifact and replaces it
  only after the write succeeds, so a failed or interrupted write keeps the
  previous model. The seasonal baseline gate is unchanged.
* The artifact carries its monthly observations, chronological evaluation,
  feature schema, implementation hashes and dependency versions. Startup
  rejects conflicting history and incomplete evidence, and the API reports
  the model file and serving history identities. The public example is
  checked against the artifact (`docs/model-provenance.md`).

### 2026-09-21, serving checks and demo

* Startup validates the model contract and one real prediction. The trend
  origin is saved with the artifact, so trimming old history keeps forecasts
  for shared months. In the regression fixture, removing 48 old months had
  moved a January forecast from 3,297 to 3,190.
* History with month start timestamps that are not midnight is rejected at
  startup, because month only requests could not match those rows.
* The demo keeps its selected forecast month in a shareable URL and restores
  it with Back. Evidence views fit narrow panels, scroll tables by keyboard
  and follow the system light preference.

### 2026-09-20, metrics and documentation

* NaN, infinite and negative model outputs return a 500 before the prediction
  histogram observes them. Zero stays valid.
* Unrecognized HTTP methods share one monitoring label so they cannot create
  unbounded time series, and missing routes under the metrics prefix count as
  404s. Real scrapes stay excluded.
* The quickstart runs the tests first and limits the baseline claim to the
  included dataset. The contribution guide asks for matching evaluation
  periods, monthly errors and reproducible serving measurements.

### 2026-09-15, browser example and development container

* A static browser example keeps the recorded source revision, assumptions
  and limits beside the result, and publishes only an explicit list of files.
  A pinned Python development container installs the project and runs a smoke
  check, and the workflow deploys the public site from the default branch.
* The README explains the logistics motivation and forecast workflow, and a
  contribution guide and a bug report form were added. Stale test counts were
  removed.

### 2026-09-14, observed history validation

* Startup rejects short, missing, nonnumeric, infinite and negative observed
  history, and validates it before the future placeholder is added. The twelve
  month minimum and the one month horizon are unchanged.

### 2026-09-21, discoverable forecast window

* Added `/forecast-window` so clients can inspect the supported month range
  before requesting a prediction. It distinguishes observed history from the
  model's training cutoff and the single unobserved month.
* Verified both range boundaries, refresh after new observations, and discovery
  without disk reads, feature construction or model execution.

### 2026-09-20, monthly backtesting

* Added an expanding window evaluation that refits each month using only prior
  observations and compares every forecast with the seasonal baseline.
* The JSON report includes individual predictions, yearly and seasonal errors,
  input and implementation hashes, and numerical package versions.
* Recorded the default synthetic example, including the year where the baseline
  wins. The evaluation leaves the serving artifact and deployment policy alone.
* Moved the project background into `docs/project-notes.md` and removed the
  tool specific instruction file from the published tree.

### 2026-09-05, repository polish

* ruff format adopted. The lint rule set (E, W, F, I, UP, B, SIM) is pinned
  in `pyproject.toml` and the format check is part of `make lint`.
* `Makefile` with the common commands (`install`, `data`, `train`, `serve`,
  `test`, `lint`, `format`, `check`, `image`, `run`, `monitor`, `k8s`). CI
  calls the same targets.
* CI: `actions/checkout@v7` and `actions/setup-python@v7`, tests on Python
  3.11, 3.12 and 3.13, read only token, superseded runs cancelled and a pip cache.
* Release workflow: a `v*` tag re runs lint and tests, then builds and
  publishes the image to GHCR with OCI source/revision/version labels.
* The train/serve artifact contract is a typed `Artifact`. `/health` and
  `/predict` declare response models, so `/docs` states what they return.
  The JSON on the wire is unchanged.
* Shared `client` fixture moved to `tests/conftest.py`. Structural tests were added for
  the workflows (read only CI token, tag only publishing with the test gate
  ahead of the push). 36 tests.
* `pyproject.toml` carries readme, license, authors, URLs and classifiers.
* README restructured: how the numbers were measured, run it section with
  the Makefile targets and their plain equivalents, contents list.
* README first screen: the held out result and the train.py gate in one
  line under the tagline, the dashboard beneath it and a four line quickstart
  first. Sections reordered to results, run it, architecture, CI and
  release, limits, license.

### 2026-09-04, README

* What the project is, the seasonal naive bar, and the held out numbers.
* The Grafana dashboard under live traffic (`docs/grafana-dashboard.png`).
* "What this does not do": the standard error on the MAE, the peak/off peak
  split, the limits of synthetic data, point predictions, passive monitoring.
* The architecture as a plain text pipeline, after GitHub's mermaid rendering
  proved unreliable for the diagram.

### 2026-07-25, runtime verification

* `VERIFICATION.md`: `docker run`, the k3d cluster and the live dashboard
  executed and observed, closing the "pending Docker Desktop" notes.

### 2026-07-21, milestones 2 to 6

* Serving: FastAPI app with `/health` and a validated one step `/predict`.
  Artifact and history loaded once at startup. API tests including the
  serving skew guard and the exact edges of the range gate.
* Containerize: slim self training image, exact runtime pins, dev
  dependencies split out.
* CI: lint, test and image build on every push.
* Kubernetes: deployment with liveness and readiness probes, ClusterIP
  service, structural tests that pin probe paths, the port chain and the
  selector/label match.
* Monitoring: request count, latency and prediction distribution histograms,
  unhandled 500s counted, Prometheus config, provisioned Grafana dashboard,
  scrape annotations and a test that the dashboard only queries metrics the app
  exports.

### 2026-07-20, milestone 1

* Seeded synthetic dataset modelled on DoD PCS seasonality.
* Log target ridge that beats the seasonal naive on the held out months, with
  the artifact refused if it does not.
* Leakage guards: lags shifted beforeuse, the contiguity check in
  `build_features`, the committed CSV pinned to the generator.
