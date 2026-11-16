"""The real time protocol is declared before any result and keeps its fixed rules."""

import json
from pathlib import Path

PROTOCOL = Path(__file__).resolve().parents[1] / "docs/real-time-protocol.json"


def test_protocol_declares_its_fixed_rules():
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["id"] == "bts-freight-tsi-real-time-v1"
    assert protocol["series"] == "TSIFRGHT"
    assert protocol["forecasters"] == [
        "monthly_refit_ridge",
        "last_observation",
        "seasonal_naive",
    ]
    assert protocol["outcome_policies"] == ["first_release", "latest_available"]
    assert protocol["origins"]["first_target"] == "2020-01"
    assert protocol["origins"]["last_target"] == "2025-12"
    assert len(protocol["component_series"]["ids"]) == 7
    assert "Cass" in protocol["component_series"]["rights_rule"]
    assert protocol["declared_utc"].startswith("2026-11-16")
