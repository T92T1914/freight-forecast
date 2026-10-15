"""Exercise retention and reconciliation independently of model promotion."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from src.ledger import SERIES, UNITS, Ledger
from src.provenance import FEATURE_SCHEMA, digest


def issuance():
    policy = {
        "family": "last_observation",
        "series": SERIES,
        "units": UNITS,
        "horizon": 1,
        "feature_schema": "bts-last-observation-v1",
        "model_id": digest({"family": "last_observation"}),
    }
    return {
        "schema_version": 1,
        "evidence_kind": "synthetic_fixture",
        "classification": "forecast",
        "series": SERIES,
        "units": UNITS,
        "target_month": "2099-02",
        "issued_at": "2026-01-02T12:00:00+00:00",
        "issue_timezone": "UTC",
        "horizon": 1,
        "prediction": 100.0,
        "last_observation": 100.0,
        "seasonal_naive": 90.0,
        "uncertainty": None,
        "source": {
            "url": "https://example.invalid/fixture",
            "published_at": "2026-01-01T12:00:00+00:00",
            "retrieved_at": "2026-01-02T10:00:00+00:00",
            "publication_verified": False,
            "publication_basis": "Synthetic clock fixture, not a BTS release",
        },
        "vintage": {
            "sha256": digest({"fixture": 1}),
            "history_id": digest([]),
            "kind": "synthetic_fixture",
            "available_through": "2099-01",
        },
        "artifact": {
            **policy,
            "policy": policy,
            "policy_id": digest(policy),
            "training_cutoff": "2099-01",
        },
    }


def observation(issuance_id, *, vintage="first_release", value=130, predecessor=None):
    return {
        "issuance_id": issuance_id,
        "series": SERIES,
        "units": UNITS,
        "target_month": "2099-02",
        "vintage_kind": vintage,
        "value": value,
        "sha256": digest({"value": value}),
        "supersedes_event_id": predecessor,
        "source": {
            "url": "https://example.invalid/fixture",
            "published_at": "2026-02-01T12:00:00+00:00",
            "retrieved_at": "2026-02-02T12:00:00+00:00",
            "publication_verified": True,
            "publication_basis": "Synthetic publication fixture",
        },
    }


def _storage_bytes(directory):
    return {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }


def test_readonly_review_preserves_database_and_creates_no_sidecars(
    tmp_path, monkeypatch
):
    path = tmp_path / "ledger #1.sqlite"
    writer = Ledger(path)
    issued = writer.append("issuance", "original", issuance())
    expected = writer.events()
    before = _storage_bytes(tmp_path)
    statements, connections = [], []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        assert kwargs.get("uri") is True
        assert args[0].endswith("?mode=ro")
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        connections.append(connection)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr("src.ledger.sqlite3.connect", traced_connect)
        reader = Ledger.open_readonly(path)
        assert reader.review_observation("candidate", observation(issued)) == {
            "status": "admissible",
            "snapshot_id": digest(expected),
            "event_count": 1,
            "recorded": False,
            "reserves_key": False,
        }
        assert reader.events() == expected
        with reader._connection() as connection:
            # Even removing the local query guard cannot enable a database write.
            connection.execute("PRAGMA query_only=OFF")
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("INSERT INTO metadata VALUES(1)")
        with pytest.raises(ValueError, match="cannot append"):
            reader.append("observation", "candidate", observation(issued))
        with pytest.raises(ValueError, match="requires Ledger.open_readonly"):
            writer.review_observation("candidate", observation(issued))

    assert _storage_bytes(tmp_path) == before
    assert len(writer.events()) == 1
    assert "BEGIN" in statements
    assert not any(
        sql.startswith(("CREATE", "PRAGMA synchronous", "PRAGMA journal_mode"))
        for sql in statements
    )
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("variant", ["missing", "empty", "schema", "version", "wal"])
def test_readonly_opener_refuses_storage_without_creating_or_repairing_it(
    tmp_path, variant
):
    path = tmp_path / "not-created" / "ledger.sqlite"
    if variant != "missing":
        path.parent.mkdir()
        if variant == "empty":
            path.write_bytes(b"")
        elif variant == "schema":
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE metadata(version INTEGER)")
        else:
            Ledger(path)
            if variant == "version":
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("INSERT INTO metadata VALUES(1)")
                    connection.commit()
            else:
                with closing(sqlite3.connect(path)) as connection:
                    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == (
                        "wal",
                    )
    before = {
        str(file.relative_to(tmp_path)): file.read_bytes()
        for file in tmp_path.rglob("*")
        if file.is_file()
    }
    with pytest.raises((OSError, ValueError, sqlite3.Error)):
        Ledger.open_readonly(path)
    assert {
        str(file.relative_to(tmp_path)): file.read_bytes()
        for file in tmp_path.rglob("*")
        if file.is_file()
    } == before
    if variant == "missing":
        assert not path.parent.exists()


@pytest.mark.parametrize("classification", ["forecast", "elapsed_period_nowcast"])
@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (None, "rejected"),
        ("2026-01-16", "rejected"),
        ("2026-01-15T11:59:59+00:00", "rejected"),
        ("2026-01-15T12:00:00+00:00", "rejected"),
        ("2026-01-15T06:00:00-06:00", "rejected"),
        ("2026-01-15T06:00:00.000001-06:00", "admissible"),
    ],
)
def test_review_retains_exact_publication_clock_contract(
    tmp_path, record_clock, classification, published, expected
):
    ledger = Ledger(tmp_path / "review.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, classification)
    payload = _clock_outcome(issued, target, published)
    before = _storage_bytes(tmp_path)
    result = Ledger.open_readonly(ledger.path).review_observation("candidate", payload)
    assert result["status"] == expected
    assert "existing_event_id" not in result
    if expected == "rejected":
        with pytest.raises(ValueError):
            ledger.append("observation", "candidate", payload)
    assert _storage_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("series", "unrelated", "does not reconcile"),
        ("units", "shipments", "does not reconcile"),
        ("target_month", "2026-03", "does not reconcile"),
        ("issuance_id", "f" * 64, "unknown original"),
        ("value", float("inf"), "JSON"),
        ("sha256", "not a digest", "SHA256"),
    ],
)
def test_review_rejects_incompatible_or_nonfinite_candidate(
    tmp_path, field, value, reason
):
    ledger = Ledger(tmp_path / "review.sqlite")
    issued = ledger.append("issuance", "original", issuance())
    payload = observation(issued)
    payload[field] = value
    before = _storage_bytes(tmp_path)
    result = Ledger.open_readonly(ledger.path).review_observation("candidate", payload)
    assert result["status"] == "rejected"
    assert reason in result["reason"]
    assert _storage_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "source_change",
    [
        {"retrieved_at": "2026-01-21T12:00:00+00:00"},
        {"published_at": "2026-01-20T12:00:01+00:00"},
        {"publication_verified": False},
        {"availability_receipt": {}},
    ],
)
def test_review_uses_original_source_admission(tmp_path, record_clock, source_change):
    ledger = Ledger(tmp_path / "review.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, "forecast")
    payload = _clock_outcome(issued, target, "2026-01-16T12:00:00+00:00")
    payload["source"].update(source_change)
    before = _storage_bytes(tmp_path)
    assert (
        Ledger.open_readonly(ledger.path).review_observation("candidate", payload)[
            "status"
        ]
        == "rejected"
    )
    with pytest.raises(ValueError):
        ledger.append("observation", "candidate", payload)
    assert _storage_bytes(tmp_path) == before


def test_review_exact_retry_precedes_new_clock_admission_but_recapture_conflicts(
    tmp_path, record_clock
):
    ledger = Ledger(tmp_path / "retry.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, "forecast")
    payload = _clock_outcome(issued, target, "2026-01-16T12:00:00+00:00")
    original = ledger.append("observation", "saved-key", payload)
    reader = Ledger.open_readonly(ledger.path)
    before = _storage_bytes(tmp_path)
    record_clock.current -= timedelta(days=1)
    assert reader.review_observation("saved-key", deepcopy(payload)) == {
        "status": "exact_retry",
        "existing_event_id": original,
        "snapshot_id": digest(ledger.events()),
        "event_count": 2,
        "recorded": False,
        "reserves_key": False,
    }
    # A recapture keeps the value but changes the complete saved payload.
    recapture = deepcopy(payload)
    recapture["source"]["retrieved_at"] = "2026-01-20T12:00:01+00:00"
    result = reader.review_observation("saved-key", recapture)
    assert result["status"] == "rejected"
    assert "incompatible contents" in result["reason"]
    same_instant = deepcopy(payload)
    same_instant["source"]["published_at"] = "2026-01-16T06:00:00-06:00"
    assert reader.review_observation("saved-key", same_instant)["status"] == "rejected"
    assert reader.review_observation("new-key", payload)["status"] == "rejected"
    assert reader.review_observation("original", payload)["status"] == "rejected"
    assert _storage_bytes(tmp_path) == before


@pytest.mark.parametrize("damage", ["link", "source", "retrieval", "identity"])
def test_review_refuses_invalid_restored_history_even_for_exact_retry(tmp_path, damage):
    ledger = Ledger(tmp_path / "restore.sqlite")
    issued = ledger.append("issuance", "original", issuance())
    saved_payload = observation(issued)
    saved = ledger.append("observation", "saved-key", saved_payload)
    restored = observation(issued, vintage="revision", predecessor=saved)
    if damage == "link":
        restored["supersedes_event_id"] = "f" * 64
    elif damage == "source":
        restored["source"]["published_at"] = "2026-02-01"
    elif damage == "retrieval":
        restored["source"]["retrieved_at"] = "2026-03-03T12:00:00+00:00"
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=3,
            key="invalid-history",
            kind="observation",
            payload=restored,
            issuance_id=issued,
        )
        if damage == "identity":
            connection.execute("DROP TRIGGER no_update")
            connection.execute(
                "UPDATE events SET payload_id=? WHERE seq=3", ("f" * 64,)
            )
    before = _storage_bytes(tmp_path)
    result = Ledger.open_readonly(ledger.path).review_observation(
        "saved-key", saved_payload
    )
    assert result["status"] == "rejected"
    assert "existing_event_id" not in result
    assert _storage_bytes(tmp_path) == before


def test_review_revision_correction_and_first_release_rules_match_append(tmp_path):
    ledger = Ledger(tmp_path / "history.sqlite")
    issued = ledger.append("issuance", "original", issuance())
    first_payload = observation(issued)
    first = ledger.append("observation", "first", first_payload)
    reader = Ledger.open_readonly(ledger.path)
    assert reader.review_observation("duplicate", first_payload)["status"] == "rejected"
    for publication, expected in [
        ("2026-02-01T11:59:59+00:00", "rejected"),
        ("2026-02-01T06:00:00-06:00", "admissible"),
        ("2026-03-01T12:00:00+00:00", "admissible"),
    ]:
        revision = observation(issued, vintage="revision", value=140, predecessor=first)
        revision["source"].update(
            published_at=publication, retrieved_at="2026-03-02T12:00:00+00:00"
        )
        assert reader.review_observation("revision", revision)["status"] == expected
    revised = ledger.append("observation", "revision", revision)
    ledger.append(
        "correction",
        "exclude-first",
        {
            "issuance_id": issued,
            "target_event_id": first,
            "action": "exclude_from_evaluation",
            "reason": "Synthetic correction control",
        },
    )
    corrected = observation(issued, value=135, predecessor=first)
    before = _storage_bytes(tmp_path)
    assessment = reader.review_observation("corrected", corrected)
    assert assessment["status"] == "admissible"
    assert assessment["snapshot_id"] == digest(reader.events())
    assert reader.events() == ledger.events()
    assert reader.view()["summaries"]["synthetic_fixture"]["scored_targets"] == 0
    assert (
        reader.view(outcome_policy="latest_available")["rows"][0]["outcome"]["event_id"]
        == revised
    )
    assert _storage_bytes(tmp_path) == before
    ledger.append("observation", "corrected", corrected)
    assert reader.view()["cohorts"][0]["mae"]["prediction"] == 35


@pytest.mark.parametrize(
    "outcome", ["admissible", "exact_retry", "rejected", "missing"]
)
def test_review_cli_reports_status_without_recording_events(tmp_path, outcome):
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    issued = ledger.append("issuance", "original", issuance())
    payload = observation(issued)
    event = None
    if outcome == "exact_retry":
        event = ledger.append("observation", "candidate", payload)
    if outcome == "rejected":
        payload["source"]["published_at"] = "2026-02-01"
    record = tmp_path / "candidate.json"
    record.write_text(json.dumps(payload), encoding="utf-8")
    before = _storage_bytes(tmp_path)
    destination = (
        path if outcome != "missing" else tmp_path / "missing" / "ledger.sqlite"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "src.ledger",
            "--db",
            str(destination),
            "review-observation",
            "--key",
            "candidate",
            "--record",
            str(record),
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    review = json.loads(result.stdout)
    assert review["status"] == ("rejected" if outcome == "missing" else outcome)
    assert result.returncode == (2 if outcome in ("missing", "rejected") else 0)
    assert result.stderr == b""
    if event:
        assert review["existing_event_id"] == event
    else:
        assert "existing_event_id" not in review
    assert _storage_bytes(tmp_path) == before
    assert not (tmp_path / "missing").exists()


def test_review_snapshot_does_not_reserve_a_key_against_later_append(
    tmp_path, monkeypatch
):
    ledger = Ledger(tmp_path / "snapshot.sqlite")
    issued = ledger.append("issuance", "original", issuance())
    candidate = observation(issued)
    reader = Ledger.open_readonly(ledger.path)
    original_events = reader.events()
    read_events = reader._read_events
    commit_started = Event()
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if not kwargs.get("uri"):
            connection.set_trace_callback(
                lambda sql: commit_started.set() if sql == "COMMIT" else None
            )
        return connection

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = []

        def concurrent_append(connection):
            events = read_events(connection)
            pending.append(
                pool.submit(ledger.append, "observation", "candidate", candidate)
            )
            assert commit_started.wait(3), "independent writer never reached commit"
            try:
                pending[0].result(timeout=0.5)
            except TimeoutError:
                pass
            else:
                raise AssertionError(
                    "writer committed between history and candidate queries"
                )
            return events

        monkeypatch.setattr("src.ledger.sqlite3.connect", traced_connect)
        monkeypatch.setattr(reader, "_read_events", concurrent_append)
        result = reader.review_observation("candidate", candidate)
        assert result["status"] == "admissible"
        assert result["snapshot_id"] == digest(original_events)
        assert result["event_count"] == 1
        assert result["reserves_key"] is False
        written = pending[0].result(timeout=3)

    monkeypatch.setattr(reader, "_read_events", read_events)
    result = reader.review_observation("candidate", candidate)
    assert result["status"] == "exact_retry"
    assert result["existing_event_id"] == written
    assert result["snapshot_id"] != digest(original_events)


@pytest.mark.parametrize("variant", ["duplicate", "large", "malformed", "missing"])
def test_review_cli_refuses_invalid_record_without_storage_changes(tmp_path, variant):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("issuance", "original", issuance())
    record = tmp_path / "record.json"
    if variant == "duplicate":
        record.write_bytes(
            b'{"source":{"published_at":null,"published_at":"2026-01-01"}}'
        )
    elif variant == "large":
        record.write_bytes(b" " * (65536 + 1))
    elif variant == "malformed":
        record.write_bytes(b"{not json}")
    before = _storage_bytes(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "src.ledger",
            "--db",
            str(ledger.path),
            "review-observation",
            "--key",
            "candidate",
            "--record",
            str(record),
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2
    assessment = json.loads(result.stdout)
    assert assessment["status"] == "rejected"
    assert assessment["recorded"] is False
    assert assessment["reserves_key"] is False
    assert _storage_bytes(tmp_path) == before


def test_unchanged_public_fixture_is_accepted_as_saved_readonly_history(tmp_path):
    fixture_path = Path(__file__).resolve().parents[1] / "docs" / "ledger-fixture.json"
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    writer = Ledger(tmp_path / "public-fixture.sqlite")
    with writer._connection() as connection:
        for event in fixture["events"]:
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
    reader = Ledger.open_readonly(writer.path)
    before = _storage_bytes(tmp_path)
    assert reader.view() == fixture
    for event in fixture["events"]:
        if event["kind"] == "observation":
            review = reader.review_observation(
                event["idempotency_key"], event["payload"]
            )
            assert review["status"] == "exact_retry"
            assert review["existing_event_id"] == event["event_id"]
            assert review["snapshot_id"] == digest(fixture["events"])
    assert _storage_bytes(tmp_path) == before
    assert fixture_path.read_bytes() == fixture_bytes


def test_readonly_export_keeps_database_alias_and_sidecar_guards(tmp_path):
    writer = Ledger(tmp_path / "ledger.sqlite")
    writer.append("issuance", "original", issuance())
    reader = Ledger.open_readonly(writer.path)
    alias = tmp_path / "alias.json"
    os.link(writer.path, alias)
    before = _storage_bytes(tmp_path)
    for destination in (alias, writer.path, str(writer.path) + "-journal"):
        with pytest.raises(ValueError, match="conflicts with ledger storage"):
            reader.export(destination)
    assert _storage_bytes(tmp_path) == before


def test_exact_retry_and_conflict_survive_restart(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    record = issuance()
    event = ledger.append("issuance", "run1", record)
    record["prediction"] = 1
    assert Ledger(path).append("issuance", "run1", issuance()) == event
    with pytest.raises(ValueError, match="incompatible"):
        ledger.append("issuance", "run1", record)
    assert len(ledger.events()) == 1
    assert ledger.events()[0]["payload"]["prediction"] == 100


def test_concurrent_identical_and_distinct_issuances(tmp_path):
    path = tmp_path / "ledger.sqlite"
    Ledger(path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(
            pool.map(
                lambda _: Ledger(path).append("issuance", "same", issuance()), range(8)
            )
        )
        unique = list(
            pool.map(
                lambda key: Ledger(path).append("issuance", key, issuance()),
                ["a", "b", "c"],
            )
        )
    assert len(set(ids)) == 1
    assert len(set(unique)) == 3
    view = Ledger(path).view()
    assert view["summaries"]["synthetic_fixture"]["repeat_issuances"] == 3


def test_revisions_and_missing_first_release_are_not_extra_forecasts(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    issued = ledger.append("issuance", "i", issuance())
    first = ledger.append("observation", "o", observation(issued))
    revised = observation(issued, vintage="revision", value=140, predecessor=first)
    revised["source"]["published_at"] = "2026-03-01T12:00:00+00:00"
    revised["source"]["retrieved_at"] = "2026-03-02T12:00:00+00:00"
    ledger.append("observation", "r", revised)
    assert ledger.view()["cohorts"][0]["mae"]["prediction"] == 30
    assert (
        ledger.view(outcome_policy="latest_available")["cohorts"][0]["mae"][
            "prediction"
        ]
        == 40
    )
    assert ledger.view()["cohorts"][0]["scored_targets"] == 1
    second = ledger.append("issuance", "i2", issuance())
    ledger.append(
        "observation", "latest", observation(second, vintage="latest_snapshot")
    )
    assert ledger.view()["rows"][1]["status"] == "repeat_issuance"
    other = deepcopy(issuance())
    other["target_month"] = "2099-03"
    other["vintage"]["available_through"] = "2099-02"
    third = ledger.append("issuance", "i3", other)
    missing = observation(third, vintage="unavailable", value=None)
    missing.update(
        target_month="2099-03", sha256=None, reason="First release not recovered"
    )
    missing["source"].update(published_at=None, publication_verified=False)
    ledger.append("observation", "missing", missing)
    assert ledger.view()["summaries"]["synthetic_fixture"]["unobserved"] == 1


def test_corrections_preserve_originals_and_unfavorable_results(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    original = ledger.append("issuance", "i1", issuance())
    first = ledger.append("observation", "o1", observation(original))
    view = ledger.view()
    assert view["cohorts"][0]["mae"]["prediction"] == 30  # No promotion gate.
    ledger.append(
        "correction",
        "c1",
        {
            "issuance_id": original,
            "target_event_id": first,
            "action": "exclude_from_evaluation",
            "reason": "Incorrect release transcription",
        },
    )
    assert ledger.events()[1]["payload"]["value"] == 130
    assert ledger.view()["rows"][0]["status"] == "unobserved"
    replacement = observation(original, value=135, predecessor=first)
    ledger.append("observation", "corrected-first", replacement)
    assert ledger.view()["cohorts"][0]["mae"]["prediction"] == 35
    assert ledger.events()[1]["payload"]["value"] == 130
    with (
        sqlite3.connect(ledger.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="append-only"),
    ):
        connection.execute("DELETE FROM events")


def test_losing_fitted_policy_is_still_retained(tmp_path):
    ledger = Ledger(tmp_path / "losing.sqlite")
    record = issuance()
    record["prediction"] = 160
    policy = record["artifact"]["policy"]
    policy.update(family="monthly_refit_ridge", feature_schema=FEATURE_SCHEMA)
    record["artifact"].update(feature_schema=FEATURE_SCHEMA, policy_id=digest(policy))
    issued = ledger.append("issuance", "losing", record)
    ledger.append("observation", "outcome", observation(issued, value=100))
    mae = ledger.view()["cohorts"][0]["mae"]
    assert mae["prediction"] == 60
    assert mae["last_observation"] == 0


def test_latest_snapshot_does_not_become_missing_first_release(tmp_path):
    ledger = Ledger(tmp_path / "latest.sqlite")
    issued = ledger.append("issuance", "issued", issuance())
    ledger.append(
        "observation", "latest", observation(issued, vintage="latest_snapshot")
    )
    assert ledger.view()["rows"][0]["status"] == "unobserved"
    assert ledger.view()["cohorts"][0]["scored_targets"] == 0
    assert (
        ledger.view(outcome_policy="latest_available")["cohorts"][0]["scored_targets"]
        == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(prediction=float("nan")),
        lambda r: r.update(prediction=float("inf")),
        lambda r: r.update(prediction=True),
        lambda r: r.update(units="moves"),
        lambda r: r.update(issued_at="2026-01-02T12:00:00"),
        lambda r: r.update(target_month="2099-04"),
        lambda r: r.update(horizon=2),
        lambda r: r["artifact"].update(feature_schema="unknown"),
        lambda r: r["artifact"].update(training_cutoff="2099-03"),
        lambda r: r["artifact"]["policy"].update(family="other"),
        lambda r: r.update(uncertainty={"lower": 80, "upper": 120}),
    ],
)
def test_invalid_or_incompatible_issuance_has_no_partial_record(tmp_path, change):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    bad = issuance()
    change(bad)
    with pytest.raises((ValueError, TypeError)):
        ledger.append("issuance", "bad", bad)
    assert ledger.events() == []


def test_real_issue_requires_timing_and_correct_product_classification(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    record = issuance()
    now = datetime.now(UTC)
    previous = now.replace(day=1) - timedelta(days=1)
    target = now.strftime("%Y-%m")
    record.update(
        evidence_kind="real_issuance", target_month=target, issued_at=now.isoformat()
    )
    record["vintage"].update(
        kind="latest_vintage_snapshot", available_through=previous.strftime("%Y-%m")
    )
    record["artifact"]["training_cutoff"] = previous.strftime("%Y-%m")
    with pytest.raises(ValueError, match="elapsed"):
        ledger.append("issuance", "badclass", record)
    record["classification"] = "elapsed_period_nowcast"
    with pytest.raises(ValueError, match="verified"):
        ledger.append("issuance", "missingtiming", record)
    record["source"]["publication_verified"] = True
    ledger.append("issuance", "nowcast", record)
    backdated = deepcopy(record)
    backdated["issued_at"] = (now - timedelta(minutes=10)).isoformat()
    with pytest.raises(ValueError, match="backdated"):
        ledger.append("issuance", "backdated", backdated)


def test_failed_reconciliation_and_revision_are_atomic(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    issued = ledger.append("issuance", "i", issuance())
    wrong = observation(issued)
    wrong["units"] = "moves"
    with pytest.raises(ValueError, match="reconcile"):
        ledger.append("observation", "wrong", wrong)
    with pytest.raises(ValueError, match="predecessor"):
        ledger.append(
            "observation", "revision", observation(issued, vintage="revision")
        )
    assert len(ledger.events()) == 1


def test_process_interruption_rolls_back_partial_transaction(tmp_path):
    path = tmp_path / "ledger.sqlite"
    Ledger(path).append("issuance", "committed", issuance())
    code = """
import os, sqlite3, sys
c=sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute('PRAGMA synchronous=FULL')
c.execute('BEGIN IMMEDIATE')
c.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?,?)",
          ('partial','partial','issuance','partial','{}','bad','now'))
os._exit(7)
"""
    process = subprocess.run([sys.executable, "-c", code, os.fspath(path)], check=False)
    assert process.returncode == 7
    assert len(Ledger(path).events()) == 1
    Ledger(path).append("issuance", "afterrestart", issuance())
    assert len(Ledger(path).events()) == 2


def test_export_is_rebuildable_and_detects_changed_saved_payload(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.append("issuance", "i", issuance())
    assert Ledger(ledger.path).view() == ledger.view()
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("DROP TRIGGER no_update")
        connection.execute(
            "UPDATE events SET payload=?", (json.dumps({"changed": True}),)
        )
    with pytest.raises(ValueError, match="identity"):
        ledger.view()


@pytest.mark.parametrize("kind", ["issuance", "observation", "correction"])
def test_readback_rejects_changed_issuance_column_without_rehashing(tmp_path, kind):
    ledger = Ledger(tmp_path / "link.sqlite")
    original = ledger.append("issuance", "original", issuance())
    other = ledger.append("issuance", "other", issuance())
    observed = ledger.append("observation", "observed", observation(original))
    corrected = ledger.append(
        "correction",
        "corrected",
        {
            "issuance_id": original,
            "target_event_id": observed,
            "action": "exclude_from_evaluation",
            "reason": "Fixture transcription correction",
        },
    )
    before = ledger.events()
    chosen = {"issuance": original, "observation": observed, "correction": corrected}
    with ledger._connection() as connection:
        connection.execute("DROP TRIGGER no_update")
        connection.execute(
            "UPDATE events SET issuance_id=? WHERE event_id=?", (other, chosen[kind])
        )
        # This is a link inconsistency, not changed payload bytes or hashes.
        actual = connection.execute(
            "SELECT payload, payload_id FROM events WHERE event_id=?", (chosen[kind],)
        ).fetchone()
    retained = next(event for event in before if event["event_id"] == chosen[kind])
    assert json.loads(actual[0]) == retained["payload"]
    assert actual[1] == retained["payload_id"]
    for read in (ledger.events, ledger.view):
        with pytest.raises(ValueError, match="issuance link"):
            read()


def _restore_event(connection, *, seq, key, kind, payload, issuance_id):
    """Restore internally hashed fixture bytes, including deliberately bad links."""
    payload_id = digest(payload)
    event_id = digest({"kind": kind, "key": key, "payload_id": payload_id})
    connection.execute(
        "INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
        (
            seq,
            event_id,
            key,
            kind,
            issuance_id,
            json.dumps(payload),
            payload_id,
            "2026-03-02T12:00:00+00:00",
        ),
    )
    return event_id


@pytest.mark.parametrize("reference", ["unknown", "observation", "later"])
def test_restored_observation_requires_earlier_original_issuance(tmp_path, reference):
    ledger = Ledger(tmp_path / "restore.sqlite")
    original = ledger.append("issuance", "original", issuance())
    observed = ledger.append("observation", "observed", observation(original))
    later = ledger.append("issuance", "later", issuance())
    linked = {"unknown": "f" * 64, "observation": observed, "later": later}[reference]
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=0 if reference == "later" else 4,
            key="bad-restore",
            kind="observation",
            payload=observation(linked),
            issuance_id=linked,
        )
    with pytest.raises(ValueError, match="earlier original issuance"):
        ledger.view()


@pytest.mark.parametrize(
    "field,value",
    [("series", "other"), ("units", "moves"), ("target_month", "2099-03")],
)
def test_restored_observation_target_must_match_original(tmp_path, field, value):
    ledger = Ledger(tmp_path / "target.sqlite")
    original = ledger.append("issuance", "original", issuance())
    payload = observation(original)
    payload[field] = value
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=2,
            key="wrong-target",
            kind="observation",
            payload=payload,
            issuance_id=original,
        )
    with pytest.raises(ValueError, match="saved observation"):
        ledger.view()


@pytest.mark.parametrize("reference", ["unknown", "other_issuance", "correction"])
def test_restored_correction_requires_matching_earlier_target(tmp_path, reference):
    ledger = Ledger(tmp_path / "correction.sqlite")
    original = ledger.append("issuance", "original", issuance())
    other = ledger.append("issuance", "other", issuance())
    observed = ledger.append("observation", "observed", observation(original))
    payload = {
        "issuance_id": original,
        "target_event_id": observed,
        "action": "exclude_from_evaluation",
        "reason": "Fixture correction",
    }
    correction = ledger.append("correction", "good-correction", payload)
    payload["target_event_id"] = {
        "unknown": "f" * 64,
        "other_issuance": other,
        "correction": correction,
    }[reference]
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=5,
            key="wrong-correction",
            kind="correction",
            payload=payload,
            issuance_id=original,
        )
    with pytest.raises(ValueError, match="saved correction target"):
        ledger.view()


@pytest.mark.parametrize("reference", ["unknown", "other_issuance", "unavailable"])
def test_restored_revision_requires_matching_observed_predecessor(tmp_path, reference):
    ledger = Ledger(tmp_path / "revision.sqlite")
    original = ledger.append("issuance", "original", issuance())
    other = ledger.append("issuance", "other", issuance())
    observed = ledger.append("observation", "other-outcome", observation(other))
    unavailable = observation(original, vintage="unavailable", value=None)
    unavailable.update(sha256=None, reason="Fixture missing release")
    unavailable["source"].update(published_at=None, publication_verified=False)
    missing = ledger.append("observation", "missing", unavailable)
    predecessor = {
        "unknown": "f" * 64,
        "other_issuance": observed,
        "unavailable": missing,
    }[reference]
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=5,
            key="wrong-revision",
            kind="observation",
            payload=observation(original, vintage="revision", predecessor=predecessor),
            issuance_id=original,
        )
    with pytest.raises(ValueError, match="saved revision predecessor"):
        ledger.view()


@pytest.mark.parametrize("vintage", ["first_release", "latest_snapshot"])
def test_restored_first_release_replacement_requires_excluded_first(tmp_path, vintage):
    ledger = Ledger(tmp_path / "first.sqlite")
    original = ledger.append("issuance", "original", issuance())
    previous = ledger.append(
        "observation", "previous", observation(original, vintage=vintage)
    )
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=3,
            key="unmatched-first",
            kind="observation",
            payload=observation(original, predecessor=previous),
            issuance_id=original,
        )
    with pytest.raises(ValueError, match="saved first release"):
        ledger.view()


@pytest.mark.parametrize("excluded", [False, True])
def test_restored_first_release_requires_same_history_as_append(tmp_path, excluded):
    ledger = Ledger(tmp_path / "duplicate-first.sqlite")
    original = ledger.append("issuance", "original", issuance())
    previous = ledger.append("observation", "previous", observation(original))
    if excluded:
        ledger.append(
            "correction",
            "exclude-previous",
            {
                "issuance_id": original,
                "target_event_id": previous,
                "action": "exclude_from_evaluation",
                "reason": "Fixture correction",
            },
        )
    payload = observation(original, value=135)
    with pytest.raises(ValueError, match="first release"):
        ledger.append("observation", "append-would-reject", payload)
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=4,
            key="restored-invalid-first",
            kind="observation",
            payload=payload,
            issuance_id=original,
        )
    for read in (ledger.events, ledger.view):
        with pytest.raises(ValueError, match="saved .*first release"):
            read()


def test_restored_revision_cannot_precede_predecessor_publication(tmp_path):
    ledger = Ledger(tmp_path / "earlier-revision.sqlite")
    original = ledger.append("issuance", "original", issuance())
    first = ledger.append("observation", "first", observation(original))
    payload = observation(original, vintage="revision", predecessor=first)
    payload["source"]["published_at"] = "2026-01-31T12:00:00+00:00"
    with pytest.raises(ValueError, match="publication precedes"):
        ledger.append("observation", "append-would-reject", payload)
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=3,
            key="restored-earlier-revision",
            kind="observation",
            payload=payload,
            issuance_id=original,
        )
    for read in (ledger.events, ledger.view):
        with pytest.raises(ValueError, match="saved revision publication precedes"):
            read()


def test_restored_correction_cannot_invent_an_exclusion_action(tmp_path):
    ledger = Ledger(tmp_path / "action.sqlite")
    original = ledger.append("issuance", "original", issuance())
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=2,
            key="wrong-action",
            kind="correction",
            payload={
                "issuance_id": original,
                "target_event_id": original,
                "action": "keep_in_evaluation",
                "reason": "Unsupported fixture action",
            },
            issuance_id=original,
        )
    with pytest.raises(ValueError, match="saved correction target"):
        ledger.view()


def test_cli_rejects_bad_link_without_replacing_export_or_database(tmp_path):
    ledger = Ledger(tmp_path / "cli.sqlite")
    original = ledger.append("issuance", "original", issuance())
    other = ledger.append("issuance", "other", issuance())
    observed = ledger.append("observation", "observed", observation(original))
    with ledger._connection() as connection:
        connection.execute("DROP TRIGGER no_update")
        connection.execute(
            "UPDATE events SET issuance_id=? WHERE event_id=?", (other, observed)
        )
        # Restore the guard after the injected corruption. Otherwise the CLI's
        # normal schema setup recreates it and changes the SQLite file header.
        connection.execute(
            "CREATE TRIGGER no_update BEFORE UPDATE ON events "
            "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;"
        )
    retained_database = ledger.path.read_bytes()
    output = tmp_path / "retained.json"
    output.write_bytes(b"previous validated export\n")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.ledger",
            "--db",
            os.fspath(ledger.path),
            "export",
            "--output",
            os.fspath(output),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert process.returncode != 0
    assert "saved event issuance link does not reconcile" in process.stderr
    assert output.read_bytes() == b"previous validated export\n"
    assert ledger.path.read_bytes() == retained_database


@pytest.fixture
def record_clock(monkeypatch):
    """Only the recording clock is mocked; these are not actual issuances."""

    class Clock(datetime):
        current = datetime(2026, 1, 15, 12, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr("src.ledger.datetime", Clock)
    return Clock


def _real_clock_fixture(ledger, clock, classification):
    record = issuance()
    target, available = (
        ("2026-02", "2026-01")
        if classification == "forecast"
        else ("2026-01", "2025-12")
    )
    record.update(
        evidence_kind="real_issuance",
        classification=classification,
        target_month=target,
        issued_at=clock.current.isoformat(),
    )
    record["vintage"].update(
        kind="latest_vintage_snapshot", available_through=available
    )
    record["artifact"]["training_cutoff"] = available
    record["source"]["publication_verified"] = True
    issued = ledger.append("issuance", "inert-real-clock-fixture", record)
    clock.current += timedelta(days=5)
    return issued, target


def _clock_outcome(issued, target, published, *, vintage="first_release"):
    payload = observation(issued, vintage=vintage)
    payload["target_month"] = target
    payload["source"].update(
        published_at=published, retrieved_at="2026-01-20T12:00:00+00:00"
    )
    return payload


def _availability_fixture(clock, *, horizon=1):
    """Declared capture fields are synthetic controls in an owned database."""
    record = issuance()
    record.update(
        evidence_kind="real_issuance",
        target_month="2026-02" if horizon == 1 else "2026-04",
        issued_at=clock.current.isoformat(),
        horizon=horizon,
    )
    record["vintage"].update(
        kind="latest_vintage_snapshot", available_through="2026-01"
    )
    policy = record["artifact"]["policy"]
    if horizon == 3:
        policy.update(
            family="last_observation_h3",
            horizon=3,
            feature_schema="bts-last-observation-h3-v1",
            model_id=digest({"family": "last_observation_h3", "version": 1}),
        )
    record["artifact"].update(
        **policy, policy_id=digest(policy), training_cutoff="2026-01"
    )
    captured = "2026-01-14T12:00:00+00:00"
    url = (
        "https://data.transportation.gov/resource/bw6n-ddqk.csv"
        "?$select=obs_date,tsi_freight&$order=obs_date&$limit=50000"
    )
    record["source"].update(
        url=url,
        published_at=None,
        retrieved_at=captured,
        publication_verified=False,
        publication_basis="Synthetic availability fixture, not source evidence",
        availability_receipt={
            "schema_version": 1,
            "kind": "available_by_capture",
            "source_url": url,
            "retrieved_at": captured,
            "available_by": captured,
            "sha256": record["vintage"]["sha256"],
            "history_id": record["vintage"]["history_id"],
            "dataset_id": "bw6n-ddqk",
            "field": "tsi_freight",
            "last_month": "2026-01",
            "http_status": 200,
        },
    )
    return record


def test_input_availability_receipt_retains_unknown_publication_and_exact_retry(
    tmp_path, record_clock
):
    ledger = Ledger(tmp_path / "availability-fixture.sqlite")
    record = _availability_fixture(record_clock)
    first = ledger.append("issuance", "owned-capture-fixture", record)
    assert (
        Ledger(ledger.path).append(
            "issuance", "owned-capture-fixture", deepcopy(record)
        )
        == first
    )
    saved = ledger.events()[0]["payload"]
    assert saved["source"]["published_at"] is None
    assert saved["source"]["publication_verified"] is False
    assert ledger.view()["summaries"]["real_issuance"]["scored_targets"] == 0
    changed = deepcopy(record)
    changed["prediction"] = changed["last_observation"] = 101
    with pytest.raises(ValueError, match="incompatible contents"):
        ledger.append("issuance", "owned-capture-fixture", changed)
    assert len(ledger.events()) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("kind", "first_release"),
        ("http_status", True),
        ("http_status", 201),
        ("dataset_id", "other"),
        ("field", "other"),
        ("source_url", "https://example.invalid/other"),
        ("sha256", "0" * 64),
        ("history_id", "0" * 64),
        ("last_month", "2026-02"),
        ("retrieved_at", "2026-01-14T13:00:00+00:00"),
        ("available_by", "2026-01-14T13:00:00+00:00"),
        ("available_by", "2026-01-14T11:00:00+00:00"),
    ],
)
def test_invalid_availability_fields_refuse_before_event_commit(
    tmp_path, record_clock, field, value
):
    ledger = Ledger(tmp_path / "invalid-capture.sqlite")
    record = _availability_fixture(record_clock)
    record["source"]["availability_receipt"][field] = value
    with pytest.raises(ValueError):
        ledger.append("issuance", "invalid", record)
    assert ledger.events() == []


@pytest.mark.parametrize(
    "change",
    [
        lambda source: source.update(published_at="2026-01-14T12:00:00+00:00"),
        lambda source: source.update(publication_verified=True),
        lambda source: source.update(retrieved_at="2026-01-16T12:00:00+00:00"),
    ],
)
def test_availability_cannot_invent_publication_or_future_capture(
    tmp_path, record_clock, change
):
    ledger = Ledger(tmp_path / "clock-refusal.sqlite")
    record = _availability_fixture(record_clock)
    change(record["source"])
    with pytest.raises(ValueError):
        ledger.append("issuance", "invalid", record)
    assert ledger.events() == []


@pytest.mark.parametrize(
    "family,schema",
    [
        ("last_observation", "bts-last-observation-v1"),
        ("monthly_refit_ridge", FEATURE_SCHEMA),
    ],
)
def test_three_step_does_not_expand_old_feature_contracts(
    tmp_path, record_clock, family, schema
):
    ledger = Ledger(tmp_path / "unsupported-h3.sqlite")
    record = _availability_fixture(record_clock, horizon=3)
    policy = record["artifact"]["policy"]
    policy.update(family=family, feature_schema=schema)
    record["artifact"].update(feature_schema=schema, policy_id=digest(policy))
    with pytest.raises(ValueError, match="unsupported artifact feature contract"):
        ledger.append("issuance", "wrong-family", record)
    assert ledger.events() == []


def test_supported_horizons_have_separate_policy_cohorts(tmp_path, record_clock):
    ledger = Ledger(tmp_path / "separate-horizons.sqlite")
    one = ledger.append("issuance", "one-step", _availability_fixture(record_clock))
    three = ledger.append(
        "issuance", "three-step", _availability_fixture(record_clock, horizon=3)
    )
    assert one != three
    record_clock.current = datetime(2026, 3, 3, 12, tzinfo=UTC)
    outcome = observation(one)
    outcome["target_month"] = "2026-02"
    ledger.append("observation", "one-step-outcome", outcome)
    view = ledger.view()
    assert len(view["cohorts"]) == 2
    assert sorted(cohort["scored_targets"] for cohort in view["cohorts"]) == [0, 1]
    assert len({cohort["policy_id"] for cohort in view["cohorts"]}) == 2


def test_three_step_ledger_refuses_wrong_target_and_elapsed_classification(
    tmp_path, record_clock
):
    ledger = Ledger(tmp_path / "wrong-target.sqlite")
    record = _availability_fixture(record_clock, horizon=3)
    record["target_month"] = "2026-05"
    with pytest.raises(ValueError, match="declared observation horizon"):
        ledger.append("issuance", "wrong-calendar", record)
    record_clock.current = datetime(2026, 4, 15, 12, tzinfo=UTC)
    record = _availability_fixture(record_clock, horizon=3)
    record["classification"] = "elapsed_period_nowcast"
    with pytest.raises(ValueError, match="three-step outlook requires a future"):
        ledger.append("issuance", "nowcast", record)
    assert ledger.events() == []


def test_official_equivalent_capture_redirect_remains_supported(tmp_path, record_clock):
    ledger = Ledger(tmp_path / "official-redirect.sqlite")
    record = _availability_fixture(record_clock)
    record["source"]["availability_receipt"]["resolved_source_url"] = (
        "https://data.bts.gov:443/api/id/bw6n-ddqk.csv"
        "?%24limit=50000&%24order=obs_date&%24select=obs_date%2Ctsi_freight"
    )
    ledger.append("issuance", "official-redirect-fixture", record)
    assert len(ledger.events()) == 1


def test_availability_receipt_is_not_an_outcome_publication_receipt(
    tmp_path, record_clock
):
    ledger = Ledger(tmp_path / "outcome-refusal.sqlite")
    record = _availability_fixture(record_clock)
    issued = ledger.append("issuance", "input", record)
    record_clock.current = datetime(2026, 3, 3, 12, tzinfo=UTC)
    payload = observation(issued)
    payload["target_month"] = record["target_month"]
    payload["source"]["availability_receipt"] = record["source"]["availability_receipt"]
    before = ledger.events()
    with pytest.raises(ValueError, match="cannot establish outcome publication"):
        ledger.append("observation", "unknown-publication", payload)
    assert ledger.events() == before


def test_readback_refuses_rehashed_bad_availability_join(tmp_path, record_clock):
    ledger = Ledger(tmp_path / "bad-restored-input.sqlite")
    record = _availability_fixture(record_clock, horizon=3)
    record["issued_at"] = "2026-03-02T12:00:00+00:00"
    record["source"]["availability_receipt"]["sha256"] = "0" * 64
    key = "bad-restored-input"
    expected = digest({"kind": "issuance", "key": key, "payload_id": digest(record)})
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=1,
            key=key,
            kind="issuance",
            payload=record,
            issuance_id=expected,
        )
    with pytest.raises(
        ValueError, match="saved availability receipt does not match captured vintage"
    ):
        ledger.view()


def test_readback_refuses_availability_as_outcome_timing(tmp_path, record_clock):
    ledger = Ledger(tmp_path / "bad-restored-outcome.sqlite")
    record = _availability_fixture(record_clock)
    issued = ledger.append("issuance", "input", record)
    payload = observation(issued)
    payload["target_month"] = record["target_month"]
    payload["source"]["availability_receipt"] = record["source"]["availability_receipt"]
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=2,
            key="bad-outcome",
            kind="observation",
            payload=payload,
            issuance_id=issued,
        )
    with pytest.raises(
        ValueError,
        match="saved availability receipt cannot establish outcome publication",
    ):
        ledger.view()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/resource/bw6n-ddqk.csv?$select=obs_date,tsi_freight&$order=obs_date&$limit=50000",
        "https://data.transportation.gov/resource/other.csv?$select=obs_date,tsi_freight&$order=obs_date&$limit=50000",
        "https://data.transportation.gov/resource/bw6n-ddqk.csv",
        "https://name@data.transportation.gov/resource/bw6n-ddqk.csv?$select=obs_date,tsi_freight&$order=obs_date&$limit=50000",
        "https://data.transportation.gov:8443/resource/bw6n-ddqk.csv?$select=obs_date,tsi_freight&$order=obs_date&$limit=50000",
    ],
)
@pytest.mark.parametrize("redirect", [False, True])
def test_availability_source_and_redirect_are_confined_to_official_projection(
    tmp_path, record_clock, url, redirect
):
    ledger = Ledger(tmp_path / "unofficial-capture.sqlite")
    record = _availability_fixture(record_clock)
    receipt = record["source"]["availability_receipt"]
    if redirect:
        receipt["resolved_source_url"] = url
    else:
        record["source"]["url"] = receipt["source_url"] = url
    with pytest.raises(ValueError, match="supported official CSV request"):
        ledger.append("issuance", "unofficial", record)
    assert ledger.events() == []


@pytest.mark.parametrize("classification", ["forecast", "elapsed_period_nowcast"])
@pytest.mark.parametrize("vintage", ["first_release", "latest_snapshot"])
@pytest.mark.parametrize(
    "published",
    ["2026-01-14T12:00:00+00:00", "2026-01-15T06:00:00-06:00"],
)
def test_real_outcome_cannot_be_published_before_or_at_issuance(
    tmp_path, record_clock, classification, vintage, published
):
    ledger = Ledger(tmp_path / "chronology.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, classification)
    before = ledger.events()
    with pytest.raises(ValueError, match="real outcome publication must follow"):
        ledger.append(
            "observation",
            "not-later",
            _clock_outcome(issued, target, published, vintage=vintage),
        )
    assert ledger.events() == before
    assert ledger.view()["summaries"]["real_issuance"]["scored_targets"] == 0


@pytest.mark.parametrize("classification", ["forecast", "elapsed_period_nowcast"])
@pytest.mark.parametrize("vintage", ["first_release", "latest_snapshot"])
def test_real_later_publication_is_scored_and_retries_preserve_identity(
    tmp_path, record_clock, classification, vintage
):
    ledger = Ledger(tmp_path / "later.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, classification)
    payload = _clock_outcome(
        issued, target, "2026-01-15T06:00:00.000001-06:00", vintage=vintage
    )
    observed = ledger.append("observation", "later", payload)
    assert ledger.append("observation", "later", payload) == observed
    policy = "first_release" if vintage == "first_release" else "latest_available"
    view = Ledger(ledger.path).view(outcome_policy=policy)
    assert len(view["events"]) == 2
    assert view["summaries"]["real_issuance"]["scored_targets"] == 1
    assert view["cohorts"][0]["mae"]["prediction"] == 30


@pytest.mark.parametrize("classification", ["forecast", "elapsed_period_nowcast"])
@pytest.mark.parametrize("vintage", ["first_release", "latest_snapshot"])
def test_restore_cannot_score_real_outcome_known_at_issuance(
    tmp_path, record_clock, classification, vintage
):
    ledger = Ledger(tmp_path / "restored-clock.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, classification)
    payload = _clock_outcome(
        issued, target, "2026-01-15T06:00:00-06:00", vintage=vintage
    )
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=2,
            key="restored-not-later",
            kind="observation",
            payload=payload,
            issuance_id=issued,
        )
    for read in (ledger.events, ledger.view):
        with pytest.raises(ValueError, match="saved real outcome publication"):
            read()


def test_real_revision_uses_issuance_and_predecessor_publication_boundaries(
    tmp_path, record_clock
):
    ledger = Ledger(tmp_path / "revision-clock.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, "forecast")
    first = ledger.append(
        "observation",
        "first",
        _clock_outcome(issued, target, "2026-01-16T12:00:00+00:00"),
    )
    revision = _clock_outcome(
        issued, target, "2026-01-14T12:00:00+00:00", vintage="revision"
    )
    revision["supersedes_event_id"] = first
    with pytest.raises(ValueError, match="real outcome publication must follow"):
        ledger.append("observation", "old-revision", revision)
    revision["source"]["published_at"] = "2026-01-15T12:00:01+00:00"
    with pytest.raises(ValueError, match="revision publication precedes"):
        ledger.append("observation", "before-first", revision)
    revision["source"]["published_at"] = "2026-01-17T12:00:00+00:00"
    ledger.append("observation", "later-revision", revision)
    assert (
        ledger.view(outcome_policy="latest_available")["rows"][0]["status"]
        == "observed"
    )


def test_restored_real_revision_keeps_issuance_chronology(tmp_path, record_clock):
    ledger = Ledger(tmp_path / "restored-revision-clock.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, "forecast")
    first = ledger.append(
        "observation",
        "first",
        _clock_outcome(issued, target, "2026-01-16T12:00:00+00:00"),
    )
    revision = _clock_outcome(
        issued, target, "2026-01-14T12:00:00+00:00", vintage="revision"
    )
    revision["supersedes_event_id"] = first
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=3,
            key="restored-old-revision",
            kind="observation",
            payload=revision,
            issuance_id=issued,
        )
    with pytest.raises(ValueError, match="saved real outcome publication"):
        ledger.view(outcome_policy="latest_available")


@pytest.mark.parametrize("classification", ["forecast", "elapsed_period_nowcast"])
def test_real_unavailable_and_corrections_remain_distinct(
    tmp_path, record_clock, classification
):
    ledger = Ledger(tmp_path / "missing-clock.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, classification)
    missing = _clock_outcome(issued, target, None, vintage="unavailable")
    missing.update(value=None, sha256=None, reason="Inert missing release")
    missing["source"]["publication_verified"] = False
    ledger.append("observation", "missing", missing)
    observed = ledger.append(
        "observation",
        "later",
        _clock_outcome(issued, target, "2026-01-16T12:00:00+00:00"),
    )
    ledger.append(
        "correction",
        "exclude",
        {
            "issuance_id": issued,
            "target_event_id": observed,
            "action": "exclude_from_evaluation",
            "reason": "Inert correction",
        },
    )
    view = ledger.view()
    assert len(view["events"]) == 4
    assert view["summaries"]["real_issuance"]["scored_targets"] == 0
    assert view["rows"][0]["status"] == "unobserved"


@pytest.mark.parametrize("kind", ["synthetic_fixture", "retrospective_replay"])
def test_nonreal_records_preserve_earlier_outcome_histories(
    tmp_path, record_clock, kind
):
    ledger = Ledger(tmp_path / "nonreal.sqlite")
    record = issuance()
    if kind == "retrospective_replay":
        record.update(evidence_kind=kind, classification=kind)
        record["vintage"]["kind"] = "latest_vintage_snapshot"
    issued = ledger.append("issuance", "nonreal", record)
    payload = observation(issued)
    payload["source"].update(
        published_at="2026-01-01T12:00:00+00:00",
        retrieved_at="2026-01-02T12:00:00+00:00",
    )
    ledger.append("observation", "earlier", payload)
    view = Ledger(ledger.path).view()
    assert view["summaries"][kind]["scored_targets"] == 1
    assert view["summaries"]["real_issuance"]["issuances"] == 0


def test_cli_export_rejects_restored_real_chronology_without_replacing_output(
    tmp_path, record_clock
):
    ledger = Ledger(tmp_path / "cli-clock.sqlite")
    issued, target = _real_clock_fixture(ledger, record_clock, "forecast")
    payload = _clock_outcome(issued, target, "2026-01-14T12:00:00+00:00")
    with ledger._connection() as connection:
        _restore_event(
            connection,
            seq=2,
            key="restored-old-outcome",
            kind="observation",
            payload=payload,
            issuance_id=issued,
        )
    retained = ledger.path.read_bytes()
    output = tmp_path / "completed.json"
    output.write_bytes(b"previous validated export\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.ledger",
            "--db",
            os.fspath(ledger.path),
            "export",
            "--output",
            os.fspath(output),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert "saved real outcome publication must follow issuance" in result.stderr
    assert output.read_bytes() == b"previous validated export\n"
    assert ledger.path.read_bytes() == retained
