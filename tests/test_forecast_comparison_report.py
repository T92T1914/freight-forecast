"""The saved comparison matches the code, and the report matches the saved file."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from scipy import stats

from src import forecast_comparison as comparison
from src.real_time_backtest import PROTOCOL as REAL_TIME_PROTOCOL
from src.real_time_backtest import evaluate
from src.vintages import load_vintages
from tools.render_forecast_comparison import END, START, render

ROOT = Path(__file__).resolve().parents[1]
SAVED = json.loads((ROOT / "docs/forecast-comparison.json").read_text("utf-8"))
DOCUMENT = (ROOT / "docs/forecast-comparison.md").read_text("utf-8")
PROTOCOL = json.loads(comparison.PROTOCOL.read_text(encoding="utf-8"))
COMMITTED = ["AIRRTMFMD11", "WATERBORNED11", "PETROLEUMD11", "NATURALGASD11"]
COMPONENTS = PROTOCOL["data"]["component_series"]
RIDGE, LAST, YEAR = PROTOCOL["forecasters"]


def _readings():
    for series, by_outcome in SAVED["results"].items():
        for outcome, by_loss in by_outcome.items():
            for loss, cell in by_loss.items():
                yield series, outcome, loss, cell


def _close(saved, got):
    """Equal in structure, with numbers equal to a relative difference of 1e-6."""
    if isinstance(got, dict):
        for key, value in got.items():
            _close(saved[key], value)
    elif isinstance(got, list):
        assert len(saved) == len(got)
        for left, right in zip(saved, got, strict=True):
            _close(left, right)
    elif isinstance(got, float):
        assert saved == pytest.approx(got, rel=1e-6, abs=1e-12)
    else:
        assert saved == got


def test_saved_file_names_the_protocol_it_followed():
    raw = comparison.PROTOCOL.read_bytes().replace(b"\r\n", b"\n")
    assert SAVED["protocol_sha256"] == hashlib.sha256(raw).hexdigest()
    assert SAVED["protocol_id"] == PROTOCOL["id"]
    assert list(SAVED["results"]) == [PROTOCOL["data"]["series"], *COMPONENTS]
    assert len(list(_readings())) == 32


@pytest.mark.parametrize("series", ["TSIFRGHT", *COMMITTED])
def test_retained_series_reproduce(series):
    if series == "TSIFRGHT":
        rows = comparison.load_single()
    else:
        stem = ROOT / "data/public" / f"{series.lower()}-alfred-vintages"
        table = load_vintages(stem.with_suffix(".csv"), stem.with_suffix(".json"))
        protocol = json.loads(REAL_TIME_PROTOCOL.read_bytes())
        rows = evaluate(table, protocol)["predictions"]
    got = comparison.compare_series(rows, PROTOCOL)
    # Adjusted values and readings are checked across all series below.
    _close(SAVED["results"][series], got)
    same = sum(row["origin_vintage"] == row["first_release_vintage"] for row in rows)
    assert SAVED["inputs"][series]["same_vintage_targets"] == same


@pytest.mark.parametrize("series", ["TSIFRGHT", *COMPONENTS])
def test_every_series_agrees_with_its_own_saved_errors(series):
    # This also covers the three series whose tables are not committed.
    if series == "TSIFRGHT":
        report = json.loads(comparison.SINGLE.read_bytes())
        errors = {
            outcome: {
                name: report["results"][outcome]["overall"][key]["mae"]
                for name, key in comparison.KEYS.items()
            }
            for outcome in PROTOCOL["data"]["outcomes"]
        }
    else:
        panel = json.loads(comparison.COMPONENTS.read_bytes())["series"][series]
        errors = {
            outcome: {
                RIDGE: panel[outcome]["model_mae"],
                LAST: panel[outcome]["last_observation_mae"],
                YEAR: panel[outcome]["seasonal_naive_mae"],
            }
            for outcome in PROTOCOL["data"]["outcomes"]
        }
        assert SAVED["inputs"][series]["table_sha256"] == panel["table_sha256"]
    months = SAVED["inputs"][series]["months"]
    freedom = {"diebold_mariano_hln": months - 1, "fixed_smoothing": 8}
    for outcome, by_loss in SAVED["results"][series].items():
        for loss, cell in by_loss.items():
            for pair in cell["pairs"]:
                if loss == "absolute":
                    gap = errors[outcome][pair["first"]]
                    gap -= errors[outcome][pair["second"]]
                    assert pair["mean_difference"] == pytest.approx(gap, rel=1e-5)
                for test, df in freedom.items():
                    result = pair[test]
                    p_value = 2 * stats.t.sf(abs(result["statistic"]), df)
                    assert result["p_value"] == pytest.approx(p_value, rel=1e-9)
                    assert ("p_adjusted" in result) == (series in COMPONENTS)


def test_a_replay_that_drifts_from_the_saved_errors_stops(tmp_path, monkeypatch):
    report = json.loads(comparison.SINGLE.read_bytes())
    report["predictions"][0]["model"] += 1
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(comparison, "SINGLE", changed)
    with pytest.raises(ValueError, match="does not reproduce"):
        comparison.load_single()
    panel = json.loads(comparison.COMPONENTS.read_bytes())
    panel["series"]["AIRRTMFMD11"]["first_release"]["model_mae"] *= 1.01
    drifted = tmp_path / "panel.json"
    drifted.write_text(json.dumps(panel), encoding="utf-8")
    monkeypatch.setattr(comparison, "COMPONENTS", drifted)
    with pytest.raises(ValueError, match="does not reproduce"):
        comparison.load_component("AIRRTMFMD11", ROOT / "data/public")


def test_adjustments_and_readings_follow_from_the_raw_statistics():
    again = json.loads(json.dumps(SAVED["results"]))
    for by_outcome in again.values():
        for by_loss in by_outcome.values():
            for cell in by_loss.values():
                for pair in cell["pairs"]:
                    del pair["verdict"]
                    for test in comparison.TESTS:
                        pair[test].pop("p_adjusted", None)
    comparison.adjust_and_read(again, PROTOCOL)
    _close(SAVED["results"], again)


def test_report_tables_are_the_saved_statistics():
    inside = DOCUMENT.split(START)[1].split(END)[0].strip()
    assert inside == render(SAVED)
    # Pinned rows, so swapped or mislabeled columns cannot pass.
    assert (
        "| Series | Pair | Mean difference | DM statistic | DM p | DM Holm p "
        "| Fixed statistic | Fixed p | Fixed Holm p | Reading |"
    ) in inside
    assert (
        "| Freight index | Ridge minus last | 0.3266 | 2.016 | 0.0476 | none "
        "| 1.947 | 0.0874 | none | Unsettled |"
    ) in inside
    assert (
        "| Series | Outcome | Loss | Set at block 4 | Ridge p | Last p "
        "| Last year p | Set at block 12 |"
    ) in inside
    assert (
        "| Freight index | first release | absolute loss | Last | 0.0482 "
        "| 1.0000 | 0.0089 | Last |"
    ) in inside


@pytest.mark.parametrize(
    "first,second,told,unsettled,not_told",
    [(RIDGE, LAST, 5, 7, 20), (RIDGE, YEAR, 3, 21, 8), (LAST, YEAR, 7, 19, 6)],
)
def test_counts_stated_in_the_report(first, second, told, unsettled, not_told):
    found = Counter(
        pair["verdict"]
        for *_, cell in _readings()
        for pair in cell["pairs"]
        if (pair["first"], pair["second"]) == (first, second)
    )
    assert found == {
        "told_apart": told,
        "unsettled": unsettled,
        "not_told_apart": not_told,
    }
    stated = (
        f"{told} are told apart, {unsettled} are unsettled and "
        f"{not_told} are not told apart"
    )
    assert stated in " ".join(DOCUMENT.split())


def test_named_findings_in_the_report():
    told = {(RIDGE, LAST): [], (RIDGE, YEAR): [], (LAST, YEAR): []}
    for series, outcome, loss, cell in _readings():
        for pair in cell["pairs"]:
            if pair["verdict"] == "told_apart":
                told[pair["first"], pair["second"]].append(
                    (series, outcome, loss, pair["mean_difference"] < 0)
                )
    assert {(s, o, loss, ridge) for s, o, loss, ridge in told[RIDGE, LAST]} == {
        ("TSIFRGHT", "latest_available", "absolute", False),
        ("TSIFRGHT", "latest_available", "squared", False),
        ("RAILFRTINTERMODALD11", "first_release", "absolute", False),
        ("NATURALGASD11", "latest_available", "absolute", True),
        ("NATURALGASD11", "latest_available", "squared", True),
    }
    assert {s for s, *_ in told[RIDGE, YEAR]} == {"RAILFRTINTERMODALD11"}
    assert all(first_lower for *_, first_lower in told[RIDGE, YEAR])
    assert all(first_lower for *_, first_lower in told[LAST, YEAR])
    assert {s for s, *_ in told[LAST, YEAR]} == {
        "TSIFRGHT",
        "RAILFRTINTERMODALD11",
        "PETROLEUMD11",
    }


def test_confidence_set_counts_in_the_report():
    sets = [(s, cell["confidence_sets"]) for s, _, _, cell in _readings()]
    without_last = [s for s, c in sets if LAST not in c["4"]["set"]]
    without_ridge = [s for s, c in sets if RIDGE not in c["4"]["set"]]
    differ = [s for s, c in sets if c["4"]["set"] != c["12"]["set"]]
    assert without_last == ["NATURALGASD11"] * 3
    assert len(without_ridge) == 11
    assert without_ridge.count("TSIFRGHT") == 4
    assert without_ridge.count("PETROLEUMD11") == 4
    assert len(differ) == 8


def test_unsettled_readings_are_mostly_the_primary_test_alone():
    shapes = Counter()
    for series, _, _, cell in _readings():
        key = "p_adjusted" if series in COMPONENTS else "p_value"
        for pair in cell["pairs"]:
            if pair["verdict"] == "unsettled":
                shapes[pair["diebold_mariano_hln"][key] < 0.05] += 1
    assert shapes == {True: 46, False: 1}


def test_where_the_report_says_nothing_is_told_apart():
    quiet = {}
    for series, outcome, loss, cell in _readings():
        for pair in cell["pairs"]:
            if pair["verdict"] == "not_told_apart":
                key = (pair["first"], pair["second"])
                quiet.setdefault(key, set()).add((series, outcome, loss))
    for series in ("TRUCKD11", "RAILFRTCARLOADSD11", "WATERBORNED11"):
        assert sum(s == series for s, *_ in quiet[RIDGE, LAST]) == 4
    assert ("TSIFRGHT", "first_release", "squared") in quiet[RIDGE, LAST]
    assert ("TSIFRGHT", "first_release", "absolute") not in quiet[RIDGE, LAST]
    for series, outcome, _ in quiet[RIDGE, YEAR]:
        assert series in ("WATERBORNED11", "NATURALGASD11") or (
            series == "TSIFRGHT" and outcome == "latest_available"
        )
    for series, outcome, _ in quiet[LAST, YEAR]:
        assert series == "NATURALGASD11" or (
            series == "WATERBORNED11" and outcome == "latest_available"
        )


def test_ridge_against_last_year_direction():
    higher = [
        (series, outcome, loss)
        for series, outcome, loss, cell in _readings()
        if cell["pairs"][1]["mean_difference"] >= 0
    ]
    assert higher == [
        ("WATERBORNED11", "latest_available", "absolute"),
        ("WATERBORNED11", "latest_available", "squared"),
    ]


def test_shape_of_the_unsettled_readings():
    raw_misses = holm_decides = smaller = 0
    for series, _, _, cell in _readings():
        key = "p_adjusted" if series in COMPONENTS else "p_value"
        for pair in cell["pairs"]:
            primary = pair["diebold_mariano_hln"]
            check = pair["fixed_smoothing"]
            if pair["verdict"] != "unsettled" or not primary[key] < 0.05:
                continue
            raw_misses += check["p_value"] >= 0.05
            holm_decides += check["p_value"] < 0.05
            smaller += abs(check["statistic"]) < abs(primary["statistic"])
    assert (raw_misses, holm_decides, smaller) == (31, 15, 46)
