"""Export saved demonstration values from one validated, trusted model file."""

import argparse
import hashlib
import io
import json
import subprocess
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.features import build_features
from src.provenance import validate_provenance
from src.train import MODEL_PATH


def export_evidence(path: Path, source_commit: str) -> dict:
    model_bytes = path.read_bytes()
    artifact = joblib.load(io.BytesIO(model_bytes))
    history = pd.DataFrame(artifact["provenance"]["history"])
    history["date"] = pd.to_datetime(history["date"])
    validate_provenance(artifact, history)
    features, columns = build_features(history)
    evaluation = artifact["provenance"]["evaluation"]["rows"]
    held_out = features.set_index("date").loc[
        [pd.Timestamp(row["date"]) for row in evaluation], columns
    ]
    predictions = artifact["model"].predict(held_out)
    if not np.allclose(
        predictions, [r["prediction"] for r in evaluation], rtol=1e-12, atol=1e-9
    ):
        raise ValueError("saved evaluation predictions do not match the fitted model")
    return {
        "source_commit": source_commit,
        "source": "src.export_evidence and the validated model artifact",
        "data_type": artifact["provenance"]["data_type"],
        "trained_through": artifact["trained_through"],
        "evaluation": artifact["provenance"]["evaluation"]["method"],
        "metrics": artifact["metrics"],
        "model_id": hashlib.sha256(model_bytes).hexdigest(),
        "history_id": artifact["provenance"]["history_id"],
        "provenance": artifact["provenance"],
        "rows": [
            {
                "month": row["date"][:7],
                "actual": row["actual"],
                "model": row["prediction"],
                "baseline": row["naive"],
            }
            for row in evaluation
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # The revision is a locator. File digests in the artifact identify the exact
    # training implementation even when the checkout contained local changes.
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    evidence = export_evidence(args.model, revision)
    evidence["source_worktree_dirty"] = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--", "src"], cwd=root, text=True
        ).strip()
    )
    args.output.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print("Exported", len(evidence["rows"]), "months from model", evidence["model_id"])


if __name__ == "__main__":
    main()
