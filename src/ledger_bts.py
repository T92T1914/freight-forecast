"""Build declared BTS point records from the existing validated snapshot path."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.ledger import (
    SERIES,
    UNITS,
    Ledger,
    _month,
    _shift_month,
    _time,
    validate_issuance,
)
from src.provenance import digest, history_rows
from src.real_backtest import load_snapshot


def prepare_record(
    data_path,
    manifest_path,
    *,
    evidence_kind,
    publication=None,
    availability=None,
    horizon=1,
):
    return _prepare_record(
        data_path,
        manifest_path,
        evidence_kind=evidence_kind,
        publication=publication,
        availability=availability,
        horizon=horizon,
    )


def _prepare_record(
    data_path,
    manifest_path,
    *,
    evidence_kind,
    publication=None,
    availability=None,
    horizon=1,
    retry_clock=None,
):
    if type(horizon) is not int or horizon not in (1, 3):
        raise ValueError(
            "BTS adapter supports only one-step or declared three-step points"
        )
    if publication is not None and availability is not None:
        raise ValueError("choose publication or availability evidence, not both")
    if horizon == 3 and evidence_kind != "real_issuance":
        raise ValueError("three-step outlook supports only a real future issuance")
    history, manifest = load_snapshot(Path(data_path), Path(manifest_path))
    clock = datetime.now(UTC) if retry_clock is None else retry_clock
    available = history["date"].iloc[-1].strftime("%Y-%m")
    target = _shift_month(available, horizon)
    if horizon == 3 and _month(target) <= clock:
        raise ValueError("three-step outlook requires a future forecast target")
    comparator_month = _shift_month(target, -12)
    comparator = history.loc[
        history["date"].dt.strftime("%Y-%m") == comparator_month, "volume"
    ]
    if len(comparator) != 1:
        raise ValueError("snapshot lacks the target month's prior-year comparator")
    family = "last_observation" if horizon == 1 else "last_observation_h3"
    policy = {
        "family": family,
        "series": SERIES,
        "units": UNITS,
        "horizon": horizon,
        "feature_schema": (
            "bts-last-observation-v1" if horizon == 1 else "bts-last-observation-h3-v1"
        ),
        "model_id": digest({"family": family, "version": 1}),
    }
    publication = publication or {
        "published_at": None,
        "publication_verified": False,
        "publication_basis": (
            "Retained latest-vintage snapshot has no exact publication receipt"
        ),
    }
    if not isinstance(publication, dict) or set(publication) != {
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
    record = {
        "schema_version": 1,
        "evidence_kind": evidence_kind,
        "classification": classification,
        "series": SERIES,
        "units": UNITS,
        "target_month": target,
        "issued_at": clock.isoformat(),
        "issue_timezone": "UTC",
        "horizon": horizon,
        "prediction": float(history["volume"].iloc[-1]),
        "last_observation": float(history["volume"].iloc[-1]),
        "seasonal_naive": float(comparator.iloc[0]),
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
    if availability is not None:
        record["source"]["publication_basis"] = (
            "Official response bytes were available by capture completion; "
            "exact first publication time is unknown"
        )
        record["source"]["availability_receipt"] = availability
    # The old unverified one-step record remains inspectable before admission.
    if availability is not None or horizon == 3:
        validate_issuance(record, clock)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    evidence = parser.add_mutually_exclusive_group()
    evidence.add_argument("--publication-record", type=Path)
    evidence.add_argument("--availability-record", type=Path)
    parser.add_argument("--horizon", type=int, choices=(1, 3), default=1)
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
    availability = (
        json.loads(args.availability_record.read_bytes())
        if args.availability_record
        else None
    )
    ledger = Ledger(args.db) if args.db.exists() else None
    previous = (
        next(
            (
                event
                for event in ledger.events()
                if event["idempotency_key"] == args.key
            ),
            None,
        )
        if ledger is not None
        else None
    )
    retry = previous is not None and previous["kind"] == "issuance"
    record = _prepare_record(
        args.data,
        args.manifest,
        evidence_kind=args.kind,
        publication=publication,
        availability=availability,
        horizon=args.horizon,
        retry_clock=_time(previous["payload"]["issued_at"]) if retry else None,
    )
    ledger = ledger or Ledger(args.db)
    if retry:
        # A retry preserves the first issuance clock, not a newly generated time.
        record["issued_at"] = previous["payload"]["issued_at"]
        record["classification"] = previous["payload"]["classification"]
    print(ledger.append("issuance", args.key, record))


if __name__ == "__main__":
    main()
