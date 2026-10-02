"""Exercise retention and reconciliation independently of model promotion."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

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
