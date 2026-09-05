# Changelog

What changed and when, newest first, grouped by the milestones in
`CLAUDE.md`. Dates are commit dates. No version has been tagged yet; the
first `v*` tag is what publishes the image (`.github/workflows/release.yml`).

## [Unreleased]

### 2026-09-05 — repository polish

- ruff format adopted; the lint rule set (E, W, F, I, UP, B, SIM) is pinned
  in `pyproject.toml` and the format check is part of `make lint`.
- `Makefile` with the common commands (`install`, `data`, `train`, `serve`,
  `test`, `lint`, `format`, `check`, `image`, `run`, `monitor`, `k8s`); CI
  calls the same targets.
- CI: `actions/checkout@v7` and `actions/setup-python@v7`; tests on Python
  3.11, 3.12 and 3.13; read-only token; superseded runs cancelled; pip cache.
- Release workflow: a `v*` tag re-runs lint and tests, then builds and
  publishes the image to GHCR with OCI source/revision/version labels.
- The train/serve artifact contract is a typed `Artifact`; `/health` and
  `/predict` declare response models, so `/docs` states what they return.
  The JSON on the wire is unchanged.
- Shared `client` fixture moved to `tests/conftest.py`; structural tests for
  the workflows (read-only CI token, tag-only publishing with the test gate
  ahead of the push). 36 tests.
- `pyproject.toml` carries readme, license, authors, URLs and classifiers.
- README restructured: how the numbers were measured, run-it section with
  the Makefile targets and their plain equivalents, contents list.

### 2026-09-04 — README

- What the project is, the seasonal-naive bar, and the held-out numbers.
- The Grafana dashboard under live traffic (`docs/grafana-dashboard.png`).
- "What this does not do": the standard error on the MAE, the peak/off-peak
  split, the limits of synthetic data, point predictions, passive monitoring.
- The architecture as a plain-text pipeline, after GitHub's mermaid rendering
  proved unreliable for the diagram.

### 2026-07-25 — runtime verification

- `VERIFICATION.md`: `docker run`, the k3d cluster and the live dashboard
  executed and observed, closing the "pending Docker Desktop" notes.

### 2026-07-21 — milestones 2 to 6

- Serving: FastAPI app with `/health` and a validated one-step `/predict`;
  artifact and history loaded once at startup. API tests including the
  serving-skew guard and the exact edges of the range gate.
- Containerize: slim self-training image, exact runtime pins, dev
  dependencies split out.
- CI: lint, test and image build on every push.
- Kubernetes: deployment with liveness and readiness probes, ClusterIP
  service, structural tests that pin probe paths, the port chain and the
  selector/label match.
- Monitoring: request count, latency and prediction-distribution histograms;
  unhandled 500s counted; Prometheus config, provisioned Grafana dashboard,
  scrape annotations; a test that the dashboard only queries metrics the app
  exports.

### 2026-07-20 — milestone 1

- Seeded synthetic dataset modelled on DoD PCS seasonality.
- Log-target ridge that beats the seasonal naive on the held-out months, with
  the artifact refused if it does not.
- Leakage guards: lags shifted before use, the contiguity check in
  `build_features`, the committed CSV pinned to the generator.
