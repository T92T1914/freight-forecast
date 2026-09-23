"""A forecast must identify both its fitted model and its observation history."""

from copy import deepcopy
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from src import serve
from src.provenance import digest, history_rows


def test_changed_recorded_observation_cannot_silently_replace_model_evidence(
    client, monkeypatch
):
    history = serve.app.state.history.copy()
    history.loc[len(history) - 12, "volume"] += 100
    monkeypatch.setattr(serve.pd, "read_csv", lambda *args, **kwargs: history)
    with pytest.raises(ValueError, match="history.*provenance"), TestClient(serve.app):
        pytest.fail("Changed recorded observations reached readiness")


def test_response_identity_connects_health_window_and_forecast(client):
    health = client.get("/health").json()
    window = client.get("/forecast-window").json()
    prediction = client.post(
        "/predict", json={"month": window["next_unobserved_month"]}
    ).json()
    for key in ("model_id", "history_id"):
        assert len(health[key]) == 64
        assert window[key] == prediction[key] == health[key]
    assert health["model_id"] == sha256(serve.MODEL_PATH.read_bytes()).hexdigest()
    assert health["history_id"] == digest(history_rows(serve.app.state.history))


@pytest.mark.parametrize(
    "change",
    [
        lambda a: a.pop("provenance"),
        lambda a: a["provenance"].update(schema_version=2),
        lambda a: a["provenance"].update(feature_schema="obsolete"),
        lambda a: a["provenance"].update(history_id="0" * 64),
        lambda a: a["provenance"]["history"][-1].update(volume=123),
        lambda a: a["provenance"]["evaluation"]["rows"][-1].update(prediction=123),
        lambda a: a["provenance"]["evaluation"]["rows"].pop(),
        lambda a: a["provenance"]["dependencies"].pop("scikit-learn"),
        lambda a: a["provenance"].update(training_cutoff="2021-12-01"),
        lambda a: a["provenance"].update(training_rows=1),
        lambda a: a["provenance"]["evaluation"].update(method="random split"),
        lambda a: a["metrics"].update(naive_mape=float("nan")),
        lambda a: a["metrics"].update(naive_mae=float("inf")),
    ],
)
def test_incompatible_or_incomplete_provenance_prevents_readiness(
    client, monkeypatch, change
):
    artifact = deepcopy(serve.app.state.artifact)
    change(artifact)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with (
        pytest.raises(ValueError, match="model artifact provenance"),
        TestClient(serve.app),
    ):
        pytest.fail("Invalid provenance reached readiness")


def test_model_identity_uses_the_bytes_loaded_despite_replacement(client, monkeypatch):
    original = serve.MODEL_PATH.read_bytes()
    load = serve.joblib.load

    def replacing_load(stream):
        # The pathname can change after the snapshot has been opened. Hashing
        # the pathname again would label this loaded model with different bytes.
        monkeypatch.setattr(
            type(serve.MODEL_PATH), "read_bytes", lambda _: b"replacement"
        )
        return load(stream)

    monkeypatch.setattr(serve.joblib, "load", replacing_load)
    with TestClient(serve.app) as restarted:
        assert (
            restarted.get("/health").json()["model_id"] == sha256(original).hexdigest()
        )
