"""Evaluate new intervals around retained point forecasts without refitting."""

import argparse
import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.intervals import ResidualCalibrator, month_number, summarize  # noqa: E402

PROTOCOL = ROOT / "docs/interval-protocol.json"
METHODS = ("model", "last_observation", "seasonal_naive")
INTERVALS = ("fixed", "rolling", "adaptive")


def text_hash(path):
    return hashlib.sha256(path.read_text(encoding="utf-8-sig").encode()).hexdigest()


def load_inputs():
    protocol = json.loads(PROTOCOL.read_text())
    expected = {
        "schema_version": 1,
        "id": "bts-retrospective-residual-intervals-v1",
        "input": "docs/real-data-example.json",
        "source_policy": "monthly_refit",
        "calibration_start": "2015-01",
        "calibration_end": "2019-12",
        "evaluation_start": "2020-01",
        "evaluation_end": "2025-12",
        "point_methods": list(METHODS),
        "interval_methods": list(INTERVALS),
        "window": 60,
        "target_miscoverage": "0.1",
        "adaptive_step": "0.005",
    }
    if any(protocol.get(key) != value for key, value in expected.items()):
        raise ValueError("Review another protocol before changing study controls")
    source = ROOT / protocol["input"]
    if text_hash(source) != protocol["input_sha256_lf"]:
        raise ValueError("Retained point forecast identity changed")
    saved = json.loads(source.read_text())
    if (
        saved["selected_policy"] != "monthly_refit"
        or saved["test"]["policy"] != "monthly_refit"
        or saved["units"] != "index_points_2000_average_100"
    ):
        raise ValueError("Unexpected source policy or units")
    calibration = [
        r
        for r in saved["development"]["monthly_refit"]["predictions"]
        if protocol["calibration_start"] <= r["month"] <= protocol["calibration_end"]
    ]
    evaluation = saved["test"]["predictions"]
    for rows, start, size in [
        (calibration, "2015-01", 60),
        (evaluation, "2020-01", 72),
    ]:
        if len(rows) != size or [month_number(r["month"]) for r in rows] != list(
            range(month_number(start), month_number(start) + size)
        ):
            raise ValueError("Source calendar differs from declared coverage")
        for row in rows:
            if month_number(row["observed_through"]) != month_number(
                row["month"]
            ) - 1 or month_number(row["trained_through"]) >= month_number(row["month"]):
                raise ValueError("Point forecast uses future observations")
    return protocol, saved, calibration, evaluation


def evaluate(calibration, evaluation, protocol):
    groups = []
    for point_method in METHODS:
        initial = [
            (r["month"], abs(r["actual"] - r[point_method])) for r in calibration
        ]
        for method in INTERVALS:
            calibrator = ResidualCalibrator(
                initial,
                method=method,
                window=protocol["window"],
                alpha=protocol["target_miscoverage"],
                gamma=protocol["adaptive_step"],
            )
            rows = []
            for row in evaluation:
                calibrator.issue(row["month"], row[point_method])
                rows.append(calibrator.observe(row["month"], row["actual"]))
            groups.append(
                {
                    "point_method": point_method,
                    "interval_method": method,
                    "summary": summarize(rows),
                    "by_year": {
                        year: summarize(
                            [r for r in rows if r["month"].startswith(year)]
                        )
                        for year in sorted({r["month"][:4] for r in rows})
                    },
                    "rows": rows,
                }
            )
    return groups


def run(output):
    if output.exists() or output.with_suffix(output.suffix + ".tmp").exists():
        raise ValueError("Use a new output path; retained attempts are not overwritten")
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    ).strip():
        raise ValueError("Commit the protocol and implementation before evaluation")
    protocol, saved, calibration, evaluation = load_inputs()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    hashes = {
        name: text_hash(ROOT / name)
        for name in (
            "src/intervals.py",
            "tools/evaluate_intervals.py",
            "docs/interval-protocol.json",
        )
    }
    result = {
        "schema_version": 1,
        "kind": "retrospective_intervals_on_retained_point_forecasts",
        "evaluated_revision": revision,
        "implementation_sha256_lf": hashes,
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "python": platform.python_version(),
        "protocol": protocol,
        "units": saved["units"],
        "point_forecast_source": {
            "path": protocol["input"],
            "sha256_lf": protocol["input_sha256_lf"],
            "evaluated_revision": saved["validation_update"]["evaluated_revision"],
            "data_manifest": saved["provenance"]["data"],
        },
        "refitting_performed": False,
        "groups": evaluate(calibration, evaluation, protocol),
    }
    if any(text_hash(ROOT / name) != value for name, value in hashes.items()):
        raise ValueError("Implementation changed during evaluation")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = run(parser.parse_args().output)
    for group in result["groups"]:
        print(group["point_method"], group["interval_method"], group["summary"])


if __name__ == "__main__":
    main()
