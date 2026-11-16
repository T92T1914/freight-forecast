"""An origin reads nothing published after it, and the retained table is intact."""

import json

import pandas as pd
import pytest

from src.vintages import (
    MANIFEST,
    TABLE,
    first_vintage,
    load_vintages,
    lookahead_rows,
    origin_history,
)

TARGETS = pd.date_range("2020-01-01", "2025-12-01", freq="MS")


@pytest.fixture(scope="module")
def table():
    return load_vintages()


def test_manifest_states_source_and_no_account():
    manifest = json.loads(MANIFEST.read_bytes())
    assert manifest["series"] == "TSIFRGHT"
    assert manifest["account_or_key_used"] is False
    assert manifest["vintage_dates"] == 136
    assert len(manifest["table_sha256"]) == 64


def test_every_origin_reads_only_published_values(table):
    assert len(TARGETS) == 72
    for target in TARGETS:
        history = origin_history(table, target)
        origin = first_vintage(table, target - pd.DateOffset(months=1))
        assert not history.empty
        assert history["obs_date"].max() == target - pd.DateOffset(months=1)
        assert lookahead_rows(history, target, origin).empty


def test_a_planted_later_value_is_caught(table):
    target = pd.Timestamp("2022-06-01")
    history = origin_history(table, target)
    origin = first_vintage(table, target - pd.DateOffset(months=1))
    later = pd.DataFrame(
        {
            "vintage_date": [origin + pd.Timedelta(days=40)],
            "obs_date": [target - pd.DateOffset(months=1)],
            "value": [999.0],
        }
    )
    planted = pd.concat([history, later], ignore_index=True)
    assert len(lookahead_rows(planted, target, origin)) == 1
    target_row = pd.DataFrame(
        {"vintage_date": [origin], "obs_date": [target], "value": [999.0]}
    )
    assert len(lookahead_rows(pd.concat([history, target_row]), target, origin)) == 1


def test_a_changed_table_fails_its_hash(tmp_path, table):
    copy = tmp_path / "t.csv"
    copy.write_bytes(TABLE.read_bytes() + b"2026-11-01,2026-08-01,1\n")
    with pytest.raises(ValueError, match="SHA256"):
        load_vintages(copy, MANIFEST)
