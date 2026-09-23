"""The public example must come from the artifact actually checked and served."""

from copy import deepcopy
from hashlib import sha256

import joblib
import pytest

from src.export_evidence import export_evidence
from tools.build_site import validate_evidence


def test_export_retains_model_history_and_evaluation(serving_artifact_path):
    evidence = export_evidence(serving_artifact_path, "a" * 40)
    artifact = joblib.load(serving_artifact_path)
    assert (
        evidence["model_id"] == sha256(serving_artifact_path.read_bytes()).hexdigest()
    )
    assert evidence["history_id"] == artifact["provenance"]["history_id"]
    assert evidence["metrics"] == artifact["metrics"]
    assert len(evidence["rows"]) == 24
    assert evidence["data_type"] == "seeded synthetic shipments"
    validate_evidence(evidence)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("model_id"),
        lambda d: d.update(history_id="0" * 64),
        lambda d: d["rows"][0].update(model=0),
        lambda d: d["metrics"].update(model_mae=0),
        lambda d: d.update(trained_through="2021-12-01"),
        lambda d: d.update(data_type="operational observations"),
        lambda d: d["provenance"]["history"][0].update(volume=1),
    ],
)
def test_site_rejects_detached_or_changed_evidence(serving_artifact_path, change):
    evidence = deepcopy(export_evidence(serving_artifact_path, "a" * 40))
    change(evidence)
    with pytest.raises(ValueError):
        validate_evidence(evidence)


def test_export_checks_fitted_estimator_against_recorded_predictions(
    serving_artifact_path, tmp_path
):
    artifact = joblib.load(serving_artifact_path)
    artifact["model"].regressor_.named_steps["ridge"].intercept_ += 1
    path = tmp_path / "mismatched.joblib"
    joblib.dump(artifact, path)
    with pytest.raises(ValueError, match="predictions do not match"):
        export_evidence(path, "a" * 40)
