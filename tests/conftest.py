"""Fixtures shared across test modules.

The API tests need a trained artifact on disk, because the app loads it at
startup and refuses to start without it. Building it here, the same way a
deployment does, means no test module carries its own copy of that setup.
"""

import joblib
import pytest
from fastapi.testclient import TestClient

from src import serve
from src import train as train_mod
from src.serve import app


@pytest.fixture(scope="session")
def serving_artifact_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("serving-artifact") / "model.joblib"
    joblib.dump(train_mod.train(train_mod.load_data()), path)
    return path


@pytest.fixture
def client(monkeypatch, serving_artifact_path):
    # Rebuild the contract under test without replacing the user's saved model.
    # A fresh lifespan also prevents state changed by one API test leaking out.
    monkeypatch.setattr(serve, "MODEL_PATH", serving_artifact_path)
    monkeypatch.setattr(train_mod, "MODEL_PATH", serving_artifact_path)
    # entering the context runs the lifespan, i.e. the startup model load
    with TestClient(app) as c:
        yield c
