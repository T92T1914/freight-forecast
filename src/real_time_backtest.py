"""Replay the BTS study on the values each origin could have seen.

The protocol is docs/real-time-protocol.json. The estimator, its features and the
build gate are the existing ones. Nothing is tuned.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.features import build_features
from src.train import make_model
from src.vintages import (
    MANIFEST,
    TABLE,
    first_vintage,
    load_vintages,
    lookahead_rows,
    origin_history,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/real-time-protocol.json"
METHODS = ("model", "last_observation", "seasonal_naive")


def _metrics(rows: list[dict], outcome: str) -> dict:
    actual = np.array([row[outcome] for row in rows])
    result = {}
    for name in METHODS:
        error = np.array([row[name] for row in rows]) - actual
        result[name] = {
            "mae": float(np.abs(error).mean()),
            "rmse": float(np.sqrt(np.square(error).mean())),
            "mape": float((np.abs(error) / actual).mean()),
        }
    return result


def forecast_origin(table: pd.DataFrame, target: pd.Timestamp) -> dict:
    """Forecast one target from the vintage that first held the month before it."""
    snapshot = origin_history(table, target)
    origin = first_vintage(table, target - pd.DateOffset(months=1))
    if not lookahead_rows(snapshot, target, origin).empty:
        raise ValueError(f"origin for {target:%Y-%m} reads a later value")
    history = snapshot.rename(columns={"obs_date": "date", "value": "volume"})[
        ["date", "volume"]
    ]
    prefix = pd.concat(
        [history, pd.DataFrame({"date": [target], "volume": [np.nan]})],
        ignore_index=True,
    )
    features, columns = build_features(prefix)
    training, row = features.iloc[:-1], features.iloc[[-1]]
    model = make_model()
    model.fit(training[columns], training["volume"])
    prediction = float(model.predict(row[columns])[0])
    if not np.isfinite(prediction) or prediction <= 0:
        raise ValueError(f"invalid model prediction for {target:%Y-%m}")
    return {
        "month": f"{target:%Y-%m}",
        "origin_vintage": f"{origin:%Y-%m-%d}",
        "observed_through": f"{target - pd.DateOffset(months=1):%Y-%m}",
        "training_months": len(training),
        "model": prediction,
        "last_observation": float(history["volume"].iloc[-1]),
        "seasonal_naive": float(row["lag_12"].iloc[0]),
    }


def evaluate(table: pd.DataFrame, protocol: dict) -> dict:
    targets = pd.date_range(
        pd.Timestamp(protocol["origins"]["first_target"] + "-01"),
        pd.Timestamp(protocol["origins"]["last_target"] + "-01"),
        freq="MS",
    )
    latest_vintage = table["vintage_date"].max()
    latest = table.loc[table["vintage_date"] == latest_vintage].set_index("obs_date")[
        "value"
    ]
    rows = []
    for target in targets:
        row = forecast_origin(table, target)
        first = first_vintage(table, target)
        row["first_release_vintage"] = f"{first:%Y-%m-%d}"
        row["first_release"] = float(
            table.loc[
                (table["vintage_date"] == first) & (table["obs_date"] == target),
                "value",
            ].iloc[0]
        )
        row["latest_available"] = float(latest.loc[target])
        row["absolute_errors"] = {
            outcome: {name: abs(row[name] - row[outcome]) for name in METHODS}
            for outcome in ("first_release", "latest_available")
        }
        rows.append(row)
    by_outcome = {}
    for outcome in ("first_release", "latest_available"):
        by_outcome[outcome] = {
            "overall": _metrics(rows, outcome),
            "by_year": {
                year: _metrics(
                    [r for r in rows if r["month"].startswith(year)], outcome
                )
                for year in sorted({r["month"][:4] for r in rows})
            },
        }
    return {
        "schema_version": 1,
        "evaluation_kind": "real_time_vintage_one_observation_step",
        "protocol_id": protocol["id"],
        "units": "index_points_2000_average_100",
        "months": len(rows),
        "latest_vintage": f"{latest_vintage:%Y-%m-%d}",
        "results": by_outcome,
        "predictions": rows,
        "limitations": protocol["limits"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, default=TABLE)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    table = load_vintages(args.table, args.manifest)
    protocol_raw = args.protocol.read_bytes()
    report = evaluate(table, json.loads(protocol_raw))
    report["provenance"] = {
        "vintage_manifest": json.loads(args.manifest.read_bytes()),
        "protocol_sha256": hashlib.sha256(
            protocol_raw.replace(b"\r\n", b"\n")
        ).hexdigest(),
        "protocol_hash_line_endings": "LF",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"Saved real time evaluation to {args.output}")


if __name__ == "__main__":
    main()
