"""Train the baseline forecaster and evaluate it against a seasonal naive.

Reproduces end to end: regenerates the dataset if missing, builds features,
trains on all but the last 24 months, and evaluates on those held-out months.
The naive predictor is "same month last year" -- the bar a model must clear
to be worth serving at all. Fails with exit code 1 if the model loses.
"""

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import TransformedTargetRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src import generate_data
from src.features import build_features

TEST_MONTHS = 24
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "model.joblib"


def load_data() -> pd.DataFrame:
    if not generate_data.DATA_PATH.exists():
        generate_data.main()
    return pd.read_csv(generate_data.DATA_PATH, parse_dates=["date"])


def train(df: pd.DataFrame) -> dict:
    feats, feature_cols = build_features(df)
    train_set = feats.iloc[:-TEST_MONTHS]
    test_set = feats.iloc[-TEST_MONTHS:]

    # Seasonality in shipment volume is multiplicative (the peak scales with
    # the overall level), so the model fits log(volume) and predictions are
    # mapped back to move counts inside the regressor itself.
    model = TransformedTargetRegressor(
        regressor=make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        func=np.log,
        inverse_func=np.exp,
    )
    model.fit(train_set[feature_cols], train_set["volume"])

    preds = model.predict(test_set[feature_cols])
    naive = test_set["lag_12"]  # same month last year
    actual = test_set["volume"]

    metrics = {
        "model_mae": mean_absolute_error(actual, preds),
        "model_mape": mean_absolute_percentage_error(actual, preds),
        "naive_mae": mean_absolute_error(actual, naive),
        "naive_mape": mean_absolute_percentage_error(actual, naive),
    }
    return {
        "model": model,
        "feature_columns": feature_cols,
        "metrics": metrics,
        "trained_through": str(train_set["date"].iloc[-1].date()),
    }


def main() -> None:
    artifact = train(load_data())
    m = artifact["metrics"]

    print(f"Held-out test: last {TEST_MONTHS} months")
    print(f"{'':16}{'MAE':>10}{'MAPE':>10}")
    print(f"{'Seasonal naive':16}{m['naive_mae']:>10.0f}{m['naive_mape']:>9.1%}")
    print(f"{'Ridge model':16}{m['model_mae']:>10.0f}{m['model_mape']:>9.1%}")

    if m["model_mae"] >= m["naive_mae"]:
        print("FAIL: model does not beat the seasonal naive; not saving artifact")
        sys.exit(1)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_PATH)
    print(f"Saved artifact to {MODEL_PATH}")


if __name__ == "__main__":
    main()
