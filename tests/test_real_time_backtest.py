"""The saved real time report is what the code computes from the retained vintages."""

import json
from pathlib import Path

import pytest

from src.real_time_backtest import PROTOCOL, evaluate
from src.vintages import load_vintages

SAVED = Path(__file__).resolve().parents[1] / "docs/real-time-example.json"


def _same(a, b):
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float):
        return a == pytest.approx(b, rel=1e-6)
    return a == b


@pytest.fixture(scope="module")
def recomputed():
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    return evaluate(load_vintages(), protocol)


def test_saved_report_matches_the_code(recomputed):
    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    assert saved["months"] == recomputed["months"] == 72
    assert _same(saved["results"], recomputed["results"])
    assert _same(saved["predictions"], recomputed["predictions"])


def test_every_origin_precedes_its_first_release(recomputed):
    for row in recomputed["predictions"]:
        assert row["origin_vintage"] <= row["first_release_vintage"]
        assert row["observed_through"] < row["month"]


def test_the_report_states_how_many_origins_already_hold_the_target():
    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    held = [
        row
        for row in saved["predictions"]
        if row["origin_vintage"] == row["first_release_vintage"]
    ]
    report = (SAVED.parent / "real-time-evaluation.md").read_text(encoding="utf-8")
    prose = " ".join(report.split())
    assert f"On {len(held)} of the {saved['months']} origins" in prose
    assert f"Those {len(held)} months are not one step ahead" in prose


def test_last_observation_is_the_origin_vintage_value(recomputed):
    first = recomputed["predictions"][0]
    assert first["month"] == "2020-01"
    assert first["observed_through"] == "2019-12"
    assert first["training_months"] == 228
