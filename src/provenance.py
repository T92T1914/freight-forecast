"""Bind trusted model artifacts to their monthly observations and evaluation."""

import hashlib
import json
import platform
from importlib.metadata import version
from math import isfinite
from numbers import Real
from pathlib import Path

import pandas as pd

from src.generate_data import generate

SCHEMA_VERSION = 1
FEATURE_SCHEMA = "monthly-trend-lag12-roll3-month-dummies-v1"
EVALUATION_METHOD = "fixed fit, chronological held-out months, observed lag inputs"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def history_rows(history: pd.DataFrame) -> list[dict]:
    """Canonicalize representation, not the meaning of an observation."""
    ordered = history.sort_values("date")
    return [
        {"date": date.date().isoformat(), "volume": float(volume)}
        for date, volume in ordered[["date", "volume"]].itertuples(
            index=False, name=None
        )
    ]


def make_provenance(history, train_set, test_set, predictions, columns) -> dict:
    rows = history_rows(history)
    root = Path(__file__).parent
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_schema": FEATURE_SCHEMA,
        "feature_columns": columns,
        "history": rows,
        "history_id": digest(rows),
        "data_type": (
            "seeded synthetic shipments"
            if rows == history_rows(generate())
            else "provided observations; origin not classified"
        ),
        "training_cutoff": train_set["date"].iloc[-1].date().isoformat(),
        "training_rows": len(train_set),
        "evaluation": {
            "method": EVALUATION_METHOD,
            "acceptance_rule": "model_mae < naive_mae",
            "rows": [
                {
                    "date": row.date.date().isoformat(),
                    "actual": float(row.volume),
                    "prediction": float(prediction),
                    "naive": float(row.lag_12),
                }
                for row, prediction in zip(
                    test_set.itertuples(), predictions, strict=True
                )
            ],
        },
        "estimator": {"family": "log-target StandardScaler Ridge", "alpha": 1.0},
        "implementation": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("train.py", "features.py", "provenance.py")
        },
        "dependencies": {
            "python": platform.python_version(),
            **{
                name: version(name)
                for name in ("numpy", "pandas", "scikit-learn", "joblib")
            },
        },
    }


def validate_provenance(artifact: dict, history: pd.DataFrame) -> str:
    """Reject mixed evidence before startup. Digests are identities, not signatures."""
    provenance = artifact.get("provenance")
    try:
        if not isinstance(provenance, dict):
            raise ValueError("missing provenance; retrain with python -m src.train")
        if (
            type(provenance["schema_version"]) is not int
            or provenance["schema_version"] != SCHEMA_VERSION
            or provenance["feature_schema"] != FEATURE_SCHEMA
            or provenance["feature_columns"] != artifact["feature_columns"]
        ):
            raise ValueError("incompatible provenance schema")
        rows = provenance["history"]
        if not isinstance(rows, list) or len(rows) < 13:
            raise ValueError("missing provenance observations")
        original = pd.DataFrame(rows)
        original["date"] = pd.to_datetime(original["date"])
        dates = original["date"]
        if not dates.equals(
            pd.Series(pd.date_range(dates.iloc[0], periods=len(rows), freq="MS"))
        ):
            raise ValueError("invalid provenance calendar")
        if rows != history_rows(original) or digest(rows) != provenance["history_id"]:
            raise ValueError("provenance history digest mismatch")
        if any(not isfinite(row["volume"]) or row["volume"] < 0 for row in rows):
            raise ValueError("invalid provenance volume")
        if rows[0]["date"] != artifact["trend_origin"]:
            raise ValueError("provenance trend_origin mismatch")
        if provenance["training_cutoff"] != artifact["trained_through"]:
            raise ValueError("provenance training cutoff mismatch")
        evaluation = provenance["evaluation"]
        evaluated = evaluation["rows"]
        if evaluation["method"] != EVALUATION_METHOD:
            raise ValueError("unsupported provenance evaluation method")
        if provenance["data_type"] not in (
            "seeded synthetic shipments",
            "provided observations; origin not classified",
        ):
            raise ValueError("missing provenance data classification")
        if provenance[
            "data_type"
        ] == "seeded synthetic shipments" and rows != history_rows(generate()):
            raise ValueError(
                "provenance synthetic data differs from recorded generator"
            )
        cutoff = provenance["training_cutoff"]
        expected = [row for row in rows if row["date"] > cutoff]
        if len(evaluated) != len(expected) or not evaluated:
            raise ValueError("provenance evaluation coverage mismatch")
        known = {row["date"]: row["volume"] for row in rows}
        for row, source in zip(evaluated, expected, strict=True):
            lag_date = (
                (pd.Timestamp(row["date"]) - pd.DateOffset(years=1)).date().isoformat()
            )
            if (
                row["date"] != source["date"]
                or row["actual"] != source["volume"]
                or row["naive"] != known.get(lag_date)
                or not isfinite(row["prediction"])
                or row["prediction"] < 0
            ):
                raise ValueError("provenance evaluation observation mismatch")
        for label, field in (("model", "prediction"), ("naive", "naive")):
            errors = [abs(row[field] - row["actual"]) for row in evaluated]
            mae = sum(errors) / len(errors)
            mape = sum(
                error / max(abs(row["actual"]), 2.220446049250313e-16)
                for row, error in zip(evaluated, errors, strict=True)
            ) / len(errors)
            for key, measured in ((label + "_mae", mae), (label + "_mape", mape)):
                reported = artifact["metrics"][key]
                if (
                    isinstance(reported, bool)
                    or not isinstance(reported, Real)
                    or not isfinite(reported)
                    or reported < 0
                    or abs(reported - measured) > 1e-9 * max(1, measured)
                ):
                    raise ValueError("provenance evaluation metric mismatch")
        if (
            evaluation["acceptance_rule"] != "model_mae < naive_mae"
            or artifact["metrics"]["model_mae"] >= artifact["metrics"]["naive_mae"]
        ):
            raise ValueError("provenance evaluation does not satisfy acceptance rule")
        if (
            type(provenance["training_rows"]) is not int
            or provenance["training_rows"] < 1
            or provenance["training_rows"] != len(rows) - len(evaluated) - 12
        ):
            raise ValueError("provenance training coverage mismatch")
        for name in ("train.py", "features.py", "provenance.py"):
            value = provenance["implementation"][name]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("invalid provenance implementation identity")
        for name in ("python", "numpy", "pandas", "scikit-learn", "joblib"):
            if (
                not isinstance(provenance["dependencies"][name], str)
                or not provenance["dependencies"][name]
            ):
                raise ValueError("missing provenance dependency version")
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise ValueError(f"model artifact provenance invalid: {exc}") from exc

    current = history_rows(history)
    overlap = [row for row in current if row["date"] in known]
    if not overlap or any(row["volume"] != known[row["date"]] for row in overlap):
        raise ValueError(
            "history conflicts with model provenance; retrain for revised observations"
        )
    return digest(current)
