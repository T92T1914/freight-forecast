"""Serving history may shrink without changing the fitted trend coordinate."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src import serve
from src.features import build_features
from src.generate_data import generate
from src.train import train


@pytest.mark.parametrize("drop", [1, 12, 48, 84])
def test_trimming_old_history_preserves_shared_forecasts(client, monkeypatch, drop):
    before_features = serve.app.state.features.copy()
    before = client.post("/predict", json={"month": "2025-01"}).json()
    recent = serve.app.state.history.iloc[drop:].reset_index(drop=True)
    monkeypatch.setattr(serve.pd, "read_csv", lambda *args, **kwargs: recent)
    with TestClient(serve.app) as restarted:
        after = restarted.post("/predict", json={"month": "2025-01"})
        assert after.status_code == 200
        assert after.json() == before
        after_features = serve.app.state.features
        pd.testing.assert_frame_equal(
            before_features.loc[after_features.index], after_features
        )


def test_training_records_the_origin_even_for_reordered_input():
    data = generate().iloc[::-1]
    artifact = train(data)
    assert artifact["trend_origin"] == "2017-01-01"


def test_explicit_origin_matches_full_history_features():
    data = generate()
    complete, columns = build_features(data)
    trimmed, actual_columns = build_features(
        data.iloc[24:], trend_origin=data["date"].min()
    )
    assert actual_columns == columns
    expected = complete.set_index("date").loc[trimmed["date"]]
    pd.testing.assert_frame_equal(expected, trimmed.set_index("date"))


@pytest.mark.parametrize("origin", [None, "bad", "2017-01-02", "2025-01-01", 2017])
def test_invalid_origin_prevents_serving(client, monkeypatch, origin):
    artifact = dict(serve.app.state.artifact, trend_origin=origin)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with (
        pytest.raises(ValueError, match="model artifact.*trend_origin"),
        TestClient(serve.app),
    ):
        pass


def test_legacy_artifact_requires_explicit_retraining(client, monkeypatch):
    artifact = dict(serve.app.state.artifact)
    artifact.pop("trend_origin", None)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with pytest.raises(ValueError, match="retrain"), TestClient(serve.app):
        pass


def test_origin_cannot_follow_the_training_cutoff(client, monkeypatch):
    artifact = dict(serve.app.state.artifact, trained_through="2016-12-01")
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with (
        pytest.raises(ValueError, match="trend_origin.*training cutoff"),
        TestClient(serve.app),
    ):
        pass
