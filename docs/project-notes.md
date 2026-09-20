# Project notes

I chose monthly shipment forecasting because seasonal demand was part of my
logistics work. This repository uses synthetic data to make that problem
reproducible without publishing operational records.

## How the pieces fit

The generator produces a monthly series with a seasonal pattern. Features use
previous observations, including the same month last year and the previous
quarter's average. A ridge model fits log volume, then converts its predictions
back to moves. The training command compares it with the seasonal baseline before
saving an artifact.

FastAPI loads that artifact and the observed history at startup. Requests reuse
the prepared features. Docker builds the service, Kubernetes manifests describe
a local deployment, and Prometheus and Grafana show request and prediction
metrics. [The README](../README.md) has the commands and
[the verification record](../VERIFICATION.md) separates demonstrated behavior
from deployment plans.

## Development history

The initial work covered six areas: data and baseline evaluation, API serving,
containerization, CI, Kubernetes manifests, and monitoring. Later changes added
history validation, cached serving features, reproducible examples, and a browser
demo. [The changelog](../CHANGELOG.md) records the dated changes.

## What I want evidence for

Does the model help compared with using last year's number? Does that hold in the
busy months? Can someone reproduce the comparison? Does the deployed API use the
same features as training?

Those questions matter more than adding a more complicated model. The synthetic
results demonstrate the pipeline. They do not establish performance on a real
shipment network. Contribution setup and verification rules live in
[CONTRIBUTING.md](../CONTRIBUTING.md).
