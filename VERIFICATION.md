# Runtime verification record

Earlier commit messages in this history noted that Docker, Kubernetes, and
dashboard verification were "pending Docker Desktop on this machine." Those
loops are closed. All four runtime done-criteria were executed and observed
on 2026-07-26:

**Milestone 3 — `docker run` serves predictions.** Image built from this
Dockerfile (training ran during the build and beat the naive baseline, or
the build would have failed). Container answered on port 8000:

```
GET  /health  -> {"status":"ok","model_loaded":true,"trained_through":"2022-12-01","test_mae":226.2,"test_mape":0.0335}
POST /predict {"month":"2025-01"} -> {"predicted_volume":3297,"naive_same_month_last_year":3477}
POST /predict {"month":"2024-07"} -> {"predicted_volume":11975,"naive_same_month_last_year":11248}
```

Identical numbers to the local virtualenv run — same code, same seed, same
artifact.

**Milestone 4 — CI green on GitHub.** First workflow run on this repository:
lint, test, and build-image jobs all passed (see the Actions tab).

**Milestone 5 — the endpoint answers through the cluster.** k3d cluster,
image side-loaded, `k8s/` manifests applied. Both replicas reached Ready
(readiness probes passing), and the same `/health` and `/predict` responses
came back through the ClusterIP service via `kubectl port-forward`.

**Milestone 6 — the dashboard shows live traffic.** The
`monitoring/docker-compose.yml` stack (API + Prometheus + provisioned
Grafana) came up; after a traffic burst, Grafana's own datasource query for
the dashboard's request-rate panel returned `/predict` at 0.8 req/s and the
prediction-distribution histogram held 70 observations.
