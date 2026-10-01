"""Build one-step BTS point records from the existing validated snapshot path."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.ledger import SERIES, UNITS, Ledger, _month, _next_month
from src.provenance import digest, history_rows
from src.real_backtest import load_snapshot


def prepare_record(data_path, manifest_path, *, evidence_kind, publication=None):
    history, manifest = load_snapshot(Path(data_path), Path(manifest_path))
    clock = datetime.now(UTC)
    available = history["date"].iloc[-1].strftime("%Y-%m")
    target = _next_month(available)
    policy = {
        "family": "last_observation",
        "series": SERIES,
        "units": UNITS,
        "horizon": 1,
        "feature_schema": "bts-last-observation-v1",
        "model_id": digest({"family": "last_observation", "version": 1}),
    }
    publication = publication or {
        "published_at": None,
        "publication_verified": False,
        "publication_basis": (
            "Retained latest-vintage snapshot has no exact publication receipt"
        ),
    }
    if set(publication) != {
        "published_at",
        "publication_verified",
        "publication_basis",
    }:
        raise ValueError("publication record must contain only the three timing fields")
    if evidence_kind not in ("real_issuance", "retrospective_replay"):
        raise ValueError("BTS adapter supports real issuance or an explicit replay")
    if evidence_kind == "retrospective_replay":
        classification = "retrospective_replay"
    else:
        classification = (
            "forecast" if _month(target) > clock else "elapsed_period_nowcast"
        )
    return {
        "schema_version": 1,
        "evidence_kind": evidence_kind,
        "classification": classification,
        "series": SERIES,
        "units": UNITS,
        "target_month": target,
        "issued_at": clock.isoformat(),
        "issue_timezone": "UTC",
        "horizon": 1,
        "prediction": float(history["volume"].iloc[-1]),
        "last_observation": float(history["volume"].iloc[-1]),
        "seasonal_naive": float(history["volume"].iloc[-12]),
        "uncertainty": None,
        "source": {
            "url": manifest["source_url"],
            "retrieved_at": manifest["retrieved_utc"],
            **publication,
        },
        "vintage": {
            # The loader verified this hash against the exact bytes it parsed.
            # A second path read could identify a newer file than the history.
            "sha256": manifest["sha256"],
            "history_id": digest(history_rows(history)),
            "kind": manifest["vintage_kind"],
            "available_through": available,
        },
        "artifact": {
            **policy,
            "policy": policy,
            "policy_id": digest(policy),
            "training_cutoff": available,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--publication-record", type=Path)
    parser.add_argument(
        "--kind", choices=("real_issuance", "retrospective_replay"), required=True
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    publication = (
        json.loads(args.publication_record.read_bytes())
        if args.publication_record
        else None
    )
    record = prepare_record(
        args.data, args.manifest, evidence_kind=args.kind, publication=publication
    )
    ledger = Ledger(args.db)
    previous = next(
        (event for event in ledger.events() if event["idempotency_key"] == args.key),
        None,
    )
    if previous is not None and previous["kind"] == "issuance":
        # A retry preserves the first issuance clock, not a newly generated time.
        record["issued_at"] = previous["payload"]["issued_at"]
        record["classification"] = previous["payload"]["classification"]
    print(ledger.append("issuance", args.key, record))


if __name__ == "__main__":
    main()
