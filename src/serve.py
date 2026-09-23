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

import hashlib
import io
import time
from contextlib import asynccontextmanager
from datetime import date
from math import isfinite
from numbers import Real

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel, Field, field_validator

from src.features import build_features
from src.generate_data import DATA_PATH
from src.provenance import validate_provenance
from src.train import MODEL_PATH, Artifact


class PredictRequest(BaseModel):
    """A calendar month, e.g. {"month": "2025-01"}. The pattern rejects
    malformed input before any code runs (FastAPI returns 422)."""

    month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$", examples=["2025-01"])

    @field_validator("month")
    @classmethod
    def calendar_month(cls, value: str) -> str:
        # Shape alone admits year 0000, which would fail inside the handler
        # as a server error. Calendar validity belongs to request validation.
        date.fromisoformat(f"{value}-01")
        return value


# Response models exist so /docs and /openapi.json state the contract a
# consumer can rely on, instead of the fields merely happening to be there.
class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    trained_through: str
    test_mae: float
    test_mape: float
    model_id: str
    history_id: str


class PredictResponse(BaseModel):
    month: str
    predicted_volume: int
    naive_same_month_last_year: int
    trained_through: str
    model_id: str
    history_id: str


class ForecastWindowResponse(BaseModel):
    """Discover supported requests without probing invalid months."""

    first_supported_month: str
    last_supported_month: str
    observed_through: str
    next_unobserved_month: str
    trained_through: str
    horizon_months: int
    refresh_policy: str
    model_id: str
    history_id: str


def _trend_origin(artifact: object) -> pd.Timestamp:
    """Never guess the origin of an already fitted trend from serving history."""
    if not isinstance(artifact, dict):
        raise ValueError("model artifact must be a mapping")
    raw = artifact.get("trend_origin")
    try:
        origin = date.fromisoformat(raw)
        if origin.day != 1 or origin.isoformat() != raw:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "model artifact trend_origin must be an ISO month start; "
            "retrain with `python -m src.train`"
        ) from exc
    return pd.Timestamp(origin)


def _validate_artifact(artifact: dict, features: pd.DataFrame, columns: list[str]):
    """Check the trusted local training artifact before advertising readiness."""
    if artifact.get("feature_columns") != columns:
        raise ValueError("model artifact feature columns do not match serving features")
    trained = artifact.get("trained_through")
    try:
        cutoff = date.fromisoformat(trained)
        if cutoff.day != 1 or cutoff.isoformat() != trained:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "model artifact trained_through must be an ISO month start"
        ) from exc
    if date.fromisoformat(artifact["trend_origin"]) > cutoff:
        raise ValueError("model artifact trend_origin is after its training cutoff")
    metrics = artifact.get("metrics")
    for key in ("model_mae", "model_mape"):
        value = metrics.get(key) if isinstance(metrics, dict) else None
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not isfinite(value)
            or value < 0
        ):
            raise ValueError(f"model artifact {key} must be finite and nonnegative")
    model = artifact.get("model")
    if not callable(getattr(model, "predict", None)):
        raise ValueError("model artifact must contain a predictive model")
    # Exercise the fitted estimator as well as the manifest. An obsolete
    # estimator can disagree with an otherwise valid feature-column list.
    try:
        predictions = np.asarray(model.predict(features.loc[:, columns].tail(1)))
        if (
            predictions.shape != (1,)
            or predictions.dtype.kind not in "iuf"
            or not np.isfinite(predictions).all()
            or (predictions < 0).any()
        ):
            raise ValueError("expected one finite, nonnegative forecast")
    except Exception as exc:
        raise ValueError("model artifact failed startup prediction validation") from exc


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
    # Hash the exact bytes deserialized, even if publication replaces the file
    # during startup. Artifacts must still come from a trusted local source.
    model_bytes = MODEL_PATH.read_bytes()
    app.state.artifact = joblib.load(io.BytesIO(model_bytes))
    app.state.model_id = hashlib.sha256(model_bytes).hexdigest()
    origin = _trend_origin(app.state.artifact)
    app.state.history = (
        pd.read_csv(DATA_PATH, parse_dates=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    if len(app.state.history) < 12:
        raise ValueError("history must contain at least 12 observed months")
    if origin > app.state.history["date"].iloc[0]:
        raise ValueError("model artifact trend_origin is after the loaded history")
    volumes = pd.to_numeric(app.state.history["volume"], errors="coerce")
    if not volumes.map(isfinite).all() or (volumes < 0).any():
        raise ValueError("observed history volumes must be finite, nonnegative numbers")
    # Validate observations before adding the intentionally unknown future
    # volume. Otherwise feature dropna can silently erase forecastable months.
    app.state.history["volume"] = volumes
    first, last = _forecastable_range(app.state.history)
    # History is immutable for this app lifetime. Include the one unobserved
    # month now: all lag/rolling inputs look backwards, so the placeholder
    # cannot change any earlier feature row. A restart rebuilds this cache
    # from the newly loaded data. Changes to recorded observations need a
    # matching retrained artifact. Appended observations do not imply refitting.
    extended = pd.concat(
        [
            app.state.history,
            pd.DataFrame({"date": [last], "volume": [float("nan")]}),
        ],
        ignore_index=True,
    )
    feats, columns = build_features(extended, trend_origin=origin)
    app.state.features = feats.set_index("date")
    _validate_artifact(app.state.artifact, app.state.features, columns)
    app.state.history_id = validate_provenance(app.state.artifact, app.state.history)
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

_KNOWN_PATHS = {"/health", "/predict", "/forecast-window"}
_KNOWN_METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
}


@app.middleware("http")
async def track_requests(request: Request, call_next):
    if request.url.path == "/metrics" or request.url.path.startswith("/metrics/"):
        return await call_next(request)
    # Both paths and methods come from requests. Bound both label dimensions
    # so distinct unknown requests cannot keep creating new time series.
    path = request.url.path if request.url.path in _KNOWN_PATHS else "other"
    method = request.method if request.method in _KNOWN_METHODS else "other"
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # unhandled errors never produce a response object here, so without
        # this branch 500s would vanish from the very metrics meant to
        # surface them — an error-rate panel reading zero during an outage
        REQUEST_COUNT.labels(method, path, "500").inc()
        REQUEST_LATENCY.labels(path).observe(time.perf_counter() - start)
        raise
    REQUEST_COUNT.labels(method, path, str(response.status_code)).inc()
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
        "model_id": app.state.model_id,
        "history_id": app.state.history_id,
    }


@app.get("/forecast-window", response_model=ForecastWindowResponse)
def forecast_window():
    """Report the same loaded-history bounds used by /predict.

    Earlier supported months are historical queries, not additional future
    horizons. The training cutoff describes the model, while observed_through
    describes the lag history available to that model at this startup.
    """
    first, last = app.state.forecastable_range
    return {
        "first_supported_month": f"{first:%Y-%m}",
        "last_supported_month": f"{last:%Y-%m}",
        "observed_through": f"{app.state.history['date'].iloc[-1]:%Y-%m}",
        "next_unobserved_month": f"{last:%Y-%m}",
        "trained_through": app.state.artifact["trained_through"],
        "horizon_months": 1,
        "refresh_policy": "loaded at startup; restart after updating history or model",
        "model_id": app.state.model_id,
        "history_id": app.state.history_id,
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
    if not isfinite(predicted) or predicted < 0:
        raise HTTPException(
            status_code=500, detail="model produced an invalid forecast"
        )
    PREDICTED_VOLUME.observe(predicted)

    return {
        "month": f"{ts:%Y-%m}",
        "predicted_volume": round(predicted),
        "naive_same_month_last_year": int(row["lag_12"].iloc[0]),
        "trained_through": artifact["trained_through"],
        "model_id": app.state.model_id,
        "history_id": app.state.history_id,
    }
