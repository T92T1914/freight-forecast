"""Snapshot capture preserves original bytes and qualifies only availability."""

import hashlib
import io
import json
import os
from datetime import UTC, datetime, timedelta
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request

import pandas as pd
import pytest

from src import bts_capture as capture
from src.provenance import digest, history_rows
from src.real_backtest import load_snapshot
from tools.capture_bts_snapshot import main

NOW = datetime(2026, 10, 14, 14, tzinfo=UTC)


def csv_bytes():
    rows = pd.DataFrame(
        {
            "obs_date": [
                value.strftime("%Y-%m-%dT00:00:00.000")
                for value in pd.date_range("2024-01-01", periods=25, freq="MS")
            ],
            "tsi_freight": [100 + index / 10 for index in range(25)],
        }
    )
    return rows.to_csv(index=False, lineterminator="\r\n").encode("utf-8")


def metadata_bytes():
    return json.dumps(
        {
            "id": "bw6n-ddqk",
            "columns": [
                {"fieldName": "obs_date", "dataTypeName": "calendar_date"},
                {"fieldName": "tsi_freight", "dataTypeName": "number"},
            ],
        },
        indent=2,
    ).encode("utf-8")


class Response(io.BytesIO):
    def __init__(self, raw, url, status=200):
        super().__init__(raw)
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["Date"] = "Wed, 14 Oct 2026 14:00:00 GMT"
        self.headers["Content-Type"] = (
            "text/csv" if url.endswith("50000") else "application/json"
        )

    def geturl(self):
        return self.url


def opener(source=None, metadata=None, *, resolved=None, status=200):
    responses = {
        capture.SOURCE_URL: csv_bytes() if source is None else source,
        capture.METADATA_URL: metadata_bytes() if metadata is None else metadata,
    }
    requests = []

    def open_url(request, *, timeout):
        requests.append(request.full_url)
        assert timeout == capture.HTTP_TIMEOUT_SECONDS
        assert request.get_header("Accept-encoding") == "identity"
        return Response(
            responses[request.full_url], resolved or request.full_url, status
        )

    return open_url, requests


def test_capture_keeps_exact_bytes_and_joins_clock_hash_history_and_month(tmp_path):
    source = csv_bytes()
    metadata = metadata_bytes()
    open_url, requests = opener(source, metadata)
    moments = iter(NOW + timedelta(seconds=index) for index in range(4))
    output = tmp_path / "new-capture"
    receipt = capture.capture_snapshot(
        output, clock=lambda: next(moments), open_url=open_url
    )

    assert requests == [capture.SOURCE_URL, capture.METADATA_URL]
    assert (output / "source.csv").read_bytes() == source
    assert (output / "metadata.json").read_bytes() == metadata
    manifest = json.loads((output / "manifest.json").read_bytes())
    history, manifest = load_snapshot(output / "source.csv", output / "manifest.json")
    assert receipt == json.loads((output / "availability.json").read_bytes())
    assert receipt["sha256"] == manifest["sha256"] == hashlib.sha256(source).hexdigest()
    assert receipt["history_id"] == digest(history_rows(history))
    assert receipt["last_month"] == manifest["last_month"] == "2026-01"
    assert receipt["source_url"] == manifest["source_url"] == capture.SOURCE_URL
    assert receipt["retrieved_at"] == manifest["retrieved_utc"]
    assert (
        receipt["available_by"]
        == receipt["retrieved_at"]
        == (NOW + timedelta(seconds=1)).isoformat()
    )
    assert receipt["schema_version"] == 1 and receipt["http_status"] == 200
    assert receipt["kind"] == "available_by_capture"
    assert receipt["dataset_id"] == "bw6n-ddqk" and receipt["field"] == "tsi_freight"
    assert "published_at" not in receipt and "publication_verified" not in receipt
    http = json.loads((output / "http-receipts.json").read_bytes())
    assert all(row["body_complete"] for row in http)
    assert http[0]["sha256"] == receipt["sha256"]


def test_parser_uses_captured_buffer_but_changed_retained_bytes_refuse_admission(
    tmp_path, monkeypatch
):
    source = csv_bytes()
    original = capture.load_snapshot

    def validate(data, manifest):
        (tmp_path / "capture" / "source.csv").write_bytes(b"later unrelated bytes")
        assert data.read_bytes() == source
        return original(data, manifest)

    monkeypatch.setattr(capture, "load_snapshot", validate)
    open_url, _ = opener(source)
    with pytest.raises(ValueError, match="changed before admission"):
        capture.capture_snapshot(
            tmp_path / "capture", clock=lambda: NOW, open_url=open_url
        )
    failure = json.loads((tmp_path / "capture" / "capture-failed.json").read_bytes())
    assert failure["requests"][0]["sha256"] == hashlib.sha256(source).hexdigest()
    assert not (tmp_path / "capture" / "availability.json").exists()
    assert (tmp_path / "capture" / "source.csv").read_bytes() != source


@pytest.mark.parametrize(
    "metadata",
    [
        b"not JSON",
        b"[]",
        b'{"id":"other","columns":[]}',
        b'{"id":"bw6n-ddqk","id":"other","columns":[]}',
        b'{"id":"bw6n-ddqk","columns":[],"bad":NaN}',
        b'{"id":"bw6n-ddqk","columns":[{"fieldName":[],"dataTypeName":"number"}]}',
    ],
)
def test_invalid_metadata_retains_bodies_without_an_admission_receipt(
    tmp_path, metadata
):
    source = csv_bytes()
    open_url, _ = opener(source, metadata)
    output = tmp_path / "capture"
    with pytest.raises(ValueError):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == source
    assert (output / "metadata.json").read_bytes() == metadata
    assert (
        json.loads((output / "capture-failed.json").read_bytes())["status"] == "failed"
    )
    assert not (output / "availability.json").exists()
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize(
    "source",
    [
        b"<html>unexpected response</html>",
        csv_bytes().replace(b"2024-02-01", b"2024-01-01"),
        csv_bytes().replace(b"100.1", b"inf"),
        csv_bytes().replace(b"100.1", b"-1"),
    ],
)
def test_bad_snapshot_refuses_after_original_bytes_are_retained(tmp_path, source):
    open_url, _ = opener(source)
    output = tmp_path / "capture"
    with pytest.raises(ValueError):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == source
    assert not (output / "availability.json").exists()
    assert not (output / "manifest.json").exists()


@pytest.mark.parametrize("status", [403, 429, 500])
def test_unsuccessful_http_body_is_preserved_and_never_admitted(tmp_path, status):
    body = b"source refusal or outage"
    open_url, requests = opener(body, status=status)
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="HTTP status 200"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert requests == [capture.SOURCE_URL]
    assert (output / "source.csv").read_bytes() == body
    failure = json.loads((output / "capture-failed.json").read_bytes())
    assert failure["requests"][0]["http_status"] == status
    assert failure["requests"][0]["body_complete"]
    assert not (output / "availability.json").exists()


@pytest.mark.parametrize(
    "resolved",
    [
        "http://data.bts.gov/resource/bw6n-ddqk.csv",
        "https://unrelated.example/resource/bw6n-ddqk.csv",
        capture.SOURCE_URL.replace("bw6n-ddqk", "different"),
        capture.SOURCE_URL + "&$where=tsi_freight>100",
    ],
)
def test_conflicting_response_source_is_not_admitted(tmp_path, resolved):
    open_url, _ = opener(resolved=resolved)
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="unsupported BTS URL"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == csv_bytes()
    assert not (output / "availability.json").exists()


def test_redirect_handler_refuses_foreign_destination_before_a_request():
    headers = Message()
    with pytest.raises(HTTPError, match="unsupported BTS redirect"):
        capture._OfficialRedirects("source").redirect_request(
            Request(capture.SOURCE_URL),
            io.BytesIO(b"redirect response"),
            302,
            "Found",
            headers,
            "https://unrelated.example/data.csv",
        )
    resolved = capture.SOURCE_URL.replace(
        "data.transportation.gov/resource/", "data.bts.gov/api/id/"
    )
    redirected = capture._OfficialRedirects("source").redirect_request(
        Request(capture.SOURCE_URL), io.BytesIO(), 302, "Found", headers, resolved
    )
    assert redirected.full_url == resolved


def test_oversized_capture_keeps_bounded_failure_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "MAX_SOURCE_BYTES", 20)
    open_url, _ = opener()
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="byte bound"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == csv_bytes()[:21]
    failure = json.loads((output / "capture-failed.json").read_bytes())
    assert not failure["requests"][0]["body_complete"]
    assert failure["requests"][0]["bytes"] == 21
    assert not (output / "availability.json").exists()


def test_partial_read_failure_keeps_received_bytes_and_primary_error(tmp_path):
    class Interrupted(Response):
        def read1(self, size):
            if self.tell():
                raise TimeoutError("declared test timeout")
            return super().read1(10)

    def open_url(request, *, timeout):
        return Interrupted(csv_bytes(), request.full_url)

    output = tmp_path / "capture"
    with pytest.raises(TimeoutError, match="declared test timeout"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == csv_bytes()[:10]
    failure = json.loads((output / "capture-failed.json").read_bytes())
    assert failure["error"] == "declared test timeout"
    assert not failure["requests"][0]["body_complete"]
    assert not (output / "availability.json").exists()


def test_body_deadline_is_a_failure_without_an_admission_receipt(tmp_path, monkeypatch):
    moments = iter([0, capture.HTTP_TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(capture.time, "monotonic", lambda: next(moments))
    open_url, _ = opener()
    output = tmp_path / "capture"
    with pytest.raises(TimeoutError, match="read deadline"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == b""
    assert not (output / "availability.json").exists()


def test_reverse_completion_clock_is_not_an_availability_bound(tmp_path):
    moments = iter([NOW, NOW - timedelta(seconds=1)])
    open_url, requests = opener()
    output = tmp_path / "capture"
    with pytest.raises(ValueError, match="completion clock precedes"):
        capture.capture_snapshot(output, clock=lambda: next(moments), open_url=open_url)
    assert requests == [capture.SOURCE_URL]
    assert not (output / "availability.json").exists()


@pytest.mark.parametrize("kind", ["directory", "file", "hardlink"])
def test_existing_output_or_file_alias_refuses_before_network_and_preserves_data(
    tmp_path, kind
):
    original = tmp_path / "retained.csv"
    original.write_bytes(b"original input")
    output = tmp_path / "capture"
    if kind == "directory":
        output.mkdir()
        (output / "prior.json").write_bytes(b"previous capture")
    elif kind == "file":
        output.write_bytes(b"previous output")
    else:
        os.link(original, output)
    open_url, requests = opener()
    with pytest.raises(FileExistsError, match="new directory"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert not requests
    assert original.read_bytes() == b"original input"
    if kind == "directory":
        assert (output / "prior.json").read_bytes() == b"previous capture"
    elif kind == "file":
        assert output.read_bytes() == b"previous output"
    else:
        assert output.samefile(original)


def test_existing_symlink_output_is_not_reused(tmp_path):
    original = tmp_path / "retained"
    original.mkdir()
    output = tmp_path / "capture"
    try:
        output.symlink_to(original, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlink creation unavailable on this platform: {error}")
    open_url, requests = opener()
    with pytest.raises(FileExistsError, match="new directory"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert not requests and output.is_symlink()
    assert not list(original.iterdir())


def test_cli_reports_failure_without_a_success_message(tmp_path, monkeypatch, capsys):
    open_url, _ = opener(status=500)
    actual = capture.capture_snapshot
    monkeypatch.setattr(
        "tools.capture_bts_snapshot.capture_snapshot",
        lambda output: actual(output, clock=lambda: NOW, open_url=open_url),
    )
    with pytest.raises(SystemExit) as error:
        main(["--output", str(tmp_path / "capture")])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "HTTP status 200" in output.err


def test_completion_delivery_failure_removes_admission_files_and_keeps_raw_bytes(
    tmp_path, monkeypatch
):
    original = capture._write_json

    def fail(path, value):
        if path.name == "http-receipts.json":
            raise OSError("injected evidence delivery failure")
        return original(path, value)

    monkeypatch.setattr(capture, "_write_json", fail)
    open_url, _ = opener()
    output = tmp_path / "capture"
    with pytest.raises(OSError, match="evidence delivery failure"):
        capture.capture_snapshot(output, clock=lambda: NOW, open_url=open_url)
    assert (output / "source.csv").read_bytes() == csv_bytes()
    assert (output / "metadata.json").read_bytes() == metadata_bytes()
    assert (output / "capture-failed.json").exists()
    assert not (output / "availability.json").exists()
    assert not (output / "manifest.json").exists()
