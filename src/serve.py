"""FastAPI serving layer for the shipment volume forecaster.

The model artifact and the historical data are loaded ONCE at startup and
shared across requests -- loading per-request would add disk I/O and
deserialization to every call for no benefit. If the artifact is missing the
app refuses to start with a clear message, rather than limping along and
failing on the first request.

Forecasts are one-step-ahead only: any month with a full 12-month feature
history, up through one month past the last observed month. That is the
horizon the model was evaluated at in training; serving further-out months
would be shipping a claim no evaluation supports.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.features import build_features
from src.generate_data import DATA_PATH
from src.train import MODEL_PATH


class PredictRequest(BaseModel):
    """A calendar month, e.g. {"month": "2025-01"}. The pattern rejects
    malformed input before any code runs (FastAPI returns 422)."""
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2025-01"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not MODEL_PATH.exists():
        raise RuntimeError(
            f"model artifact not found at {MODEL_PATH}; "
            "run `python -m src.train` first"
        )
    if not DATA_PATH.exists():
        raise RuntimeError(
            f"dataset not found at {DATA_PATH}; "
            "run `python -m src.generate_data` first"
        )
    app.state.artifact = joblib.load(MODEL_PATH)
    app.state.history = pd.read_csv(DATA_PATH, parse_dates=["date"])
    yield


app = FastAPI(title="freight-forecast", lifespan=lifespan)


def _forecastable_range(history: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Months the model can honestly predict: first month with a full
    12-month lag history through one month past the end of the data."""
    first = history["date"].iloc[0] + pd.DateOffset(months=12)
    last = history["date"].iloc[-1] + pd.DateOffset(months=1)
    return first, last


@app.get("/health")
def health():
    artifact = app.state.artifact
    return {
        "status": "ok",
        "model_loaded": True,
        "trained_through": artifact["trained_through"],
        "test_mae": round(artifact["metrics"]["model_mae"], 1),
        "test_mape": round(artifact["metrics"]["model_mape"], 4),
    }


@app.post("/predict")
def predict(req: PredictRequest):
    ts = pd.Timestamp(f"{req.month}-01")

    history: pd.DataFrame = app.state.history
    artifact = app.state.artifact
    first, last = _forecastable_range(history)
    if not (first <= ts <= last):
        raise HTTPException(
            status_code=400,
            detail=(
                f"month {req.month} outside forecastable range "
                f"{first:%Y-%m}..{last:%Y-%m} (one step ahead of the data)"
            ),
        )

    df = history
    if ts == last:
        # next unobserved month: append a placeholder row so lag/rolling
        # features (which only look backwards) can be built for it
        df = pd.concat(
            [history, pd.DataFrame({"date": [ts], "volume": [float("nan")]})],
            ignore_index=True,
        )
    feats, _ = build_features(df)
    row = feats[feats["date"] == ts]
    X = row[artifact["feature_columns"]]
    predicted = float(artifact["model"].predict(X)[0])

    return {
        "month": f"{ts:%Y-%m}",
        "predicted_volume": round(predicted),
        "naive_same_month_last_year": int(row["lag_12"].iloc[0]),
        "trained_through": artifact["trained_through"],
    }
