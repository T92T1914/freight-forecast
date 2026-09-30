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
