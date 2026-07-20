import numpy as np
import pandas as pd
import pytest

from src.features import build_features


def _toy_frame(n: int = 30) -> pd.DataFrame:
    # volume = 1, 2, 3, ... makes lag/rolling values exact and easy to check
    return pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=n, freq="MS"),
            "volume": np.arange(1, n + 1, dtype=float),
        }
    )


def test_drops_rows_without_full_history():
    feats, _ = build_features(_toy_frame(30))
    assert len(feats) == 30 - 12


def test_lag_12_is_last_years_value():
    feats, _ = build_features(_toy_frame(30))
    assert (feats["lag_12"] == feats["volume"] - 12).all()


def test_roll_3_uses_only_prior_months():
    # mean of the three previous values of an increasing-by-1 series
    # is exactly current - 2; any leakage of the current month breaks this
    feats, _ = build_features(_toy_frame(30))
    assert (feats["roll_3"] == feats["volume"] - 2).all()


def test_rejects_series_with_a_missing_month():
    df = _toy_frame(30).drop(index=5).reset_index(drop=True)
    with pytest.raises(ValueError, match="contiguous"):
        build_features(df)


def test_month_dummies_are_one_hot():
    feats, feature_cols = build_features(_toy_frame(30))
    dummy_cols = [c for c in feature_cols if c.startswith("m_")]
    assert len(dummy_cols) == 12
    assert (feats[dummy_cols].sum(axis=1) == 1).all()
