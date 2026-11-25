"""The comparison protocol is declared before any statistic and keeps its rules."""

import json
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _load(name: str) -> dict:
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def test_protocol_declares_its_fixed_rules():
    protocol = _load("forecast-comparison-protocol.json")
    earlier = _load("real-time-protocol.json")
    assert protocol["id"] == "freight-forecast-comparison-v1"
    assert protocol["extends"] == earlier["id"]
    assert protocol["data"]["series"] == earlier["series"]
    assert protocol["data"]["component_series"] == earlier["component_series"]["ids"]
    assert protocol["data"]["outcomes"] == earlier["outcome_policies"]
    assert protocol["forecasters"] == earlier["forecasters"]
    assert len(protocol["pairs"]) == 3
    assert protocol["losses"] == ["absolute", "squared"]
    assert protocol["horizon"]["h"] == 1
    assert protocol["significance_level"] == 0.05
    assert protocol["tests"]["fixed_smoothing"]["bandwidth_m"] == int(
        earlier["origins"]["count"] ** (1 / 3)
    )
    confidence = protocol["model_confidence_set"]
    assert confidence["size"] == protocol["significance_level"]
    assert (confidence["block_length"], confidence["sensitivity_block_length"]) == (
        4,
        12,
    )
    assert (confidence["replications"], confidence["seed"]) == (10000, 20261125)
    assert protocol["clark_west"]["applies"] is False


def test_same_vintage_limit_matches_the_saved_replay():
    saved = _load("real-time-example.json")["predictions"]
    same = sum(row["origin_vintage"] == row["first_release_vintage"] for row in saved)
    assert 0 < same < len(saved)
    limits = " ".join(_load("forecast-comparison-protocol.json")["limits"])
    assert "also holds the target month" in limits


def test_clark_west_reason_matches_the_features():
    # The stated reason rests on the model having no one month lag feature.
    import pandas as pd

    from src.features import build_features

    frame = pd.DataFrame(
        {
            "date": pd.date_range("2000-01-01", periods=24, freq="MS"),
            "volume": range(100, 124),
        }
    )
    _, columns = build_features(frame)
    assert columns == ["t", "lag_12", "roll_3"] + [f"m_{m}" for m in range(1, 13)]
