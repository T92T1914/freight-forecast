"""Qualification boundaries need independent controls before a real consumer run."""

import base64
import copy
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from tools import qualify_service as qualification

IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def source():
    return {
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "files": {
            name: "d" * 64
            for name in (
                "src/serve.py",
                "src/train.py",
                "src/features.py",
                "src/provenance.py",
                "requirements.txt",
            )
        },
    }


def history():
    return (
        "date,volume\n"
        + "".join(f"2025-{month:02d}-01,{month}\n" for month in range(1, 13))
    ).encode()


def test_independent_history_identity_preserves_float_representation():
    identity, rows = qualification.history_identity(history())
    expected = [
        {"date": f"2025-{month:02d}-01", "volume": float(month)}
        for month in range(1, 13)
    ]
    assert rows == expected
    encoded = json.dumps(
        expected, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    assert identity == hashlib.sha256(encoded.encode()).hexdigest()
    reordered = b"date,volume\n" + b"".join(
        reversed(history().splitlines(keepends=True)[1:])
    )
    assert qualification.history_identity(reordered) == (identity, rows)


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.replace(b"2025-02-01,2", b"2025-03-01,2"),
        lambda value: value.replace(b"2025-02-01,2", b"2025-02-02,2"),
        lambda value: value.replace(b"2025-02-01,2", b"2025-02-01,inf"),
    ],
)
def test_independent_history_refuses_bad_calendar_and_nonfinite_data(change):
    with pytest.raises(qualification.QualificationError):
        qualification.history_identity(change(history()))


def test_output_admission_keeps_prior_receipt_and_input(tmp_path):
    retained = tmp_path / "retained"
    retained.mkdir()
    receipt = retained / "qualification.json"
    receipt.write_bytes(b"accepted prior receipt")
    with pytest.raises(qualification.QualificationError, match="new output directory"):
        qualification.admit_output(retained)
    assert receipt.read_bytes() == b"accepted prior receipt"
    source = tmp_path / "original.csv"
    source.write_bytes(history())
    with pytest.raises(qualification.QualificationError):
        qualification.admit_output(source)
    assert source.read_bytes() == history()


@pytest.mark.parametrize(
    "image,context", [("latest", "desktop-linux"), (IMAGE, "default")]
)
def test_mutable_image_and_unqualified_context_refused_before_operations(
    tmp_path, source, image, context
):
    with pytest.raises(qualification.QualificationError):
        qualification.Consumer(image, tmp_path, source, context=context)
    assert list(tmp_path.iterdir()) == []


def test_timed_out_command_retains_partial_checkpoint_without_shell(tmp_path, source):
    calls = []

    def timeout(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.TimeoutExpired(
            argv, kwargs["timeout"], output="partial output", stderr="partial error"
        )

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        consumer.docker("inspect", "owned-name", timeout=3)
    assert calls[0][0] == [
        "docker",
        "--context",
        "desktop-linux",
        "inspect",
        "owned-name",
    ]
    assert "shell" not in calls[0][1]
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    report = json.loads((tmp_path / "qualification.json").read_bytes())
    command = report["commands"][0]
    assert command["status"] == "failed"
    assert command["stdout"] == "partial output"
    assert command["stderr"] == "partial error"
    assert command["timeout_seconds"] == 3


def test_utf8_command_output_is_captured_as_bytes_and_decoded_in_parent(
    tmp_path, source
):
    stdout, stderr = "diagnostic \u0101\n".encode(), "complete \u03b1\n".encode()

    def runner(argv, **kwargs):
        assert kwargs["text"] is False
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    result = consumer.docker("inspect", "owned")
    assert (
        result.stdout == "diagnostic \u0101\n" and result.stderr == "complete \u03b1\n"
    )
    command = json.loads((tmp_path / "qualification.json").read_bytes())["commands"][0]
    assert base64.b64decode(command["stdout_base64"]) == stdout
    assert base64.b64decode(command["stderr_base64"]) == stderr
    assert command["status"] == "completed"


def test_invalid_utf8_refuses_without_losing_original_command_bytes(tmp_path, source):
    stdout, stderr = b"captured output\n", b"\x81invalid UTF8\n"

    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    with pytest.raises(UnicodeDecodeError):
        consumer.docker("inspect", "owned")
    command = json.loads((tmp_path / "qualification.json").read_bytes())["commands"][0]
    assert base64.b64decode(command["stdout_base64"]) == stdout
    assert base64.b64decode(command["stderr_base64"]) == stderr
    assert command["status"] == "failed" and command["returncode"] == 0
    assert "UnicodeDecodeError" in command["error"] and command["stderr_decode_error"]


def test_missing_command_stream_refuses_and_retains_captured_other_stream(
    tmp_path, source
):
    def runner(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b"captured output\n", stderr=None)

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    with pytest.raises(
        qualification.QualificationError, match="stderr was not captured"
    ):
        consumer.docker("inspect", "owned")
    command = json.loads((tmp_path / "qualification.json").read_bytes())["commands"][0]
    assert command["status"] == "failed" and command["stderr_capture_error"]
    assert base64.b64decode(command["stdout_base64"]) == b"captured output\n"


def test_timeout_retains_utf8_and_invalid_partial_bytes_without_masking_timeout(
    tmp_path, source
):
    stdout, stderr = "partial \u0101".encode(), b"\xffpartial error"

    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            argv, kwargs["timeout"], output=stdout, stderr=stderr
        )

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    with pytest.raises(subprocess.TimeoutExpired):
        consumer.docker("inspect", "owned", timeout=3)
    command = json.loads((tmp_path / "qualification.json").read_bytes())["commands"][0]
    assert command["stdout"] == "partial \u0101"
    assert base64.b64decode(command["stdout_base64"]) == stdout
    assert base64.b64decode(command["stderr_base64"]) == stderr
    assert "TimeoutExpired" in command["error"] and command["stderr_decode_error"]


@pytest.mark.parametrize(
    "stderr",
    [
        "error: no such object: owned-auto-removed\n",
        "Error: No such container: owned-auto-removed\n",
    ],
)
def test_cleanup_recognizes_owned_container_absence_with_docker_case_variants(
    tmp_path, source, stderr
):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout=b"", stderr=stderr.encode())

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    consumer.names.append("owned-auto-removed")
    consumer.cleanup()
    assert len(calls) == 1 and calls[0][-2:] == ["inspect", "owned-auto-removed"]
    assert consumer.report["cleanup"][0]["already_absent"] is True
    assert "error" not in consumer.report["cleanup"][0]


def test_cleanup_keeps_unknown_daemon_failure_visible(tmp_path, source):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"Cannot connect to Docker daemon\n"
        )

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    consumer.names.append("owned")
    consumer.cleanup()
    assert len(calls) == 1
    assert "already_absent" not in consumer.report["cleanup"][0]
    assert "unavailable" in consumer.report["cleanup"][0]["error"]


def test_cleanup_refuses_unowned_container(tmp_path, source):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        document = [
            {
                "Id": "e" * 64,
                "Config": {"Labels": {qualification.OWNER_LABEL: "someone-else"}},
            }
        ]
        return SimpleNamespace(returncode=0, stdout=json.dumps(document), stderr="")

    consumer = qualification.Consumer(IMAGE, tmp_path, source, run=runner)
    consumer.names.append("collision")
    consumer.cleanup()
    assert len(calls) == 1
    assert calls[0][-2:] == ["inspect", "collision"]
    assert "unowned" in consumer.report["cleanup"][0]["error"]


def test_cleanup_still_removes_owned_container_when_checkpoint_storage_fails(
    tmp_path, source, monkeypatch
):
    calls = []
    consumer = qualification.Consumer(IMAGE, tmp_path, source)

    def runner(argv, **kwargs):
        calls.append(argv)
        document = [
            {
                "Id": "e" * 64,
                "Config": {"Labels": {qualification.OWNER_LABEL: consumer.owner}},
            }
        ]
        return SimpleNamespace(returncode=0, stdout=json.dumps(document), stderr="")

    consumer.run = runner
    consumer.names.append("owned")

    def unavailable(*args):
        raise OSError("checkpoint disk unavailable")

    monkeypatch.setattr(qualification, "checkpoint", unavailable)
    consumer.cleanup()
    assert calls[-1][-3:] == ["rm", "--force", "e" * 64]
    assert consumer.report["cleanup"][0]["removed"] is True
    assert consumer.report["evidence_errors"]


@pytest.fixture
def process(source):
    return {
        "uid": 10001,
        "gid": 10001,
        "pid1": {
            "Uid": "10001\t10001\t10001\t10001",
            "Gid": "10001\t10001\t10001\t10001",
            "CapEff": "0000000000000000",
            "NoNewPrivs": "1",
        },
        "cgroup": {
            "memory.max": str(qualification.MEMORY_BYTES),
            "cpu.max": "50000 100000",
            "pids.max": "64",
        },
        "required_reads": {"model": True, "history": True},
        "write_probe": {"refused": True, "errno": 30},
        "source_files": source["files"],
        "model_id": "a" * 64,
        "history_csv_base64": base64.b64encode(history()).decode(),
        "history_csv_sha256": hashlib.sha256(history()).hexdigest(),
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["pid1"].update(CapEff="0000000000000001"),
        lambda value: value["pid1"].update(NoNewPrivs="0"),
        lambda value: value["cgroup"].update(**{"memory.max": "max"}),
        lambda value: value["cgroup"].update(**{"cpu.max": "100000 100000"}),
        lambda value: value["write_probe"].update(refused=False),
        lambda value: value["source_files"].update(**{"src/serve.py": "0" * 64}),
    ],
)
def test_actual_process_admission_rejects_each_containment_or_source_failure(
    source, process, change
):
    good, _ = qualification.process_policy(process, source["files"])
    assert good["history_id"] == qualification.history_identity(history())[0]
    altered = copy.deepcopy(process)
    change(altered)
    with pytest.raises((qualification.QualificationError, ValueError)):
        qualification.process_policy(altered, source["files"])


def test_summary_retains_errors_and_slow_responses():
    samples = [
        {"status": 200, "latency_ms": 1.0},
        {"status": 503, "latency_ms": 30.0},
        {"status": None, "latency_ms": 5000.0, "error": "timeout"},
    ]
    summary = qualification.samples_summary(samples)
    assert summary["statuses"] == {"200": 1, "503": 1, "None": 1}
    assert summary["errors"] == 1
    assert summary["latency_ms_all_outcomes"]["median"] == 30.0
    assert summary["latency_ms_all_outcomes"]["max"] == 5000.0


def test_controlled_request_holds_only_final_byte_of_valid_body(monkeypatch):
    class Connection:
        def __init__(self):
            self.sent = []
            self.closed = False

        def sendall(self, data):
            self.sent.append(data)

        def close(self):
            self.closed = True

    connection = Connection()
    calls = []

    def connect(address, **kwargs):
        calls.append((address, kwargs))
        return connection

    monkeypatch.setattr(qualification.socket, "create_connection", connect)
    actual, final, sample = qualification.begin_partial_request(
        "http://127.0.0.1:32100", "2025-01"
    )
    headers, partial = connection.sent[0].split(b"\r\n\r\n", 1)
    assert actual is connection
    assert final == b"}"
    assert json.loads(partial + final) == {"month": "2025-01"}
    assert f"Content-Length: {len(partial) + 1}".encode() in headers
    assert calls == [(("127.0.0.1", 32100), {"timeout": 5})]
    assert sample["status"] is None


@pytest.mark.parametrize("base", ["http://example.com:80", "https://127.0.0.1:8000"])
def test_controlled_request_refuses_other_network_scope(base, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unqualified target must not open a socket")

    monkeypatch.setattr(qualification.socket, "create_connection", unexpected)
    with pytest.raises(qualification.QualificationError):
        qualification.begin_partial_request(base, "2025-01")
