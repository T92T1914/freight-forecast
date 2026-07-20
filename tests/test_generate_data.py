import pandas as pd

from src.generate_data import N_MONTHS, generate


def test_shape_and_columns():
    df = generate()
    assert list(df.columns) == ["date", "volume"]
    assert len(df) == N_MONTHS
    assert (df["volume"] > 0).all()
    assert pd.api.types.is_integer_dtype(df["volume"])


def test_same_seed_is_deterministic():
    pd.testing.assert_frame_equal(generate(seed=7), generate(seed=7))


def test_peak_season_dominates_winter():
    df = generate()
    month = df["date"].dt.month
    peak_mean = df[month.between(5, 8)]["volume"].mean()
    winter_mean = df[month.isin([12, 1, 2])]["volume"].mean()
    assert peak_mean > 2 * winter_mean


def test_peak_totals_near_40k():
    df = generate()
    first_year = df[df["date"].dt.year == 2017]
    peak_total = first_year[first_year["date"].dt.month.between(5, 8)]["volume"].sum()
    assert 34_000 < peak_total < 44_000
