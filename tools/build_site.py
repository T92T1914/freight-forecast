"""Build a public site from an explicit list of public example files."""

import hashlib
import json
import math
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_site"
FILES = {
    "site/index.html": "index.html",
    "site/style.css": "style.css",
    "site/app.js": "app.js",
    "site/selection-state.mjs": "selection-state.mjs",
    "site/backtest.js": "backtest.js",
    "docs/backtest-example.json": "backtest.json",
    "docs/visual-example-data.json": "data.json",
    "docs/freight-forecast-example.svg": "example.svg",
}


def validate_evidence(data):
    """Check that displayed values still belong to the saved artifact evidence."""
    for key in ("model_id", "history_id"):
        value = data.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("Example data must retain its " + key)
    provenance = data["provenance"]
    digest = hashlib.sha256(
        json.dumps(
            provenance["history"],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    if data["history_id"] != provenance["history_id"] or digest != data["history_id"]:
        raise ValueError("Saved example history identity differs from provenance")
    if (
        data["trained_through"] != provenance["training_cutoff"]
        or data["data_type"] != provenance["data_type"]
    ):
        raise ValueError("Saved example metadata differs from provenance")
    expected = [
        {
            "month": r["date"][:7],
            "actual": r["actual"],
            "model": r["prediction"],
            "baseline": r["naive"],
        }
        for r in provenance["evaluation"]["rows"]
    ]
    if not expected or data["rows"] != expected:
        raise ValueError("Saved example rows differ from provenance")
    for label, field in (("model", "model"), ("naive", "baseline")):
        error = sum(abs(row[field] - row["actual"]) for row in expected) / len(expected)
        if not math.isclose(
            data["metrics"][label + "_mae"], error, rel_tol=1e-12, abs_tol=1e-9
        ):
            raise ValueError("Saved example metric differs from its rows")


def main():
    data = json.loads((ROOT / "docs/visual-example-data.json").read_text())
    if not data.get("source_commit"):
        raise ValueError("Example data must retain its source revision.")
    validate_evidence(data)
    backtest = json.loads((ROOT / "docs/backtest-example.json").read_text())
    if not backtest.get("provenance", {}).get("implementation_sha256"):
        raise ValueError("Backtest data must retain its implementation hashes.")
    OUT.mkdir(exist_ok=True)
    for source, target in FILES.items():
        path = ROOT / source
        if not path.is_file() or path.is_symlink():
            raise ValueError("Expected a regular source file: " + source)
        shutil.copyfile(path, OUT / target)
    unexpected = {p.name for p in OUT.iterdir()} - set(FILES.values())
    if unexpected:
        raise ValueError("Unexpected site output files: " + str(sorted(unexpected)))
    print("Built", len(FILES), "public files in", OUT)


if __name__ == "__main__":
    main()
