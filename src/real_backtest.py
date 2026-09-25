"""Evaluate the existing estimator on a frozen BTS index, without serving changes."""

import argparse
import hashlib
import json
import platform
import re
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest import _observations
from src.features import build_features
from src.train import make_model

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/public/bts-freight-tsi-2026-09-24.csv"
MANIFEST = ROOT / "data/public/bts-freight-tsi-2026-09-24.json"
PROTOCOL = ROOT / "docs/real-data-protocol.json"
POLICIES = ("fixed", "monthly_refit")
METHODS = ("model", "last_observation", "seasonal_naive")
POLICY_DESCRIPTION = (
    "Refit through development end before final test. Fixed freezes estimator "
    "coefficients but updates observed lag features. Monthly_refit refits after "
    "each observed month."
)


def load_snapshot(data_path: Path, manifest_path: Path) -> tuple[pd.DataFrame, dict]:
    """Check source identity and calendar before exposing observations to a model."""
    raw = data_path.read_bytes()
    manifest = json.loads(manifest_path.read_bytes())
    expected = {
        "schema_version": 1,
        "dataset_id": "bw6n-ddqk",
        "field": "tsi_freight",
        "units": "index_points_2000_average_100",
        "seasonally_adjusted": True,
        "vintage_kind": "latest_vintage_snapshot",
    }
    if not isinstance(manifest, dict) or any(
        manifest.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("manifest does not describe the supported BTS Freight TSI")
    if manifest.get("sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("snapshot SHA256 does not match its manifest")
    source = pd.read_csv(BytesIO(raw))
    if list(source.columns) != ["obs_date", "tsi_freight"]:
        raise ValueError("snapshot must contain only obs_date and tsi_freight")
    # Parse the documented export format explicitly, without locale guessing.
    source["obs_date"] = pd.to_datetime(
        source["obs_date"], format="%Y-%m-%dT%H:%M:%S.%f", errors="raise"
    )
    history = _observations(
        source.rename(columns={"obs_date": "date", "tsi_freight": "volume"})
    )
    observed = {
        "rows": len(history),
        "first_month": history["date"].iloc[0].strftime("%Y-%m"),
        "last_month": history["date"].iloc[-1].strftime("%Y-%m"),
    }
    if any(manifest.get(key) != value for key, value in observed.items()):
        raise ValueError("snapshot coverage does not match its manifest")
    return history, manifest


def validate_protocol(protocol: dict) -> list[pd.Period]:
    """Require adjacent chronological blocks and the declared selection rule."""
    if not isinstance(protocol, dict):
        raise ValueError("protocol must be a JSON object")
    if protocol.get("warmup_months") != 12 or protocol.get("metrics") != [
        "mae",
        "rmse",
        "mape",
    ]:
        raise ValueError("protocol requires 12 warmup months and MAE/RMSE/MAPE")
    if protocol.get("seasonal_subgroups") is not None:
        raise ValueError("this index evaluation does not define seasonal subgroups")
    if protocol.get("schema_version") != 1 or protocol.get("model") != (
        "StandardScaler + Ridge(alpha=1.0), log target / exp inverse"
    ):
        raise ValueError("unsupported evaluation protocol or estimator")
    if protocol.get("policies") != list(POLICIES) or protocol.get("selection") != {
        "metric": "development_mae",
        "tie_break": "fixed",
        "final_candidates": "selected_policy_only",
    }:
        raise ValueError("unsupported policy selection rule")
    if protocol.get("baselines") != list(METHODS[1:]):
        raise ValueError("both matched baselines are required")
    keys = (
        "data_start",
        "training_end",
        "development_start",
        "development_end",
        "test_start",
        "test_end",
    )
    values = [protocol.get(key) for key in keys]
    if not all(isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}", v) for v in values):
        raise ValueError("protocol dates must be YYYY-MM")
    dates = [pd.Period(value, freq="M") for value in values]
    first, train_end, dev_start, dev_end, test_start, test_end = dates
    if (
        train_end.ordinal - first.ordinal + 1 < 24
        or dev_start != train_end + 1
        or dev_end < dev_start
        or test_start != dev_end + 1
        or test_end < test_start
    ):
        raise ValueError(
            "protocol needs adjacent ordered training/development/test blocks"
        )
    return dates


def _metrics(rows: list[dict]) -> dict:
    actual = np.array([row["actual"] for row in rows])
    result = {}
    for name in METHODS:
        error = np.array([row[name] for row in rows]) - actual
        result[name] = {
            "mae": float(np.abs(error).mean()),
            "rmse": float(np.sqrt(np.square(error).mean())),
            "mape": float((np.abs(error) / actual).mean()),
        }
    return result


def _block(
    history: pd.DataFrame, start: pd.Period, end: pd.Period, policy: str
) -> dict:
    rows = []
    model = None
    cutoff = None
    count = None
    for target_month in pd.period_range(start, end, freq="M"):
        target_date = target_month.to_timestamp()
        observed = history.loc[history["date"] < target_date]
        prefix = pd.concat(
            [observed, pd.DataFrame({"date": [target_date], "volume": [np.nan]})],
            ignore_index=True,
        )
        features, columns = build_features(prefix)
        training, target = features.iloc[:-1], features.iloc[[-1]]
        if model is None or policy == "monthly_refit":
            model = make_model()
            model.fit(training[columns], training["volume"])
            cutoff = training["date"].iloc[-1].strftime("%Y-%m")
            count = len(training)
        prediction = float(model.predict(target[columns])[0])
        if not np.isfinite(prediction) or prediction <= 0:
            raise ValueError(f"invalid model prediction for {target_month}")
        actual = float(history.loc[history["date"] == target_date, "volume"].iloc[0])
        row = {
            "month": str(target_month),
            "trained_through": cutoff,
            "training_months": count,
            "observed_through": str(target_month - 1),
            "actual": actual,
            "model": prediction,
            "last_observation": float(observed["volume"].iloc[-1]),
            "seasonal_naive": float(target["lag_12"].iloc[0]),
        }
        row["absolute_errors"] = {name: abs(row[name] - actual) for name in METHODS}
        rows.append(row)
    return {
        "policy": policy,
        "months": len(rows),
        "metrics": _metrics(rows),
        "by_year": {
            year: _metrics([row for row in rows if row["month"].startswith(year)])
            for year in sorted({row["month"][:4] for row in rows})
        },
        "predictions": rows,
    }


def evaluate(history: pd.DataFrame, protocol: dict, *, development_only=False) -> dict:
    """Select on development errors before evaluating the selected final policy."""
    first, _, dev_start, dev_end, test_start, test_end = validate_protocol(protocol)
    history = _observations(history)
    # Later observations never enter fitting, selection, or reported test metrics.
    final_month = dev_end if development_only else test_end
    history = history.loc[
        history["date"].between(first.to_timestamp(), final_month.to_timestamp())
    ].reset_index(drop=True)
    expected = pd.date_range(
        first.to_timestamp(), final_month.to_timestamp(), freq="MS"
    )
    if not history["date"].equals(pd.Series(expected)):
        raise ValueError("observations do not cover every protocol month")
    development = {
        policy: _block(history, dev_start, dev_end, policy) for policy in POLICIES
    }
    # Ordered min gives the declared fixed-model tie break, with no test feedback.
    selected = min(POLICIES, key=lambda p: development[p]["metrics"]["model"]["mae"])
    return {
        "schema_version": 1,
        "evaluation_kind": "retrospective_latest_vintage_one_observation_step",
        "units": "index_points_2000_average_100",
        "protocol": {**protocol, "final_policy_fit": POLICY_DESCRIPTION},
        "development": development,
        "selected_policy": selected,
        "test": None
        if development_only
        else _block(history, test_start, test_end, selected),
        "limitations": [
            "Historical revisions and seasonal adjustment may use later information.",
            "Observation month is not publication time. Release lag is not simulated.",
            "National index points are not shipment counts or household goods demand.",
            "No serving model is replaced. Baseline losses remain valid results.",
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--development-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() in {
        p.resolve() for p in (args.data, args.manifest, args.protocol)
    }:
        parser.error("output must not overwrite an input")
    try:
        history, manifest = load_snapshot(args.data, args.manifest)
        protocol_raw = args.protocol.read_text(encoding="utf-8").encode("utf-8")
        report = evaluate(
            history, json.loads(protocol_raw), development_only=args.development_only
        )
        report["provenance"] = {
            "data": manifest,
            "protocol_sha256": hashlib.sha256(protocol_raw).hexdigest(),
            "protocol_hash_line_endings": "LF",
            "implementation_sha256": {
                name: hashlib.sha256(
                    (ROOT / name).read_text(encoding="utf-8").encode()
                ).hexdigest()
                for name in (
                    "src/real_backtest.py",
                    "src/backtest.py",
                    "src/features.py",
                    "src/train.py",
                )
            },
            "python": platform.python_version(),
            "packages": {
                name: version(name) for name in ("numpy", "pandas", "scikit-learn")
            },
        }
        content = json.dumps(report, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(f"Saved {report['selected_policy']} evaluation to {args.output}")


if __name__ == "__main__":
    main()
