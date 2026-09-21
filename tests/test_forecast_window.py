"""The discoverable window must match actual serving and refreshed history."""

import pandas as pd
from fastapi.testclient import TestClient

from src import serve


def test_reported_window_matches_prediction_boundaries(client):
    response = client.get("/forecast-window")
    assert response.status_code == 200
    window = response.json()
    first = pd.Timestamp(window["first_supported_month"])
    last = pd.Timestamp(window["last_supported_month"])
    for month in (first, last):
        assert (
            client.post("/predict", json={"month": f"{month:%Y-%m}"}).status_code == 200
        )
    for month in (first - pd.DateOffset(months=1), last + pd.DateOffset(months=1)):
        assert (
            client.post("/predict", json={"month": f"{month:%Y-%m}"}).status_code == 400
        )
    assert window["next_unobserved_month"] == window["last_supported_month"]
    assert pd.Timestamp(window["observed_through"]) + pd.DateOffset(months=1) == last
    assert window["horizon_months"] == 1
    assert window["trained_through"] == client.get("/health").json()["trained_through"]


def test_window_refreshes_with_observations_without_claiming_retraining(
    client, monkeypatch
):
    before = client.get("/forecast-window").json()
    history = serve.app.state.history.copy()
    extra = pd.DataFrame(
        {"date": [pd.Timestamp(before["next_unobserved_month"])], "volume": [3500]}
    )
    history = pd.concat([history, extra], ignore_index=True)
    # Keep the altered read scoped to this restart; leave the session client
    # with its original loaded history when the check finishes.
    with monkeypatch.context() as patch:
        patch.setattr(serve.pd, "read_csv", lambda *args, **kwargs: history)
        with TestClient(serve.app) as restarted:
            after = restarted.get("/forecast-window").json()
            assert after["observed_through"] == before["next_unobserved_month"]
            assert pd.Timestamp(after["next_unobserved_month"]) == pd.Timestamp(
                before["next_unobserved_month"]
            ) + pd.DateOffset(months=1)
            assert after["trained_through"] == before["trained_through"]
            assert (
                restarted.post(
                    "/predict", json={"month": after["next_unobserved_month"]}
                ).status_code
                == 200
            )
    with TestClient(serve.app):
        assert client.get("/forecast-window").json() == before


def test_window_uses_loaded_state_and_does_not_run_model(client, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError(
            "Discovery must not read files, rebuild features or predict"
        )

    monkeypatch.setattr(serve.pd, "read_csv", unexpected)
    monkeypatch.setattr(serve, "build_features", unexpected)
    monkeypatch.setattr(serve.app.state.artifact["model"], "predict", unexpected)
    assert client.get("/forecast-window").status_code == 200


def test_window_has_a_documented_response_contract(client):
    document = client.get("/openapi.json").json()
    schema = document["paths"]["/forecast-window"]["get"]["responses"]["200"]
    assert schema["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ForecastWindowResponse"
    )
