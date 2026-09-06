"""Endpoint tests. The `client` fixture (conftest.py) serves a trained artifact."""

import joblib
import pandas as pd
from fastapi.testclient import TestClient

from src import serve
from src import train as train_mod
from src.features import build_features
from src.generate_data import DATA_PATH


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
    artifact = joblib.load(train_mod.MODEL_PATH)
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    feats, _ = build_features(df)
    row = feats[feats["date"] == "2024-07-01"]
    offline = float(artifact["model"].predict(row[artifact["feature_columns"]])[0])

    api = client.post("/predict", json={"month": "2024-07"}).json()
    assert api["predicted_volume"] == round(offline)


def test_openapi_declares_the_response_fields(client):
    """/docs is the contract a consumer reads. The response fields must be
    declared there, not just happen to appear in the JSON."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["PredictResponse"]["required"]) == {
        "month",
        "predicted_volume",
        "naive_same_month_last_year",
        "trained_through",
    }
    assert set(schemas["HealthResponse"]["required"]) == {
        "status",
        "model_loaded",
        "trained_through",
        "test_mae",
        "test_mape",
    }


def test_malformed_month_is_rejected(client):
    assert client.post("/predict", json={"month": "2025-13"}).status_code == 422
    assert client.post("/predict", json={"month": "not-a-month"}).status_code == 422
    assert client.post("/predict", json={}).status_code == 422


def test_out_of_range_months_are_rejected(client):
    # beyond one step ahead of the data
    assert client.post("/predict", json={"month": "2026-06"}).status_code == 400
    # before a full 12-month history exists
    assert client.post("/predict", json={"month": "2017-05"}).status_code == 400


def test_range_gate_boundaries_are_exact(client):
    """Pin the exact edges of the one-step-ahead gate, derived from the data.

    An off-by-one in the gate would not 400 — it would admit a month with no
    feature row and 500 on an empty frame — so the edges themselves must be
    tested, not just far-out months."""
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    first_ok = df["date"].iloc[0] + pd.DateOffset(months=12)
    last_ok = df["date"].iloc[-1] + pd.DateOffset(months=1)

    def status(ts):
        return client.post("/predict", json={"month": f"{ts:%Y-%m}"}).status_code

    assert status(first_ok) == 200
    assert status(last_ok) == 200
    assert status(first_ok - pd.DateOffset(months=1)) == 400
    assert status(last_ok + pd.DateOffset(months=1)) == 400


def test_placeholder_branch_matches_offline(client):
    """The next-unobserved-month path builds features through a placeholder
    row — the only serving-side construction that differs from training, and
    the endpoint's actual production use. Pin it to the offline model."""
    artifact = joblib.load(train_mod.MODEL_PATH)
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    next_month = df["date"].iloc[-1] + pd.DateOffset(months=1)
    df = pd.concat(
        [df, pd.DataFrame({"date": [next_month], "volume": [float("nan")]})],
        ignore_index=True,
    )
    feats, _ = build_features(df)
    row = feats[feats["date"] == next_month]
    offline = float(artifact["model"].predict(row[artifact["feature_columns"]])[0])

    api = client.post("/predict", json={"month": f"{next_month:%Y-%m}"}).json()
    assert api["predicted_volume"] == round(offline)


def test_every_served_month_matches_uncached_features(client, monkeypatch):
    """Every historical/next-month response matches the former per-call path."""
    history = serve.app.state.history
    artifact = serve.app.state.artifact
    first, last = serve.app.state.forecastable_range

    def unexpected_rebuild(*args, **kwargs):
        raise AssertionError("feature construction belongs to startup")

    monkeypatch.setattr(serve, "build_features", unexpected_rebuild)
    for ts in pd.date_range(first, last, freq="MS"):
        data = history
        if ts == last:
            data = pd.concat(
                [data, pd.DataFrame({"date": [ts], "volume": [float("nan")]})],
                ignore_index=True,
            )
        feats, _ = build_features(data)
        row = feats[feats["date"] == ts]
        expected = round(
            float(artifact["model"].predict(row[artifact["feature_columns"]])[0])
        )
        response = client.post("/predict", json={"month": f"{ts:%Y-%m}"})
        assert response.status_code == 200
        assert response.json() == {
            "month": f"{ts:%Y-%m}",
            "predicted_volume": expected,
            "naive_same_month_last_year": int(row["lag_12"].iloc[0]),
            "trained_through": artifact["trained_through"],
        }


def test_restart_rebuilds_features_even_when_dates_and_row_count_match(
    client, monkeypatch
):
    history = serve.app.state.history.copy()
    before = serve.app.state.features.copy()
    history.loc[len(history) - 12, "volume"] += 100
    monkeypatch.setattr(serve.pd, "read_csv", lambda *args, **kwargs: history)
    with TestClient(serve.app):
        after = serve.app.state.features
        assert before.index.equals(after.index)
        assert after.iloc[-1]["lag_12"] == before.iloc[-1]["lag_12"] + 100
