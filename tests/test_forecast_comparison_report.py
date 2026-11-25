"""The saved comparison matches the code, and the report matches the saved file."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

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
