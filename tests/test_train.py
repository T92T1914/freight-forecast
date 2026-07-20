import joblib
import pytest

from src.features import build_features
from src.generate_data import generate
from src.train import TEST_MONTHS, train


# tests build their data via generate() rather than load_data() so the
# suite never writes into the repo tree and cannot depend on a stale CSV
@pytest.fixture(scope="module")
def artifact():
    return train(generate())


def test_model_beats_seasonal_naive(artifact):
    m = artifact["metrics"]
    assert m["model_mae"] < m["naive_mae"]
    assert m["model_mape"] < m["naive_mape"]


def test_artifact_roundtrip(tmp_path, artifact):
    path = tmp_path / "model.joblib"
    joblib.dump(artifact, path)
    loaded = joblib.load(path)

    feats, feature_cols = build_features(generate())
    preds = loaded["model"].predict(feats[feature_cols].iloc[-TEST_MONTHS:])
    assert len(preds) == TEST_MONTHS
    assert (preds > 0).all()
    assert loaded["feature_columns"] == feature_cols


def test_predictions_are_in_volume_units(artifact):
    # the log transform must be inverted inside the model: forecasts for
    # peak months should be thousands of moves, not log-scale values
    feats, feature_cols = build_features(generate())
    august = feats[feats["date"].dt.month == 8].iloc[[-1]]
    pred = artifact["model"].predict(august[feature_cols])[0]
    assert 5_000 < pred < 20_000
