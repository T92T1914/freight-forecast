import joblib
import pytest
from fastapi.testclient import TestClient

from src import train as train_mod
from src.serve import app


@pytest.fixture(scope="module")
def client():
    # serving requires the artifact; build it the same way a deployment would
    if not train_mod.MODEL_PATH.exists():
        artifact = train_mod.train(train_mod.load_data())
        train_mod.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(artifact, train_mod.MODEL_PATH)
    # context manager runs the lifespan (startup model load)
    with TestClient(app) as c:
        yield c


def test_health_reports_model(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["trained_through"]
    assert body["test_mae"] > 0


def test_predict_next_unobserved_month(client):
    r = client.post("/predict", json={"month": "2025-01"})
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2025-01"
    assert body["predicted_volume"] > 0
    assert body["naive_same_month_last_year"] > 0


def test_seasonality_is_visible_through_the_api(client):
    july = client.post("/predict", json={"month": "2024-07"}).json()
    january = client.post("/predict", json={"month": "2024-01"}).json()
    # the PCS peak: July forecasts must dwarf January's
    assert july["predicted_volume"] > 2 * january["predicted_volume"]


def test_prediction_matches_offline_model(client):
    """The API must serve the same number the artifact produces offline —
    a serving-skew guard."""
    from src.features import build_features
    from src.generate_data import DATA_PATH
    import pandas as pd

    artifact = joblib.load(train_mod.MODEL_PATH)
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    feats, _ = build_features(df)
    row = feats[feats["date"] == "2024-07-01"]
    offline = float(artifact["model"].predict(row[artifact["feature_columns"]])[0])

    api = client.post("/predict", json={"month": "2024-07"}).json()
    assert api["predicted_volume"] == round(offline)


def test_malformed_month_is_rejected(client):
    assert client.post("/predict", json={"month": "2025-13"}).status_code == 422
    assert client.post("/predict", json={"month": "not-a-month"}).status_code == 422
    assert client.post("/predict", json={}).status_code == 422


def test_out_of_range_months_are_rejected(client):
    # beyond one step ahead of the data
    assert client.post("/predict", json={"month": "2026-06"}).status_code == 400
    # before a full 12-month history exists
    assert client.post("/predict", json={"month": "2017-05"}).status_code == 400
