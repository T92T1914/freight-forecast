# The common commands, in one place. CI calls these same targets, so what
# runs on a laptop and what gates a merge cannot drift apart.
#
# PY is the interpreter to use; default to whatever `python` resolves to
# (a venv on PATH, or the setup-python interpreter in CI).
PY ?= python

.DEFAULT_GOAL := help

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-9s %s\n", $$1, $$2}'

install: ## runtime + dev dependencies into the active interpreter
	$(PY) -m pip install -r requirements-dev.txt

data: ## regenerate data/shipments.csv from the fixed seed
	$(PY) -m src.generate_data

train: ## train, evaluate against the seasonal naive, save models/model.joblib
	$(PY) -m src.train

serve: ## run the API locally with reload (http://127.0.0.1:8000/docs)
	$(PY) -m uvicorn src.serve:app --reload

test: ## run the test suite
	$(PY) -m pytest -q

lint: ## ruff check + format check, exactly what CI runs
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

format: ## rewrite files so that lint passes
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

check: lint test ## everything CI gates on, minus the image build

image: ## build the serving image (the build trains the model)
	docker build -t freight-forecast .

run: image ## serve predictions from the container on :8000
	docker run --rm -p 8000:8000 freight-forecast

monitor: ## API + Prometheus + Grafana via docker compose
	docker compose -f monitoring/docker-compose.yml up --build

k8s: ## apply the deployment and service to the current kubectl context
	kubectl apply -f k8s/

.PHONY: help install data train serve test lint format check image run monitor k8s
