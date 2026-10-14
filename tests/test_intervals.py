"""Independent boundary and temporal checks for residual interval forecasts."""

import json
from dataclasses import asdict
from fractions import Fraction
from math import isfinite

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


@pytest.mark.parametrize("method", ["fixed", "rolling", "adaptive"])
@pytest.mark.parametrize("point", [-1.75e308, 1.75e308])
def test_endpoint_overflow_refuses_before_pending_and_allows_retry(method, point):
    c = ResidualCalibrator([("2025-01", 1e307)], method=method, window=1, alpha="0.5")
    before = vars(c).copy()
    assert isfinite(point)
    with pytest.raises(ValueError, match="endpoint must be finite"):
        c.issue("2025-02", point)
    assert vars(c) == before
    issued = c.issue("2025-02", 0)
    assert issued.lower == -1e307 and issued.upper == 1e307
    row = c.observe("2025-02", 0)
    assert isfinite(row["width"]) and isfinite(row["interval_score"])
    json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("method", ["fixed", "rolling", "adaptive"])
def test_finite_endpoints_with_unrepresentable_width_are_not_admitted(method):
    c = ResidualCalibrator([("2025-01", 9e307)], method=method, window=1, alpha="0.5")
    before = vars(c).copy()
    assert isfinite(-9e307) and isfinite(9e307)
    for _ in range(2):
        with pytest.raises(ValueError, match="interval width must be finite"):
            c.issue("2025-02", 0)
        assert vars(c) == before


@pytest.mark.parametrize("method", ["fixed", "rolling", "adaptive"])
@pytest.mark.parametrize("actual", [-1e308, 1e308])
def test_score_overflow_keeps_complete_state_for_corrected_observation(method, actual):
    c = ResidualCalibrator([("2025-01", 1)], method=method, window=1, alpha="0.5")
    issued = c.issue("2025-02", 0)
    before = vars(c).copy()
    assert isfinite(actual)
    with pytest.raises(ValueError, match="interval score must be finite"):
        c.observe("2025-02", actual)
    assert vars(c) == before and c.pending is issued
    row = c.observe("2025-02", 0)
    assert row["width"] == row["interval_score"] == 2
    assert c.pending is None and c.observed == month_number("2025-02")
    json.dumps(row, allow_nan=False)
    c.issue("2025-03", 0)


@pytest.mark.parametrize("method", ["rolling", "adaptive"])
def test_unbounded_residual_overflow_preserves_history_month_alpha_and_pending(method):
    c = ResidualCalibrator([("2025-01", 0)], method=method, window=1, alpha="0.1")
    issued = c.issue("2025-02", 1e308)
    assert issued.status == "unbounded"
    before = vars(c).copy()
    with pytest.raises(ValueError, match="residual must be finite"):
        c.observe("2025-02", -1e308)
    assert vars(c) == before and c.pending is issued
    row = c.observe("2025-02", 1e308)
    assert c.history == (("2025-02", 0),)
    assert row["width"] is row["interval_score"] is None
    json.dumps(row, allow_nan=False)
    c.issue("2025-03", 0)


def test_fixed_unbounded_observation_does_not_require_an_unused_residual():
    c = ResidualCalibrator([("2025-01", 0)], method="fixed", window=1, alpha="0.1")
    original = c.history
    c.issue("2025-02", 1e308)
    row = c.observe("2025-02", -1e308)
    assert row["covered"] and row["score_status"] == "infinite"
    assert c.history is original and c.pending is None
    json.dumps(row, allow_nan=False)
    c.issue("2025-03", 0)


@pytest.mark.parametrize("method", ["fixed", "rolling", "adaptive"])
@pytest.mark.parametrize("alpha", ["0.5", "0.1"])
def test_finite_and_intentionally_unbounded_statuses_match_strict_json(method, alpha):
    c = ResidualCalibrator([("2025-01", 1)], method=method, window=1, alpha=alpha)
    issued = c.issue("2025-02", 0)
    row = c.observe("2025-02", 2)
    summary = summarize([row])
    json.dumps(
        {"issued": asdict(issued), "row": row, "summary": summary}, allow_nan=False
    )
    if alpha == "0.5":
        assert (
            row["status"]
            == row["score_status"]
            == summary["aggregate_status"]
            == "finite"
        )
        assert row["width"] == 2 and row["interval_score"] == 6
        assert summary["mean_width"] == 2 and summary["mean_interval_score"] == 6
    else:
        assert row["status"] == "unbounded"
        assert row["score_status"] == summary["aggregate_status"] == "infinite"
        assert (
            row["lower"]
            is row["upper"]
            is row["width"]
            is row["interval_score"]
            is None
        )


def test_unrepresentable_integer_refuses_without_state_change():
    c = ResidualCalibrator(history(), window=10)
    before = vars(c).copy()
    with pytest.raises(ValueError, match="point must be finite"):
        c.issue("2019-11", 10**400)
    assert vars(c) == before


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
