"""Literal supplemental metadata controls preserve canonical capture history."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_bts_release import NOW, RAW, captured, event, save_json

from src import bts_release as release
from src.provenance import digest

SCRIPT_TEXT = (
    '\n  {"@context":"https://example.invalid/context",'
    '"datePublished":"2026-10-14T09:00:00-04:00",'
    '"dateModified":"2026-10-15T10:00:00Z",'
    '"name":"Café &amp; <em>raw</em>","escape":"\\u2603"}\n  '
)


def with_metadata(markup):
    return RAW.replace(b"</head>", markup.encode("utf-8") + b"</head>")


def literal_body():
    return with_metadata(
        '<meta property="article:published_time" content="2026-10-14T09:00:00-04:00">\n'
        '<time datetime="2026-10-14T09:00:00-04:00">'
        " Reviewed &amp; unverified </time>\n"
        '<script type="application/ld+json">' + SCRIPT_TEXT + "</script>\n"
        '<script>throw new Error("do not execute"); 999.9 Freight TSI </script>\n'
        "<style>Freight TSI 888.8; datePublished:2099-01-01</style>\n"
    )


def source_bytes(capture):
    return {
        name: (capture / name).read_bytes()
        for name in ("body.bin", "capture.json", "release.json")
    }


def test_literal_jsonld_is_untrimmed_inert_and_separate_from_canonical_facts(tmp_path):
    raw = literal_body()
    capture, receipt = captured(tmp_path, raw=raw)
    before = source_bytes(capture)
    old_facts = release.load_capture(capture)[1]
    assert old_facts["metadata_leads"][2]["text"] == ""
    report = release.inspect_metadata(capture, tmp_path / "inspection")
    assert report["kind"] == "supplemental_metadata_inspection"
    assert report["status"] == "unverified_metadata_inspection"
    assert report["capture_id"] == receipt["capture_id"]
    assert report["canonical_contract"] == "legacy_release_facts_v1"
    assert report["scan_complete"] and report["previews_complete"]
    assert report["lead_counts"] == {"meta": 1, "time": 1, "jsonld_script": 1}
    assert report["recognized_lead_count"] == report["retained_lead_count"] == 3
    assert report["omitted_lead_count"] == 0
    meta, time, script = report["leads"]
    assert meta["attributes"]["content"] == "2026-10-14T09:00:00-04:00"
    assert time["text"] == "Reviewed & unverified"
    assert script["text"] == SCRIPT_TEXT
    assert script["text_characters"] == len(SCRIPT_TEXT)
    assert script["closed"] and script["closure"] == "end_tag"
    assert not script["text_truncated"] and not script["empty"]
    assert [lead["order"] for lead in report["leads"]] == [1, 2, 3]
    prefix = raw.decode("utf-8").split('<script type="application/ld+json">')[0]
    assert script["line"] == prefix.count("\n") + 1
    assert script["column"] == len(prefix.rsplit("\n", 1)[-1]) + 1
    for name, content in before.items():
        assert report["source_files"][name] == {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    assert source_bytes(capture) == before
    assert release.load_capture(capture)[1] == old_facts
    assert old_facts["value"] == 123.4
    assert all(
        report[name] is False
        for name in (
            "publication_verified",
            "first_release_verified",
            "json_validated",
            "admission_performed",
        )
    )
    assert "published_at" not in report
    assert json.loads((tmp_path / "inspection/metadata.json").read_bytes()) == report
    assert list((tmp_path / "inspection").iterdir()) == [
        tmp_path / "inspection/metadata.json"
    ]


@pytest.mark.parametrize(
    ("markup", "presence", "count", "empty", "whitespace"),
    [
        ("", "absent", 0, 0, 0),
        ('<script type="application/ld+json"></script>', "present", 1, 1, 0),
        ('<script type="application/ld+json"> \n\t </script>', "present", 1, 0, 1),
        ('<script type="application/ld+json">{not JSON}</script>', "present", 1, 0, 0),
        (
            '<script type="application/ld+json" src="https://example.invalid/no-fetch"/>',
            "present",
            1,
            1,
            0,
        ),
    ],
)
def test_absent_empty_whitespace_malformed_and_external_script_are_explicit(
    tmp_path, markup, presence, count, empty, whitespace
):
    capture, _ = captured(tmp_path, raw=with_metadata(markup))
    report = release.inspect_metadata(capture, tmp_path / "inspection")
    assert report["jsonld_presence"] == presence
    assert report["lead_counts"]["jsonld_script"] == count
    assert report["empty_jsonld_script_count"] == empty
    assert report["whitespace_only_jsonld_script_count"] == whitespace
    assert report["scan_complete"] is True
    assert report["json_validated"] is False
    if count:
        assert report["leads"][0]["text"] == (
            ""
            if markup.endswith("/>")
            else markup.split(">", 1)[1].split("</script>")[0]
        )


def test_multiple_conflicting_and_duplicate_json_claims_are_only_literal_leads(
    tmp_path,
):
    first = '{"datePublished":"2026-10-14","datePublished":"2099-01-01"}'
    second = '{"dateModified":"2026-10-15T00:00:00Z"}'
    capture, _ = captured(
        tmp_path,
        raw=with_metadata(
            '<script type="application/ld+json">' + first + "</script>"
            '<script type="application/ld+json">' + second + "</script>"
        ),
    )
    report = release.inspect_metadata(capture, tmp_path / "inspection")
    assert [lead["text"] for lead in report["leads"]] == [first, second]
    assert report["json_validated"] is False
    assert report["publication_verified"] is False


@pytest.mark.parametrize("tag", ["script", "style"])
def test_unclosed_raw_text_tail_is_incomplete_instead_of_empty(tmp_path, tag):
    start = '<script type="application/ld+json">' if tag == "script" else "<style>"
    tail = '\n  {"datePublished":"unclosed literal"}\n</html>'
    raw = RAW.replace(b"</html>", (start + tail).encode())
    capture, _ = captured(tmp_path, raw=raw)
    report = release.inspect_metadata(capture, tmp_path / "inspection")
    assert report["scan_complete"] is False
    assert report["previews_complete"] is False
    assert report["unclosed_script_or_style_count"] == 1
    if tag == "script":
        lead = report["leads"][0]
        assert lead["text"] == tail + RAW.split(b"</html>", 1)[1].decode("utf-8")
        assert lead["closed"] is False and lead["empty"] is False
        assert report["empty_jsonld_script_count"] == 0
    else:
        assert report["jsonld_presence"] == "undetermined_in_incomplete_scan"


def test_preview_and_lead_bounds_retain_exact_counts_and_incomplete_flags(tmp_path):
    text = "é" * 2050
    markup = '<script type="application/ld+json">' + text + "</script>"
    markup += '<script type="application/ld+json">{}</script>' * 33
    capture, _ = captured(tmp_path, raw=with_metadata(markup))
    report = release.inspect_metadata(capture, tmp_path / "inspection")
    assert report["scan_complete"] is True
    assert report["previews_complete"] is False
    assert report["recognized_lead_count"] == 34
    assert report["retained_lead_count"] == 32 and report["omitted_lead_count"] == 2
    assert report["lead_counts"]["jsonld_script"] == 34
    assert report["leads"][0]["text"] == "é" * 2048
    assert report["leads"][0]["text_characters"] == 2050
    assert report["leads"][0]["text_truncated"] is True
    assert report["truncated_preview_count"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "body",
        "facts",
        "receipt",
        "schema",
        "bool_schema",
        "facts_bool_schema",
        "failed",
    ],
)
def test_legacy_tamper_and_unsupported_schemas_refuse_without_input_repair(
    tmp_path, mutation
):
    capture, _ = captured(tmp_path, raw=literal_body())
    if mutation == "body":
        (capture / "body.bin").write_bytes(literal_body().replace(b"123.4", b"123.5"))
    elif mutation == "facts":
        facts = json.loads((capture / "release.json").read_bytes())
        facts["metadata_leads"][2]["text"] = SCRIPT_TEXT
        save_json(capture / "release.json", facts)
    elif mutation == "facts_bool_schema":
        facts = json.loads((capture / "release.json").read_bytes())
        facts["schema_version"] = True
        save_json(capture / "release.json", facts)
    elif mutation in ("receipt", "schema", "bool_schema"):
        receipt = json.loads((capture / "capture.json").read_bytes())
        if mutation == "receipt":
            receipt["bytes"] += 1
        else:
            receipt["schema_version"] = 2 if mutation == "schema" else True
            receipt.pop("capture_id")
            receipt["capture_id"] = digest(receipt)
        save_json(capture / "capture.json", receipt)
    else:
        (capture / "failed.json").write_bytes(b"{}")
    before = source_bytes(capture)
    with pytest.raises(ValueError):
        release.inspect_metadata(capture, tmp_path / "inspection")
    assert source_bytes(capture) == before
    assert not (tmp_path / "inspection/metadata.json").exists()
    assert (tmp_path / "inspection/failed.json").exists()


@pytest.mark.parametrize("kind", ["directory", "body", "hardlink", "symlink"])
def test_existing_output_and_aliases_preserve_inputs(tmp_path, kind):
    capture, _ = captured(tmp_path)
    output = capture
    if kind == "body":
        output = capture / "body.bin"
    elif kind in ("hardlink", "symlink"):
        output = tmp_path / "alias"
        if kind == "hardlink":
            os.link(capture / "body.bin", output)
        else:
            try:
                output.symlink_to(capture, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                pytest.skip(f"symlink unavailable: {error}")
    before = source_bytes(capture)
    with pytest.raises(FileExistsError):
        release.inspect_metadata(capture, output)
    assert source_bytes(capture) == before
    assert not (capture / "failed.json").exists()


def test_source_replacement_between_validation_and_publication_refuses(
    tmp_path, monkeypatch
):
    capture, _ = captured(tmp_path, raw=literal_body())
    write = release._write
    changed = literal_body().replace(b"123.4", b"123.5")

    def replace_source(path, raw):
        write(path, raw)
        if path.name == "metadata.partial.json":
            (capture / "body.bin").write_bytes(changed)

    monkeypatch.setattr(release, "_write", replace_source)
    with pytest.raises(ValueError, match="do not match"):
        release.inspect_metadata(capture, tmp_path / "inspection")
    assert not (tmp_path / "inspection/metadata.json").exists()
    assert (tmp_path / "inspection/failed.json").exists()


@pytest.mark.parametrize(
    "failure", ["write", "replace", "after_replace", "failure_receipt"]
)
def test_output_failure_has_no_completed_report_and_preserves_primary_error(
    tmp_path, monkeypatch, failure
):
    capture, _ = captured(tmp_path)
    before = source_bytes(capture)
    original = OSError("owned metadata output failure")
    write, replace = release._write, release.os.replace

    def failing_write(path, raw):
        if path.name == "metadata.partial.json" and failure == "write":
            raise original
        if path.name == "failed.json" and failure == "failure_receipt":
            raise RuntimeError("secondary evidence error")
        return write(path, raw)

    def failing_replace(source, destination):
        if failure == "after_replace":
            replace(source, destination)
        raise original

    monkeypatch.setattr(release, "_write", failing_write)
    monkeypatch.setattr(release.os, "replace", failing_replace)
    with pytest.raises(OSError) as caught:
        release.inspect_metadata(capture, tmp_path / "inspection")
    assert caught.value is original
    assert not (tmp_path / "inspection/metadata.json").exists()
    assert source_bytes(capture) == before
    if failure != "failure_receipt":
        assert (tmp_path / "inspection/failed.json").exists()
    else:
        assert any("secondary evidence error" in note for note in original.__notes__)


def test_output_byte_bound_refuses_without_a_completed_report(tmp_path, monkeypatch):
    capture, _ = captured(tmp_path, raw=literal_body())
    before = source_bytes(capture)
    original = release._capture_buffers

    def validated(path):
        value = original(path)
        monkeypatch.setattr(release, "MAX_JSON_BYTES", 10)
        return value

    monkeypatch.setattr(release, "_capture_buffers", validated)
    with pytest.raises(ValueError, match="output byte bound"):
        release.inspect_metadata(capture, tmp_path / "inspection")
    assert source_bytes(capture) == before
    assert not (tmp_path / "inspection/metadata.json").exists()


def test_actual_cli_supplements_saved_capture_without_admission_or_changed_facts(
    tmp_path,
):
    capture, _ = captured(tmp_path, raw=literal_body())
    before = source_bytes(capture)
    output = tmp_path / "inspection"
    cli = Path(__file__).resolve().parents[1] / "tools/capture_bts_release.py"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(cli),
            "inspect-metadata",
            "--capture",
            str(capture),
            "--output",
            str(output),
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"Canonical facts unchanged" in result.stdout
    report = json.loads((output / "metadata.json").read_bytes())
    assert report["leads"][2]["text"] == SCRIPT_TEXT
    assert report["publication_verified"] is False
    original = save_json(tmp_path / "event.json", event())
    prepared = release.prepare_candidate(
        capture, original, "key", tmp_path / "candidate", clock=lambda: NOW
    )
    assert prepared["status"] == "not_admitted"
    assert not (tmp_path / "candidate/observation.json").exists()
    assert source_bytes(capture) == before
