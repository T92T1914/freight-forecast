"""Reject unusable observed history before exposing a healthy service."""

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import serve
from src.train import train


@pytest.fixture(autouse=True)
def model_ready(client):
    """Use the normal artifact setup before replacing the input history."""


def history(size=24):
    return pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", periods=size, freq="MS"),
            "volume": [1000.0] * size,
        }
    )


@pytest.mark.parametrize("size", [0, 1, 11])
def test_short_history_fails_during_startup(monkeypatch, size):
    monkeypatch.setattr(serve.pd, "read_csv", lambda *a, **kw: history(size))
    with (
        pytest.raises(ValueError, match="at least 12"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pass


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, "broken"])
@pytest.mark.parametrize("index", [0, 12, 23])
def test_invalid_observation_is_not_silently_dropped(monkeypatch, bad, index):
    frame = history().astype({"volume": "object"})
    frame.loc[index, "volume"] = bad
    monkeypatch.setattr(serve.pd, "read_csv", lambda *a, **kw: frame)
    with (
        pytest.raises(ValueError, match="finite, nonnegative"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pass


def test_twelve_observed_months_allow_one_future_prediction(monkeypatch):
    complete = serve.app.state.history.copy()
    complete.loc[0, "volume"] = 0
    artifact = train(complete)
    monkeypatch.setattr(serve.joblib, "load", lambda _: artifact)
    frame = complete.iloc[:12]
    monkeypatch.setattr(serve.pd, "read_csv", lambda *a, **kw: frame)
    app = FastAPI(lifespan=serve.lifespan)
    with TestClient(app):
        assert len(app.state.features) == 1
        assert app.state.features.index[0] == pd.Timestamp("2018-01-01")
        assert app.state.features.iloc[0]["lag_12"] == 0


@pytest.mark.parametrize("hours", [1, 12, 23])
def test_non_midnight_csv_cannot_advertise_unusable_forecasts(
    monkeypatch, tmp_path, hours
):
    frame = history()
    frame["date"] += pd.Timedelta(hours=hours)
    path = tmp_path / "observations.csv"
    frame.to_csv(path, index=False)
    monkeypatch.setattr(serve, "DATA_PATH", path)
    with (
        pytest.raises(ValueError, match="contiguous monthly"),
        TestClient(FastAPI(lifespan=serve.lifespan)),
    ):
        pytest.fail("invalid calendar reached readiness")
