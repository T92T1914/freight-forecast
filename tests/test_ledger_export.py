"""Keep derived snapshots from damaging retained events or earlier output."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src import ledger as ledger_mod
from src.ledger import Ledger
from tools.ledger_fixture import fixture_record
from tools.ledger_fixture import main as fixture_main


@pytest.fixture
def ledger(tmp_path):
    result = Ledger(tmp_path / "ledger.sqlite")
    result.append("issuance", "export-fixture", fixture_record("2026-03"))
    return result


@pytest.mark.parametrize("policy", ["first_release", "latest_available"])
def test_export_replaces_only_completed_view(ledger, tmp_path, policy):
    destination = tmp_path / "reports" / "ledger.json"
    destination.parent.mkdir()
    destination.write_bytes(b"previous export")
    before = ledger.path.read_bytes()
    expected = ledger.view(outcome_policy=policy)

    ledger.export(destination, outcome_policy=policy)

    assert json.loads(destination.read_bytes()) == expected
    assert ledger.path.read_bytes() == before
    assert Ledger(ledger.path).events() == expected["events"]
    assert expected["summaries"]["real_issuance"]["issuances"] == 0
    assert list(destination.parent.iterdir()) == [destination]


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_export_rejects_ledger_and_reserved_sidecars(ledger, suffix):
    destination = Path(str(ledger.path) + suffix)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="conflicts with ledger storage"):
        ledger.export(destination)

    assert ledger.path.read_bytes() == before
    assert len(ledger.events()) == 1
    if suffix:
        assert not destination.exists()


def test_export_rejects_existing_hardlink_to_storage(ledger, tmp_path):
    destination = tmp_path / "ledger-alias.json"
    os.link(ledger.path, destination)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="conflicts with ledger storage"):
        ledger.export(destination)

    assert destination.read_bytes() == before == ledger.path.read_bytes()
    assert destination.samefile(ledger.path)


def test_export_rejects_existing_hardlink_to_sidecar(ledger, tmp_path):
    protected = Path(str(ledger.path) + "-wal")
    protected.write_bytes(b"retained sidecar fixture")
    destination = tmp_path / "sidecar-alias.json"
    os.link(protected, destination)

    with pytest.raises(ValueError, match="conflicts with ledger storage"):
        ledger.export(destination)

    assert destination.read_bytes() == b"retained sidecar fixture"
    assert destination.samefile(protected)


def test_export_rejects_normalized_database_path(ledger):
    destination = ledger.path.parent / "." / "unused" / ".." / ledger.path.name
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="conflicts with ledger storage"):
        ledger.export(destination)

    assert ledger.path.read_bytes() == before
    assert not (ledger.path.parent / "unused").exists()


def _symlink(target, link):
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlink creation unavailable: {error}")


def test_export_rejects_symlink_to_database(ledger, tmp_path):
    destination = tmp_path / "ledger-link.json"
    _symlink(ledger.path, destination)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="conflicts with ledger storage"):
        ledger.export(destination)

    assert destination.is_symlink()
    assert ledger.path.read_bytes() == before


def test_export_keeps_separate_output_symlink(ledger, tmp_path):
    target = tmp_path / "view.json"
    target.write_bytes(b"previous output")
    destination = tmp_path / "view-link.json"
    _symlink(target, destination)

    ledger.export(destination)

    assert destination.is_symlink()
    assert json.loads(target.read_bytes()) == ledger.view()


@pytest.mark.parametrize(
    "failure", ["view", "serialization", "create", "write", "flush", "fsync", "replace"]
)
def test_export_failure_preserves_output_and_cleans_its_temporary(
    ledger, tmp_path, monkeypatch, failure
):
    destination = tmp_path / "view.json"
    destination.write_bytes(b"previous complete output")
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"not owned by exporter")
    before = ledger.path.read_bytes()
    original_view = ledger.view()

    def fail(*args, **kwargs):
        raise OSError(f"injected {failure} failure")

    if failure == "view":
        monkeypatch.setattr(ledger, "view", fail)
    elif failure == "serialization":
        monkeypatch.setattr(ledger, "view", lambda **kwargs: {"bad": float("nan")})
    elif failure == "create":
        monkeypatch.setattr(ledger_mod.tempfile, "NamedTemporaryFile", fail)
    elif failure in ("write", "flush"):
        create = ledger_mod.tempfile.NamedTemporaryFile

        class FailingStream:
            def __init__(self, *args, **kwargs):
                self.stream = create(*args, **kwargs)
                self.name = self.stream.name

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, value):
                self.stream.write(value[:10] if failure == "write" else value)
                if failure == "write":
                    fail()

            def flush(self):
                if failure == "flush":
                    fail()
                self.stream.flush()

        monkeypatch.setattr(ledger_mod.tempfile, "NamedTemporaryFile", FailingStream)
    elif failure == "fsync":
        monkeypatch.setattr(ledger_mod.os, "fsync", fail)
    else:
        monkeypatch.setattr(ledger_mod.os, "replace", fail)

    with pytest.raises((ValueError, OSError)):
        ledger.export(destination)

    assert destination.read_bytes() == b"previous complete output"
    assert unrelated.read_bytes() == b"not owned by exporter"
    assert ledger.path.read_bytes() == before
    assert Ledger(ledger.path).view() == original_view
    assert set(tmp_path.iterdir()) == {ledger.path, destination, unrelated}


def test_export_replace_failure_does_not_create_a_partial_destination(
    ledger, tmp_path, monkeypatch
):
    destination = tmp_path / "new-view.json"

    def fail(*args):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(ledger_mod.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failure"):
        ledger.export(destination)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == [ledger.path]


@pytest.mark.parametrize("primary", [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("secondary", [PermissionError, KeyboardInterrupt, SystemExit])
def test_export_cleanup_failure_preserves_original_error(
    ledger, tmp_path, monkeypatch, primary, secondary
):
    destination = tmp_path / "view.json"
    destination.write_bytes(b"previous output")
    failure = primary("original replacement failure")

    def fail_replace(*args):
        raise failure

    def fail_cleanup(*args, **kwargs):
        raise secondary("injected owned temporary cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger_mod.os, "replace", fail_replace)
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(primary) as captured:
            ledger.export(destination)

    assert captured.value is failure
    assert any("temporary export cleanup failed" in note for note in failure.__notes__)
    assert any(secondary.__name__ in note for note in failure.__notes__)
    assert destination.read_bytes() == b"previous output"
    assert len(ledger.events()) == 1
    retained = set(tmp_path.iterdir()) - {ledger.path, destination}
    assert len(retained) == 1
    temporary = retained.pop()
    assert json.loads(temporary.read_bytes()) == ledger.view()
    temporary.unlink()


@pytest.mark.parametrize("signal", [OSError, KeyboardInterrupt, SystemExit])
def test_export_interruption_after_replacement_keeps_completed_snapshot(
    ledger, tmp_path, monkeypatch, signal
):
    destination = tmp_path / "view.json"
    destination.write_bytes(b"previous complete output")
    before = ledger.path.read_bytes()
    expected = ledger.view()
    replace = ledger_mod.os.replace
    failure = signal("injected signal after successful replacement")

    def replace_then_interrupt(*args):
        replace(*args)
        raise failure

    monkeypatch.setattr(ledger_mod.os, "replace", replace_then_interrupt)
    with pytest.raises(signal) as captured:
        ledger.export(destination)

    assert captured.value is failure
    assert json.loads(destination.read_bytes()) == expected
    assert ledger.path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {ledger.path, destination}


def test_export_cleanup_note_failure_does_not_mask_original(
    ledger, tmp_path, monkeypatch
):
    class OriginalError(OSError):
        def add_note(self, note):
            raise SystemExit("injected note reporting failure")

    failure = OriginalError("original replacement failure")

    def fail_replace(*args):
        raise failure

    def fail_cleanup(*args, **kwargs):
        raise KeyboardInterrupt("injected cleanup interruption")

    with monkeypatch.context() as patch:
        patch.setattr(ledger_mod.os, "replace", fail_replace)
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(OriginalError) as captured:
            ledger.export(tmp_path / "new-view.json")

    assert captured.value is failure
    assert not (tmp_path / "new-view.json").exists()
    temporary = (set(tmp_path.iterdir()) - {ledger.path}).pop()
    temporary.unlink()


def test_cli_rejects_database_destination_without_corrupting_events(ledger):
    before = ledger.path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.ledger",
            "--db",
            os.fspath(ledger.path),
            "export",
            "--output",
            os.fspath(ledger.path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert "conflicts with ledger storage" in result.stderr
    assert ledger.path.read_bytes() == before
    assert len(Ledger(ledger.path).events()) == 1


def test_fixture_rejects_storage_destination_before_adding_records(ledger, monkeypatch):
    before = ledger.path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        ["ledger_fixture.py", "--db", str(ledger.path), "--output", str(ledger.path)],
    )

    with pytest.raises(ValueError, match="conflicts with ledger storage"):
        fixture_main()

    assert ledger.path.read_bytes() == before
    assert len(ledger.events()) == 1


def test_fixture_uses_completed_export_and_retains_only_synthetic_records(
    tmp_path, monkeypatch
):
    database = tmp_path / "fixture.sqlite"
    destination = tmp_path / "fixture.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["ledger_fixture.py", "--db", str(database), "--output", str(destination)],
    )

    fixture_main()

    exported = json.loads(destination.read_bytes())
    assert exported == Ledger(database).view()
    assert exported["summaries"]["real_issuance"]["issuances"] == 0
    assert exported["summaries"]["synthetic_fixture"]["issuances"] == 3
    assert set(tmp_path.iterdir()) == {database, destination}
