"""The source adapter keeps observation and publication clocks separate."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from src import ledger_bts
from src.ledger import Ledger
from src.ledger_bts import prepare_record
from src.provenance import digest, history_rows
from src.real_backtest import DATA, MANIFEST, load_snapshot


def test_existing_bts_snapshot_is_a_replay_with_matched_baselines(tmp_path):
    record = prepare_record(DATA, MANIFEST, evidence_kind="retrospective_replay")
    history, manifest = load_snapshot(DATA, MANIFEST)
    assert record["target_month"] == "2026-08"
    assert record["vintage"]["available_through"] == "2026-07"
    assert record["vintage"]["sha256"] == manifest["sha256"]
    assert record["vintage"]["history_id"] == digest(history_rows(history))
    assert (
        record["prediction"] == record["last_observation"] == history["volume"].iloc[-1]
    )
    assert record["seasonal_naive"] == history["volume"].iloc[-12]
    assert record["source"]["published_at"] is None
    assert record["classification"] == "retrospective_replay"
    assert record["uncertainty"] is None
    ledger = Ledger(tmp_path / "bts.sqlite")
    ledger.append("issuance", "replay", record)
    assert ledger.view()["summaries"]["real_issuance"]["issuances"] == 0


def test_unverified_dataset_update_is_not_a_publication_receipt(tmp_path):
    record = prepare_record(DATA, MANIFEST, evidence_kind="real_issuance")
    with pytest.raises(ValueError, match="verified source publication"):
        Ledger(tmp_path / "bts.sqlite").append("issuance", "real", record)


def test_modified_snapshot_and_incompatible_manifest_fail(tmp_path):
    data = tmp_path / "changed.csv"
    data.write_bytes(DATA.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA256"):
        prepare_record(data, MANIFEST, evidence_kind="retrospective_replay")
    manifest = tmp_path / "wrong.json"
    raw = json.loads(MANIFEST.read_bytes())
    raw["units"] = "moves"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="supported BTS"):
        prepare_record(DATA, manifest, evidence_kind="retrospective_replay")


@pytest.mark.parametrize("after_load", ["replace", "delete"])
def test_record_keeps_identity_of_validated_bytes_after_path_changes(
    tmp_path, monkeypatch, after_load
):
    data = tmp_path / "snapshot.csv"
    data.write_bytes(DATA.read_bytes())
    history, manifest = load_snapshot(data, MANIFEST)

    def validated_then_changed(data_path, manifest_path):
        result = load_snapshot(data_path, manifest_path)
        if after_load == "replace":
            data_path.write_bytes(b"different file after the validated read")
        else:
            data_path.unlink()
        return result

    monkeypatch.setattr(ledger_bts, "load_snapshot", validated_then_changed)
    record = prepare_record(data, MANIFEST, evidence_kind="retrospective_replay")
    assert record["vintage"]["sha256"] == manifest["sha256"]
    assert record["vintage"]["history_id"] == digest(history_rows(history))
    assert record["vintage"]["available_through"] == "2026-07"
    assert record["target_month"] == "2026-08"
    assert record["prediction"] == history["volume"].iloc[-1]
    assert record["seasonal_naive"] == history["volume"].iloc[-12]
    ledger = Ledger(tmp_path / "captured.sqlite")
    issued = ledger.append("issuance", "captured-replay", record)
    assert ledger.append("issuance", "captured-replay", record) == issued
    assert ledger.events()[0]["payload"]["vintage"] == record["vintage"]
    assert ledger.view()["summaries"]["real_issuance"]["issuances"] == 0


def test_command_retry_keeps_original_clock(tmp_path):
    db = tmp_path / "bts.sqlite"
    command = [
        sys.executable,
        "-m",
        "src.ledger_bts",
        "--data",
        str(DATA),
        "--manifest",
        str(MANIFEST),
        "--kind",
        "retrospective_replay",
        "--db",
        str(db),
        "--key",
        "one",
    ]
    first = subprocess.run(command, capture_output=True, check=True, text=True)
    second = subprocess.run(command, capture_output=True, check=True, text=True)
    assert first.stdout == second.stdout
    assert len(Ledger(db).events()) == 1


def test_fixture_view_rebuilds_from_exact_event_history(tmp_path):
    source = Path(__file__).resolve().parents[1] / "docs/ledger-fixture.json"
    saved = json.loads(source.read_bytes())
    ledger = Ledger(tmp_path / "restore.sqlite")
    with ledger._connection() as connection:
        for event in saved["events"]:
            connection.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                (
                    event["seq"],
                    event["event_id"],
                    event["idempotency_key"],
                    event["kind"],
                    event["issuance_id"],
                    json.dumps(event["payload"]),
                    event["payload_id"],
                    event["recorded_at"],
                ),
            )
    assert ledger.view() == saved


@pytest.fixture
def outlook_clock(monkeypatch):
    """Contract fixtures use an owned clock and never issue real forecasts."""

    class Clock(datetime):
        current = datetime(2026, 10, 14, 15, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(ledger_bts, "datetime", Clock)
    monkeypatch.setattr("src.ledger.datetime", Clock)
    return Clock


def outlook_snapshot(tmp_path, clock, *, start="2025-01", end="2026-08"):
    """Invented indexed values make comparator selection independently visible."""
    dates = pd.date_range(start + "-01", end + "-01", freq="MS")
    raw = (
        "obs_date,tsi_freight\n"
        + "".join(
            f"{date:%Y-%m-%d}T00:00:00.000,{100 + index}\n"
            for index, date in enumerate(dates)
        )
    ).encode()
    data, metadata = tmp_path / "fixture.csv", tmp_path / "fixture-manifest.json"
    data.write_bytes(raw)
    manifest = json.loads(MANIFEST.read_bytes())
    captured = clock.current.replace(hour=14).isoformat()
    manifest.update(
        sha256=hashlib.sha256(raw).hexdigest(),
        rows=len(dates),
        first_month=start,
        last_month=end,
        retrieved_utc=captured,
    )
    metadata.write_text(json.dumps(manifest))
    history, _ = load_snapshot(data, metadata)
    receipt = {
        "schema_version": 1,
        "kind": "available_by_capture",
        "source_url": manifest["source_url"],
        "retrieved_at": captured,
        "available_by": captured,
        "sha256": manifest["sha256"],
        "history_id": digest(history_rows(history)),
        "dataset_id": "bw6n-ddqk",
        "field": "tsi_freight",
        "last_month": end,
        "http_status": 200,
    }
    return data, metadata, receipt


@pytest.mark.parametrize(
    "available,issued,target,seasonal,last",
    [
        ("2026-08", "2026-10-14T15:00:00+00:00", "2026-11", 110, 119),
        ("2026-10", "2026-12-14T15:00:00+00:00", "2027-01", 112, 121),
    ],
)
def test_future_three_step_record_uses_calendar_target_and_prior_year_comparator(
    tmp_path, outlook_clock, available, issued, target, seasonal, last
):
    outlook_clock.current = datetime.fromisoformat(issued)
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock, end=available)
    record = prepare_record(
        data, manifest, evidence_kind="real_issuance", availability=receipt, horizon=3
    )
    assert record["target_month"] == target
    assert record["prediction"] == record["last_observation"] == last
    assert record["seasonal_naive"] == seasonal
    assert record["horizon"] == 3 and record["classification"] == "forecast"
    assert record["artifact"]["feature_schema"] == "bts-last-observation-h3-v1"
    assert record["artifact"]["policy"]["family"] == "last_observation_h3"
    assert record["artifact"]["policy_id"] == digest(record["artifact"]["policy"])
    assert record["source"]["published_at"] is None
    assert record["source"]["publication_verified"] is False
    ledger = Ledger(tmp_path / "fixture.sqlite")
    issued_id = ledger.append("issuance", "owned-outlook-fixture", record)
    assert (
        Ledger(ledger.path).append("issuance", "owned-outlook-fixture", record)
        == issued_id
    )
    assert ledger.view()["cohorts"][0]["scored_targets"] == 0
    assert ledger.view()["cohorts"][0]["mae"]["prediction"] is None


def test_one_step_availability_is_a_nowcast_and_preserves_original_policy(
    tmp_path, outlook_clock
):
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock)
    record = prepare_record(
        data, manifest, evidence_kind="real_issuance", availability=receipt
    )
    assert record["target_month"] == "2026-09"
    assert record["classification"] == "elapsed_period_nowcast"
    assert record["seasonal_naive"] == 108
    assert record["artifact"]["feature_schema"] == "bts-last-observation-v1"
    assert record["artifact"]["model_id"] == digest(
        {"family": "last_observation", "version": 1}
    )
    assert record["horizon"] == 1


@pytest.mark.parametrize(
    "issued", ["2026-11-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00"]
)
def test_current_or_elapsed_three_step_target_refuses(tmp_path, outlook_clock, issued):
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock)
    outlook_clock.current = datetime.fromisoformat(issued)
    with pytest.raises(ValueError, match="future forecast target"):
        prepare_record(
            data,
            manifest,
            evidence_kind="real_issuance",
            availability=receipt,
            horizon=3,
        )


def test_three_step_missing_publication_and_prior_year_value_refuse(
    tmp_path, outlook_clock
):
    data, manifest, _ = outlook_snapshot(tmp_path, outlook_clock)
    with pytest.raises(ValueError, match="verified source publication"):
        prepare_record(data, manifest, evidence_kind="real_issuance", horizon=3)
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock, start="2026-01")
    with pytest.raises(ValueError, match="prior-year comparator"):
        prepare_record(
            data,
            manifest,
            evidence_kind="real_issuance",
            availability=receipt,
            horizon=3,
        )


@pytest.mark.parametrize("horizon", [True, 0, 2, 4, 3.0])
def test_adapter_refuses_undeclared_horizon(tmp_path, outlook_clock, horizon):
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock)
    with pytest.raises(ValueError, match="only one-step or declared three-step"):
        prepare_record(
            data,
            manifest,
            evidence_kind="real_issuance",
            availability=receipt,
            horizon=horizon,
        )


def test_availability_receipt_is_validated_against_exact_parsed_bytes(
    tmp_path, outlook_clock
):
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock)
    wrong = deepcopy(receipt)
    wrong["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="captured vintage"):
        prepare_record(
            data, manifest, evidence_kind="real_issuance", availability=wrong, horizon=3
        )
    with pytest.raises(ValueError, match="not both"):
        prepare_record(
            data,
            manifest,
            evidence_kind="real_issuance",
            availability=receipt,
            publication={},
        )


def test_future_observations_cannot_enter_current_issuance(tmp_path, outlook_clock):
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock, end="2026-11")
    with pytest.raises(
        ValueError, match="available observations cannot be future periods"
    ):
        prepare_record(
            data,
            manifest,
            evidence_kind="real_issuance",
            availability=receipt,
            horizon=3,
        )


def test_three_step_cli_retry_after_target_begins_keeps_original_record(
    tmp_path, outlook_clock, monkeypatch, capsys
):
    data, manifest, receipt = outlook_snapshot(tmp_path, outlook_clock)
    receipt_path = tmp_path / "availability.json"
    receipt_path.write_text(json.dumps(receipt))
    db = tmp_path / "owned-fixture.sqlite"
    arguments = [
        "ledger_bts",
        "--data",
        str(data),
        "--manifest",
        str(manifest),
        "--availability-record",
        str(receipt_path),
        "--kind",
        "real_issuance",
        "--horizon",
        "3",
        "--db",
        str(db),
        "--key",
        "owned-fixture",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    ledger_bts.main()
    first = capsys.readouterr().out
    original = Ledger(db).events()
    outlook_clock.current = datetime(2026, 11, 1, tzinfo=UTC)
    ledger_bts.main()
    assert capsys.readouterr().out == first
    assert Ledger(db).events() == original
    monkeypatch.setattr(sys, "argv", [*arguments[:-1], "new-key"])
    with pytest.raises(ValueError, match="future forecast target"):
        ledger_bts.main()
    assert Ledger(db).events() == original


def test_cli_receipt_options_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ledger_bts",
            "--data",
            "unused.csv",
            "--manifest",
            "unused.json",
            "--publication-record",
            "publication.json",
            "--availability-record",
            "availability.json",
            "--kind",
            "real_issuance",
            "--db",
            "unused.sqlite",
            "--key",
            "unused",
        ],
    )
    with pytest.raises(SystemExit) as error:
        ledger_bts.main()
    assert error.value.code == 2
