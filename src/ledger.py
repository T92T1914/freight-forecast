"""Keep issuances and outcome vintages without changing serving promotion."""

import argparse
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.provenance import FEATURE_SCHEMA, digest

SERIES = "bts_freight_tsi"
UNITS = "index_points_2000_average_100"
SCHEMA = 1
MAX_RECORD_BYTES = 65536


def _text(value, label):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"invalid {label}")
    return value


def _hash(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"invalid {label}")
    return value


def _month(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("month must be YYYY-MM")
    return datetime.strptime(value, "%Y-%m").replace(tzinfo=UTC)


def _next_month(value):
    month = _month(value)
    year, number = month.year + (month.month == 12), month.month % 12 + 1
    return f"{year:04}-{number:02}"


def _time(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError("missing offset")
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamps require an explicit timezone offset") from exc


def _number(value):
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("index values must be finite positive numbers")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("index values must be finite positive numbers")


def _source(source, clock, *, required_publication):
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    url = _text(source.get("url"), "source URL")
    if not url.startswith("https://"):
        raise ValueError("source URL must use HTTPS")
    retrieved = _time(source.get("retrieved_at"))
    if retrieved > clock:
        raise ValueError("retrieval is after the record clock")
    published = source.get("published_at")
    if published is not None and _time(published) > retrieved:
        raise ValueError("source publication is after retrieval")
    if type(source.get("publication_verified")) is not bool:
        raise ValueError("publication verification must be explicit")
    _text(source.get("publication_basis"), "publication basis")
    if required_publication and (
        published is None or source["publication_verified"] is not True
    ):
        raise ValueError("real issuance requires verified source publication timing")


def validate_issuance(record, clock):
    """Validate product and information boundaries, never comparative MAE."""
    if (
        not isinstance(record, dict)
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != SCHEMA
    ):
        raise ValueError("unsupported issuance schema")
    if record.get("series") != SERIES or record.get("units") != UNITS:
        raise ValueError("ledger supports BTS index points, not shipment counts")
    kind = record.get("evidence_kind")
    if kind not in ("real_issuance", "synthetic_fixture", "retrospective_replay"):
        raise ValueError("unsupported evidence kind")
    classification = record.get("classification")
    if classification not in (
        "forecast",
        "elapsed_period_nowcast",
        "retrospective_replay",
    ):
        raise ValueError("unsupported product classification")
    if (kind == "retrospective_replay") != (classification == "retrospective_replay"):
        raise ValueError("replay must remain separately classified")
    issued = _time(record.get("issued_at"))
    target = _month(record.get("target_month"))
    if issued > clock:
        raise ValueError("issuance time is in the future")
    if kind == "real_issuance" and clock - issued > timedelta(minutes=5):
        raise ValueError("real issuance cannot be backdated")
    if classification == "forecast" and target <= issued.astimezone(UTC):
        raise ValueError("elapsed or current periods are not future forecasts")
    if classification == "elapsed_period_nowcast" and target > issued.astimezone(UTC):
        raise ValueError("nowcast target has not begun")
    _source(record.get("source"), issued, required_publication=kind == "real_issuance")
    vintage, artifact = record.get("vintage"), record.get("artifact")
    if not isinstance(vintage, dict) or not isinstance(artifact, dict):
        raise ValueError("vintage and artifact descriptors are required")
    for name in ("sha256", "history_id"):
        _hash(vintage.get(name), "vintage " + name)
    if vintage.get("kind") not in (
        "latest_vintage_snapshot",
        "as_published_snapshot",
        "synthetic_fixture",
    ):
        raise ValueError("unsupported input vintage")
    if (kind == "synthetic_fixture") != (vintage["kind"] == "synthetic_fixture"):
        raise ValueError("fixture vintage must remain explicitly synthetic")
    available = vintage.get("available_through")
    _month(available)
    if kind == "real_issuance" and _month(available) > issued:
        raise ValueError("available observations cannot be future periods")
    cutoff = artifact.get("training_cutoff")
    _month(cutoff)
    if cutoff > available or record["target_month"] != _next_month(available):
        raise ValueError("one-step policy requires the immediately preceding month")
    if artifact.get("series") != SERIES or artifact.get("units") != UNITS:
        raise ValueError("artifact series or units do not match")
    if type(record.get("horizon")) is not int or record["horizon"] != 1:
        raise ValueError("only the existing one-step contract is supported")
    if type(artifact.get("horizon")) is not int or artifact["horizon"] != 1:
        raise ValueError("artifact horizon does not match")
    for name in ("model_id", "policy_id"):
        _hash(artifact.get(name), name)
    _text(artifact.get("feature_schema"), "feature schema")
    policy = artifact.get("policy")
    if not isinstance(policy, dict) or digest(policy) != artifact["policy_id"]:
        raise ValueError("policy identity differs from its declaration")
    for name in ("series", "units", "horizon", "feature_schema", "model_id"):
        if policy.get(name) != artifact.get(name):
            raise ValueError("artifact is incompatible with the declared policy")
    supported = {
        "last_observation": "bts-last-observation-v1",
        "monthly_refit_ridge": FEATURE_SCHEMA,
    }
    if supported.get(policy.get("family")) != artifact["feature_schema"]:
        raise ValueError("unsupported artifact feature contract")
    if record.get("uncertainty") is not None:
        raise ValueError("this point policy supplies no uncertainty method")
    for name in ("prediction", "last_observation", "seasonal_naive"):
        _number(record.get(name))
    if (
        policy["family"] == "last_observation"
        and record["prediction"] != record["last_observation"]
    ):
        raise ValueError("last-observation policy must retain its actual prediction")
    _text(record.get("issue_timezone"), "issue timezone convention")


class Ledger:
    """One local SQLite writer at a time, with append-only event records."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    version INTEGER NOT NULL CHECK(version=1)
                );
                INSERT INTO metadata SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM metadata);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL
                        CHECK(kind IN ('issuance','observation','correction')),
                    issuance_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                """
            )
            if connection.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
                raise ValueError("unsupported ledger schema")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            yield connection
        finally:
            connection.close()

    def append(self, kind, key, payload):
        """Return the original event on exact retry, reject conflicting retries."""
        _text(key, "idempotency key")
        if kind not in ("issuance", "observation", "correction"):
            raise ValueError("unknown event kind")
        if not isinstance(payload, dict):
            raise ValueError("record must be an object")
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(encoded.encode()) > MAX_RECORD_BYTES:
            raise ValueError("record exceeds 64 KiB")
        # Capture an owned JSON copy before entering the transaction.
        payload = json.loads(encoded)
        payload_id = digest(payload)
        event_id = digest({"kind": kind, "key": key, "payload_id": payload_id})
        clock = datetime.now(UTC)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT event_id, kind, payload_id FROM events "
                    "WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
                if previous:
                    if previous[1:] != (kind, payload_id):
                        raise ValueError("idempotency key has incompatible contents")
                    connection.commit()
                    return previous[0]
                if kind == "issuance":
                    validate_issuance(payload, clock)
                    issuance_id = event_id
                else:
                    issuance_id = payload.get("issuance_id")
                    original = connection.execute(
                        "SELECT payload FROM events "
                        "WHERE event_id=? AND kind='issuance'",
                        (issuance_id,),
                    ).fetchone()
                    if original is None:
                        raise ValueError("unknown original issuance")
                    issuance = json.loads(original[0])
                    self._validate_linked(connection, kind, payload, issuance, clock)
                connection.execute(
                    "INSERT INTO events VALUES(NULL,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        key,
                        kind,
                        issuance_id,
                        encoded,
                        payload_id,
                        clock.isoformat(),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return event_id

    @staticmethod
    def _validate_linked(connection, kind, payload, issuance, clock):
        if not isinstance(payload, dict):
            raise ValueError("linked record must be an object")
        if kind == "correction":
            if payload.get("action") != "exclude_from_evaluation":
                raise ValueError("correction must explicitly exclude a record")
            _text(payload.get("reason"), "correction reason")
            target = connection.execute(
                "SELECT kind, issuance_id FROM events WHERE event_id=?",
                (payload.get("target_event_id"),),
            ).fetchone()
            if (
                target is None
                or target[0] == "correction"
                or target[1] != payload["issuance_id"]
            ):
                raise ValueError("correction target does not match the issuance")
            return
        for name in ("series", "units", "target_month"):
            if payload.get(name) != issuance[name]:
                raise ValueError("observation does not reconcile with issuance")
        vintage = payload.get("vintage_kind")
        if vintage not in (
            "first_release",
            "revision",
            "latest_snapshot",
            "unavailable",
        ):
            raise ValueError("unknown observation vintage")
        _source(
            payload.get("source"), clock, required_publication=vintage != "unavailable"
        )
        if vintage == "unavailable":
            if payload.get("value") is not None or payload.get("sha256") is not None:
                raise ValueError("unavailable release cannot contain an invented value")
            _text(payload.get("reason"), "unavailability reason")
            return
        _number(payload.get("value"))
        _hash(payload.get("sha256"), "outcome SHA256")
        earlier = connection.execute(
            "SELECT event_id, payload FROM events "
            "WHERE issuance_id=? AND kind='observation'",
            (payload["issuance_id"],),
        ).fetchall()
        excluded = {
            json.loads(row[0])["target_event_id"]
            for row in connection.execute(
                "SELECT payload FROM events WHERE issuance_id=? AND kind='correction'",
                (payload["issuance_id"],),
            ).fetchall()
        }
        Ledger._validate_observation_history(
            payload, [(row[0], json.loads(row[1])) for row in earlier], excluded
        )

    @staticmethod
    def _validate_observation_history(payload, earlier, excluded):
        """Keep first-release and revision links consistent on append and restore."""
        vintage = payload.get("vintage_kind")
        predecessor = payload.get("supersedes_event_id")
        if vintage == "first_release":
            firsts = [
                event_id
                for event_id, old in earlier
                if old["vintage_kind"] == "first_release"
            ]
            if any(event_id not in excluded for event_id in firsts):
                raise ValueError("first release is already recorded")
            if firsts and predecessor not in set(firsts) & excluded:
                raise ValueError(
                    "corrected first release must reference excluded predecessor"
                )
            if not firsts and predecessor is not None:
                raise ValueError("first release does not replace a later vintage")
        if vintage == "revision":
            old = next(
                (old for event_id, old in earlier if event_id == predecessor), None
            )
            if old is None or old["vintage_kind"] == "unavailable":
                raise ValueError("revision requires an existing observed predecessor")
            if _time(payload["source"]["published_at"]) < _time(
                old["source"]["published_at"]
            ):
                raise ValueError("revision publication precedes its predecessor")

    def events(self):
        """Read a consistent snapshot and verify identities and earlier links."""
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY seq").fetchall()
        result, earlier = [], {}
        for (
            seq,
            event_id,
            key,
            kind,
            issuance_id,
            encoded,
            payload_id,
            recorded,
        ) in rows:
            payload = json.loads(encoded)
            if (
                digest(payload) != payload_id
                or digest({"kind": kind, "key": key, "payload_id": payload_id})
                != event_id
            ):
                raise ValueError("saved event identity does not reconcile")
            if not isinstance(payload, dict):
                raise ValueError("saved event payload must be an object")
            if kind == "issuance":
                expected_issuance = event_id
            else:
                expected_issuance = payload.get("issuance_id")
                _hash(expected_issuance, "saved original issuance")
                original = earlier.get(expected_issuance)
                if original is None or original["kind"] != "issuance":
                    raise ValueError("saved event has no earlier original issuance")
                if kind == "observation":
                    for name in ("series", "units", "target_month"):
                        if payload.get(name) != original["payload"][name]:
                            raise ValueError("saved observation does not reconcile")
                    vintage = payload.get("vintage_kind")
                    predecessor_id = payload.get("supersedes_event_id")
                    if vintage == "revision" or (
                        vintage == "first_release" and predecessor_id is not None
                    ):
                        _hash(predecessor_id, "saved revision predecessor")
                        predecessor = earlier.get(predecessor_id)
                        if (
                            predecessor is None
                            or predecessor["kind"] != "observation"
                            or predecessor["issuance_id"] != expected_issuance
                            or predecessor["payload"]["vintage_kind"] == "unavailable"
                        ):
                            raise ValueError(
                                "saved revision predecessor does not match"
                            )
                    linked_observations = [
                        (item["event_id"], item["payload"])
                        for item in earlier.values()
                        if item["kind"] == "observation"
                        and item["issuance_id"] == expected_issuance
                    ]
                    excluded = {
                        item["payload"]["target_event_id"]
                        for item in earlier.values()
                        if item["kind"] == "correction"
                        and item["issuance_id"] == expected_issuance
                    }
                    try:
                        self._validate_observation_history(
                            payload, linked_observations, excluded
                        )
                    except ValueError as error:
                        raise ValueError(f"saved {error}") from error
                elif kind == "correction":
                    target_id = payload.get("target_event_id")
                    _hash(target_id, "saved correction target")
                    target = earlier.get(target_id)
                    if (
                        target is None
                        or target["kind"] == "correction"
                        or target["issuance_id"] != expected_issuance
                        or payload.get("action") != "exclude_from_evaluation"
                    ):
                        raise ValueError("saved correction target does not match")
            if issuance_id != expected_issuance:
                raise ValueError("saved event issuance link does not reconcile")
            result.append(
                {
                    "seq": seq,
                    "event_id": event_id,
                    "idempotency_key": key,
                    "kind": kind,
                    "issuance_id": issuance_id,
                    "payload": payload,
                    "payload_id": payload_id,
                    "recorded_at": recorded,
                }
            )
            earlier[event_id] = result[-1]
        return result

    def view(self, *, outcome_policy="first_release"):
        """Evaluate earliest valid issuance per series, policy and target month."""
        if outcome_policy not in ("first_release", "latest_available"):
            raise ValueError("declare first_release or latest_available outcome policy")
        events = self.events()
        excluded = {
            event["payload"]["target_event_id"]
            for event in events
            if event["kind"] == "correction"
        }
        rows, seen = [], set()
        for event in events:
            if event["kind"] != "issuance":
                continue
            issued = event["payload"]
            grouping = tuple(
                issued[name]
                for name in (
                    "series",
                    "units",
                    "target_month",
                    "evidence_kind",
                    "classification",
                )
            )
            grouping += (issued["artifact"]["policy_id"],)
            status = "excluded" if event["event_id"] in excluded else "unobserved"
            if status != "excluded" and grouping in seen:
                status = "repeat_issuance"
            if status not in ("excluded", "repeat_issuance"):
                seen.add(grouping)
            candidates = [
                observation
                for observation in events
                if observation["kind"] == "observation"
                and observation["issuance_id"] == event["event_id"]
                and observation["event_id"] not in excluded
                and observation["payload"]["vintage_kind"] != "unavailable"
            ]
            if outcome_policy == "first_release":
                candidates = [
                    item
                    for item in candidates
                    if item["payload"]["vintage_kind"] == "first_release"
                ]
            outcome = max(
                candidates,
                key=lambda item: (
                    _time(item["payload"]["source"]["published_at"]),
                    item["seq"],
                ),
                default=None,
            )
            if outcome is not None and status == "unobserved":
                status = "observed"
            rows.append(
                {
                    "issuance_id": event["event_id"],
                    "issuance": issued,
                    "status": status,
                    "outcome": outcome,
                }
            )
        summaries = {}
        for kind in ("real_issuance", "synthetic_fixture", "retrospective_replay"):
            selected = [row for row in rows if row["issuance"]["evidence_kind"] == kind]
            scored = [row for row in selected if row["status"] == "observed"]
            summaries[kind] = {
                "issuances": len(selected),
                "scored_targets": len(scored),
                "unobserved": sum(row["status"] == "unobserved" for row in selected),
                "excluded": sum(row["status"] == "excluded" for row in selected),
                "repeat_issuances": sum(
                    row["status"] == "repeat_issuance" for row in selected
                ),
                "target_months": sorted(
                    {row["issuance"]["target_month"] for row in selected}
                ),
            }
        cohorts = []
        cohort_keys = sorted(
            {
                (
                    row["issuance"]["evidence_kind"],
                    row["issuance"]["classification"],
                    row["issuance"]["artifact"]["policy_id"],
                )
                for row in rows
            }
        )
        for kind, classification, policy_id in cohort_keys:
            selected = [
                row
                for row in rows
                if (
                    row["issuance"]["evidence_kind"],
                    row["issuance"]["classification"],
                    row["issuance"]["artifact"]["policy_id"],
                )
                == (kind, classification, policy_id)
            ]
            scored = [row for row in selected if row["status"] == "observed"]
            cohorts.append(
                {
                    "evidence_kind": kind,
                    "classification": classification,
                    "policy_id": policy_id,
                    "scored_targets": len(scored),
                    "target_months": sorted(
                        {row["issuance"]["target_month"] for row in selected}
                    ),
                    "mae": {
                        method: sum(
                            abs(
                                row["issuance"][method]
                                - row["outcome"]["payload"]["value"]
                            )
                            for row in scored
                        )
                        / len(scored)
                        if scored
                        else None
                        for method in (
                            "prediction",
                            "last_observation",
                            "seasonal_naive",
                        )
                    },
                }
            )
        return {
            "schema_version": SCHEMA,
            "outcome_policy": outcome_policy,
            "issuance_policy": (
                "earliest non-excluded per series, units, target, product and policy"
            ),
            "events": events,
            "rows": rows,
            "summaries": summaries,
            "cohorts": cohorts,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("issuance", "observation", "correction"):
        action = sub.add_parser(command)
        action.add_argument("--key", required=True)
        action.add_argument("--record", type=Path, required=True)
    export = sub.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument(
        "--outcome-policy",
        choices=("first_release", "latest_available"),
        default="first_release",
    )
    args = parser.parse_args()
    ledger = Ledger(args.db)
    if args.command == "export":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                ledger.view(outcome_policy=args.outcome_policy),
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        if args.record.stat().st_size > MAX_RECORD_BYTES:
            parser.error("record exceeds 64 KiB")
        print(
            ledger.append(args.command, args.key, json.loads(args.record.read_bytes()))
        )


if __name__ == "__main__":
    main()
