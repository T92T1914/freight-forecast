"""The source adapter keeps observation and publication clocks separate."""

import json
import subprocess
import sys
from pathlib import Path

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
