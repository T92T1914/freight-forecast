"""Keep issuances and outcome vintages without changing serving promotion."""

import argparse
import json
import math
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

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
    return _shift_month(value, 1)


def _shift_month(value, steps):
    month = _month(value)
    if type(steps) is not int:
        raise ValueError("calendar shift must be an integer")
    year, number = divmod(month.year * 12 + month.month - 1 + steps, 12)
    if not 1 <= year <= 9999:
        raise ValueError("calendar shift is outside supported years")
    number += 1
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


def _official_capture_url(value):
    """Constrain availability evidence to the supported official CSV request."""
    url = urlsplit(_text(value, "capture source URL"))
    expected = [
        ("$select", "obs_date,tsi_freight"),
        ("$order", "obs_date"),
        ("$limit", "50000"),
    ]
    if (
        url.scheme != "https"
        or url.hostname not in ("data.transportation.gov", "data.bts.gov")
        or url.port not in (None, 443)
        or url.username is not None
        or url.password is not None
        or url.fragment
        or url.path not in ("/resource/bw6n-ddqk.csv", "/api/id/bw6n-ddqk.csv")
        or sorted(parse_qsl(url.query, keep_blank_values=True)) != sorted(expected)
    ):
        raise ValueError("availability requires the supported official CSV request")


def _source(source, clock, *, required_publication, allow_availability=False):
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    if "availability_receipt" in source and not allow_availability:
        raise ValueError("availability receipt cannot establish outcome publication")
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
    receipt = source.get("availability_receipt")
    if "availability_receipt" in source:
        if not isinstance(receipt, dict):
            raise ValueError("availability receipt must be an object")
        if published is not None or source["publication_verified"] is not False:
            raise ValueError(
                "availability receipt does not establish exact publication"
            )
        if (
            type(receipt.get("schema_version")) is not int
            or receipt["schema_version"] != 1
            or receipt.get("kind") != "available_by_capture"
            or receipt.get("dataset_id") != "bw6n-ddqk"
            or receipt.get("field") != "tsi_freight"
            or type(receipt.get("http_status")) is not int
            or receipt["http_status"] != 200
            or receipt.get("source_url") != url
        ):
            raise ValueError("availability receipt does not match the supported source")
        _official_capture_url(url)
        if "resolved_source_url" in receipt:
            _official_capture_url(receipt["resolved_source_url"])
        captured = _time(receipt.get("retrieved_at"))
        if _time(receipt.get("available_by")) != captured or captured != retrieved:
            raise ValueError(
                "availability receipt clocks do not match capture retrieval"
            )
        for name in ("sha256", "history_id"):
            _hash(receipt.get(name), "availability " + name)
        _month(receipt.get("last_month"))
    if (
        required_publication
        and (published is None or source["publication_verified"] is not True)
        and receipt is None
    ):
        raise ValueError("real issuance requires verified source publication timing")
    return receipt


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
    receipt = _source(
        record.get("source"),
        issued,
        required_publication=kind == "real_issuance",
        allow_availability=True,
    )
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
    if receipt is not None and (
        any(receipt[name] != vintage[name] for name in ("sha256", "history_id"))
        or receipt["last_month"] != available
        or vintage["kind"] == "synthetic_fixture"
    ):
        raise ValueError("availability receipt does not match captured vintage")
    if kind == "real_issuance" and _month(available) > issued:
        raise ValueError("available observations cannot be future periods")
    cutoff = artifact.get("training_cutoff")
    _month(cutoff)
    horizon = record.get("horizon")
    if type(horizon) is not int or horizon not in (1, 3):
        raise ValueError(
            "only the one-step and declared three-step contracts are supported"
        )
    if cutoff > available or record["target_month"] != _shift_month(available, horizon):
        raise ValueError("policy target must match its declared observation horizon")
    if artifact.get("series") != SERIES or artifact.get("units") != UNITS:
        raise ValueError("artifact series or units do not match")
    if type(artifact.get("horizon")) is not int or artifact["horizon"] != horizon:
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
        "last_observation": ("bts-last-observation-v1", 1),
        "monthly_refit_ridge": (FEATURE_SCHEMA, 1),
        "last_observation_h3": ("bts-last-observation-h3-v1", 3),
    }
    if supported.get(policy.get("family")) != (artifact["feature_schema"], horizon):
        raise ValueError("unsupported artifact feature contract")
    if horizon == 3 and (classification != "forecast" or target <= issued):
        raise ValueError("three-step outlook requires a future forecast target")
    if record.get("uncertainty") is not None:
        raise ValueError("this point policy supplies no uncertainty method")
    for name in ("prediction", "last_observation", "seasonal_naive"):
        _number(record.get(name))
    if (
        policy["family"] in ("last_observation", "last_observation_h3")
        and record["prediction"] != record["last_observation"]
    ):
        raise ValueError("last-observation policy must retain its actual prediction")
    _text(record.get("issue_timezone"), "issue timezone convention")


class Ledger:
    """One local SQLite writer at a time, with append-only event records."""

    def __init__(self, path):
        self.path = Path(path)
        self._read_only = False
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

    @classmethod
    def open_readonly(cls, path):
        """Open existing rollback journal storage without initializing anything."""
        ledger = cls.__new__(cls)
        ledger.path = Path(path).resolve()
        ledger._read_only = True
        with ledger._connection() as connection:
            ledger._validate_schema(connection)
        return ledger

    @staticmethod
    def _validate_schema(connection):
        expected = {
            "metadata": [("version", "INTEGER", 1, 0)],
            "events": [
                ("seq", "INTEGER", 0, 1),
                ("event_id", "TEXT", 1, 0),
                ("idempotency_key", "TEXT", 1, 0),
                ("kind", "TEXT", 1, 0),
                ("issuance_id", "TEXT", 1, 0),
                ("payload", "TEXT", 1, 0),
                ("payload_id", "TEXT", 1, 0),
                ("recorded_at", "TEXT", 1, 0),
            ],
        }
        for table, columns in expected.items():
            actual = connection.execute(f"PRAGMA table_info({table})").fetchall()
            if [(row[1], row[2], row[3], row[5]) for row in actual] != columns:
                raise ValueError("unsupported ledger schema")
        if connection.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
            raise ValueError("unsupported ledger schema")
        unique_columns = {
            tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index[0],)
                )
            )
            for index in connection.execute(
                "SELECT name FROM pragma_index_list('events') "
                'WHERE "unique"=1 AND partial=0'
            )
        }
        if not {("event_id",), ("idempotency_key",)} <= unique_columns:
            raise ValueError("unsupported ledger schema")

    @contextmanager
    def _connection(self):
        if self._read_only:
            # WAL readers can create shared memory files. Refuse that format before
            # SQLite opens storage. The supported writer uses DELETE journals.
            with self.path.open("rb") as stream:
                header = stream.read(20)
            if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
                raise ValueError("read only review requires rollback journal storage")
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=ro",
                uri=True,
                timeout=10,
                isolation_level=None,
            )
        else:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            if self._read_only:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA journal_mode=DELETE")
            yield connection
        finally:
            connection.close()

    def append(self, kind, key, payload):
        """Return the original event on exact retry, reject conflicting retries."""
        if self._read_only:
            raise ValueError("read only ledger cannot append events")
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

    def review_observation(self, key, payload):
        """Check one snapshot without reserving a key or recording an event.

        This assessment also rechecks restored observation sources and values at
        their retained recording clocks. The historical reader is unchanged.
        A later append must still perform its own admission in a write transaction.
        """
        if not self._read_only:
            raise ValueError("observation review requires Ledger.open_readonly")
        assessment = {"recorded": False, "reserves_key": False}
        try:
            _text(key, "idempotency key")
            if not isinstance(payload, dict):
                raise ValueError("record must be an object")
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            if len(encoded.encode()) > MAX_RECORD_BYTES:
                raise ValueError("record exceeds 64 KiB")
            payload = json.loads(encoded)
            payload_id = digest(payload)
            with self._connection() as connection:
                connection.execute("BEGIN")
                self._validate_schema(connection)
                events = self._read_events(connection)
                # Restored history must also satisfy original source admission.
                # Keep the established events/view/export contract unchanged.
                issuances = {
                    event["event_id"]: event["payload"]
                    for event in events
                    if event["kind"] == "issuance"
                }
                for event in events:
                    if event["kind"] == "observation":
                        self._validate_observation(
                            event["payload"],
                            issuances[event["issuance_id"]],
                            _time(event["recorded_at"]),
                        )
                    elif event["kind"] == "correction":
                        _text(event["payload"].get("reason"), "correction reason")
                    elif event["kind"] != "issuance":
                        raise ValueError("unknown saved event kind")
                assessment.update(snapshot_id=digest(events), event_count=len(events))
                previous = connection.execute(
                    "SELECT event_id, kind, payload_id FROM events "
                    "WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
                if previous:
                    if previous[1:] != ("observation", payload_id):
                        raise ValueError("idempotency key has incompatible contents")
                    return {
                        **assessment,
                        "status": "exact_retry",
                        "existing_event_id": previous[0],
                    }
                issuance = issuances.get(payload.get("issuance_id"))
                if issuance is None:
                    raise ValueError("unknown original issuance")
                self._validate_linked(
                    connection, "observation", payload, issuance, datetime.now(UTC)
                )
            return {**assessment, "status": "admissible"}
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            TypeError,
            OverflowError,
            KeyError,
        ) as error:
            return {**assessment, "status": "rejected", "reason": str(error)}

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
        Ledger._validate_observation(payload, issuance, clock)
        if payload["vintage_kind"] == "unavailable":
            return
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
    def _validate_observation(payload, issuance, clock):
        """Share source, value and chronology admission with saved history review."""
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
        Ledger._validate_outcome_publication(payload, issuance)
        if vintage == "unavailable":
            if payload.get("value") is not None or payload.get("sha256") is not None:
                raise ValueError("unavailable release cannot contain an invented value")
            _text(payload.get("reason"), "unavailability reason")
            return
        _number(payload.get("value"))
        _hash(payload.get("sha256"), "outcome SHA256")

    @staticmethod
    def _validate_outcome_publication(payload, issuance):
        """A real prediction cannot be scored against a declared earlier outcome."""
        if (
            issuance.get("evidence_kind") != "real_issuance"
            or payload.get("vintage_kind") == "unavailable"
        ):
            return
        source = payload.get("source")
        published = source.get("published_at") if isinstance(source, dict) else None
        if _time(published) <= _time(issuance.get("issued_at")):
            raise ValueError("real outcome publication must follow issuance")

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
            return self._read_events(connection)

    def _read_events(self, connection):
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
                try:
                    validate_issuance(payload, _time(recorded))
                except ValueError as error:
                    raise ValueError(f"saved {error}") from error
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
                        if isinstance(payload.get("source"), dict) and (
                            "availability_receipt" in payload["source"]
                        ):
                            raise ValueError(
                                "availability receipt cannot establish "
                                "outcome publication"
                            )
                        self._validate_outcome_publication(payload, original["payload"])
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

    def validate_export_destination(self, destination):
        """Keep derived output separate from the database and its sidecars."""
        destination = Path(destination).resolve()
        for database in (self.path.absolute(), self.path.resolve()):
            for suffix in ("", "-journal", "-wal", "-shm"):
                protected = Path(str(database) + suffix)
                if destination == protected.resolve() or (
                    destination.exists()
                    and protected.exists()
                    and destination.samefile(protected)
                ):
                    raise ValueError("export destination conflicts with ledger storage")
        return destination

    def export(self, destination, *, outcome_policy="first_release"):
        """Publish one completed JSON snapshot without truncating existing output."""
        destination = self.validate_export_destination(destination)
        encoded = (
            json.dumps(
                self.view(outcome_policy=outcome_policy), indent=2, allow_nan=False
            )
            + "\n"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException as error:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except BaseException as cleanup_error:
                    # Reporting cleanup must not replace the export error.
                    with suppress(BaseException):
                        error.add_note(
                            "temporary export cleanup failed: "
                            f"{type(cleanup_error).__name__}: {cleanup_error}"
                        )
            raise


def _review_record(path):
    """Bound candidate input and refuse ambiguous duplicate object keys."""

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate record field: {key}")
            result[key] = value
        return result

    with path.open("rb") as stream:
        encoded = stream.read(MAX_RECORD_BYTES + 1)
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("record exceeds 64 KiB")
    return json.loads(encoded, object_pairs_hook=unique_object)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("issuance", "observation", "correction", "review-observation"):
        action = sub.add_parser(command)
        if command == "review-observation":
            action.description = (
                "Assess one saved ledger snapshot without recording an event or "
                "reserving a key. Saved observation sources and values are also "
                "rechecked at their original recording clocks. A later append "
                "performs its own admission. Exact publication timing is required "
                "for an observed value. Date only evidence remains outside the ledger."
            )
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
    if args.command == "review-observation":
        try:
            ledger = Ledger.open_readonly(args.db)
            result = ledger.review_observation(args.key, _review_record(args.record))
        except (OSError, sqlite3.Error, ValueError) as error:
            result = {
                "status": "rejected",
                "reason": str(error),
                "recorded": False,
                "reserves_key": False,
            }
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        if result["status"] == "rejected":
            raise SystemExit(2)
        return
    ledger = Ledger(args.db)
    if args.command == "export":
        ledger.export(args.output, outcome_policy=args.outcome_policy)
    else:
        if args.record.stat().st_size > MAX_RECORD_BYTES:
            parser.error("record exceeds 64 KiB")
        print(
            ledger.append(args.command, args.key, json.loads(args.record.read_bytes()))
        )


if __name__ == "__main__":
    main()
