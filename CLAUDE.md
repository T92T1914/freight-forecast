# freight-forecast

Portfolio project. A shipment volume forecasting model served as a
containerized API with CI, Kubernetes deployment, and monitoring.
The owner is a CS student (AI/ML concentration) with six years in
Air Force logistics: JPPSO household goods movement, freight rates,
storage management, and search and rescue support. Forecasting
demand cycles was his operational reality, not a class exercise.
He is building this to learn it deeply and
defend it in interviews. Depth of understanding beats speed. Always.

## Stack

Python 3.11+, pandas, scikit-learn, FastAPI, pytest, Docker,
GitHub Actions, Kubernetes (k3d or minikube), prometheus-client.

## Working rules

1. One milestone per session. Never start the next milestone
   without being told to.
2. Before writing any code, state the plan in a few plain
   sentences and wait for approval.
3. Small commits. One logical change each, clear message. Never
   one giant commit.
4. Explain every non-obvious decision at the moment it is made,
   in plain language, including what the alternatives were.
5. Never add a dependency without saying why it is needed.
6. The owner writes the README section for each milestone
   himself. Do not write it for him. Review his draft if asked.
7. When a milestone is done, quiz the owner with five questions
   about what was built and why. Push back on weak answers until
   they hold.
8. Tests are required for all endpoints and model logic before a
   milestone counts as done.

## Milestones

1. **Data and baseline model.** Source or generate a monthly
   household goods shipment volume dataset. Synthetic is fine if it
   has realistic seasonality, modeled on the DoD PCS peak the owner
   worked at JPPSO, where nearly 40,000 moves landed between May
   and August each year. Train a baseline forecasting model and
   evaluate it against a naive predictor. Save the model artifact.
   Done when metrics beat the naive baseline and one script
   reproduces training end to end.
2. **Serving.** FastAPI app with /predict and /health. Model
   loads at startup, inputs are validated, pytest covers the
   endpoints. Done when tests pass and the API runs locally.
3. **Containerize.** Dockerfile with a small image that runs the
   API. Done when docker run serves predictions.
4. **CI.** GitHub Actions workflow that lints, runs tests, and
   builds the image on every push. Done when the check is green
   on GitHub.
5. **Kubernetes.** Local cluster via k3d or minikube. Deployment,
   service, liveness and readiness probes. Done when the endpoint
   answers through the cluster.
6. **Monitoring.** Prometheus metrics endpoint covering request
   count, latency, and prediction distribution, plus one Grafana
   dashboard. Done when the dashboard shows live traffic.

After milestone 6, resume bullets get written from real results,
not intentions.

All six milestones were completed and verified in 2026. This file now
governs maintenance only: the working rules still apply to any change.
