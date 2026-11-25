"""The comparison tests match their named references on a synthetic fixture."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from arch.bootstrap import MCS
from dieboldmariano import dm_test
from scipy import signal, stats
from statsmodels.stats.multitest import multipletests

from src import forecast_comparison as comparison

FIXTURE = Path(__file__).parent / "fixtures/forecast-comparison.json"
PROTOCOL = json.loads(comparison.PROTOCOL.read_text(encoding="utf-8"))
TESTS = PROTOCOL["tests"]
LOSSES = {"absolute": lambda u, v: abs(u - v), "squared": lambda u, v: (u - v) ** 2}
CASES = [(loss, pair) for loss in LOSSES for pair in PROTOCOL["pairs"]]


@pytest.fixture(scope="module")
def rows():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


def _differential(rows, loss, pair):
    table = comparison.loss_table(rows, "first_release", loss)
    return (table[pair[0]] - table[pair[1]]).to_numpy()


@pytest.mark.parametrize("loss,pair", CASES)
def test_diebold_mariano_matches_the_reference(rows, loss, pair):
    first, second = (comparison.KEYS[name] for name in pair)
    statistic, p_value = dm_test(
        [row["first_release"] for row in rows],
        [row[first] for row in rows],
        [row[second] for row in rows],
        loss=LOSSES[loss],
        h=1,
        harvey_correction=True,
        variance_estimator="acf",
    )
    got = comparison.diebold_mariano_hln(_differential(rows, loss, pair))
    precision = TESTS["diebold_mariano_hln"]["reference_precision"]
    assert abs(statistic) >= 0.001
    assert got["statistic"] == pytest.approx(
        statistic, rel=precision["statistic_relative"], abs=0
    )
    assert got["p_value"] == pytest.approx(
        p_value, rel=0, abs=precision["p_value_absolute"]
    )


@pytest.mark.parametrize("loss,pair", CASES)
def test_fixed_smoothing_matches_the_reference_parts(rows, loss, pair):
    d = _differential(rows, loss, pair)
    m = TESTS["fixed_smoothing"]["bandwidth_m"]
    _, ordinates = signal.periodogram(
        d,
        fs=1.0,
        window="boxcar",
        detrend=False,
        scaling="density",
        return_onesided=False,
    )
    statistic = np.sqrt(len(d)) * d.mean() / np.sqrt(ordinates[1 : m + 1].mean())
    got = comparison.fixed_smoothing(d, m)
    precision = TESTS["fixed_smoothing"]["reference_precision"]
    assert got["statistic"] == pytest.approx(
        statistic, rel=precision["statistic_relative"], abs=0
    )
    assert got["p_value"] == pytest.approx(
        2 * stats.t.sf(abs(statistic), 2 * m),
        rel=0,
        abs=precision["p_value_absolute"],
    )


@pytest.mark.parametrize(
    "p_values",
    [
        [0.001, 0.2, 0.03, 0.04, 0.5, 0.008, 0.9],
        [0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
        [1.0, 0.0001, 1.0, 0.0071, 0.0072, 0.3, 0.3],
    ],
)
def test_holm_matches_the_reference(p_values):
    expected = multipletests(p_values, method="holm")[1]
    precision = PROTOCOL["multiple_comparisons"]["reference_precision"]
    assert comparison.holm(p_values) == pytest.approx(
        list(expected), rel=0, abs=precision["p_value_absolute"]
    )


@pytest.mark.parametrize("block", [4, 12])
def test_confidence_set_is_arch_with_the_protocol_settings(rows, block):
    losses = comparison.loss_table(rows, "first_release", "absolute")
    direct = MCS(
        losses,
        size=0.05,
        reps=10000,
        block_size=block,
        method="r",
        bootstrap="circular",
        seed=20261125,
    )
    direct.compute()
    got = comparison.confidence_set(losses, PROTOCOL["model_confidence_set"], block)
    assert sorted(got["set"]) == sorted(direct.included)
    for name, p_value in got["p_values"].items():
        assert p_value == direct.pvalues.loc[name, "Pvalue"]


def test_degenerate_differentials_are_not_computed():
    for differential in (np.zeros(72), np.full(72, 0.1)):
        assert comparison.diebold_mariano_hln(differential) is None
        assert comparison.fixed_smoothing(differential, 4) is None
    same = pd.DataFrame(
        {
            "monthly_refit_ridge": np.arange(72.0),
            "last_observation": np.arange(72.0),
            "seasonal_naive": np.arange(72.0)[::-1],
        }
    )
    assert comparison.confidence_set(same, PROTOCOL["model_confidence_set"], 4) is None


@pytest.mark.parametrize(
    "p_values,expected",
    [
        ([0.01, 0.04], "told_apart"),
        ([0.01, 0.05], "unsettled"),
        ([None, 0.01], "unsettled"),
        ([0.05, 0.9], "not_told_apart"),
        ([None, 0.2], "not_told_apart"),
        ([None, None], "not_computed"),
    ],
)
def test_verdict_rule(p_values, expected):
    assert comparison.verdict(p_values, PROTOCOL["significance_level"]) == expected


def test_a_changed_input_stops_before_any_statistic(rows):
    saved = {"first_release": {key: 1.0 for key in comparison.KEYS.values()}}
    with pytest.raises(ValueError, match="does not reproduce"):
        comparison._require_reproduced("FIXTURE", rows, saved)


def test_holm_covers_the_component_series_only():
    components = PROTOCOL["data"]["component_series"]
    single = PROTOCOL["data"]["series"]

    def series():
        pair = {name: {"p_value": 0.02} for name in comparison.TESTS}
        return {
            outcome: {
                loss: {"pairs": [json.loads(json.dumps(pair)) for _ in range(3)]}
                for loss in PROTOCOL["losses"]
            }
            for outcome in PROTOCOL["data"]["outcomes"]
        }

    results = {name: series() for name in [single, *components]}
    results[components[0]]["first_release"]["absolute"]["pairs"][0][
        "fixed_smoothing"
    ] = None
    comparison.adjust_and_read(results, PROTOCOL)
    alone = results[single]["first_release"]["absolute"]["pairs"][0]
    assert "p_adjusted" not in alone["diebold_mariano_hln"]
    assert alone["verdict"] == "told_apart"
    member = results[components[1]]["first_release"]["absolute"]["pairs"][0]
    assert member["diebold_mariano_hln"]["p_adjusted"] == pytest.approx(0.14)
    # The test that was not computed still counts toward a family of 7.
    assert member["fixed_smoothing"]["p_adjusted"] == pytest.approx(0.14)
    assert member["verdict"] == "not_told_apart"
