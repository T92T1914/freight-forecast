"""Synthetic reduced controls do not authenticate actual publication history."""

import io
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from src import bts_release as release
from src.ledger import SERIES, UNITS, Ledger
from src.provenance import digest
from tools.capture_bts_release import main

URL = (
    "https://www.bts.gov/newsroom/august-2026-freight-transportation-services-"
    "index-tsi-fell-04-july-2026-and-fell-30-august"
)
NOW = datetime(2026, 10, 15, 10, tzinfo=UTC)
RAW = (
    Path(__file__).parent / "fixtures/bts-release-reduced-synthetic.html"
).read_bytes()


class Response(io.BytesIO):
    def __init__(self, raw=RAW, url=URL, status=200):
        super().__init__(raw)
        self.url, self.status = url, status
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=UTF-8"
        self.headers["Date"] = "Thu, 15 Oct 2026 10:00:00 GMT"
        self.headers["Last-Modified"] = "Wed, 14 Oct 2026 19:50:37 GMT"

    def geturl(self):
        return self.url


def captured(tmp_path, *, raw=RAW, now=NOW):
    output = tmp_path / "capture"
    receipt = release.capture_release(
        URL,
        output,
        clock=lambda: now,
        open_url=lambda request, **kwargs: Response(raw),
    )
    return output, receipt


def event():
    policy = {
        "family": "last_observation",
        "series": SERIES,
        "units": UNITS,
        "horizon": 1,
        "feature_schema": "bts-last-observation-v1",
        "model_id": digest({"synthetic": "last observation"}),
    }
    payload = {
        "schema_version": 1,
        "evidence_kind": "synthetic_fixture",
        "classification": "forecast",
        "series": SERIES,
        "units": UNITS,
        "target_month": "2026-08",
        "issued_at": "2026-07-15T12:00:00+00:00",
        "issue_timezone": "UTC",
        "horizon": 1,
        "prediction": 100.0,
        "last_observation": 100.0,
        "seasonal_naive": 90.0,
        "uncertainty": None,
        "source": {
            "url": "https://example.invalid/synthetic",
            "published_at": "2026-07-10T12:00:00+00:00",
            "retrieved_at": "2026-07-11T12:00:00+00:00",
            "publication_verified": False,
            "publication_basis": "Synthetic fixture",
        },
        "vintage": {
            "sha256": digest({"synthetic": 1}),
            "history_id": digest([]),
            "kind": "synthetic_fixture",
            "available_through": "2026-07",
        },
        "artifact": {
            **policy,
            "policy": policy,
            "policy_id": digest(policy),
            "training_cutoff": "2026-07",
        },
    }
    payload_id = digest(payload)
    event_id = digest(
        {"kind": "issuance", "key": "synthetic-original", "payload_id": payload_id}
    )
    return {
        "seq": 1,
        "event_id": event_id,
        "idempotency_key": "synthetic-original",
        "kind": "issuance",
        "issuance_id": event_id,
        "payload": payload,
        "payload_id": payload_id,
        "recorded_at": NOW.isoformat(),
    }


def save_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    return path


def review(tmp_path, receipt):
    def proof(name, role):
        raw = ("SYNTHETIC REVIEW CONTROL ONLY: " + name).encode()
        filename = name + ".txt"
        (tmp_path / filename).write_bytes(raw)
        return {
            "file": filename,
            "role": role,
            "sha256": release._sha(raw),
            "url": "https://example.invalid/synthetic-proof/" + name,
            "basis": "Synthetic invented " + name + ", not official publication proof",
        }

    return {
        "schema_version": 1,
        "evidence_scope": "synthetic_rehearsal",
        "source_sha256": receipt["sha256"],
        "source_url": URL,
        "target_month": "2026-08",
        "value": 123.4,
        "publication": {
            **proof("publication", "actual_publication"),
            "published_at": "2026-10-14T13:00:00+00:00",
            "display_date_offset": "-04:00",
            "timezone_basis": (
                "Synthetic declared fixed offset, not a BTS timezone claim"
            ),
        },
        "first_release": proof("first-release", "original_unrevised_release"),
        "unit_evidence": {
            **proof("units", "series_units"),
            "series": SERIES,
            "units": UNITS,
            "seasonally_adjusted": True,
        },
    }


def prepared_inputs(tmp_path):
    capture, receipt = captured(tmp_path)
    original = save_json(tmp_path / "event.json", event())
    declaration = save_json(tmp_path / "review.json", review(tmp_path, receipt))
    return capture, original, declaration


def test_source_facts_remain_day_precision_with_unknown_timezone_and_no_unit_base():
    facts = release.inspect_release(RAW, URL)
    assert facts["target_month"] == "2026-08" and facts["value"] == 123.4
    assert facts["value_location"]["row_label"] == "Aug-26"
    assert facts["value_location"]["table_id"] == "synthetic-freight"
    assert facts["displayed_bulletin_date"]["date"] == "2026-10-14"
    assert facts["displayed_bulletin_date"]["precision"] == "day"
    assert facts["displayed_bulletin_date"]["timezone"] is None
    assert facts["units"] is None and facts["mutable_release_warning"]
    assert not facts["publication_verified"] and not facts["first_release_verified"]
    assert "published_at" not in facts


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("https:", "http:"),
        URL.replace("www.bts.gov", "bts.gov.evil.invalid"),
        URL.replace("www.bts.gov", "www.bts.gov:444"),
        URL + "?format=json",
        URL + "#x",
        URL.replace("www.bts.gov", "person@www.bts.gov"),
        "\n" + URL,
        URL.replace("/newsroom/", "/newsroom/../newsroom/"),
        "https://www.bts.gov/newsroom/transportation-services-index-release-schedule",
    ],
)
def test_initial_url_policy_refuses_unsupported_routes_before_transport(tmp_path, url):
    calls = []
    with pytest.raises(ValueError, match="unsupported official"):
        release.capture_release(
            url, tmp_path / "capture", open_url=lambda *a, **k: calls.append(a)
        )
    assert not calls
    assert (tmp_path / "capture/failed.json").exists()


@pytest.mark.parametrize(
    "old,new",
    [
        (b"<h1>The August", b"<h1>The September"),
        (b"<th>Freight TSI</th>", b"<th>Passenger TSI</th>"),
        (b"<td>123.4</td>", b"<td>123.4%</td>"),
        (b"<td>123.4</td>", b"<td>NaN</td>"),
        (b"Wednesday, October 14", b"Thursday, October 14"),
        (b"</html>", b""),
        (
            b"<tr><td>Aug-26</td><td>123.4</td></tr>",
            b"<tr><td>Aug-26</td><td>123.4</td></tr><tr><td>Aug-26</td><td>130</td></tr>",
        ),
    ],
)
def test_unsupported_or_conflicting_document_facts_refuse(tmp_path, old, new):
    raw = RAW.replace(old, new)
    with pytest.raises(ValueError):
        captured(tmp_path, raw=raw)
    assert (tmp_path / "capture/body.bin").read_bytes() == raw
    assert not (tmp_path / "capture/capture.json").exists()


def test_unqualified_exact_metadata_is_retained_as_a_lead_only(tmp_path):
    raw = RAW.replace(
        b"</head>",
        b'<meta property="article:published_time" '
        b'content="2026-10-14T09:00:00-04:00"></head>',
    )
    capture, _ = captured(tmp_path, raw=raw)
    facts = json.loads((capture / "release.json").read_bytes())
    assert (
        facts["metadata_leads"][0]["attributes"]["property"] == "article:published_time"
    )
    assert facts["publication_verified"] is False
    result = release.prepare_candidate(
        capture,
        save_json(tmp_path / "event.json", event()),
        "one-key",
        tmp_path / "candidate",
        clock=lambda: NOW,
    )
    assert result["status"] == "not_admitted"
    assert not (tmp_path / "candidate/observation.json").exists()


def test_capture_hashes_the_original_buffer_and_does_not_relabel_http_clocks(tmp_path):
    capture, receipt = captured(tmp_path)
    saved, facts = release.load_capture(capture)
    assert saved == receipt and (capture / "body.bin").read_bytes() == RAW
    assert receipt["sha256"] == facts["source_sha256"] == release._sha(RAW)
    assert receipt["retrieved_at"] == NOW.isoformat()
    assert receipt["response_headers"]["Last-Modified"].endswith("GMT")
    assert "published_at" not in receipt and "published_at" not in facts


def test_capture_requests_html_with_the_declared_agent_and_identity_encoding(tmp_path):
    requests = []

    def open_url(request, *, timeout):
        requests.append(request.full_url)
        assert request.get_method() == "GET"
        assert timeout == release.TIMEOUT_SECONDS
        assert {name.lower(): value for name, value in request.header_items()} == {
            "user-agent": "FreightForecastReleaseCapture/1.0",
            "accept": "text/html",
            "accept-encoding": "identity",
        }
        return Response()

    release.capture_release(
        URL, tmp_path / "capture", clock=lambda: NOW, open_url=open_url
    )
    assert requests == [URL]


@pytest.mark.parametrize(
    "field,value",
    [
        ("Content-Type", "text/html; charset=iso-8859-1"),
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Encoding", "gzip"),
        ("Content-Length", "10"),
    ],
)
def test_transport_or_encoding_conflicts_keep_body_but_no_completion(
    tmp_path, field, value
):
    response = Response()
    if field in response.headers:
        del response.headers[field]
    response.headers[field] = value
    with pytest.raises(ValueError):
        release.capture_release(
            URL,
            tmp_path / "capture",
            clock=lambda: NOW,
            open_url=lambda *a, **k: response,
        )
    assert (tmp_path / "capture/body.bin").read_bytes() == RAW
    assert not (tmp_path / "capture/capture.json").exists()


def test_redirects_check_origin_path_and_count_before_following():
    handler = release._Redirects(URL)
    response = io.BytesIO()
    for _ in range(release.MAX_REDIRECTS):
        request = handler.redirect_request(
            Request(URL), response, 302, "Found", Message(), URL
        )
        assert request.full_url == URL
    with pytest.raises(HTTPError, match="excessive"):
        handler.redirect_request(Request(URL), response, 302, "Found", Message(), URL)
    for destination in (
        "https://evil.invalid/",
        URL.replace("august-2026", "july-2026"),
    ):
        with pytest.raises(HTTPError, match="unsupported"):
            release._Redirects(URL).redirect_request(
                Request(URL), response, 302, "Found", Message(), destination
            )


@pytest.mark.parametrize("kind", ["file", "directory", "hardlink", "symlink"])
def test_existing_output_and_aliases_preserve_data_without_transport(tmp_path, kind):
    original = tmp_path / "original"
    original.write_bytes(b"keep original")
    output = tmp_path / "capture"
    if kind == "file":
        output.write_bytes(b"previous output")
    elif kind == "directory":
        output.mkdir()
    elif kind == "hardlink":
        try:
            os.link(original, output)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"hard link creation unavailable: {error}")
    else:
        try:
            output.symlink_to(original)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(FileExistsError):
        release.capture_release(
            URL, output, open_url=lambda *a, **k: pytest.fail("transport called")
        )
    assert original.read_bytes() == b"keep original"


def test_oversized_body_is_a_retained_bounded_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "MAX_BODY_BYTES", 20)
    with pytest.raises(ValueError, match="byte bound"):
        captured(tmp_path)
    assert (tmp_path / "capture/body.bin").read_bytes() == RAW[:21]
    assert not json.loads((tmp_path / "capture/failed.json").read_bytes())["details"][
        "body_complete"
    ]


def test_partial_response_preserves_prefix_and_original_read_error(tmp_path):
    class Interrupted(Response):
        def read1(self, size):
            raise IncompleteRead(b"partial bytes", 100)

    with pytest.raises(IncompleteRead):
        release.capture_release(
            URL,
            tmp_path / "capture",
            clock=lambda: NOW,
            open_url=lambda *a, **k: Interrupted(),
        )
    assert (tmp_path / "capture/body.bin").read_bytes() == b"partial bytes"
    assert (
        json.loads((tmp_path / "capture/failed.json").read_bytes())["error_type"]
        == "IncompleteRead"
    )


def test_error_body_is_retained_without_outcome_admission(tmp_path):
    def open_url(*args, **kwargs):
        raise HTTPError(URL, 503, "Unavailable", Message(), io.BytesIO(b"unavailable"))

    with pytest.raises(HTTPError):
        release.capture_release(
            URL, tmp_path / "capture", clock=lambda: NOW, open_url=open_url
        )
    assert (tmp_path / "capture/body.bin").read_bytes() == b"unavailable"
    assert not (tmp_path / "capture/capture.json").exists()


def test_blocked_redirect_receipt_retains_location_without_requesting_it(tmp_path):
    destination = "https://example.invalid/forbidden"
    headers = Message()
    headers["Location"] = destination

    def refuse(*args, **kwargs):
        raise HTTPError(
            URL, 302, "unsupported BTS redirect", headers, io.BytesIO(b"refused")
        )

    with pytest.raises(HTTPError, match="unsupported BTS redirect"):
        release.capture_release(
            URL, tmp_path / "capture", clock=lambda: NOW, open_url=refuse
        )
    failure = json.loads((tmp_path / "capture/failed.json").read_bytes())
    assert failure["details"]["response_headers"]["Location"] == destination
    assert failure["details"]["http_status"] == 302


def test_passenger_value_and_percentage_text_do_not_replace_scoped_freight_row():
    raw = RAW.replace(
        b"</body>",
        b"<p>Passenger TSI rose 50% to 999.0.</p>"
        b"<table><tr><th>Month</th><th>Passenger TSI</th></tr>"
        b"<tr><td>Aug-26</td><td>999.0</td></tr></table></body>",
    )
    assert release.inspect_release(raw, URL)["value"] == 123.4


def test_bulletin_calendar_year_can_follow_its_target_year():
    raw = (
        RAW.replace(b"August 2026", b"November 2026")
        .replace(b"Wednesday, October 14, 2026", b"Thursday, January 14, 2027")
        .replace(b"BTS 26-76", b"BTS 27-1")
        .replace(b"Aug-26", b"Nov-26")
    )
    facts = release.inspect_release(
        raw, URL.replace("august-2026-", "november-2026-", 1)
    )
    assert facts["target_month"] == "2026-11"
    assert facts["displayed_bulletin_date"]["date"] == "2027-01-14"


def test_declared_proof_prepares_frozen_exact_schema_without_touching_sources(tmp_path):
    capture, original, declaration = prepared_inputs(tmp_path)
    before = {
        path: path.read_bytes()
        for path in (capture / "body.bin", original, declaration)
    }
    result = release.prepare_candidate(
        capture,
        original,
        "retained-key",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    payload = json.loads((tmp_path / "candidate/observation.json").read_bytes())
    assert result["status"] == "ready_for_ledger_review" and not result["reasons"]
    assert result["idempotency_key"] == "retained-key"
    assert result["observation_payload_id"] == digest(payload)
    assert payload["sha256"] == release._sha(RAW) and payload["value"] == 123.4
    assert payload["issuance_id"] == event()["event_id"]
    assert "synthetic_rehearsal" in payload["source"]["publication_basis"]
    assert "declared consistency" in result["limitation"]
    assert "authenticity of supplied ledger history" in result["limitation"]
    assert before == {path: path.read_bytes() for path in before}
    assert not list((tmp_path / "candidate").glob("*.partial.json"))
    for filename, original_name in (
        ("proof-publication.bin", "publication.txt"),
        ("proof-first-release.bin", "first-release.txt"),
        ("proof-unit-evidence.bin", "units.txt"),
    ):
        assert (tmp_path / "candidate" / filename).read_bytes() == (
            tmp_path / original_name
        ).read_bytes()


@pytest.mark.parametrize("section", ["publication", "first_release", "unit_evidence"])
def test_well_formed_wrong_proof_digest_cannot_freeze_observation(tmp_path, section):
    capture, original, declaration = prepared_inputs(tmp_path)
    proof = json.loads(declaration.read_bytes())
    proof[section]["sha256"] = "a" * 64
    save_json(declaration, proof)
    result = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    assert result["status"] == "not_admitted"
    assert any(section + " proof bytes" in reason for reason in result["reasons"])
    assert not (tmp_path / "candidate/observation.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "a" * 64},
        {"source_url": "https://example.invalid/other"},
        {"target_month": "2026-09"},
        {"value": 999.0},
        {"publication": None},
        {"first_release": None},
        {"unit_evidence": None},
    ],
)
def test_missing_or_conflicting_proof_is_explicitly_non_admitted(tmp_path, change):
    capture, original, declaration = prepared_inputs(tmp_path)
    proof = json.loads(declaration.read_bytes())
    proof.update(change)
    save_json(declaration, proof)
    result = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    assert result["status"] == "not_admitted" and result["reasons"]
    assert (
        result["observation_file"] is None
        and not (tmp_path / "candidate/observation.json").exists()
    )


@pytest.mark.parametrize(
    "role", ["scheduled_publication", "last_modified", "retrieval"]
)
def test_timestamp_role_is_not_inferred_from_a_well_formed_iso_value(tmp_path, role):
    capture, original, declaration = prepared_inputs(tmp_path)
    proof = json.loads(declaration.read_bytes())
    proof["publication"]["role"] = role
    save_json(declaration, proof)
    result = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    assert result["status"] == "not_admitted"


def test_display_date_check_uses_declared_offset_instead_of_utc_date(tmp_path):
    capture, original, declaration = prepared_inputs(tmp_path)
    proof = json.loads(declaration.read_bytes())
    proof["publication"]["published_at"] = "2026-10-15T02:00:00+00:00"
    save_json(declaration, proof)
    good = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "good",
        review=declaration,
        clock=lambda: NOW,
    )
    assert good["status"] == "ready_for_ledger_review"
    proof["publication"]["display_date_offset"] = "+00:00"
    save_json(declaration, proof)
    bad = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "bad",
        review=declaration,
        clock=lambda: NOW,
    )
    assert bad["status"] == "not_admitted"
    assert any("displayed date" in reason for reason in bad["reasons"])


@pytest.mark.parametrize(
    "filename",
    ["../outside.txt", "folder/proof.txt", "C:\\proof.txt", "NUL.txt", "proof.txt."],
)
def test_proof_paths_cannot_escape_the_review_directory(tmp_path, filename):
    capture, original, declaration = prepared_inputs(tmp_path)
    proof = json.loads(declaration.read_bytes())
    proof["publication"]["file"] = filename
    save_json(declaration, proof)
    result = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    assert result["status"] == "not_admitted"
    assert any("safe basename" in reason for reason in result["reasons"])


def test_symlink_proof_is_not_followed(tmp_path):
    capture, original, declaration = prepared_inputs(tmp_path)
    linked = tmp_path / "linked.txt"
    try:
        linked.symlink_to(tmp_path / "publication.txt")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    proof = json.loads(declaration.read_bytes())
    proof["publication"]["file"] = linked.name
    save_json(declaration, proof)
    result = release.prepare_candidate(
        capture,
        original,
        "key",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    assert result["status"] == "not_admitted"
    assert any("without symlinks" in reason for reason in result["reasons"])
    assert not (tmp_path / "candidate/proof-publication.bin").exists()


def test_synthetic_declaration_cannot_qualify_real_issuance():
    proof = {"schema_version": 1, "evidence_scope": "synthetic_rehearsal"}
    facts = {"target_month": "2026-08", "value": 123.4}
    receipt = {"sha256": release._sha(RAW), "resolved_url": URL}
    reasons = release._review_reasons(
        proof, receipt, facts, {"evidence_kind": "real_issuance"}
    )
    assert "synthetic review evidence cannot qualify a real issuance" in reasons


def test_prepare_existing_output_alias_does_not_modify_issuance(tmp_path):
    capture, original, declaration = prepared_inputs(tmp_path)
    alias = tmp_path / "candidate"
    original_bytes = original.read_bytes()
    try:
        os.link(original, alias)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"hard link creation unavailable: {error}")
    with pytest.raises(FileExistsError):
        release.prepare_candidate(
            capture, original, "key", alias, review=declaration, clock=lambda: NOW
        )
    assert original.read_bytes() == original_bytes == alias.read_bytes()


def test_changed_body_and_original_event_identity_refuse(tmp_path):
    capture, original, declaration = prepared_inputs(tmp_path)
    saved = (capture / "body.bin").read_bytes()
    (capture / "body.bin").write_bytes(saved.replace(b"123.4", b"123.5"))
    with pytest.raises(ValueError, match="do not match"):
        release.prepare_candidate(
            capture,
            original,
            "key",
            tmp_path / "changed-body",
            review=declaration,
            clock=lambda: NOW,
        )
    (capture / "body.bin").write_bytes(saved)
    altered = event()
    altered["payload"]["prediction"] = 200.0
    save_json(original, altered)
    with pytest.raises(ValueError, match="payload identity"):
        release.prepare_candidate(
            capture,
            original,
            "key",
            tmp_path / "changed-event",
            review=declaration,
            clock=lambda: NOW,
        )


def test_recapture_has_new_receipt_and_payload_exact_retry_reuses_the_frozen_file(
    tmp_path,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    capture1, receipt1 = captured(first)
    capture2, receipt2 = captured(second, now=NOW + timedelta(seconds=1))
    assert receipt1["sha256"] == receipt2["sha256"]
    assert receipt1["capture_id"] != receipt2["capture_id"]
    original = save_json(tmp_path / "event.json", event())
    declaration = save_json(tmp_path / "review.json", review(tmp_path, receipt1))
    one = release.prepare_candidate(
        capture1,
        original,
        "key",
        tmp_path / "one",
        review=declaration,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    two = release.prepare_candidate(
        capture2,
        original,
        "key",
        tmp_path / "two",
        review=declaration,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    assert one["observation_payload_id"] != two["observation_payload_id"]
    assert "Reuse the frozen" in one["retry"]


def test_final_marker_failure_preserves_primary_error_and_no_ready_observation(
    tmp_path, monkeypatch
):
    capture, original, declaration = prepared_inputs(tmp_path)
    replace = release.os.replace

    def fail(source, destination):
        if destination.name == "candidate.json":
            raise OSError("injected completion marker failure")
        return replace(source, destination)

    monkeypatch.setattr(release.os, "replace", fail)
    with pytest.raises(OSError, match="completion marker failure"):
        release.prepare_candidate(
            capture,
            original,
            "key",
            tmp_path / "candidate",
            review=declaration,
            clock=lambda: NOW,
        )
    assert not (tmp_path / "candidate/observation.json").exists()
    assert not (tmp_path / "candidate/candidate.json").exists()
    assert (tmp_path / "candidate/failed.json").exists()
    assert (capture / "body.bin").read_bytes() == RAW


def test_prepared_record_reaches_existing_disposable_ledger_and_retries(tmp_path):
    capture, _, declaration = prepared_inputs(tmp_path)
    ledger = Ledger(tmp_path / "synthetic.sqlite")
    identity = ledger.append("issuance", "synthetic-original", event()["payload"])
    original = save_json(tmp_path / "saved-event.json", ledger.events()[0])
    result = release.prepare_candidate(
        capture,
        original,
        "synthetic-outcome",
        tmp_path / "candidate",
        review=declaration,
        clock=lambda: NOW,
    )
    payload = json.loads((tmp_path / "candidate/observation.json").read_bytes())
    assert payload["issuance_id"] == identity
    assert result["observation_payload_id"] == digest(payload)
    observed = ledger.append("observation", "synthetic-outcome", payload)
    assert ledger.append("observation", "synthetic-outcome", payload) == observed
    changed = deepcopy(payload)
    changed["value"] += 1
    with pytest.raises(ValueError, match="incompatible contents"):
        ledger.append("observation", "synthetic-outcome", changed)
    assert (
        Ledger(tmp_path / "synthetic.sqlite").view()["rows"][0]["status"] == "observed"
    )


def test_cli_non_admission_exits_two_without_a_fake_record(tmp_path, capsys):
    capture, _ = captured(tmp_path)
    original = save_json(tmp_path / "event.json", event())
    assert (
        main(
            [
                "prepare",
                "--capture",
                str(capture),
                "--issuance-event",
                str(original),
                "--key",
                "retained",
                "--output",
                str(tmp_path / "candidate"),
            ]
        )
        == 2
    )
    assert "not_admitted" in capsys.readouterr().out
    assert not (tmp_path / "candidate/observation.json").exists()
