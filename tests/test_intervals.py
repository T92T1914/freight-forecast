"""Independent boundary and temporal checks for residual interval forecasts."""

from fractions import Fraction

import pytest

from src.intervals import ResidualCalibrator, month_number, radius, summarize
from tools.evaluate_intervals import evaluate, load_inputs


def history(n=10):
    return [(f"2019-{i + 1:02d}", float(i + 1)) for i in range(n)]


def test_conservative_order_statistic_and_unbounded_rank():
    assert radius(list(range(1, 11)), Fraction(1, 10)) == (10, 10)
    assert radius(list(range(1, 11)), Fraction(1, 20)) == (None, 11)
    assert radius(list(range(1, 11)), Fraction(1)) == (0, 0)
    assert radius([0] * 9, Fraction(1, 10)) == (0, 9)


@pytest.mark.parametrize("method", ["fixed", "rolling", "adaptive"])
def test_future_observation_cannot_change_previously_issued_interval(method):
    a = ResidualCalibrator(history(), method=method, window=10)
    b = ResidualCalibrator(history(), method=method, window=10)
    issued = a.issue("2019-11", 100)
    assert issued == b.issue("2019-11", 100)
    a.observe("2019-11", 100)
    b.observe("2019-11", 1000)
    assert issued.lower == 90 and issued.upper == 110
    if method != "fixed":
        assert a.issue("2019-12", 100) != b.issue("2019-12", 100)
    else:
        assert a.issue("2019-12", 100) == b.issue("2019-12", 100)


def test_manual_interval_score_and_adaptive_direction():
    c = ResidualCalibrator(history(), method="adaptive", window=10)
    c.issue("2019-11", 100)
    miss = c.observe("2019-11", 112)
    assert miss["width"] == 20
    assert miss["interval_score"] == 60  # 20 + 2/0.1 * (112-110)
    assert not miss["covered"]
    assert c.alpha == Fraction(191, 2000)
    c.issue("2019-12", 100)
    hit = c.observe("2019-12", 100)
    assert hit["covered"] and c.alpha == Fraction(12, 125)


def test_unbounded_interval_is_retained_without_finite_average():
    c = ResidualCalibrator(history(2), window=2)
    issued = c.issue("2019-03", 100)
    assert issued.status == "unbounded" and issued.lower is issued.upper is None
    row = c.observe("2019-03", 1e9)
    assert row["covered"] and row["interval_score"] is None
    result = summarize([row])
    assert result["coverage"] == 1 and result["unbounded"] == 1
    assert result["aggregate_status"] == "infinite"
    assert result["mean_width"] is result["mean_interval_score"] is None
    assert result["finite_only_months"] == 0


def test_exact_boundary_is_covered_and_new_residual_not_in_current_calibration():
    c = ResidualCalibrator(history(), window=10)
    issued = c.issue("2019-11", 100)
    row = c.observe("2019-11", 110)
    assert row["covered"] and row["interval_score"] == 20
    assert issued.calibration_last == "2019-10"
    assert c.issue("2019-12", 100).calibration_first == "2019-02"


def test_observation_protocol_and_failed_observation_preserve_pending():
    c = ResidualCalibrator(history(), window=10)
    with pytest.raises(ValueError):
        c.observe("2019-11", 100)
    with pytest.raises(ValueError):
        c.issue("2019-12", 100)
    first = c.issue("2019-11", 100)
    for month, actual in [("2019-12", 100), ("2019-11", float("nan"))]:
        with pytest.raises(ValueError):
            c.observe(month, actual)
        assert c.pending == first
    with pytest.raises(ValueError):
        c.issue("2019-11", 100)
    c.observe("2019-11", 100)
    with pytest.raises(ValueError):
        c.observe("2019-11", 100)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [("2019-01", -1)],
        [("2019-13", 0)],
        [("2019-01", True)],
        [("2019-01", float("inf"))],
    ],
)
def test_invalid_calibration_rejected(bad):
    with pytest.raises(ValueError):
        ResidualCalibrator(bad, window=max(1, len(bad)))


def test_calendar_order_and_snapshot_ownership():
    original = history()
    c = ResidualCalibrator(original, window=10)
    original[-1] = ("2019-10", 999)
    assert c.issue("2019-11", 100).upper == 110
    assert month_number("2020-01") == month_number("2019-12") + 1
    with pytest.raises(ValueError):
        ResidualCalibrator(list(reversed(history())), window=10)


def test_retained_input_is_identified_without_new_interval_evaluation():
    protocol, saved, calibration, evaluation = load_inputs()
    assert saved["selected_policy"] == "monthly_refit"
    assert len(calibration) == protocol["window"] == 60
    assert len(evaluation) == 72


def test_evaluation_future_change_preserves_all_previous_outputs():
    from copy import deepcopy

    initial = [
        {
            "month": month,
            "actual": error,
            "model": 0,
            "last_observation": 0,
            "seasonal_naive": 0,
        }
        for month, error in history()
    ]
    rows = [
        {
            "month": month,
            "actual": 12,
            "model": 0,
            "last_observation": 1,
            "seasonal_naive": 2,
        }
        for month in ["2019-11", "2019-12"]
    ]
    protocol = {"window": 10, "target_miscoverage": "0.1", "adaptive_step": "0.005"}
    altered = deepcopy(rows)
    altered[-1]["actual"] = 9999
    baseline = evaluate(initial, rows, protocol)
    variant = evaluate(initial, altered, protocol)
    assert len(baseline) == len(variant) == 9
    for left, right in zip(baseline, variant, strict=True):
        assert left["rows"][0] == right["rows"][0]
        for field in ["lower", "upper", "alpha", "rank", "calibration_last"]:
            assert left["rows"][1][field] == right["rows"][1][field]
