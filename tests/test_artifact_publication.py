"""An interrupted training save must leave the last usable model intact."""

from pathlib import Path

import joblib
import pytest

from src import train as training
from src.features import build_features
from src.generate_data import generate
from src.train import train as fit_model


@pytest.fixture
def destination(tmp_path, monkeypatch):
    path = tmp_path / "models" / "model.joblib"
    monkeypatch.setattr(training, "MODEL_PATH", path)
    monkeypatch.setattr(training, "load_data", lambda: None)
    monkeypatch.setattr(
        training,
        "train",
        lambda _: {
            "metrics": {
                "model_mae": 1,
                "model_mape": 0.01,
                "naive_mae": 2,
                "naive_mape": 0.02,
            }
        },
    )
    return path


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", [OSError("disk full"), KeyboardInterrupt()])
def test_partial_serialization_never_replaces_live_artifact(
    destination, monkeypatch, existing, failure
):
    destination.parent.mkdir()
    if existing:
        joblib.dump({"version": "last usable model"}, destination)
    previous = destination.read_bytes() if existing else None

    def interrupted_dump(artifact, target):
        if hasattr(target, "write"):
            target.write(b"incomplete model")
            target.flush()
        else:
            Path(target).write_bytes(b"incomplete model")
        # A concurrent restart must still see the old complete file, or no file.
        assert destination.exists() is existing
        if existing:
            assert destination.read_bytes() == previous
        raise failure

    monkeypatch.setattr(training.joblib, "dump", interrupted_dump)
    with pytest.raises(type(failure)):
        training.main()
    assert destination.exists() is existing
    if existing:
        assert joblib.load(destination) == {"version": "last usable model"}
    assert list(destination.parent.iterdir()) == ([destination] if existing else [])


@pytest.mark.parametrize("existing", [False, True])
def test_successful_training_save_loads_the_complete_fitted_model(
    destination, monkeypatch, existing
):
    if existing:
        destination.parent.mkdir()
        joblib.dump({"version": "last usable model"}, destination)
    saved = fit_model(generate())
    data, columns = build_features(generate())
    monkeypatch.setattr(training, "train", lambda _: saved)
    training.main()
    loaded = joblib.load(destination)
    assert loaded["model"].predict(data[columns].tail(1)) == pytest.approx(
        saved["model"].predict(data[columns].tail(1))
    )
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("stage", ["fsync", "replace"])
def test_failed_promotion_preserves_existing_model(destination, monkeypatch, stage):
    destination.parent.mkdir()
    joblib.dump({"version": "last usable model"}, destination)

    def fail(*args):
        raise OSError(f"{stage} refused")

    monkeypatch.setattr(training.os, stage, fail)
    with pytest.raises(OSError, match="refused"):
        training.main()
    assert joblib.load(destination) == {"version": "last usable model"}
    assert list(destination.parent.iterdir()) == [destination]


def test_losing_model_does_not_touch_existing_artifact(destination, monkeypatch):
    destination.parent.mkdir()
    joblib.dump({"version": "last usable model"}, destination)
    monkeypatch.setattr(
        training,
        "train",
        lambda _: {
            "metrics": {
                "model_mae": 2,
                "model_mape": 0.02,
                "naive_mae": 1,
                "naive_mape": 0.01,
            }
        },
    )
    with pytest.raises(SystemExit, match="1"):
        training.main()
    assert joblib.load(destination) == {"version": "last usable model"}
    assert list(destination.parent.iterdir()) == [destination]
