"""Create a small synthetic ledger demonstration in a disposable local store."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ledger import SERIES, UNITS, Ledger  # noqa: E402
from src.provenance import digest  # noqa: E402


def fixture_record(month):
    available = {"2026-03": "2026-02", "2026-04": "2026-03", "2026-05": "2026-04"}[
        month
    ]
    policy = {
        "family": "last_observation",
        "series": SERIES,
        "units": UNITS,
        "horizon": 1,
        "feature_schema": "bts-last-observation-v1",
        "model_id": digest({"fixture": "last_observation"}),
    }
    return {
        "schema_version": 1,
        "evidence_kind": "synthetic_fixture",
        "classification": "elapsed_period_nowcast",
        "series": SERIES,
        "units": UNITS,
        "target_month": month,
        "issued_at": "2026-06-01T12:00:00+00:00",
        "issue_timezone": "UTC",
        "horizon": 1,
        "prediction": 100.0,
        "last_observation": 100.0,
        "seasonal_naive": 90.0,
        "uncertainty": None,
        "source": {
            "url": "https://example.invalid/synthetic-fixture",
            "published_at": "2026-05-01T12:00:00+00:00",
            "retrieved_at": "2026-06-01T10:00:00+00:00",
            "publication_verified": False,
            "publication_basis": (
                "Invented test values and clocks, not a BTS publication"
            ),
        },
        "vintage": {
            "sha256": digest({"fixture_month": month}),
            "history_id": digest({"fixture": month}),
            "kind": "synthetic_fixture",
            "available_through": available,
        },
        "artifact": {
            **policy,
            "policy": policy,
            "policy_id": digest(policy),
            "training_cutoff": available,
        },
    }


def make_fixture(ledger):
    for month in ("2026-03", "2026-04", "2026-05"):
        issued = ledger.append(
            "issuance", "fixture-issuance-" + month, fixture_record(month)
        )
        observation = {
            "issuance_id": issued,
            "series": SERIES,
            "units": UNITS,
            "target_month": month,
            "vintage_kind": "first_release",
            "value": 130.0,
            "sha256": digest({"fixture_value": 130}),
            "supersedes_event_id": None,
            "source": {
                "url": "https://example.invalid/synthetic-fixture",
                "published_at": "2026-06-05T12:00:00+00:00",
                "retrieved_at": "2026-06-06T12:00:00+00:00",
                "publication_verified": True,
                "publication_basis": (
                    "Synthetic clock fixture, not a verified real release"
                ),
            },
        }
        if month == "2026-04":
            observation.update(
                vintage_kind="unavailable",
                value=None,
                sha256=None,
                reason="Synthetic delayed release",
            )
            observation["source"].update(published_at=None, publication_verified=False)
        observed = ledger.append("observation", "fixture-outcome-" + month, observation)
        if month == "2026-03":
            observation.update(
                vintage_kind="revision",
                value=135.0,
                sha256=digest({"fixture_value": 135}),
                supersedes_event_id=observed,
            )
            observation["source"].update(
                published_at="2026-07-05T12:00:00+00:00",
                retrieved_at="2026-07-06T12:00:00+00:00",
            )
            ledger.append("observation", "fixture-revision-march", observation)
        if month == "2026-05":
            ledger.append(
                "correction",
                "fixture-correction-may",
                {
                    "issuance_id": issued,
                    "target_event_id": observed,
                    "action": "exclude_from_evaluation",
                    "reason": "Synthetic incorrectly labeled first release",
                },
            )
    return ledger.view()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    view = make_fixture(Ledger(args.db))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(view, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
