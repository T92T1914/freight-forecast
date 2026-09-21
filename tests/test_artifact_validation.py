"""A loaded file must be usable before the API advertises readiness."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import serve


@pytest.fixture
def artifact(client):
    return deepcopy(serve.app.state.artifact)


@pytest.mark.parametrize(
    "change",
    [
        lambda a: a.update(model=None),
        lambda a: a.update(feature_columns=["missing_feature"]),
        lambda a: a.update(feature_columns=a["feature_columns"][:-1]),
        lambda a: a.update(feature_columns=list(reversed(a["feature_columns"]))),
        lambda a: a.update(feature_columns=a["feature_columns"] * 2),
        lambda a: a.update(feature_columns="lag_12"),
        lambda a: a.update(trained_through="2022-13-01"),
        lambda a: a.update(trained_through="2022-12-15"),
        lambda a: a.update(trained_through=None),
        lambda a: a["metrics"].update(model_mae=float("nan")),
        lambda a: a["metrics"].update(model_mape=float("inf")),
        lambda a: a["metrics"].update(model_mae=-1),
        lambda a: a["metrics"].update(model_mae=True),
        lambda a: a["metrics"].pop("model_mape"),
        lambda a: a.update(metrics=None),
    ],
)
def test_invalid_artifact_fails_before_readiness(monkeypatch, artifact, change):
    change(artifact)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with (
        pytest.raises(ValueError, match="model artifact"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pass


@pytest.mark.parametrize("value", [None, [], "model"])
def test_non_mapping_artifact_is_rejected(client, monkeypatch, value):
    monkeypatch.setattr(serve.joblib, "load", lambda _: value)
    with (
        pytest.raises(ValueError, match="model artifact"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pass


@pytest.mark.parametrize(
    "predictions", [[float("nan")], [float("inf")], [-1], [], [100, 200], 100]
)
def test_unusable_predictions_fail_before_readiness(monkeypatch, artifact, predictions):
    monkeypatch.setattr(artifact["model"], "predict", lambda _: predictions)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with (
        pytest.raises(ValueError, match="model artifact.*prediction"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pass


def test_fitted_model_schema_is_checked_even_if_manifest_looks_valid(
    monkeypatch, artifact
):
    artifact["model"].regressor_.named_steps["standardscaler"].feature_names_in_[0] = (
        "obsolete_feature"
    )
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    with (
        pytest.raises(ValueError, match="model artifact.*prediction"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pass


def test_valid_artifact_executes_one_startup_probe(monkeypatch, artifact):
    original = artifact["model"].predict
    calls = []

    def predict(frame):
        calls.append(frame.copy())
        return original(frame)

    monkeypatch.setattr(artifact["model"], "predict", predict)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    app = FastAPI(lifespan=serve.lifespan)
    with TestClient(app):
        assert len(calls) == 1
        assert calls[0].columns.tolist() == artifact["feature_columns"]
        assert len(calls[0]) == 1
        assert calls[0].index[-1] == app.state.forecastable_range[1]
