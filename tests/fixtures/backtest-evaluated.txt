"""Evaluate monthly refitting without writing a serving model artifact.

Each origin fits on observed history and predicts one unobserved month. The
next origin may learn from that month's actual volume. This is an expanding
window evaluation, distinct from train.py's single fitted model.
"""

import argparse
import hashlib
import json
import platform
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from src.features import build_features
from src.generate_data import DATA_PATH
from src.train import make_model

WARMUP_MONTHS = 12
DEFAULT_TRAIN_MONTHS = 60


def _observations(df: pd.DataFrame) -> pd.DataFrame:
    """Reject missing observations rather than quietly changing the test set."""
    if not {"date", "volume"}.issubset(df.columns):
        raise ValueError("data must contain date and volume columns")
    if df.empty:
        raise ValueError("data must contain observed months")
    history = df[["date", "volume"]].copy()
    history["date"] = pd.to_datetime(history["date"], errors="raise")
    history["volume"] = pd.to_numeric(history["volume"], errors="coerce")
    if not np.isfinite(history["volume"]).all() or (history["volume"] <= 0).any():
        raise ValueError("log-volume evaluation requires finite, positive observations")
    history = history.sort_values("date").reset_index(drop=True)
    # Validate the entire calendar before starting. Feature construction checks
    # month starts, contiguity and duplicates; invalid targets must not disappear.
    build_features(history)
    return history


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return {"months": 0}
    model = np.array([row["model_absolute_error"] for row in rows])
    naive = np.array([row["naive_absolute_error"] for row in rows])
    actual = np.array([row["actual"] for row in rows])
    model_mae = float(model.mean())
    naive_mae = float(naive.mean())
    return {
        "months": len(rows),
        "model_mae": model_mae,
        "naive_mae": naive_mae,
        "model_mape": float((model / actual).mean()),
        "naive_mape": float((naive / actual).mean()),
        "mae_delta_model_minus_naive": model_mae - naive_mae,
        "mae_improvement_fraction": (
            1 - model_mae / naive_mae if naive_mae > 0 else None
        ),
        "model_better_months": int((model < naive).sum()),
        "model_worse_months": int((model > naive).sum()),
        "tied_months": int((model == naive).sum()),
    }


def backtest(
    df: pd.DataFrame, *, initial_train_months: int = DEFAULT_TRAIN_MONTHS
) -> dict:
    """Return predictions and matched error summaries for every remaining month.

    initial_train_months counts usable training rows AFTER the 12 month lag
    warmup. At least one full seasonal cycle is required for the first fit.
    """
    if (
        isinstance(initial_train_months, bool)
        or not isinstance(initial_train_months, int)
        or initial_train_months < 12
    ):
        raise ValueError("initial_train_months must be an integer of at least 12")
    history = _observations(df)
    first_target = WARMUP_MONTHS + initial_train_months
    if len(history) <= first_target:
        raise ValueError(
            f"need at least {first_target + 1} observed months: "
            f"{WARMUP_MONTHS} warmup + {initial_train_months} training + 1 evaluation"
        )

    rows = []
    for index in range(first_target, len(history)):
        target_date = history.iloc[index]["date"]
        # The fitting/feature path receives no current or future target volumes.
        # A placeholder exposes the next calendar month with past-only lags.
        prefix = pd.concat(
            [
                history.iloc[:index],
                pd.DataFrame({"date": [target_date], "volume": [float("nan")]}),
            ],
            ignore_index=True,
        )
        features, columns = build_features(prefix)
        training = features.iloc[:-1]
        target = features.iloc[[-1]]
        model = make_model()
        model.fit(training[columns], training["volume"])
        prediction = float(model.predict(target[columns])[0])
        if not np.isfinite(prediction) or prediction <= 0:
            raise ValueError(f"invalid model prediction for {target_date:%Y-%m}")
        actual = float(history.iloc[index]["volume"])
        naive = float(target["lag_12"].iloc[0])
        rows.append(
            {
                "month": f"{target_date:%Y-%m}",
                "trained_through": f"{training['date'].iloc[-1]:%Y-%m}",
                "training_months": len(training),
                "season": "peak" if target_date.month in (5, 6, 7, 8) else "off_peak",
                "actual": actual,
                "predicted_volume": prediction,
                "naive_same_month_last_year": naive,
                "model_absolute_error": abs(actual - prediction),
                "naive_absolute_error": abs(actual - naive),
            }
        )

    return {
        "schema_version": 1,
        "evaluation": {
            "strategy": "expanding_window_monthly_refit",
            "horizon_months": 1,
            "warmup_months": WARMUP_MONTHS,
            "initial_training_months": initial_train_months,
            "first_target": rows[0]["month"],
            "last_target": rows[-1]["month"],
            "peak_months": [5, 6, 7, 8],
            "model": "StandardScaler + Ridge(alpha=1.0), log target / exp inverse",
            "baseline": "observed volume from the same month last year",
        },
        "overall": _summary(rows),
        "by_season": {
            season: _summary([row for row in rows if row["season"] == season])
            for season in ("peak", "off_peak")
        },
        "by_year": {
            year: _summary([row for row in rows if row["month"].startswith(year)])
            for year in sorted({row["month"][:4] for row in rows})
        },
        "predictions": rows,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument(
        "--initial-train-months", type=int, default=DEFAULT_TRAIN_MONTHS
    )
    parser.add_argument(
        "--output", type=Path, help="JSON destination; default is stdout"
    )
    args = parser.parse_args(argv)
    if args.output and args.output.resolve() == args.data.resolve():
        parser.error("output must not overwrite the input dataset")
    try:
        raw = args.data.read_bytes()
        # Read the exact bytes we hash, even if another process replaces the CSV.
        report = backtest(
            pd.read_csv(BytesIO(raw)), initial_train_months=args.initial_train_months
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))

    root = Path(__file__).resolve().parents[1]
    report["provenance"] = {
        "data_file": args.data.name,
        "data_sha256": hashlib.sha256(raw).hexdigest(),
        "implementation_sha256": {
            name: hashlib.sha256(
                (root / name).read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
            for name in ("src/backtest.py", "src/features.py", "src/train.py")
        },
        "python": platform.python_version(),
        "packages": {
            name: version(name) for name in ("numpy", "pandas", "scikit-learn")
        },
    }
    content = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(
            f"Saved {report['overall']['months']} monthly predictions to {args.output}"
        )
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
