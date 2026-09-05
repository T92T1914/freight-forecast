"""Fixtures shared across test modules.

The API tests need a trained artifact on disk, because the app loads it at
startup and refuses to start without it. Building it here, the same way a
deployment does, means no test module carries its own copy of that setup.
"""

import joblib
import pytest
from fastapi.testclient import TestClient

from src import train as train_mod
from src.serve import app


@pytest.fixture(scope="session")
def client():
    if not train_mod.MODEL_PATH.exists():
        artifact = train_mod.train(train_mod.load_data())
        train_mod.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(artifact, train_mod.MODEL_PATH)
    # entering the context runs the lifespan, i.e. the startup model load
    with TestClient(app) as c:
        yield c
