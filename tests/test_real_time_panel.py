"""The saved component result is what the code computes from the retained vintages."""

import json
from pathlib import Path

import pytest

from src.real_time_backtest import PROTOCOL, evaluate
from src.real_time_panel import OUTCOMES, scaled, summarize
from src.vintages import load_vintages

DATA = Path(__file__).resolve().parents[1] / "data/public"
SAVED = Path(__file__).resolve().parents[1] / "docs/real-time-components.json"
COMMITTED = ["AIRRTMFMD11", "WATERBORNED11", "PETROLEUMD11", "NATURALGASD11"]
WITHHELD = ["TRUCKD11", "RAILFRTCARLOADSD11", "RAILFRTINTERMODALD11"]


def _stem(series):
    return DATA / f"{series.lower()}-alfred-vintages"


@pytest.mark.parametrize("series", COMMITTED)
def test_committed_series_reproduce(series):
    saved = json.loads(SAVED.read_text(encoding="utf-8"))["series"][series]
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    table = load_vintages(
        _stem(series).with_suffix(".csv"), _stem(series).with_suffix(".json")
    )
    report = evaluate(table, protocol)
    for outcome in OUTCOMES:
        got = scaled(report["results"][outcome])
        for key, value in got.items():
            assert saved[outcome][key] == pytest.approx(value, rel=1e-6)


@pytest.mark.parametrize("series", WITHHELD)
def test_withheld_series_keep_their_hash_and_no_values(series):
    saved = json.loads(SAVED.read_text(encoding="utf-8"))["series"][series]
    manifest = json.loads(_stem(series).with_suffix(".json").read_bytes())
    assert saved["table_sha256"] == manifest["table_sha256"]
    assert not _stem(series).with_suffix(".csv").exists()


def test_summary_matches_the_series():
    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    assert len(saved["series"]) == 7
    again = summarize(saved["series"])
    assert again["first_release"]["scaled_vs_last_observation"][
        "mean"
    ] == pytest.approx(
        saved["across_series"]["first_release"]["scaled_vs_last_observation"]["mean"]
    )
