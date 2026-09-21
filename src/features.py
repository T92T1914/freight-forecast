"""Feature building for the monthly volume forecaster."""

import pandas as pd


def build_features(
    df: pd.DataFrame, *, trend_origin: pd.Timestamp | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """Add model features to a (date, volume) frame sorted by date.

    Returns the frame with feature columns plus the list of feature names.
    Rows without a full 12-month history are dropped, so forecasts only
    ever use information available before the month being predicted.
    Serving supplies the training origin so trimming old lag history cannot
    reset the fitted time trend. Training defaults to the first source month.
    """
    out = df.sort_values("date").reset_index(drop=True).copy()

    # lag_12 and roll_3 are computed by row position, so they are only
    # "last year" and "last quarter" if the series has no gaps or duplicates
    expected = pd.date_range(out["date"].iloc[0], periods=len(out), freq="MS")
    if not (out["date"].to_numpy() == expected.to_numpy()).all():
        raise ValueError(
            "expected a contiguous monthly series (no gaps or duplicate months)"
        )

    origin = out["date"].iloc[0] if trend_origin is None else trend_origin
    out["t"] = (out["date"].dt.year - origin.year) * 12 + (
        out["date"].dt.month - origin.month
    )
    out["lag_12"] = out["volume"].shift(12)
    out["roll_3"] = out["volume"].shift(1).rolling(3).mean()

    month_dummies = pd.get_dummies(out["date"].dt.month, prefix="m").astype(int)
    out = pd.concat([out, month_dummies], axis=1)
    out = out.dropna(subset=["lag_12", "roll_3"]).reset_index(drop=True)

    feature_cols = ["t", "lag_12", "roll_3"] + list(month_dummies.columns)
    return out, feature_cols
