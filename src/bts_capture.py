"""Retain official BTS snapshot bytes and a qualified availability bound."""

import csv
import hashlib
import io
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.provenance import digest, history_rows
from src.real_backtest import load_snapshot

SOURCE_URL = (
    "https://data.transportation.gov/resource/bw6n-ddqk.csv?"
    "$select=obs_date,tsi_freight&$order=obs_date&$limit=50000"
)
METADATA_URL = "https://data.transportation.gov/api/views/bw6n-ddqk.json"
HTTP_TIMEOUT_SECONDS = 30
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024


def _official_url(url: str, kind: str) -> bool:
    """Allow the fixed dataset and its normal official portal redirect only."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"data.transportation.gov", "data.bts.gov"}
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            return False
        query = sorted(
            parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        )
    except ValueError:
        return False
    if kind == "source":
        return parsed.path in {
            "/resource/bw6n-ddqk.csv",
            "/api/id/bw6n-ddqk.csv",
        } and query == sorted(parse_qsl(urlsplit(SOURCE_URL).query))
    return parsed.path == "/api/views/bw6n-ddqk.json" and not query


class _OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _official_url(newurl, self.kind):
            raise HTTPError(
                req.full_url, code, "unsupported BTS redirect destination", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Buffer:
    """Give the existing snapshot validator the exact captured parse buffer."""

    def __init__(self, raw):
        self.raw = raw

    def read_bytes(self):
        return self.raw


def _clock(clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("capture clock must be an aware datetime")
    return value.astimezone(UTC)


def _json(raw):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate metadata key: " + name)
            result[name] = value
        return result

    def nonfinite(value):
        raise ValueError("nonfinite metadata number: " + value)

    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def _write_json(path: Path, value) -> None:
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _capture(output, kind, url, maximum, clock, open_url, receipts):
    started = _clock(clock)
    receipt = {
        "kind": kind,
        "source_url": url,
        "started_at": started.isoformat(),
        "timeout_seconds": HTTP_TIMEOUT_SECONDS,
        "maximum_body_bytes": maximum,
        "body_complete": False,
    }
    receipts.append(receipt)
    request = Request(
        url,
        headers={
            "User-Agent": "FreightForecastSourceCapture/1.0",
            "Accept-Encoding": "identity",
        },
    )
    receipt["request_headers"] = dict(request.header_items())
    opener = open_url or build_opener(_OfficialRedirects(kind)).open
    content = bytearray()
    try:
        try:
            response = opener(request, timeout=HTTP_TIMEOUT_SECONDS)
        except HTTPError as error:
            response = error
        with response:
            receipt["http_status"] = response.status
            receipt["resolved_source_url"] = response.geturl()
            receipt["response_headers"] = list(response.headers.items())
            deadline = time.monotonic() + HTTP_TIMEOUT_SECONDS
            with (
                output / ("source.csv" if kind == "source" else "metadata.json")
            ).open("xb") as stream:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("response body read deadline exceeded")
                    chunk = response.read1(min(65536, maximum - len(content) + 1))
                    if not chunk:
                        break
                    stream.write(chunk)
                    content.extend(chunk)
                    if len(content) > maximum:
                        raise ValueError(
                            "response body exceeded the capture byte bound"
                        )
                stream.flush()
                os.fsync(stream.fileno())
            receipt["body_complete"] = True
        retrieved = _clock(clock)
        receipt["retrieved_at"] = retrieved.isoformat()
        if retrieved < started:
            raise ValueError("capture completion clock precedes request start")
        if receipt["http_status"] != 200:
            raise ValueError("official BTS response must have HTTP status 200")
        if not _official_url(receipt["resolved_source_url"], kind):
            raise ValueError("response came from an unsupported BTS URL")
        return bytes(content), receipt
    except BaseException as error:
        receipt["error_type"] = type(error).__name__
        receipt["error"] = str(error)
        if "retrieved_at" not in receipt:
            try:
                receipt["retrieved_at"] = _clock(clock).isoformat()
            except Exception as clock_error:
                receipt["clock_error"] = str(clock_error)
                error.add_note(f"failed capture completion clock: {clock_error}")
        raise
    finally:
        receipt["bytes"] = len(content)
        receipt["sha256"] = hashlib.sha256(content).hexdigest()


def capture_snapshot(output: Path, *, clock=lambda: datetime.now(UTC), open_url=None):
    """Create one new capture directory, retaining refusals without a receipt."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("capture output must be a new directory")
    output.mkdir(parents=True, exist_ok=False)
    receipts = []
    try:
        source, source_receipt = _capture(
            output, "source", SOURCE_URL, MAX_SOURCE_BYTES, clock, open_url, receipts
        )
        metadata_raw, _ = _capture(
            output,
            "metadata",
            METADATA_URL,
            MAX_METADATA_BYTES,
            clock,
            open_url,
            receipts,
        )
        metadata = _json(metadata_raw)
        if not isinstance(metadata, dict) or metadata.get("id") != "bw6n-ddqk":
            raise ValueError("metadata must identify the supported BTS dataset")
        columns = metadata.get("columns")
        if not isinstance(columns, list):
            raise ValueError("metadata must declare the BTS observation fields")
        fields = {}
        for column in columns:
            name = column.get("fieldName") if isinstance(column, dict) else None
            if not isinstance(name, str) or name in fields:
                raise ValueError("metadata observation fields must be uniquely named")
            fields[name] = column
        if (
            fields.get("obs_date", {}).get("dataTypeName") != "calendar_date"
            or fields.get("tsi_freight", {}).get("dataTypeName") != "number"
        ):
            raise ValueError("metadata fields conflict with the supported BTS snapshot")
        rows = list(csv.DictReader(io.StringIO(source.decode("utf-8"))))
        if not rows or list(rows[0]) != ["obs_date", "tsi_freight"]:
            raise ValueError("snapshot must contain only obs_date and tsi_freight")
        manifest = {
            "schema_version": 1,
            "dataset_id": "bw6n-ddqk",
            "field": "tsi_freight",
            "units": "index_points_2000_average_100",
            "seasonally_adjusted": True,
            "vintage_kind": "latest_vintage_snapshot",
            "source_url": SOURCE_URL,
            "retrieved_utc": source_receipt["retrieved_at"],
            "sha256": source_receipt["sha256"],
            "rows": len(rows),
            "first_month": rows[0]["obs_date"][:7],
            "last_month": rows[-1]["obs_date"][:7],
            "view_metadata_sha256": hashlib.sha256(metadata_raw).hexdigest(),
        }
        manifest_raw = json.dumps(manifest, allow_nan=False).encode("utf-8")
        history, manifest = load_snapshot(_Buffer(source), _Buffer(manifest_raw))
        for name, expected in (("source.csv", source), ("metadata.json", metadata_raw)):
            with (output / name).open("rb") as stream:
                if stream.read(len(expected) + 1) != expected:
                    raise ValueError("captured response bytes changed before admission")
        availability = {
            "schema_version": 1,
            "kind": "available_by_capture",
            "source_url": SOURCE_URL,
            "resolved_source_url": source_receipt["resolved_source_url"],
            "retrieved_at": source_receipt["retrieved_at"],
            "available_by": source_receipt["retrieved_at"],
            "sha256": source_receipt["sha256"],
            "history_id": digest(history_rows(history)),
            "dataset_id": "bw6n-ddqk",
            "field": "tsi_freight",
            "last_month": manifest["last_month"],
            "http_status": 200,
        }
        _write_json(output / "manifest.json", manifest)
        _write_json(output / "availability.json", availability)
        _write_json(output / "http-receipts.json", receipts)
        return availability
    except BaseException as error:
        for name in ("manifest.json", "availability.json"):
            try:
                (output / name).unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(f"capture completion cleanup failed: {cleanup_error}")
        try:
            _write_json(
                output / "capture-failed.json",
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "requests": receipts,
                },
            )
        except OSError as evidence_error:
            error.add_note(
                f"failed capture receipt could not be saved: {evidence_error}"
            )
        raise
