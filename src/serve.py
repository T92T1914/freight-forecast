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

import time
from contextlib import asynccontextmanager

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel, Field

from src.features import build_features
from src.generate_data import DATA_PATH
from src.train import MODEL_PATH, Artifact


class PredictRequest(BaseModel):
    """A calendar month, e.g. {"month": "2025-01"}. The pattern rejects
    malformed input before any code runs (FastAPI returns 422)."""

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2025-01"])


# Response models exist so /docs and /openapi.json state the contract a
# consumer can rely on, instead of the fields merely happening to be there.
class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    trained_through: str
    test_mae: float
    test_mape: float


class PredictResponse(BaseModel):
    month: str
    predicted_volume: int
    naive_same_month_last_year: int
    trained_through: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not MODEL_PATH.exists():
        raise RuntimeError(
            f"model artifact not found at {MODEL_PATH}; run `python -m src.train` first"
        )
    if not DATA_PATH.exists():
        raise RuntimeError(
            f"dataset not found at {DATA_PATH}; run `python -m src.generate_data` first"
        )
    app.state.artifact = joblib.load(MODEL_PATH)
    app.state.history = pd.read_csv(DATA_PATH, parse_dates=["date"])
    first, last = _forecastable_range(app.state.history)
    # History is immutable for this app lifetime. Include the one unobserved
    # month now: all lag/rolling inputs look backwards, so the placeholder
    # cannot change any earlier feature row. A restart rebuilds this cache
    # from the newly loaded data, including edits that keep the same dates.
    extended = pd.concat(
        [
            app.state.history,
            pd.DataFrame({"date": [last], "volume": [float("nan")]}),
        ],
        ignore_index=True,
    )
    feats, _ = build_features(extended)
    app.state.features = feats.set_index("date")
    app.state.forecastable_range = first, last
    yield


app = FastAPI(title="freight-forecast", lifespan=lifespan)

REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP requests served",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["path"],
)
# buckets span the data's real range (~2.8k winter .. ~13k summer peak);
# drift outside them is itself a signal worth alerting on
PREDICTED_VOLUME = Histogram(
    "predicted_volume_moves",
    "Distribution of predicted monthly volumes",
    buckets=(2000, 3000, 4000, 5000, 6000, 8000, 10000, 12000, 14000),
)

_KNOWN_PATHS = {"/health", "/predict"}


@app.middleware("http")
async def track_requests(request: Request, call_next):
    if request.url.path.startswith("/metrics"):
        return await call_next(request)
    # unknown paths share one label so scanners can't explode cardinality
    path = request.url.path if request.url.path in _KNOWN_PATHS else "other"
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # unhandled errors never produce a response object here, so without
        # this branch 500s would vanish from the very metrics meant to
        # surface them — an error-rate panel reading zero during an outage
        REQUEST_COUNT.labels(request.method, path, "500").inc()
        REQUEST_LATENCY.labels(path).observe(time.perf_counter() - start)
        raise
    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(path).observe(time.perf_counter() - start)
    return response


app.mount("/metrics", make_asgi_app())


def _forecastable_range(history: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Months the model can honestly predict: first month with a full
    12-month lag history through one month past the end of the data."""
    first = history["date"].iloc[0] + pd.DateOffset(months=12)
    last = history["date"].iloc[-1] + pd.DateOffset(months=1)
    return first, last


@app.get("/health", response_model=HealthResponse)
def health():
    """Liveness, plus provenance: a running instance reports the metrics its
    artifact was accepted on, so it can be asked what it is rather than what
    it was supposed to be."""
    artifact: Artifact = app.state.artifact
    return {
        "status": "ok",
        "model_loaded": True,
        "trained_through": artifact["trained_through"],
        "test_mae": round(artifact["metrics"]["model_mae"], 1),
        "test_mape": round(artifact["metrics"]["model_mape"], 4),
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    """Forecast one month, and return the seasonal-naive number next to it.

    A forecast only means something beside the thing it claims to beat, so
    the response carries "same month last year" as well. Months outside the
    forecastable range are a 400 with the range in the message, not a
    confident number extrapolated from a lag that does not exist.
    """
    ts = pd.Timestamp(f"{req.month}-01")

    artifact: Artifact = app.state.artifact
    first, last = app.state.forecastable_range
    if not (first <= ts <= last):
        raise HTTPException(
            status_code=400,
            detail=(
                f"month {req.month} outside forecastable range "
                f"{first:%Y-%m}..{last:%Y-%m} (one step ahead of the data)"
            ),
        )

    row = app.state.features.loc[[ts]]
    X = row[artifact["feature_columns"]]
    predicted = float(artifact["model"].predict(X)[0])
    PREDICTED_VOLUME.observe(predicted)

    return {
        "month": f"{ts:%Y-%m}",
        "predicted_volume": round(predicted),
        "naive_same_month_last_year": int(row["lag_12"].iloc[0]),
        "trained_through": artifact["trained_through"],
    }
