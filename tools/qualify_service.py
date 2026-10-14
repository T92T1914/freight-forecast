"""Qualify an exact trusted image in an existing local Docker environment.

This consumer creates only labeled temporary containers and a fresh evidence
directory. It never builds or pulls images, starts Docker, changes its context,
or modifies the supplied source, data, model, or earlier qualification receipts.
"""

import argparse
import base64
import concurrent.futures
import csv
import datetime as dt
import hashlib
import http.client
import io
import json
import math
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import suppress
from pathlib import Path

OWNER_LABEL = "freight-forecast.qualification-owner"
MEMORY_BYTES = 512 * 1024 * 1024
THREAD_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
SERVICE_COMMAND = [
    "uvicorn",
    "src.serve:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--workers",
    "1",
    "--limit-concurrency",
    "8",
    "--timeout-graceful-shutdown",
    "5",
]
MAX_HTTP_BYTES = 1024 * 1024

INSPECT_PROCESS = r"""
import base64, errno, hashlib, json, os
from pathlib import Path
model = Path('/app/models/model.joblib')
history = Path('/app/data/shipments.csv')
status = {}
for line in Path('/proc/1/status').read_text().splitlines():
    if ':' in line:
        key, value = line.split(':', 1)
        if key in ('Uid', 'Gid', 'CapEff', 'NoNewPrivs'):
            status[key] = value.strip()
try:
    with open('/tmp/freight-qualification-write', 'x') as stream:
        stream.write('owned probe')
    Path('/tmp/freight-qualification-write').unlink()
    write_probe = {'refused': False}
except OSError as error:
    write_probe = {'refused': True, 'errno': error.errno, 'message': str(error)}
files = list(Path('/app/src').glob('*.py')) + [Path('/app/requirements.txt')]
print(json.dumps({
    'uid': os.getuid(), 'gid': os.getgid(), 'pid1': status,
    'model_id': hashlib.sha256(model.read_bytes()).hexdigest(),
    'model_stat': {'uid': model.stat().st_uid, 'gid': model.stat().st_gid,
                   'mode': oct(model.stat().st_mode & 0o777)},
    'history_csv_base64': base64.b64encode(history.read_bytes()).decode(),
    'history_csv_sha256': hashlib.sha256(history.read_bytes()).hexdigest(),
    'required_reads': {'model': os.access(model, os.R_OK),
                       'history': os.access(history, os.R_OK)},
    'write_probe': write_probe,
    'cgroup': {name: Path('/sys/fs/cgroup', name).read_text().strip()
               for name in ('memory.max', 'cpu.max', 'pids.max')},
    'source_files': {str(path.relative_to('/app')):
                     hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
}, allow_nan=False))
"""

CREATE_FIXTURES = r"""
import joblib
from pathlib import Path
root = Path('/evidence')
artifact = joblib.load('/app/models/model.joblib')
artifact['feature_columns'] = []
joblib.dump(artifact, root / 'invalid-schema.joblib')
(root / 'invalid-model.joblib').write_bytes(b'not a trained model artifact\n')
rows = Path('/app/data/shipments.csv').read_bytes().splitlines(keepends=True)
del rows[15]
(root / 'noncontiguous-history.csv').write_bytes(b''.join(rows))
print('Created bounded fixtures from the trusted image')
"""

ACCEPTED_CONNECTION = r"""
import json, os
from pathlib import Path
owned = set()
for descriptor in Path('/proc/1/fd').iterdir():
    try:
        target = os.readlink(descriptor)
    except OSError:
        continue
    if target.startswith('socket:['):
        owned.add(target[8:-1])
connections = []
for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
    fields = line.split()
    if fields[1].endswith(':1F40') and fields[3] == '01' and fields[9] in owned:
        connections.append({
            'local': fields[1], 'remote': fields[2], 'inode': fields[9]
        })
print(json.dumps({'pid1_established_api_connections': connections}))
"""


class QualificationError(ValueError):
    """A required local consumer condition was not established."""


def strict_json(raw):
    def invalid(value):
        raise QualificationError(f"nonfinite JSON value: {value}")

    return json.loads(raw, parse_constant=invalid)


def history_identity(raw):
    """Compute the served canonical identity independently from CSV bytes."""
    source = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if source.fieldnames != ["date", "volume"]:
        raise QualificationError("history must contain exactly date and volume")
    rows = []
    for row in source:
        date = dt.date.fromisoformat(row["date"])
        volume = float(row["volume"])
        if date.day != 1 or not math.isfinite(volume) or volume < 0:
            raise QualificationError("invalid observed history")
        rows.append({"date": date.isoformat(), "volume": volume})
    rows.sort(key=lambda row: row["date"])
    if len(rows) < 12:
        raise QualificationError("history requires at least twelve observations")
    ordinals = [int(row["date"][:4]) * 12 + int(row["date"][5:7]) for row in rows]
    if ordinals != list(range(ordinals[0], ordinals[0] + len(rows))):
        raise QualificationError("history calendar is not contiguous")
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest(), rows


def admit_output(path):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise QualificationError("use a new output directory; receipts are retained")
    if "," in os.fspath(path.resolve()):
        raise QualificationError("output path cannot contain a Docker mount separator")
    path.mkdir(parents=True, exist_ok=False)
    return path.resolve()


def checkpoint(output, report):
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
        os.replace(temporary, output / "qualification.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def container_policy(document, image, owner):
    """Check configured containment and the actual single loopback binding."""
    if document.get("Image") != image:
        raise QualificationError("container image identity differs")
    config, host = document["Config"], document["HostConfig"]
    if config.get("Labels", {}).get(OWNER_LABEL) != owner:
        raise QualificationError("container is not owned by this qualification")
    if config.get("User") != "10001:10001" or config.get("Cmd") != SERVICE_COMMAND:
        raise QualificationError("unexpected service user or command")
    if (
        host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or {str(value).upper() for value in host.get("CapDrop", [])} != {"ALL"}
        or not {"no-new-privileges", "no-new-privileges:true"}.intersection(
            host.get("SecurityOpt", [])
        )
        or host.get("NanoCpus") != 500_000_000
        or host.get("Memory") != MEMORY_BYTES
        or host.get("PidsLimit") != 64
    ):
        raise QualificationError("container resource or privilege policy differs")
    bindings = document["NetworkSettings"]["Ports"]
    if set(bindings) != {"8000/tcp"} or len(bindings["8000/tcp"]) != 1:
        raise QualificationError("expected one published API port")
    binding = bindings["8000/tcp"][0]
    if binding["HostIp"] != "127.0.0.1" or not 1 <= int(binding["HostPort"]) <= 65535:
        raise QualificationError("API host binding is not qualified loopback")
    env = dict(value.split("=", 1) for value in config.get("Env", []) if "=" in value)
    if any(env.get(key) != value for key, value in THREAD_ENV.items()):
        raise QualificationError("numeric thread or bytecode settings differ")
    return "http://127.0.0.1:" + binding["HostPort"]


def process_policy(document, source):
    """Verify actual process privilege, cgroup limits and required file access."""
    pid1, cgroup = document["pid1"], document["cgroup"]
    quota, period = cgroup["cpu.max"].split()
    if (
        document["uid"] != 10001
        or document["gid"] != 10001
        or set(pid1["Uid"].split()) != {"10001"}
        or set(pid1["Gid"].split()) != {"10001"}
        or int(pid1["CapEff"], 16) != 0
        or pid1["NoNewPrivs"] != "1"
        or int(cgroup["memory.max"]) != MEMORY_BYTES
        or int(cgroup["pids.max"]) != 64
        or quota == "max"
        or int(quota) / int(period) != 0.5
        or document["required_reads"] != {"model": True, "history": True}
        or document["write_probe"].get("errno") != 30
        or document["write_probe"].get("refused") is not True
    ):
        raise QualificationError("actual process containment or access differs")
    if any(
        document["source_files"].get(name) != digest for name, digest in source.items()
    ):
        raise QualificationError("source/image file identities differ")
    raw = base64.b64decode(document["history_csv_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != document["history_csv_sha256"]:
        raise QualificationError("observed history bytes disagree with their digest")
    history_id, rows = history_identity(raw)
    if not re.fullmatch(r"[0-9a-f]{64}", document["model_id"]):
        raise QualificationError("invalid model identity")
    return {"model_id": document["model_id"], "history_id": history_id}, rows


def request(base, path, payload=None, *, timeout=5, started_event=None):
    started = time.perf_counter()
    sample = {
        "path": path,
        "payload": payload,
        "status": None,
        "body": None,
        "started_monotonic_ns": time.perf_counter_ns(),
    }
    if started_event is not None:
        started_event.set()
    try:
        data = (
            None if payload is None else json.dumps(payload, allow_nan=False).encode()
        )
        message = urllib.request.Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(message, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            sample["status"] = response.status
            body = response.read(MAX_HTTP_BYTES + 1)
        if len(body) > MAX_HTTP_BYTES:
            raise QualificationError("HTTP response exceeds size limit")
        sample["body"] = body.decode("utf-8", errors="replace")
        if path != "/metrics/":
            try:
                sample["json"] = strict_json(sample["body"])
            except (ValueError, TypeError) as error:
                sample["json_error"] = f"{type(error).__name__}: {error}"
    except (OSError, ValueError, urllib.error.URLError) as error:
        sample["error"] = f"{type(error).__name__}: {error}"
    sample["latency_ms"] = (time.perf_counter() - started) * 1000
    sample["finished_monotonic_ns"] = time.perf_counter_ns()
    return sample


def samples_summary(samples):
    latencies = sorted(sample["latency_ms"] for sample in samples)
    statuses = {}
    for sample in samples:
        status = str(sample["status"])
        statuses[status] = statuses.get(status, 0) + 1

    def percentile(fraction):
        if not latencies:
            return None
        index = (len(latencies) - 1) * fraction
        low, high = math.floor(index), math.ceil(index)
        return latencies[low] + (latencies[high] - latencies[low]) * (index - low)

    return {
        "requests": len(samples),
        "statuses": statuses,
        "errors": sum("error" in sample for sample in samples),
        "latency_ms_all_outcomes": {
            "min": min(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "max": max(latencies) if latencies else None,
        },
    }


def begin_partial_request(base, month):
    """Hold the final byte of a valid body on the qualified loopback socket."""
    address = urllib.parse.urlsplit(base)
    if address.scheme != "http" or address.hostname != "127.0.0.1" or not address.port:
        raise QualificationError("partial request requires qualified loopback HTTP")
    body = json.dumps({"month": month}).encode()
    connection = socket.create_connection((address.hostname, address.port), timeout=5)
    sample = {
        "path": "/predict",
        "payload": {"month": month},
        "status": None,
        "body": None,
        "started_monotonic_ns": time.perf_counter_ns(),
        "started_utc": dt.datetime.now(dt.UTC).isoformat(),
    }
    headers = (
        "POST /predict HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{address.port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode()
    try:
        connection.sendall(headers + body[:-1])
    except BaseException:
        connection.close()
        raise
    return connection, body[-1:], sample


def finish_partial_request(connection, final_byte, sample):
    try:
        connection.sendall(final_byte)
        sample["final_byte_sent_utc"] = dt.datetime.now(dt.UTC).isoformat()
        response = http.client.HTTPResponse(connection)
        response.begin()
        sample["status"] = response.status
        body = response.read(MAX_HTTP_BYTES + 1)
        response.close()
        if len(body) > MAX_HTTP_BYTES:
            raise QualificationError("partial response exceeds size limit")
        sample["body"] = body.decode("utf-8", errors="replace")
        sample["json"] = strict_json(sample["body"])
    except (OSError, ValueError, http.client.HTTPException) as error:
        sample["error"] = f"{type(error).__name__}: {error}"
    finally:
        connection.close()
        sample["finished_monotonic_ns"] = time.perf_counter_ns()
        sample["latency_ms"] = (
            sample["finished_monotonic_ns"] - sample["started_monotonic_ns"]
        ) / 1_000_000
    return sample


class Consumer:
    def __init__(
        self, image, output, source, *, context="desktop-linux", run=subprocess.run
    ):
        if context != "desktop-linux" or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", image
        ):
            raise QualificationError(
                "use desktop-linux and one exact full local image ID"
            )
        for key in ("source_commit", "source_tree"):
            if not re.fullmatch(r"[0-9a-f]{40}", source.get(key, "")):
                raise QualificationError(f"source metadata requires {key}")
        required = {
            "src/serve.py",
            "src/train.py",
            "src/features.py",
            "src/provenance.py",
            "requirements.txt",
        }
        files = source.get("files", {})
        if (
            not isinstance(files, dict)
            or not required.issubset(files)
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in files.values()
            )
        ):
            raise QualificationError(
                "source metadata requires current runtime file digests"
            )
        self.image, self.output, self.context, self.run = image, output, context, run
        self.owner = uuid.uuid4().hex
        self.names = []
        self.cleaning = False
        self.lock = threading.RLock()
        self.report = {
            "schema_version": 1,
            "scope": "isolated local container consumer",
            "started_utc": dt.datetime.now(dt.UTC).isoformat(),
            "status": "running",
            "context": context,
            "image_config_id": image,
            "source": source,
            "owner": self.owner,
            "commands": [],
            "containers": [],
            "checks": [],
            "samples": {},
            "summaries": {},
            "cleanup": [],
            "workload": {
                "warmup": 10,
                "sequential": 100,
                "concurrent": 200,
                "clients": 2,
                "overload_bursts": 10,
                "overload_clients": 16,
                "request_timeout_seconds": 5,
            },
            "limits": {
                "cpus": 0.5,
                "memory_bytes": MEMORY_BYTES,
                "pids": 64,
                "workers": 1,
                "uvicorn_concurrency": 8,
                "shutdown_seconds": 5,
            },
            "limitations": [
                "No production scale or security certification.",
                "No Kubernetes experiment or real prospective service.",
                "Overload outcomes are observations; no 503 count is promised.",
            ],
        }

    def save(self):
        with self.lock:
            if self.cleaning:
                try:
                    checkpoint(self.output, self.report)
                except OSError as error:
                    self.report.setdefault("evidence_errors", []).append(str(error))
            else:
                checkpoint(self.output, self.report)

    def docker(self, *args, timeout=45, check=True):
        argv = ["docker", "--context", self.context, *args]
        record = {"argv": argv, "timeout_seconds": timeout, "status": "running"}
        with self.lock:
            self.report["commands"].append(record)
            self.save()
        started = time.perf_counter()
        try:
            result = self.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self.lock:
                record.update(
                    status="completed",
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            if check and result.returncode:
                raise QualificationError(
                    f"Docker {args[0]} failed ({result.returncode})"
                )
            return result
        except BaseException as error:
            with self.lock:
                record.update(status="failed", error=f"{type(error).__name__}: {error}")
                if isinstance(error, subprocess.TimeoutExpired):
                    record["stdout"] = str(error.stdout or "")
                    record["stderr"] = str(error.stderr or "")
            raise
        finally:
            with self.lock:
                record["duration_seconds"] = time.perf_counter() - started
                self.save()

    def inspect(self, container):
        return strict_json(self.docker("inspect", container).stdout)[0]

    def flags(self, name):
        flags = [
            "--name",
            name,
            "--label",
            OWNER_LABEL + "=" + self.owner,
            "--pull=never",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--cpus=0.5",
            "--memory=512m",
            "--pids-limit=64",
            "--stop-timeout=5",
        ]
        for key, value in THREAD_ENV.items():
            flags.extend(["--env", f"{key}={value}"])
        return flags

    def start(self, kind, mounts=(), *, publish=True):
        name = f"freight-qualification-{self.owner}-{kind}"
        self.names.append(name)
        argv = ["run", "--detach", *self.flags(name)]
        if publish:
            argv.extend(["--publish", "127.0.0.1::8000"])
        for source, destination in mounts:
            argv.extend(
                ["--mount", f"type=bind,source={source},target={destination},readonly"]
            )
        container = self.docker(*argv, self.image, *SERVICE_COMMAND).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise QualificationError("Docker did not return an exact container ID")
        document = self.inspect(container)
        base = container_policy(document, self.image, self.owner) if publish else None
        self.report["containers"].append(
            {"kind": kind, "id": container, "inspect": document}
        )
        self.save()
        return container, base

    def health(self, base, container):
        attempts = []
        self.report["samples"][f"startup-{container[:12]}"] = attempts
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            sample = request(base, "/health", timeout=1)
            attempts.append(sample)
            self.save()
            if sample["status"] == 200:
                return sample["json"]
            if not self.inspect(container)["State"]["Running"]:
                raise QualificationError("service exited before readiness")
            time.sleep(0.2)
        raise QualificationError("service did not become ready within 45 seconds")

    def expect(self, name, condition):
        self.report["checks"].append({"name": name, "passed": bool(condition)})
        self.save()
        if not condition:
            raise QualificationError(name)

    def validate_prediction(self, sample, identity, month):
        body = sample.get("json", {})
        return (
            sample["status"] == 200
            and all(body.get(k) == v for k, v in identity.items())
            and (
                body.get("month") == month
                and type(body.get("predicted_volume")) is int
                and body["predicted_volume"] >= 0
                and type(body.get("naive_same_month_last_year")) is int
            )
        )

    def batch(self, phase, base, month, size, clients):
        with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
            samples = list(
                executor.map(
                    lambda _: request(base, "/predict", {"month": month}), range(size)
                )
            )
        self.report["samples"][phase] = samples
        self.report["summaries"][phase] = samples_summary(samples)
        self.save()
        return samples

    def fixture_cases(self):
        fixtures = self.output / "fixtures"
        fixtures.mkdir()
        (fixtures / "missing-model").mkdir()
        name = f"freight-qualification-{self.owner}-fixture-builder"
        self.names.append(name)
        self.docker(
            "run",
            "--rm",
            *self.flags(name),
            "--mount",
            f"type=bind,source={fixtures},target=/evidence",
            "--entrypoint",
            "python",
            self.image,
            "-c",
            CREATE_FIXTURES,
        )
        cases = [
            (
                "invalid-model",
                fixtures / "invalid-model.joblib",
                "/app/models/model.joblib",
                "Application startup failed",
            ),
            (
                "invalid-schema",
                fixtures / "invalid-schema.joblib",
                "/app/models/model.joblib",
                "feature columns",
            ),
            (
                "missing-model",
                fixtures / "missing-model",
                "/app/models",
                "model artifact not found",
            ),
            (
                "noncontiguous-history",
                fixtures / "noncontiguous-history.csv",
                "/app/data/shipments.csv",
                "contiguous monthly",
            ),
        ]
        for kind, source, destination, reason in cases:
            container, _ = self.start(kind, [(source, destination)], publish=False)
            exited = self.docker("wait", container, timeout=25).stdout.strip()
            document = self.inspect(container)
            logs = (
                self.docker("logs", container).stdout
                + self.report["commands"][-1]["stderr"]
            )
            self.report.setdefault("refusals", []).append(
                {
                    "kind": kind,
                    "exit_code": exited,
                    "state": document["State"],
                    "logs": logs,
                }
            )
            self.expect(
                f"{kind} refuses startup",
                exited != "0" and reason in logs and not document["State"]["OOMKilled"],
            )

    def cleanup(self):
        self.cleaning = True
        for name in reversed(self.names):
            record = {"name": name, "removed": False}
            self.report["cleanup"].append(record)
            try:
                result = self.docker("inspect", name, check=False, timeout=10)
                if result.returncode:
                    if (
                        "No such object" in result.stderr
                        or "No such container" in result.stderr
                    ):
                        record["already_absent"] = True
                        continue
                    raise QualificationError(
                        "owned container state unavailable during cleanup"
                    )
                document = strict_json(result.stdout)[0]
                if document["Config"].get("Labels", {}).get(OWNER_LABEL) != self.owner:
                    raise QualificationError("refused cleanup of an unowned container")
                self.docker("rm", "--force", document["Id"], timeout=15)
                record["removed"] = True
            except BaseException as error:
                record["error"] = f"{type(error).__name__}: {error}"
            finally:
                self.save()

    def qualify(self):
        image = strict_json(self.docker("image", "inspect", self.image).stdout)[0]
        self.report["image_inspect"] = image
        self.expect(
            "exact Linux image with numeric user",
            image["Id"] == self.image
            and image["Os"] == "linux"
            and image["Config"]["User"] == "10001:10001",
        )
        container, base = self.start("main")
        health = self.health(base, container)
        process = strict_json(
            self.docker("exec", container, "python", "-c", INSPECT_PROCESS).stdout
        )
        self.report["process"] = process
        identity, rows = process_policy(process, self.report["source"]["files"])
        self.report["identity"] = identity
        self.expect(
            "health identity and readiness",
            health.get("status") == "ok"
            and health.get("model_loaded") is True
            and all(health.get(k) == v for k, v in identity.items()),
        )
        window_sample = request(base, "/forecast-window")
        self.report["samples"]["window"] = [window_sample]
        window = window_sample.get("json", {})
        first = month_after(rows[0]["date"][:7], 12)
        last = month_after(rows[-1]["date"][:7], 1)
        self.expect(
            "supported forecast window",
            window_sample["status"] == 200
            and window.get("first_supported_month") == first
            and window.get("last_supported_month") == last
            and window.get("observed_through") == rows[-1]["date"][:7]
            and window.get("next_unobserved_month") == last
            and window.get("horizon_months") == 1
            and all(window.get(k) == v for k, v in identity.items()),
        )
        boundary = [
            request(base, "/predict", {"month": month}) for month in (first, last)
        ]
        self.report["samples"]["boundary"] = boundary
        self.expect(
            "supported boundary predictions",
            all(
                self.validate_prediction(sample, identity, month)
                for sample, month in zip(boundary, (first, last), strict=True)
            ),
        )
        invalid = [
            ({"month": "bad"}, 422),
            ({"month": "0000-01"}, 422),
            ({}, 422),
            ({"month": month_after(last, 1)}, 400),
        ]
        samples = [request(base, "/predict", payload) for payload, _ in invalid]
        self.report["samples"]["invalid-input"] = samples
        self.expect(
            "invalid input responses",
            [s["status"] for s in samples] == [status for _, status in invalid],
        )
        for phase, size, clients in (
            ("warmup", 10, 1),
            ("sequential", 100, 1),
            ("concurrent", 200, 2),
        ):
            samples = self.batch(phase, base, last, size, clients)
            self.expect(
                f"{phase} supported workload",
                all(self.validate_prediction(s, identity, last) for s in samples),
            )
        overload = []
        for burst in range(10):
            samples = self.batch(f"overload-{burst}", base, last, 16, 16)
            overload.extend(samples)
            self.expect(
                f"overload-{burst} successful response identities",
                all(
                    self.validate_prediction(s, identity, last)
                    for s in samples
                    if s["status"] == 200
                ),
            )
        self.report["summaries"]["overload-all"] = samples_summary(overload)
        metrics = request(base, "/metrics/")
        self.report["samples"]["metrics"] = [metrics]
        self.expect(
            "supported request and prediction metrics",
            metrics["status"] == 200
            and "http_requests_total" in metrics["body"]
            and "predicted_volume_moves" in metrics["body"],
        )
        # Keep one accepted request incomplete until termination is observed.
        connection, final_byte, controlled = begin_partial_request(base, last)
        self.report["shutdown_control"] = {"request": controlled}
        barrier = threading.Barrier(3)
        events = [threading.Event(), threading.Event()]

        def in_flight(index):
            barrier.wait(timeout=5)
            return [
                request(
                    base,
                    "/predict",
                    {"month": last},
                    started_event=events[index] if count == 0 else None,
                )
                for count in range(50)
            ]

        try:
            accepted = strict_json(
                self.docker(
                    "exec", container, "python", "-c", ACCEPTED_CONNECTION
                ).stdout
            )
            self.report["shutdown_control"]["acceptance_probe"] = accepted
            self.expect(
                "controlled request accepted by process 1",
                bool(accepted["pid1_established_api_connections"]),
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(in_flight, index) for index in range(2)]
                barrier.wait(timeout=5)
                if not all(event.wait(timeout=5) for event in events):
                    raise QualificationError("shutdown workload did not start")
                started = time.perf_counter()
                stop_requested_ns = time.perf_counter_ns()
                stop_future = executor.submit(
                    self.docker, "stop", "--time", "5", container, timeout=10
                )
                deadline = time.monotonic() + 3
                shutdown_signal = None
                while time.monotonic() < deadline:
                    observed = self.docker(
                        "logs", "--timestamps", "--tail", "20", container, timeout=2
                    )
                    if "Shutting down" in observed.stdout + observed.stderr:
                        shutdown_signal = {
                            "observed_utc": dt.datetime.now(dt.UTC).isoformat(),
                            "stdout": observed.stdout,
                            "stderr": observed.stderr,
                        }
                        break
                    time.sleep(0.1)
                if shutdown_signal is None:
                    raise QualificationError(
                        "termination was not observed before body completion"
                    )
                with self.lock:
                    finish_partial_request(connection, final_byte, controlled)
                stop_future.result()
                stopped_seconds = time.perf_counter() - started
                self.report["shutdown_control"]["termination_observed"] = (
                    shutdown_signal
                )
                self.report["samples"]["shutdown-batch"] = [
                    sample for future in futures for sample in future.result()
                ]
        finally:
            connection.close()
        self.report["samples"]["shutdown-controlled-request"] = [controlled]
        stopped = self.inspect(container)
        logs = self.docker("logs", container)
        overlap = sum(
            sample["started_monotonic_ns"]
            <= stop_requested_ns
            < sample["finished_monotonic_ns"]
            for sample in self.report["samples"]["shutdown-batch"]
        )
        self.report["shutdown"] = {
            "seconds": stopped_seconds,
            "stop_requested_monotonic_ns": stop_requested_ns,
            "requests_active_at_stop_request": overlap,
            "state": stopped["State"],
            "stdout": logs.stdout,
            "stderr": logs.stderr,
        }
        self.expect(
            "graceful bounded shutdown",
            self.validate_prediction(controlled, identity, last)
            and stopped["State"]["ExitCode"] == 0
            and not stopped["State"]["OOMKilled"]
            and stopped_seconds <= 7
            and "Application shutdown complete" in logs.stderr + logs.stdout,
        )
        restarted, restart_base = self.start("restart")
        restart_health = self.health(restart_base, restarted)
        restart_prediction = request(restart_base, "/predict", {"month": last})
        self.report["samples"]["restart"] = [restart_prediction]
        self.expect(
            "same-image healthy restart",
            all(restart_health.get(k) == v for k, v in identity.items())
            and restart_prediction.get("json") == boundary[-1].get("json"),
        )
        self.docker("stop", "--time", "5", restarted, timeout=10)
        self.fixture_cases()
        self.report["status"] = "passed"
        self.save()


def month_after(month, offset):
    year, number = map(int, month.split("-"))
    ordinal = year * 12 + number - 1 + offset
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="desktop-linux")
    parser.add_argument(
        "--image", required=True, help="full sha256 local image config ID"
    )
    parser.add_argument("--source-metadata", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="new evidence directory"
    )
    args = parser.parse_args(argv)
    consumer = None
    try:
        source = strict_json(args.source_metadata.read_bytes())
        if not isinstance(source, dict):
            raise QualificationError("source metadata must be an object")
        consumer = Consumer(args.image, args.output, source, context=args.context)
        consumer.output = admit_output(args.output)
        consumer.save()
        try:
            consumer.qualify()
        except BaseException as error:
            consumer.report.update(
                status="failed", error=f"{type(error).__name__}: {error}"
            )
            with suppress(OSError):
                consumer.save()
        finally:
            consumer.cleanup()
            if any(
                "error" in item for item in consumer.report["cleanup"]
            ) or consumer.report.get("evidence_errors"):
                consumer.report["status"] = "failed"
            consumer.report["finished_utc"] = dt.datetime.now(dt.UTC).isoformat()
            consumer.save()
        print(
            f"Local service qualification {consumer.report['status']}: "
            f"{consumer.output}"
        )
        return 0 if consumer.report["status"] == "passed" else 1
    except (OSError, ValueError, TypeError) as error:
        print(f"Local service qualification refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
